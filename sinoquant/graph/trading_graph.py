# SinaQuant/graph/trading_graph.py

import os
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List
import time

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from sinoquant.llm_adapters import ChatDashScopeOpenAIUnified, ChatGoogleOpenAI

from langgraph.prebuilt import ToolNode

from sinoquant.agents import *
from sinoquant.default_config import DEFAULT_CONFIG
from sinoquant.agents.utils.memory import FinancialSituationMemory

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger

# 导入日志模块
from sinoquant.utils.logging_manager import get_logger
logger = get_logger('agents')
from sinoquant.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from sinoquant.dataflows.interface import set_config
from sinoquant.utils.text_utils import remove_thinking_content

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


def create_llm_by_provider(provider: str, model: str, backend_url: str, temperature: float, max_tokens: int, timeout: int, api_key: str = None, max_retries: int = 3):
    """
    根据供应商名称创建对应的 LLM 实例 —— 多智能体交易引擎的 LLM 工厂函数

    该函数是整个多智能体交易引擎的 LLM 创建入口，支持以下供应商：
    - google: 使用 ChatGoogleOpenAI 适配器（支持 Gemini 系列）
    - dashscope: 使用 ChatDashScopeOpenAIUnified 适配器（支持通义千问系列）
    - deepseek: 使用 ChatDeepSeekOpenAI 适配器（支持 DeepSeek 系列）
    - openai / siliconflow / openrouter / ollama: 使用标准 ChatOpenAI（OpenAI 兼容接口）
    - anthropic: 使用 ChatAnthropic 适配器（支持 Claude 系列）
    - qianfan / custom_openai: 使用 create_openai_compatible_llm 通用适配器
    - 其他未知供应商: 降级为 OpenAI 兼容模式（适用于自部署 vLLM 等）

    API Key 优先级：数据库/Web 界面配置 > 环境变量 > 默认值
    对于自定义厂家（如本地部署的 vLLM），如果未配置 API Key，
    会使用占位值 "EMPTY" 以适配无鉴权的推理服务。

    Args:
        provider: 供应商名称 (google, dashscope, deepseek, openai, siliconflow, openrouter, ollama, anthropic, qianfan, custom_openai, 或自定义)
        model: 模型名称（如 "deepseek-chat", "qwen-plus", "gemini-pro" 等）
        backend_url: API 后端地址，用于覆盖默认的供应商端点（如代理或私有部署地址）
        temperature: 温度参数，控制生成随机性（0-2，越高越随机）
        max_tokens: 最大生成 token 数，限制单次响应长度
        timeout: 请求超时时间（秒），防止长时间等待无响应
        api_key: API 密钥，必须通过数据库/Web 界面配置，而非硬编码
        max_retries: 最大重试次数（默认3次），应对网络波动和临时故障

    Returns:
        对应供应商的 LLM 实例（LangChain ChatModel 接口）

    Raises:
        ValueError: 当已知供应商未配置 API Key 时抛出
    """
    # 延迟导入，避免循环依赖；ChatDeepSeekOpenAI 是 DeepSeek 专用适配器，
    # create_openai_compatible_llm 是通用 OpenAI 兼容适配器工厂
    from sinoquant.llm_adapters.openai_compatible_base import ChatDeepSeekOpenAI, create_openai_compatible_llm

    logger.info(f"🔧 [创建LLM] provider={provider}, model={model}, url={backend_url}")
    logger.info(f"🔑 [API Key] 来源: {'数据库配置' if api_key else '未配置'}")

    # ========== Google 供应商 ==========
    # 使用 ChatGoogleOpenAI 适配器，支持 Gemini 系列模型
    # 通过 base_url 参数可指向代理或私有部署的 Google AI 端点
    if provider.lower() == "google":
        if not api_key:
            raise ValueError(
                "Google API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return ChatGoogleOpenAI(
            model=model,
            google_api_key=api_key,
            base_url=backend_url if backend_url else None,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries
        )

    # ========== DashScope 供应商（阿里云通义千问）==========
    # 使用 ChatDashScopeOpenAIUnified 适配器，统一 DashScope 的 OpenAI 兼容接口
    # 注意：该适配器使用 request_timeout 而非 timeout 参数名
    elif provider.lower() == "dashscope":
        if not api_key:
            raise ValueError(
                "DashScope API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return ChatDashScopeOpenAIUnified(
            model=model,
            api_key=api_key,
            base_url=backend_url if backend_url else None,
            temperature=temperature,
            max_tokens=max_tokens,
            request_timeout=timeout,
            max_retries=max_retries
        )

    # ========== DeepSeek 供应商 ==========
    # 使用 ChatDeepSeekOpenAI 专用适配器
    # 注意：timeout 和 max_retries 仅在 timeout 非零时传入，
    # 因为 DeepSeek 的适配器对这两个参数的处理方式不同
    elif provider.lower() == "deepseek":
        if not api_key:
            raise ValueError(
                "DeepSeek API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return ChatDeepSeekOpenAI(
            model=model,
            api_key=api_key,
            base_url=backend_url,
            temperature=temperature,
            max_tokens=max_tokens,
            **({"timeout": timeout, "max_retries": max_retries} if timeout else {})
        )

    # ========== OpenAI 及其兼容供应商 ==========
    # siliconflow（硅基流动）、openrouter（模型路由）、ollama（本地部署）
    # 这些供应商均兼容 OpenAI API 格式，直接使用 ChatOpenAI 即可
    elif provider.lower() in ["openai", "siliconflow", "openrouter", "ollama"]:
        if not api_key:
            raise ValueError(
                f"{provider} API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return ChatOpenAI(
            model=model,
            base_url=backend_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries
        )

    # ========== Anthropic 供应商（Claude 系列）==========
    # 使用 ChatAnthropic 适配器，支持 Claude 系列模型
    elif provider.lower() == "anthropic":
        if not api_key:
            raise ValueError(
                "Anthropic API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return ChatAnthropic(
            model=model,
            base_url=backend_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries
        )

    # ========== 百度千帆 / 自定义 OpenAI 兼容供应商 ==========
    # 使用通用 create_openai_compatible_llm 工厂函数创建实例
    # 适用于需要特殊认证或自定义请求格式的供应商
    elif provider.lower() in ["qianfan", "custom_openai"]:
        if not api_key:
            raise ValueError(
                f"{provider} API密钥未找到。请在 Web 界面配置 API Key（设置 -> 大模型厂家）。"
            )

        return create_openai_compatible_llm(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=backend_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries
        )

    # ========== 未知供应商：降级为 OpenAI 兼容模式 ==========
    # 适用于自部署的推理服务（如 vLLM、TGI 等），
    # 这些服务通常兼容 OpenAI API 格式但可能不需要鉴权
    else:
        logger.info(f"🔧 使用 OpenAI 兼容模式处理自定义厂家: {provider}")

        # 对于无鉴权的本地部署服务（如 vLLM），使用 "EMPTY" 作为占位 API Key
        if not api_key:
            logger.warning(f"⚠️ 未找到自定义厂家 {provider} 的 API Key，使用占位值 EMPTY（适配无鉴权 vLLM）")
            api_key = "EMPTY"

        return ChatOpenAI(
            model=model,
            base_url=backend_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries
        )


class SinaQuantGraph:
    """
    多智能体交易引擎的核心编排类

    基于辩论式工作流（Debate-based Workflow）组织多个交易智能体协同工作：
    1. 分析师层（Analysts）：市场分析师、基本面分析师、新闻分析师、社交媒体分析师
       - 各分析师独立收集数据并生成分析报告
    2. 研究层（Researchers）：看涨研究员 vs 看跌研究员，通过辩论对抗产生更全面的分析
       - Research Manager 主持辩论并做出裁决
    3. 交易层（Trader）：基于研究报告生成交易决策
    4. 风险管理层（Risk Management）：激进/保守/中性三方风险评估
       - Risk Judge 综合评估后给出最终建议

    工作流使用 LangGraph 图执行引擎，支持流式输出和进度回调。
    """

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """
        初始化多智能体交易引擎的所有组件

        初始化流程：
        1. 加载配置并同步到数据接口
        2. 创建快速推理模型和深度推理模型的 LLM 实例（支持混合模式）
        3. 初始化工具包和数据缓存目录
        4. 初始化各智能体的记忆系统（基于 ChromaDB 的长期记忆）
        5. 创建工具节点（按数据源类型分组）
        6. 初始化条件逻辑（控制辩论轮次等）
        7. 构建并编译 LangGraph 执行图

        Args:
            selected_analysts: 启用的分析师类型列表，默认启用全部四种分析师。
                可选值: "market", "social", "news", "fundamentals"
            debug: 是否启用调试模式。调试模式下会打印详细的消息追踪信息
            config: 配置字典，若为 None 则使用默认配置（DEFAULT_CONFIG）。
                配置优先级：数据库/Web界面 > 环境变量 > 默认值
        """
        self.debug = debug
        # 加载配置：用户自定义配置 > DEFAULT_CONFIG 默认配置
        self.config = config or DEFAULT_CONFIG

        # 将配置同步到数据接口层（Tushare, AKShare, BaoStock 等）
        # 确保数据源使用正确的 API Key 和参数
        set_config(self.config)

        # 创建数据缓存目录（用于存储股票行情、财务数据等缓存文件）
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # ============================================================
        # 第一阶段：初始化 LLM 实例
        # 交易引擎使用双模型架构：
        #   - 快速推理模型（quick_thinking_llm）：用于简单任务，如工具调用、格式化输出
        #   - 深度推理模型（deep_thinking_llm）：用于复杂推理，如辩论决策、风险评估
        # 支持两种模式：
        #   - 单供应商模式：快速模型和深度模型来自同一供应商
        #   - 混合模式：快速模型和深度模型来自不同供应商（如 DeepSeek + Qwen）
        # ============================================================

        # 从配置中读取模型参数（优先使用用户配置，否则使用默认值）
        quick_config = self.config.get("quick_model_config", {})
        deep_config = self.config.get("deep_model_config", {})

        # 读取快速模型参数：控制轻量级任务的生成行为
        quick_max_tokens = quick_config.get("max_tokens", 4000)
        quick_temperature = quick_config.get("temperature", 0.7)
        quick_timeout = quick_config.get("timeout", 180)
        quick_retries = quick_config.get("retry_times", 3)

        # 读取深度模型参数：控制重量级推理任务的生成行为
        deep_max_tokens = deep_config.get("max_tokens", 4000)
        deep_temperature = deep_config.get("temperature", 0.7)
        deep_timeout = deep_config.get("timeout", 180)
        deep_retries = deep_config.get("retry_times", 3)

        # 检查是否为混合模式（快速模型和深度模型来自不同厂家）
        # 混合模式允许组合不同供应商的优势，例如用 Qwen 做快速推理，DeepSeek 做深度思考
        quick_provider = self.config.get("quick_provider")
        deep_provider = self.config.get("deep_provider")
        quick_backend_url = self.config.get("quick_backend_url")
        deep_backend_url = self.config.get("deep_backend_url")

        if quick_provider and deep_provider and quick_provider != deep_provider:
            # 混合模式：快速模型和深度模型来自不同厂家
            # 需要分别创建两个不同供应商的 LLM 实例
            logger.info(f"🔀 [混合模式] 检测到不同厂家的模型组合")
            logger.info(f"   快速模型: {self.config['quick_think_llm']} ({quick_provider})")
            logger.info(f"   深度模型: {self.config['deep_think_llm']} ({deep_provider})")

            # 使用统一的工厂函数创建 LLM 实例，每个模型使用各自的供应商和 API Key
            self.quick_thinking_llm = create_llm_by_provider(
                provider=quick_provider,
                model=self.config["quick_think_llm"],
                backend_url=quick_backend_url or self.config.get("backend_url", ""),
                temperature=quick_temperature,
                max_tokens=quick_max_tokens,
                timeout=quick_timeout,
                api_key=self.config.get("quick_api_key"),
                max_retries=quick_retries
            )

            self.deep_thinking_llm = create_llm_by_provider(
                provider=deep_provider,
                model=self.config["deep_think_llm"],
                backend_url=deep_backend_url or self.config.get("backend_url", ""),
                temperature=deep_temperature,
                max_tokens=deep_max_tokens,
                timeout=deep_timeout,
                api_key=self.config.get("deep_api_key"),
                max_retries=deep_retries
            )

            logger.info(f"✅ [混合模式] LLM 实例创建成功")

        else:
            # 单供应商模式：快速模型和深度模型使用同一供应商
            # 这是更常见的配置方式，简化了 API Key 管理
            provider = self.config["llm_provider"]
            backend_url = self.config.get("backend_url", "")
            quick_api_key = self.config.get("quick_api_key")
            deep_api_key = self.config.get("deep_api_key")

            logger.info(f"🔧 [{provider}] 使用统一 LLM 初始化")
            logger.info(f"   快速模型: {self.config['quick_think_llm']}, max_tokens={quick_max_tokens}, temperature={quick_temperature}, timeout={quick_timeout}s")
            logger.info(f"   深度模型: {self.config['deep_think_llm']}, max_tokens={deep_max_tokens}, temperature={deep_temperature}, timeout={deep_timeout}s")

            # 快速推理模型：用于工具调用、格式化等轻量级任务
            self.quick_thinking_llm = create_llm_by_provider(
                provider=provider,
                model=self.config["quick_think_llm"],
                backend_url=backend_url,
                temperature=quick_temperature,
                max_tokens=quick_max_tokens,
                timeout=quick_timeout,
                api_key=quick_api_key,
                max_retries=quick_retries,
            )

            # 深度推理模型：用于辩论决策、风险评估等复杂推理任务
            self.deep_thinking_llm = create_llm_by_provider(
                provider=provider,
                model=self.config["deep_think_llm"],
                backend_url=backend_url,
                temperature=deep_temperature,
                max_tokens=deep_max_tokens,
                timeout=deep_timeout,
                api_key=deep_api_key,
                max_retries=deep_retries,
            )

            logger.info(f"✅ [{provider}] LLM 实例创建成功")

        # ============================================================
        # 第二阶段：初始化工具包
        # Toolkit 封装了所有数据获取工具（行情、新闻、财务等）
        # 每个分析师节点绑定特定的工具子集
        # ============================================================
        self.toolkit = Toolkit(config=self.config)

        # ============================================================
        # 第三阶段：初始化记忆系统
        # 使用 FinancialSituationMemory（基于 ChromaDB 向量数据库）为每个
        # 智能体维护独立的长期记忆，存储历史反思和经验教训
        # 记忆在反思阶段（reflect_and_remember）更新，在分析时检索
        # ============================================================
        memory_enabled = self.config.get("memory_enabled", True)
        if memory_enabled:
            # 使用单例ChromaDB管理器，避免并发创建冲突
            # 每个智能体拥有独立的记忆命名空间，互不干扰
            self.bull_memory = FinancialSituationMemory("bull_memory", self.config)        # 看涨研究员记忆
            self.bear_memory = FinancialSituationMemory("bear_memory", self.config)        # 看跌研究员记忆
            self.trader_memory = FinancialSituationMemory("trader_memory", self.config)    # 交易员记忆
            self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)  # 投资裁判记忆
            self.risk_manager_memory = FinancialSituationMemory("risk_manager_memory", self.config)  # 风险经理记忆
        else:
            # 记忆功能关闭时创建空对象，避免后续代码需要判空
            self.bull_memory = None
            self.bear_memory = None
            self.trader_memory = None
            self.invest_judge_memory = None
            self.risk_manager_memory = None

        # ============================================================
        # 第四阶段：创建工具节点
        # 将 Toolkit 中的工具按数据源类型分组为 ToolNode，
        # 每个分析师节点执行后调用对应的工具节点获取数据
        # ============================================================
        self.tool_nodes = self._create_tool_nodes()

        # ============================================================
        # 第五阶段：初始化条件逻辑和图组件
        # ConditionalLogic 控制辩论轮次和流程分支
        # GraphSetup 负责构建 LangGraph 执行图
        # Propagator 负责状态初始化和图执行参数
        # Reflector 负责反思和记忆更新
        # SignalProcessor 负责信号处理和决策提取
        # ============================================================

        # 条件逻辑：控制辩论轮次（投资辩论 + 风险辩论各有独立轮次配置）
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config.get("max_debate_rounds", 1),              # 投资辩论最大轮次
            max_risk_discuss_rounds=self.config.get("max_risk_discuss_rounds", 1)    # 风险辩论最大轮次
        )
        logger.info(f"🔧 [ConditionalLogic] 初始化完成:")
        logger.info(f"   - max_debate_rounds: {self.conditional_logic.max_debate_rounds}")
        logger.info(f"   - max_risk_discuss_rounds: {self.conditional_logic.max_risk_discuss_rounds}")

        # 图构建器：将所有组件组装成 LangGraph 执行图
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,      # 快速推理模型
            self.deep_thinking_llm,       # 深度推理模型
            self.toolkit,                  # 工具包
            self.tool_nodes,               # 工具节点
            self.bull_memory,              # 看涨研究员记忆
            self.bear_memory,              # 看跌研究员记忆
            self.trader_memory,            # 交易员记忆
            self.invest_judge_memory,      # 投资裁判记忆
            self.risk_manager_memory,      # 风险经理记忆
            self.conditional_logic,        # 条件逻辑（辩论轮次控制）
            self.config,                   # 全局配置
            getattr(self, 'react_llm', None),  # ReAct 模式的 LLM（可选）
        )

        # 状态传播器：负责创建初始状态和配置图执行参数
        self.propagator = Propagator()
        # 反思器：基于交易结果反思决策并更新记忆（使用快速模型以降低成本）
        self.reflector = Reflector(self.quick_thinking_llm)
        # 信号处理器：从非结构化的交易决策文本中提取结构化信号
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # ============================================================
        # 第六阶段：状态追踪和图构建
        # ============================================================
        # 运行时状态追踪
        self.curr_state = None             # 当前最新状态（供反思使用）
        self.ticker = None                 # 当前分析的股票代码
        self.log_states_dict = {}          # 日期 -> 完整状态的映射（用于持久化）

        # 根据选定的分析师类型构建并编译 LangGraph 执行图
        # 图节点包括：分析师 -> 工具节点 -> 消息清理 -> 研究员 -> 辩论 -> 交易员 -> 风险评估
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """
        按数据源类型创建工具节点

        工具节点是 LangGraph 中的特殊节点，负责执行 LLM 生成的 tool_calls。
        每个分析师节点绑定了特定的工具子集，对应的工具节点包含这些工具的执行器。

        工具节点的组织方式：
        - 每个分析师类型对应一个工具节点，key 为分析师类型名称
        - ToolNode 包含该分析师可调用的所有数据获取工具
        - LLM 在分析师节点中决定调用哪些工具，工具节点负责实际执行

        注意：ToolNode 包含所有可能的工具，但 LLM 只会调用它绑定的工具。
        ToolNode 的作用是执行 LLM 生成的 tool_calls，而不是限制 LLM 可以调用哪些工具。
        LLM 可调用的工具由分析师节点的 bind_tools() 决定。

        工具节点与分析师节点的对应关系：
        - "market" -> Market Analyst（市场分析师）：获取行情数据和技术指标
        - "social" -> Social Analyst（社交媒体分析师）：获取社交舆情数据
        - "news" -> News Analyst（新闻分析师）：获取新闻资讯数据
        - "fundamentals" -> Fundamentals Analyst（基本面分析师）：获取财务和基本面数据

        Returns:
            Dict[str, ToolNode]: 以分析师类型为 key、ToolNode 为 value 的字典
        """
        return {
            # 市场数据工具节点：提供行情和技术分析数据
            # - get_stock_market_data_unified: 统一的市场数据接口（支持多数据源降级）
            # - get_china_stock_data: 中国 A 股专用数据接口
            "market": ToolNode(
                [
                    self.toolkit.get_stock_market_data_unified,
                    self.toolkit.get_china_stock_data,
                ]
            ),
            # 社交媒体情感工具节点：提供舆情分析数据
            # - get_stock_sentiment_unified: 统一的情感分析接口
            # - get_chinese_social_sentiment: 中文社交媒体专用情感接口
            "social": ToolNode(
                [
                    self.toolkit.get_stock_sentiment_unified,
                    self.toolkit.get_chinese_social_sentiment,
                ]
            ),
            # 新闻工具节点：提供新闻资讯数据
            # - get_stock_news_unified: 统一的新闻数据接口
            # - get_google_news: Google 新闻搜索
            # - get_global_news_openai: 使用 LLM 生成全球新闻摘要
            "news": ToolNode(
                [
                    self.toolkit.get_stock_news_unified,
                    self.toolkit.get_google_news,
                    self.toolkit.get_global_news_openai,
                ]
            ),
            # 基本面工具节点：提供财务和基本面分析数据
            # - get_stock_fundamentals_unified: 统一的基本面数据接口
            # - get_china_stock_data: 中国 A 股行情数据（基本面分析需要行情辅助）
            # - get_china_fundamentals: 中国 A 股专用基本面数据
            "fundamentals": ToolNode(
                [
                    self.toolkit.get_stock_fundamentals_unified,
                    self.toolkit.get_china_stock_data,
                    self.toolkit.get_china_fundamentals,
                ]
            ),
        }

    def propagate(self, company_name, trade_date, progress_callback=None, task_id=None):
        """
        执行多智能体交易引擎的核心分析流程

        该方法是整个交易引擎的入口，负责：
        1. 初始化图执行状态（股票代码、交易日期）
        2. 流式执行 LangGraph 图（分析师 -> 研究员 -> 辩论 -> 交易员 -> 风险评估）
        3. 记录每个节点的执行时间，构建性能数据
        4. 通过进度回调函数实时反馈执行进度
        5. 提取结构化决策并清洗思维链内容
        6. 持久化分析状态到 JSON 文件

        执行模式：
        - 有进度回调时：使用 stream_mode="updates" 获取节点级增量更新
        - 无进度回调时：使用 stream_mode="values" 获取完整状态快照
        - 调试模式下：额外记录消息追踪信息

        Args:
            company_name: 股票代码或公司名称（如 "000001.SZ", "贵州茅台"）
            trade_date: 分析日期（格式取决于数据源，通常为 "YYYY-MM-DD"）
            progress_callback: 可选的进度回调函数，接收字符串消息作为参数。
                用于前端实时显示分析进度（如 "📊 市场分析师"）
            task_id: 可选的任务 ID，用于关联性能数据和异步任务追踪

        Returns:
            Tuple[dict, dict]: (最终状态字典, 结构化决策字典)
            - 最终状态字典：包含所有分析师报告、辩论记录、交易决策等完整信息
            - 结构化决策字典：包含 action（买入/卖出/持有）、confidence（置信度）、
              reasoning（推理过程）等标准化字段
        """

        # ========== 第一步：初始化执行上下文 ==========
        # 记录接收到的参数，便于调试参数传递问题
        logger.debug(f"🔍 [GRAPH DEBUG] ===== SinaQuantGraph.propagate 接收参数 =====")
        logger.debug(f"🔍 [GRAPH DEBUG] 接收到的company_name: '{company_name}' (类型: {type(company_name)})")
        logger.debug(f"🔍 [GRAPH DEBUG] 接收到的trade_date: '{trade_date}' (类型: {type(trade_date)})")
        logger.debug(f"🔍 [GRAPH DEBUG] 接收到的task_id: '{task_id}'")

        self.ticker = company_name  # 保存当前分析的股票代码，供 _log_state 等方法使用
        logger.debug(f"🔍 [GRAPH DEBUG] 设置self.ticker: '{self.ticker}'")

        # 创建 LangGraph 的初始状态
        # 初始状态包含 company_of_interest（股票代码）和 trade_date（交易日期）
        # 这两个值会贯穿整个图执行流程，传递给每个智能体
        logger.debug(f"🔍 [GRAPH DEBUG] 创建初始状态，传递参数: company_name='{company_name}', trade_date='{trade_date}'")
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        logger.debug(f"🔍 [GRAPH DEBUG] 初始状态中的company_of_interest: '{init_agent_state.get('company_of_interest', 'NOT_FOUND')}'")
        logger.debug(f"🔍 [GRAPH DEBUG] 初始状态中的trade_date: '{init_agent_state.get('trade_date', 'NOT_FOUND')}'")

        # ========== 第二步：初始化节点计时器 ==========
        # 节点计时用于性能分析，帮助识别瓶颈节点
        node_timings = {}  # 记录每个节点的执行时间：{节点名: 耗时(秒)}
        total_start_time = time.time()  # 总体开始时间（用于计算总耗时）
        current_node_start = None  # 当前节点开始时间（节点切换时更新）
        current_node_name = None  # 当前节点名称（节点切换时更新）

        # 保存 task_id，后续构建性能数据时需要关联
        self._current_task_id = task_id

        # 根据是否有进度回调选择不同的 stream_mode
        # 有进度回调 -> updates 模式（获取节点级增量更新，适合实时进度展示）
        # 无进度回调 -> values 模式（获取完整状态快照，适合批量处理）
        args = self.propagator.get_graph_args(use_progress_callback=bool(progress_callback))

        if self.debug:
            # ========== 调试模式：带追踪和进度更新的图执行 ==========
            # 调试模式会记录所有中间状态，并打印 LLM 的完整消息输出
            trace = []
            final_state = None
            for chunk in self.graph.stream(init_agent_state, **args):
                # 记录节点计时：在节点切换时，记录上一个节点的耗时
                for node_name in chunk.keys():
                    if not node_name.startswith('__'):
                        # 如果有上一个节点，记录其结束时间
                        if current_node_name and current_node_start:
                            elapsed = time.time() - current_node_start
                            node_timings[current_node_name] = elapsed
                            logger.info(f"⏱️ [{current_node_name}] 耗时: {elapsed:.2f}秒")

                        # 开始新节点计时
                        current_node_name = node_name
                        current_node_start = time.time()
                        break

                # 根据流式模式处理不同格式的 chunk
                if progress_callback and args.get("stream_mode") == "updates":
                    # updates 模式：chunk = {"Market Analyst": {...}}
                    # 每次只包含当前节点的状态增量更新
                    self._send_progress_update(chunk, progress_callback)
                    # 累积状态更新：将增量更新合并到完整状态中
                    if final_state is None:
                        final_state = init_agent_state.copy()
                    for node_name, node_update in chunk.items():
                        if not node_name.startswith('__'):
                            final_state.update(node_update)
                else:
                    # values 模式：chunk = {"messages": [...], ...}
                    # 每次包含完整的状态快照
                    if len(chunk.get("messages", [])) > 0:
                        chunk["messages"][-1].pretty_print()
                    trace.append(chunk)
                    final_state = chunk

            if not trace and final_state:
                # updates 模式下，使用累积的状态（trace 为空但 final_state 有值）
                pass
            elif trace:
                final_state = trace[-1]
        else:
            # ========== 标准模式：不带详细追踪的图执行 ==========
            if progress_callback:
                # 有进度回调的情况：使用 updates 模式获取节点级进度
                trace = []
                final_state = None
                for chunk in self.graph.stream(init_agent_state, **args):
                    # 记录节点计时：在节点切换时，记录上一个节点的耗时
                    for node_name in chunk.keys():
                        if not node_name.startswith('__'):
                            # 如果有上一个节点，记录其结束时间
                            if current_node_name and current_node_start:
                                elapsed = time.time() - current_node_start
                                node_timings[current_node_name] = elapsed
                                logger.info(f"⏱️ [{current_node_name}] 耗时: {elapsed:.2f}秒")
                                logger.info(f"🔍 [TIMING] 节点切换: {current_node_name} → {node_name}")

                            # 开始新节点计时
                            current_node_name = node_name
                            current_node_start = time.time()
                            logger.info(f"🔍 [TIMING] 开始计时: {node_name}")
                            break

                    # 通过回调函数发送进度更新
                    self._send_progress_update(chunk, progress_callback)
                    # 累积状态更新：将每个节点的增量更新合并到完整状态
                    if final_state is None:
                        final_state = init_agent_state.copy()
                    for node_name, node_update in chunk.items():
                        if not node_name.startswith('__'):
                            final_state.update(node_update)
            else:
                # 无进度回调的情况：不需要实时进度展示，但仍需计时
                # 使用 stream 模式以便逐节点计时（而非 invoke 一次性执行）
                logger.info("⏱️ 使用 invoke 模式执行分析（无进度回调）")
                trace = []
                final_state = None
                for chunk in self.graph.stream(init_agent_state, **args):
                    # 记录节点计时
                    for node_name in chunk.keys():
                        if not node_name.startswith('__'):
                            # 如果有上一个节点，记录其结束时间
                            if current_node_name and current_node_start:
                                elapsed = time.time() - current_node_start
                                node_timings[current_node_name] = elapsed
                                logger.info(f"⏱️ [{current_node_name}] 耗时: {elapsed:.2f}秒")

                            # 开始新节点计时
                            current_node_name = node_name
                            current_node_start = time.time()
                            break

                    # 累积状态更新
                    if final_state is None:
                        final_state = init_agent_state.copy()
                    for node_name, node_update in chunk.items():
                        if not node_name.startswith('__'):
                            final_state.update(node_update)

        # ========== 第三步：完成节点计时 ==========
        # 图执行结束后，记录最后一个节点的耗时（因为它没有下一个节点来触发计时记录）
        if current_node_name and current_node_start:
            elapsed = time.time() - current_node_start
            node_timings[current_node_name] = elapsed
            logger.info(f"⏱️ [{current_node_name}] 耗时: {elapsed:.2f}秒")

        # 计算总执行时间
        total_elapsed = time.time() - total_start_time

        # 调试日志：验证节点计时数据的完整性
        logger.info(f"🔍 [TIMING DEBUG] 节点计时数量: {len(node_timings)}")
        logger.info(f"🔍 [TIMING DEBUG] 总耗时: {total_elapsed:.2f}秒")
        logger.info(f"🔍 [TIMING DEBUG] 节点列表: {list(node_timings.keys())}")

        # 打印详细的时间统计报告
        logger.info("🔍 [TIMING DEBUG] 准备调用 _print_timing_summary")
        self._print_timing_summary(node_timings, total_elapsed)
        logger.info("🔍 [TIMING DEBUG] _print_timing_summary 调用完成")

        # ========== 第四步：构建性能数据并写入状态 ==========
        # 将节点计时数据转换为结构化的性能指标，包含分类统计和百分比
        performance_data = self._build_performance_data(node_timings, total_elapsed)

        # 将性能数据添加到最终状态中，供前端展示和后续分析使用
        final_state['performance_metrics'] = performance_data

        # 保存当前状态，供 reflect_and_remember() 反思阶段使用
        self.curr_state = final_state

        # 将分析状态持久化到 JSON 文件（eval_results/{ticker}/SinaQuantStrategy_logs/）
        self._log_state(trade_date, final_state)

        # ========== 第五步：提取结构化决策 ==========
        # 获取深度推理模型的类名和模型名，附加到决策信息中
        model_info = ""
        try:
            if hasattr(self.deep_thinking_llm, 'model_name'):
                model_info = f"{self.deep_thinking_llm.__class__.__name__}:{self.deep_thinking_llm.model_name}"
            else:
                model_info = self.deep_thinking_llm.__class__.__name__
        except Exception:
            model_info = "Unknown"

        # 优先使用 Trader 节点直接输出的结构化决策（structured_decision），
        # 如果不存在则回退到 SignalProcessor 解析非结构化的交易决策文本
        decision = final_state.get("structured_decision")
        if not decision or not isinstance(decision, dict):
            decision = self.process_signal(final_state.get("final_trade_decision", ""), company_name)
        decision['model_info'] = model_info

        # ========== 第六步：思维链内容清洗 ==========
        # 某些 LLM（如 DeepSeek DSML 模式）会在输出中包含 <think/> 等思维链标签，
        # 这些标签对调试有用但不应该在最终报告中展示，因此需要清洗

        # 1. 过滤 decision 中的 reasoning 字段
        #    优先使用完整的交易员投资报告替代一句话摘要，提供更详细的推理过程
        if isinstance(decision, dict):
            full_plan = final_state.get("trader_investment_plan", "") or final_state.get("final_trade_decision", "")
            if full_plan:
                decision['reasoning'] = remove_thinking_content(str(full_plan))
            elif 'reasoning' in decision:
                decision['reasoning'] = remove_thinking_content(decision['reasoning'])

        # 2. 过滤 final_state 中的各分析师报告字段
        #    这些字段可能包含 LLM 的思维链内容，需要清洗
        #    如果清洗后内容过短（<50字符），说明原始内容全是思维链标签或伪造工具调用，直接丢弃
        report_fields_to_clean = [
            'market_report', 'sentiment_report', 'news_report', 'fundamentals_report',
            'investment_plan', 'trader_investment_plan', 'final_trade_decision'
        ]
        for field in report_fields_to_clean:
            if field in final_state:
                value = final_state[field]
                if isinstance(value, str):
                    cleaned = remove_thinking_content(value)
                    if not cleaned or len(cleaned) < 50:
                        cleaned = ""  # 全是伪造工具调用，丢弃
                    final_state[field] = cleaned
                elif value is not None:
                    cleaned = remove_thinking_content(str(value))
                    if cleaned and len(cleaned) >= 50:
                        final_state[field] = cleaned
                    else:
                        final_state[field] = ""

        # 3. 过滤辩论状态中的历史记录
        #    辩论状态包含看涨/看跌/风险等各方的发言历史，同样可能包含思维链内容
        if 'investment_debate_state' in final_state and isinstance(final_state['investment_debate_state'], dict):
            debate_state = final_state['investment_debate_state']
            for key in ['bull_history', 'bear_history', 'judge_decision']:
                if key in debate_state and isinstance(debate_state[key], str):
                    debate_state[key] = remove_thinking_content(debate_state[key])

        if 'risk_debate_state' in final_state and isinstance(final_state['risk_debate_state'], dict):
            risk_state = final_state['risk_debate_state']
            for key in ['risky_history', 'safe_history', 'neutral_history', 'judge_decision']:
                if key in risk_state and isinstance(risk_state[key], str):
                    risk_state[key] = remove_thinking_content(risk_state[key])

        # 返回最终状态和结构化决策
        return final_state, decision

    def _send_progress_update(self, chunk, progress_callback):
        """
        发送进度更新到回调函数

        LangGraph 的 stream() 方法在 updates 模式下返回的 chunk 格式为：
        {node_name: {状态更新字典}}
        例如：{"Market Analyst": {"market_report": "..."}}

        本方法负责：
        1. 从 chunk 中提取当前执行的节点名称
        2. 通过节点名称映射表将技术性节点名转换为用户友好的中文进度消息
        3. 调用进度回调函数将消息传递给前端

        节点名称映射策略：
        - 分析师节点：映射为 "📊 市场分析师" 等友好的中文名称
        - 工具节点（tools_*）：设为 None，跳过不发送（避免与分析师节点重复）
        - 消息清理节点（Msg Clear *）：设为 None，跳过（内部实现细节，不需要展示给用户）
        - 研究员/交易员/风险评估节点：映射为对应的中文名称
        - 结束节点（__end__）：发送 "📊 生成报告" 表示分析完成
        - 未知节点：使用原始节点名称，加前缀 "🔍"

        Args:
            chunk: LangGraph stream 返回的状态更新块，格式为 {node_name: {...}}
            progress_callback: 进度回调函数，接收字符串消息作为参数
        """
        try:
            # 从 chunk 中提取当前执行的节点信息
            if not isinstance(chunk, dict):
                return

            # 获取第一个非特殊键作为节点名
            # LangGraph 内部使用 __start__, __end__ 等特殊键标记图的起止
            node_name = None
            for key in chunk.keys():
                if not key.startswith('__'):
                    node_name = key
                    break

            if not node_name:
                return

            logger.info(f"🔍 [Progress] 节点名称: {node_name}")

            # 检查是否为结束节点（图执行完毕）
            if '__end__' in chunk:
                logger.info(f"📊 [Progress] 检测到__end__节点")
                progress_callback("📊 生成报告")
                return

            # 节点名称映射表：将 LangGraph 的技术性节点名映射为用户友好的中文进度消息
            # None 值表示该节点不需要发送进度更新（如工具执行节点和消息清理节点）
            node_mapping = {
                # 分析师节点：四种分析师对应不同的数据源
                'Market Analyst': "📊 市场分析师",
                'Fundamentals Analyst': "💼 基本面分析师",
                'News Analyst': "📰 新闻分析师",
                'Social Analyst': "💬 社交媒体分析师",
                # 工具节点：不发送进度更新，避免与分析师节点重复显示
                # 这些节点是分析师调用数据工具的执行节点，对用户无意义
                'tools_market': None,
                'tools_fundamentals': None,
                'tools_news': None,
                'tools_social': None,
                # 消息清理节点：不发送进度更新，这是内部实现细节
                # 分析师执行后会清理消息历史中的工具调用记录
                'Msg Clear Market': None,
                'Msg Clear Fundamentals': None,
                'Msg Clear News': None,
                'Msg Clear Social': None,
                # 研究员节点：看涨/看跌研究员辩论阶段
                'Bull Researcher': "🐂 看涨研究员",
                'Bear Researcher': "🐻 看跌研究员",
                'Research Manager': "👔 研究经理",
                # 交易员节点：基于研究报告生成交易决策
                'Trader': "💼 交易员决策",
                # 风险评估节点：三方风险评估和最终裁决
                'Risky Analyst': "🔥 激进风险评估",
                'Safe Analyst': "🛡️ 保守风险评估",
                'Neutral Analyst': "⚖️ 中性风险评估",
                'Risk Judge': "🎯 风险经理",
            }

            # 查找映射的消息
            message = node_mapping.get(node_name)

            if message is None:
                # None 表示跳过该节点（工具节点、消息清理节点等内部节点）
                logger.debug(f"⏭️ [Progress] 跳过节点: {node_name}")
                return

            if message:
                # 发送进度更新到回调函数
                logger.info(f"📤 [Progress] 发送进度更新: {message}")
                progress_callback(message)
            else:
                # 未知节点：映射表中不存在但也不是 None 的节点
                # 使用原始节点名称加前缀，确保不会遗漏
                logger.warning(f"⚠️ [Progress] 未知节点: {node_name}")
                progress_callback(f"🔍 {node_name}")

        except Exception as e:
            # 进度更新失败不应中断分析流程，仅记录错误
            logger.error(f"❌ 进度更新失败: {e}", exc_info=True)

    def _build_performance_data(self, node_timings: Dict[str, float], total_elapsed: float) -> Dict[str, Any]:
        """
        构建结构化的性能数据

        将各节点的原始计时数据分类汇总，生成包含以下信息的性能指标：
        1. 总体统计：总耗时、节点数量、平均耗时、最快/最慢节点
        2. 分类统计：按角色将节点分为7类，每类包含节点明细、总耗时和占比
        3. LLM 配置信息：当前使用的供应商和模型名称

        节点分类优先级说明：
        由于 "Risky Analyst" 和 "Safe Analyst" 等风险管理节点名称中也包含 "Analyst"，
        必须先匹配风险管理节点，否则它们会被错误地归入分析师类别。

        分类类别：
        - analyst_team: 分析师团队（Market/News/Social/Fundamentals Analyst）
        - tool_calls: 工具调用（tools_* 节点）
        - message_clearing: 消息清理（Msg Clear * 节点）
        - research_team: 研究团队（Bull/Bear Researcher, Research Manager）
        - trader_team: 交易团队（Trader）
        - risk_management_team: 风险管理团队（Risky/Safe/Neutral Analyst, Risk Judge）
        - other: 其他未分类节点

        Args:
            node_timings: 每个节点的执行时间字典 {节点名: 耗时(秒)}
            total_elapsed: 总执行时间（秒）

        Returns:
            结构化的性能数据字典，包含总体统计、分类统计和 LLM 配置信息
        """
        # 节点分类：按角色将节点归入7个类别
        # 注意：风险管理节点要先于分析师节点判断，因为它们也包含'Analyst'
        analyst_nodes = {}       # 分析师团队
        tool_nodes = {}          # 工具调用
        msg_clear_nodes = {}     # 消息清理
        research_nodes = {}      # 研究团队
        trader_nodes = {}        # 交易团队
        risk_nodes = {}          # 风险管理团队
        other_nodes = {}         # 其他节点

        for node_name, elapsed in node_timings.items():
            # 优先匹配风险管理团队（因为 "Risky Analyst" 等也包含 'Analyst'）
            if 'Risky' in node_name or 'Safe' in node_name or 'Neutral' in node_name or 'Risk Judge' in node_name:
                risk_nodes[node_name] = elapsed
            # 然后匹配分析师团队（排除了风险管理节点后的 'Analyst' 节点）
            elif 'Analyst' in node_name:
                analyst_nodes[node_name] = elapsed
            # 工具节点：以 "tools_" 前缀标识
            elif node_name.startswith('tools_'):
                tool_nodes[node_name] = elapsed
            # 消息清理节点：以 "Msg Clear" 前缀标识
            elif node_name.startswith('Msg Clear'):
                msg_clear_nodes[node_name] = elapsed
            # 研究团队：包含 Researcher 或 Research Manager
            elif 'Researcher' in node_name or 'Research Manager' in node_name:
                research_nodes[node_name] = elapsed
            # 交易团队
            elif 'Trader' in node_name:
                trader_nodes[node_name] = elapsed
            # 其他未分类节点
            else:
                other_nodes[node_name] = elapsed

        # 计算总体统计数据
        slowest_node = max(node_timings.items(), key=lambda x: x[1]) if node_timings else (None, 0)
        fastest_node = min(node_timings.items(), key=lambda x: x[1]) if node_timings else (None, 0)
        avg_time = sum(node_timings.values()) / len(node_timings) if node_timings else 0

        return {
            # 总体统计信息
            "total_time": round(total_elapsed, 2),                  # 总耗时（秒）
            "total_time_minutes": round(total_elapsed / 60, 2),     # 总耗时（分钟）
            "node_count": len(node_timings),                         # 执行的节点总数
            "average_node_time": round(avg_time, 2),                # 平均节点耗时（秒）
            "slowest_node": {                                        # 最慢节点
                "name": slowest_node[0],
                "time": round(slowest_node[1], 2)
            } if slowest_node[0] else None,
            "fastest_node": {                                        # 最快节点
                "name": fastest_node[0],
                "time": round(fastest_node[1], 2)
            } if fastest_node[0] else None,
            # 所有节点的原始计时数据
            "node_timings": {k: round(v, 2) for k, v in node_timings.items()},
            # 分类计时统计：每类包含节点明细、总耗时和占总时间的百分比
            "category_timings": {
                "analyst_team": {
                    "nodes": {k: round(v, 2) for k, v in analyst_nodes.items()},
                    "total": round(sum(analyst_nodes.values()), 2),
                    "percentage": round(sum(analyst_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "tool_calls": {
                    "nodes": {k: round(v, 2) for k, v in tool_nodes.items()},
                    "total": round(sum(tool_nodes.values()), 2),
                    "percentage": round(sum(tool_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "message_clearing": {
                    "nodes": {k: round(v, 2) for k, v in msg_clear_nodes.items()},
                    "total": round(sum(msg_clear_nodes.values()), 2),
                    "percentage": round(sum(msg_clear_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "research_team": {
                    "nodes": {k: round(v, 2) for k, v in research_nodes.items()},
                    "total": round(sum(research_nodes.values()), 2),
                    "percentage": round(sum(research_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "trader_team": {
                    "nodes": {k: round(v, 2) for k, v in trader_nodes.items()},
                    "total": round(sum(trader_nodes.values()), 2),
                    "percentage": round(sum(trader_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "risk_management_team": {
                    "nodes": {k: round(v, 2) for k, v in risk_nodes.items()},
                    "total": round(sum(risk_nodes.values()), 2),
                    "percentage": round(sum(risk_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                },
                "other": {
                    "nodes": {k: round(v, 2) for k, v in other_nodes.items()},
                    "total": round(sum(other_nodes.values()), 2),
                    "percentage": round(sum(other_nodes.values()) / total_elapsed * 100, 1) if total_elapsed > 0 else 0
                }
            },
            # 当前使用的 LLM 配置信息
            "llm_config": {
                "provider": self.config.get('llm_provider', 'unknown'),
                "deep_think_model": self.config.get('deep_think_llm', 'unknown'),
                "quick_think_model": self.config.get('quick_think_llm', 'unknown')
            }
        }

    def _print_timing_summary(self, node_timings: Dict[str, float], total_elapsed: float):
        """
        打印详细的时间统计报告

        生成格式化的日志报告，展示多智能体交易引擎各节点的执行时间分布。
        报告包含以下部分：
        1. 按角色分类的节点耗时列表（按耗时降序排列）
        2. 每个分类的小计和百分比
        3. 总体统计：总耗时、节点数、平均耗时、最快/最慢节点
        4. 当前使用的 LLM 配置信息

        该报告用于：
        - 性能瓶颈识别：快速定位耗时最长的节点
        - 成本优化：根据 LLM 调用耗时调整模型选择
        - 系统调优：比较不同配置下的执行效率

        分类优先级说明：
        风险管理节点（如 "Risky Analyst"）必须优先于分析师节点匹配，
        因为它们的名称也包含 "Analyst" 字符串。

        Args:
            node_timings: 每个节点的执行时间字典 {节点名: 耗时(秒)}
            total_elapsed: 总执行时间（秒）
        """
        # 调试日志：验证方法是否被正确调用
        logger.info("🔍 [_print_timing_summary] 方法被调用")
        logger.info("🔍 [_print_timing_summary] node_timings 数量: " + str(len(node_timings)))
        logger.info("🔍 [_print_timing_summary] total_elapsed: " + str(total_elapsed))

        logger.info("=" * 80)
        logger.info("⏱️  分析性能统计报告")
        logger.info("=" * 80)

        # 节点分类：将节点按角色归入7个类别
        # 注意：风险管理节点要先于分析师节点判断，因为它们也包含'Analyst'
        analyst_nodes = []       # 分析师团队
        tool_nodes = []          # 工具调用
        msg_clear_nodes = []     # 消息清理
        research_nodes = []      # 研究团队
        trader_nodes = []        # 交易团队
        risk_nodes = []          # 风险管理团队
        other_nodes = []         # 其他节点

        for node_name, elapsed in node_timings.items():
            # 优先匹配风险管理团队（因为 "Risky Analyst" 等也包含 'Analyst'）
            if 'Risky' in node_name or 'Safe' in node_name or 'Neutral' in node_name or 'Risk Judge' in node_name:
                risk_nodes.append((node_name, elapsed))
            # 然后匹配分析师团队
            elif 'Analyst' in node_name:
                analyst_nodes.append((node_name, elapsed))
            # 工具节点
            elif node_name.startswith('tools_'):
                tool_nodes.append((node_name, elapsed))
            # 消息清理节点
            elif node_name.startswith('Msg Clear'):
                msg_clear_nodes.append((node_name, elapsed))
            # 研究团队
            elif 'Researcher' in node_name or 'Research Manager' in node_name:
                research_nodes.append((node_name, elapsed))
            # 交易团队
            elif 'Trader' in node_name:
                trader_nodes.append((node_name, elapsed))
            # 其他节点
            else:
                other_nodes.append((node_name, elapsed))

        # 打印分类统计的辅助函数
        def print_category(title: str, nodes: List[Tuple[str, float]]):
            """打印单个类别的耗时统计，按耗时降序排列"""
            if not nodes:
                return
            logger.info(f"\n📊 {title}")
            logger.info("-" * 80)
            total_category_time = sum(t for _, t in nodes)
            for node_name, elapsed in sorted(nodes, key=lambda x: x[1], reverse=True):
                percentage = (elapsed / total_elapsed * 100) if total_elapsed > 0 else 0
                logger.info(f"  • {node_name:40s} {elapsed:8.2f}秒  ({percentage:5.1f}%)")
            logger.info(f"  {'小计':40s} {total_category_time:8.2f}秒  ({total_category_time/total_elapsed*100:5.1f}%)")

        # 按角色打印各分类的耗时统计
        print_category("分析师团队", analyst_nodes)
        print_category("工具调用", tool_nodes)
        print_category("消息清理", msg_clear_nodes)
        print_category("研究团队", research_nodes)
        print_category("交易团队", trader_nodes)
        print_category("风险管理团队", risk_nodes)
        print_category("其他节点", other_nodes)

        # 打印总体统计摘要
        logger.info("\n" + "=" * 80)
        logger.info(f"🎯 总执行时间: {total_elapsed:.2f}秒 ({total_elapsed/60:.2f}分钟)")
        logger.info(f"📈 节点总数: {len(node_timings)}")
        if node_timings:
            avg_time = sum(node_timings.values()) / len(node_timings)
            logger.info(f"⏱️  平均节点耗时: {avg_time:.2f}秒")
            slowest_node = max(node_timings.items(), key=lambda x: x[1])
            logger.info(f"🐌 最慢节点: {slowest_node[0]} ({slowest_node[1]:.2f}秒)")
            fastest_node = min(node_timings.items(), key=lambda x: x[1])
            logger.info(f"⚡ 最快节点: {fastest_node[0]} ({fastest_node[1]:.2f}秒)")

        # 打印 LLM 配置信息，便于对照性能数据选择最优模型组合
        logger.info(f"\n🤖 LLM配置:")
        logger.info(f"  • 提供商: {self.config.get('llm_provider', 'unknown')}")
        logger.info(f"  • 深度思考模型: {self.config.get('deep_think_llm', 'unknown')}")
        logger.info(f"  • 快速思考模型: {self.config.get('quick_think_llm', 'unknown')}")
        logger.info("=" * 80)

    def _log_state(self, trade_date, final_state):
        """
        将分析状态持久化到 JSON 文件

        将本次分析的关键状态数据保存到 eval_results/{ticker}/SinaQuantStrategy_logs/ 目录下。
        每次分析的结果以交易日期为 key 追加到 log_states_dict 中，
        支持多次分析结果的累积存储（如回测场景下多日连续分析）。

        保存的状态数据包括：
        - 基础信息：股票代码、交易日期
        - 分析师报告：市场、社交媒体、新闻、基本面四份分析报告
        - 投资辩论状态：看涨/看跌研究员的辩论历史和裁判裁决
        - 交易决策：交易员的投资方案和最终交易决策
        - 风险辩论状态：激进/保守/中性三方的辩论历史和裁判裁决

        持久化文件路径：eval_results/{ticker}/SinaQuantStrategy_logs/full_states_log.json
        该文件可用于：
        - 回测分析：查看历史分析结果与实际走势的对比
        - 调试验证：检查各智能体的输出是否符合预期
        - 数据复用：避免重复调用 LLM 和数据源

        Args:
            trade_date: 交易日期，作为 log_states_dict 的 key
            final_state: 图执行完成后的最终状态字典
        """
        # 将状态数据追加到内存中的字典（key 为交易日期）
        self.log_states_dict[str(trade_date)] = {
            # 基础信息
            "company_of_interest": final_state["company_of_interest"],  # 股票代码
            "trade_date": final_state["trade_date"],                    # 交易日期
            # 四位分析师的报告
            "market_report": final_state["market_report"],              # 市场分析师报告
            "sentiment_report": final_state["sentiment_report"],        # 社交媒体分析师报告
            "news_report": final_state["news_report"],                  # 新闻分析师报告
            "fundamentals_report": final_state["fundamentals_report"],  # 基本面分析师报告
            # 投资辩论状态：看涨 vs 看跌研究员的对抗辩论
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],       # 看涨研究员发言历史
                "bear_history": final_state["investment_debate_state"]["bear_history"],       # 看跌研究员发言历史
                "history": final_state["investment_debate_state"]["history"],                 # 完整辩论历史
                "current_response": final_state["investment_debate_state"][                    # 当前轮次的最新发言
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][                     # 研究经理的裁决
                    "judge_decision"
                ],
            },
            # 交易员的最终投资方案
            "trader_investment_decision": final_state["trader_investment_plan"],
            # 风险辩论状态：激进 vs 保守 vs 中性的风险评估
            "risk_debate_state": {
                "risky_history": final_state["risk_debate_state"]["risky_history"],           # 激进方发言历史
                "safe_history": final_state["risk_debate_state"]["safe_history"],             # 保守方发言历史
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],       # 中性方发言历史
                "history": final_state["risk_debate_state"]["history"],                       # 完整辩论历史
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],         # 风险经理的裁决
            },
            # 最终决策
            "investment_plan": final_state["investment_plan"],                    # 投资计划
            "final_trade_decision": final_state["final_trade_decision"],          # 最终交易决策
        }

        # 确保目录存在后写入 JSON 文件
        directory = Path(f"eval_results/{self.ticker}/SinaQuantStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/SinaQuantStrategy_logs/full_states_log.json",
            "w",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4)

    def reflect_and_remember(self, returns_losses):
        """
        基于实际收益结果反思决策并更新各智能体的长期记忆

        反思是多智能体交易引擎的核心学习机制。在获取到实际交易结果后，
        每个智能体都会回顾自己的决策过程，将成功经验和失败教训存入
        ChromaDB 向量数据库的长期记忆中。在下一次分析时，智能体可以
        检索相关记忆，避免重复犯错。

        反思流程：
        1. 看涨研究员反思：回顾看涨逻辑是否正确
        2. 看跌研究员反思：回顾看跌逻辑是否正确
        3. 交易员反思：回顾交易决策的合理性
        4. 投资裁判反思：回顾辩论裁决的公正性
        5. 风险经理反思：回顾风险评估的准确性

        该方法应在 propagate() 之后、获取到实际交易收益数据后调用，
        通常在回测或历史验证场景中使用。

        Args:
            returns_losses: 实际收益/损失数据，用于对比决策与结果的差异
        """
        # 看涨研究员反思：检查看涨论据是否经得起实际走势验证
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        # 看跌研究员反思：检查看跌论据是否经得起实际走势验证
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        # 交易员反思：检查交易决策（买入/卖出/持有）是否正确
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        # 投资裁判反思：检查辩论裁决是否偏向了正确的一方
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        # 风险经理反思：检查风险评估是否准确预判了波动
        self.reflector.reflect_risk_manager(
            self.curr_state, returns_losses, self.risk_manager_memory
        )

    def process_signal(self, full_signal, stock_symbol=None):
        """
        从非结构化的交易决策文本中提取结构化信号

        当 Trader 节点未直接输出结构化决策（structured_decision）时，
        使用该方法将自由文本格式的交易决策解析为标准化的信号格式。

        结构化信号包含以下字段：
        - action: 交易动作（买入/卖出/持有）
        - confidence: 置信度（0-100）
        - reasoning: 推理过程摘要

        Args:
            full_signal: 交易决策的完整文本（来自 final_trade_decision 字段）
            stock_symbol: 可选的股票代码，用于信号上下文

        Returns:
            结构化决策字典
        """
        return self.signal_processor.process_signal(full_signal, stock_symbol)
