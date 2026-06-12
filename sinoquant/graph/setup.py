# SinaQuant/graph/setup.py
# LangGraph 工作流构建模块
# 负责将所有智能体节点组装成完整的 StateGraph 并编译：
#   阶段1: 分析师并行执行（fan-out）→ 各自工具调用 → Msg Clear
#   阶段2: 看涨/看跌研究员辩论 → 研究经理裁决
#   阶段3: 交易员基于投资计划做决策
#   阶段4: 激进/保守/中性分析师风险讨论 → 风险经理最终裁决

from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from sinoquant.agents import *
from sinoquant.agents.utils.agent_states import AgentState
from sinoquant.agents.utils.agent_utils import Toolkit

from .conditional_logic import ConditionalLogic

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


class GraphSetup:
    """LangGraph 工作流构建器

    负责将所有智能体（分析师、研究员、交易员、风险评估师）组装为完整的
    LangGraph StateGraph，定义节点和边（包括条件边），最终编译为可执行的图。

    图的拓扑结构：
    ┌─────────────────────────────────────────────────────────┐
    │ START                                                   │
    │  ├─→ Market Analyst ⇄ tools_market → Msg Clear Market  │
    │  ├─→ Fundamentals Analyst ⇄ tools_... → Msg Clear ...  │  阶段1：并行分析
    │  ├─→ News Analyst ⇄ tools_news → Msg Clear News        │
    │  └─→ Social Analyst ⇄ tools_social → Msg Clear Social  │
    └────────────────────────┬────────────────────────────────┘
                             │ fan-in（所有 Msg Clear 汇聚）
    ┌────────────────────────▼────────────────────────────────┐
    │ Bull Researcher ⇄ Bear Researcher  → Research Manager  │  阶段2：投资辩论
    └────────────────────────┬────────────────────────────────┘
                             │
    ┌────────────────────────▼────────────────────────────────┐
    │ Trader                                                  │  阶段3：交易决策
    └────────────────────────┬────────────────────────────────┘
                             │
    ┌────────────────────────▼────────────────────────────────┐
    │ Risky ⇄ Safe ⇄ Neutral (轮转辩论) → Risk Judge → END  │  阶段4：风险评估
    └─────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,
        deep_thinking_llm: ChatOpenAI,
        toolkit: Toolkit,
        tool_nodes: Dict[str, ToolNode],
        bull_memory,           # 看涨研究员的金融情境记忆
        bear_memory,           # 看跌研究员的金融情境记忆
        trader_memory,         # 交易员的金融情境记忆
        invest_judge_memory,   # 研究经理的金融情境记忆
        risk_manager_memory,   # 风险经理的金融情境记忆
        conditional_logic: ConditionalLogic,
        config: Dict[str, Any] = None,
        react_llm = None,
    ):
        """初始化工作流构建器

        Args:
            quick_thinking_llm: 快速推理模型（用于分析师、研究员、交易员等需要快速响应的节点）
            deep_thinking_llm: 深度推理模型（用于研究经理、风险经理等需要深度分析的节点）
            toolkit: 数据获取工具集，包含市场/基本面/新闻/情绪等工具
            tool_nodes: 按分析师分组的工具执行节点字典
            bull_memory/bear_memory/trader_memory/invest_judge_memory/risk_manager_memory:
                各角色的金融情境记忆，用于从历史决策中学习和反思
            conditional_logic: 条件路由控制器
            config: 全局配置字典
            react_llm: ReAct 模式 LLM（当前未使用）
        """
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.toolkit = toolkit
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.config = config or {}
        self.react_llm = react_llm

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """构建并编译 LangGraph 多智能体工作流

        工作流程四阶段：
        1. 分析师阶段：选中的分析师并行执行，各自调用工具获取数据并生成报告
        2. 投资辩论阶段：看涨/看跌研究员基于分析师报告进行多轮辩论，研究经理裁决
        3. 交易决策阶段：交易员基于投资计划生成结构化交易决策
        4. 风险评估阶段：激进/保守/中性分析师讨论风险，风险经理做最终决策

        Args:
            selected_analysts: 选中的分析师类型列表，可选值：
                - "market": 市场分析师（技术指标、价格趋势）
                - "social": 社交媒体分析师（投资者情绪）
                - "news": 新闻分析师（新闻事件影响）
                - "fundamentals": 基本面分析师（财务数据、估值）

        Returns:
            编译后的 LangGraph CompiledGraph，可调用 stream() 执行

        Raises:
            ValueError: 如果 selected_analysts 为空
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # ============================================================
        # 阶段1：创建分析师节点、工具节点和消息清理节点
        # ============================================================
        # analyst_nodes: 分析师节点字典，key 为分析师类型（market/social/news/fundamentals），
        #   value 为对应的 LangChain Runnable 节点函数
        # delete_nodes: 消息清理节点字典，每个分析师完成后清理中间工具消息，
        #   避免过多工具调用历史污染后续节点的上下文窗口
        # tool_nodes: 工具执行节点字典，每个分析师配备独立的 ToolNode，
        #   用于执行该分析师专属的数据获取工具（市场指标/财务数据/新闻/社交媒体等）
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        # --- 市场分析师节点 ---
        # 负责技术指标分析（MACD/KDJ/RSI等）和价格趋势判断，
        # 调用市场数据工具获取行情数据后生成分析报告
        if "market" in selected_analysts:
            # 现在所有LLM都使用标准市场分析师（包括阿里百炼的OpenAI兼容适配器）
            llm_provider = self.config.get("llm_provider", "").lower()

            # 检查是否使用OpenAI兼容的阿里百炼适配器
            using_dashscope_openai = (
                "dashscope" in llm_provider and
                hasattr(self.quick_thinking_llm, '__class__') and
                'OpenAI' in self.quick_thinking_llm.__class__.__name__
            )

            # 根据不同LLM提供商记录调试日志，便于排查兼容性问题
            if using_dashscope_openai:
                logger.debug(f"📈 [DEBUG] 使用标准市场分析师（阿里百炼OpenAI兼容模式）")
            elif "dashscope" in llm_provider or "阿里百炼" in self.config.get("llm_provider", ""):
                logger.debug(f"📈 [DEBUG] 使用标准市场分析师（阿里百炼原生模式）")
            elif "deepseek" in llm_provider:
                logger.debug(f"📈 [DEBUG] 使用标准市场分析师（DeepSeek）")
            else:
                logger.debug(f"📈 [DEBUG] 使用标准市场分析师")

            # 创建市场分析师节点、消息清理节点和工具节点
            analyst_nodes["market"] = create_market_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["market"] = create_msg_delete("market")
            tool_nodes["market"] = self.tool_nodes["market"]

        # --- 社交媒体分析师节点 ---
        # 负责投资者情绪分析，抓取东方财富股吧等社交平台数据，
        # 评估市场参与者的多空情绪倾向
        if "social" in selected_analysts:
            analyst_nodes["social"] = create_social_media_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["social"] = create_msg_delete("social")
            tool_nodes["social"] = self.tool_nodes["social"]

        # --- 新闻分析师节点 ---
        # 负责新闻事件分析，获取与标的相关的新闻资讯，
        # 评估新闻事件对股价的潜在影响（利好/利空/中性）
        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["news"] = create_msg_delete("news")
            tool_nodes["news"] = self.tool_nodes["news"]

        # --- 基本面分析师节点 ---
        # 负责财务数据分析（营收/利润/估值指标等），
        # 调用财务数据工具获取公司基本面信息后生成分析报告
        if "fundamentals" in selected_analysts:
            # 现在所有LLM都使用标准基本面分析师（包括阿里百炼的OpenAI兼容适配器）
            llm_provider = self.config.get("llm_provider", "").lower()

            # 检查是否使用OpenAI兼容的阿里百炼适配器
            using_dashscope_openai = (
                "dashscope" in llm_provider and
                hasattr(self.quick_thinking_llm, '__class__') and
                'OpenAI' in self.quick_thinking_llm.__class__.__name__
            )

            # 根据不同LLM提供商记录调试日志，便于排查兼容性问题
            if using_dashscope_openai:
                logger.debug(f"📊 [DEBUG] 使用标准基本面分析师（阿里百炼OpenAI兼容模式）")
            elif "dashscope" in llm_provider or "阿里百炼" in self.config.get("llm_provider", ""):
                logger.debug(f"📊 [DEBUG] 使用标准基本面分析师（阿里百炼原生模式）")
            elif "deepseek" in llm_provider:
                logger.debug(f"📊 [DEBUG] 使用标准基本面分析师（DeepSeek）")
            else:
                logger.debug(f"📊 [DEBUG] 使用标准基本面分析师")

            # 所有LLM都使用标准分析师（包含强制工具调用机制）
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["fundamentals"] = create_msg_delete("fundamentals")
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        # ============================================================
        # 阶段2：创建研究员和交易员节点
        # ============================================================
        # 看涨研究员：基于分析师报告构建看涨论点，与看跌研究员进行辩论
        # 使用 quick_thinking_llm 保证辩论响应速度，max_debate_rounds 控制最大辩论轮数
        bull_researcher_node = create_bull_researcher(
            self.quick_thinking_llm, self.bull_memory,
            max_debate_rounds=self.conditional_logic.max_debate_rounds
        )
        # 看跌研究员：基于分析师报告构建看跌论点，与看涨研究员进行辩论
        bear_researcher_node = create_bear_researcher(
            self.quick_thinking_llm, self.bear_memory,
            max_debate_rounds=self.conditional_logic.max_debate_rounds
        )
        # 研究经理：裁决看涨/看跌研究员的辩论，综合双方论点生成投资计划
        # 使用 deep_thinking_llm 以获得更深入的推理和更公正的裁决
        research_manager_node = create_research_manager(
            self.deep_thinking_llm, self.invest_judge_memory
        )

        # ============================================================
        # 阶段3：创建交易员节点
        # ============================================================
        # 交易员：基于研究经理的投资计划生成结构化交易决策（买入/卖出/持有 + 仓位比例），
        # 输出包含具体交易参数（目标价、止损价等）的投资建议
        trader_node = create_trader(self.quick_thinking_llm, self.trader_memory)

        # ============================================================
        # 阶段4：创建风险评估节点
        # ============================================================
        # 三位风险分析师以轮转辩论的方式讨论交易决策的风险：
        # 激进分析师 → 保守分析师 → 中性分析师 → (循环) → 风险经理裁决
        # 这种轮转机制确保从不同风险偏好视角全面评估交易方案
        risky_analyst = create_risky_debator(self.quick_thinking_llm)    # 激进分析师：倾向于承担风险，强调收益机会
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm) # 中性分析师：客观平衡，既看风险也看收益
        safe_analyst = create_safe_debator(self.quick_thinking_llm)      # 保守分析师：倾向规避风险，强调潜在损失
        # 风险经理：最终裁决节点，综合三位风险分析师的论点，
        # 生成最终的风险评估报告和调整后的交易建议
        risk_manager_node = create_risk_manager(
            self.deep_thinking_llm, self.risk_manager_memory
        )

        # ============================================================
        # 构建工作流图（StateGraph）
        # ============================================================
        # 使用 AgentState 作为共享状态，所有节点通过读写该状态进行通信
        # AgentState 包含消息列表、各分析师报告、辩论记录、交易决策等字段
        workflow = StateGraph(AgentState)

        # ============================================================
        # 注册所有分析师相关节点到图中
        # ============================================================
        # 每个选中的分析师注册三个节点：
        #   1. "{Type} Analyst"  — 分析师主体节点，调用LLM生成分析报告
        #   2. "Msg Clear {Type}" — 消息清理节点，删除中间工具调用消息以节省上下文空间
        #   3. "tools_{type}"    — 工具执行节点（ToolNode），执行分析师调用的数据获取工具
        # 数据流：Analyst ⇄ tools_{type}（循环调用直到无需工具） → Msg Clear
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_type.capitalize()} Analyst", node)
            workflow.add_node(
                f"Msg Clear {analyst_type.capitalize()}", delete_nodes[analyst_type]
            )
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])

        # ============================================================
        # 注册阶段2~4的节点到图中
        # ============================================================
        # 投资辩论节点（阶段2）
        workflow.add_node("Bull Researcher", bull_researcher_node)      # 看涨研究员
        workflow.add_node("Bear Researcher", bear_researcher_node)      # 看跌研究员
        workflow.add_node("Research Manager", research_manager_node)    # 研究经理（辩论裁决者）
        # 交易决策节点（阶段3）
        workflow.add_node("Trader", trader_node)                        # 交易员
        # 风险评估节点（阶段4）
        workflow.add_node("Risky Analyst", risky_analyst)               # 激进分析师
        workflow.add_node("Neutral Analyst", neutral_analyst)           # 中性分析师
        workflow.add_node("Safe Analyst", safe_analyst)                 # 保守分析师
        workflow.add_node("Risk Judge", risk_manager_node)              # 风险经理（最终裁决者）

        # ============================================================
        # 定义图的边（Edge）：控制数据流向和执行顺序
        # ============================================================

        # --- 阶段1 边：分析师扇出/并行执行 → 工具调用循环 → 扇入汇聚 ---
        # 拓扑结构：
        #   START ─┬─→ Market Analyst    ⇄ tools_market    → Msg Clear Market  ─┐
        #          ├─→ Social Analyst    ⇄ tools_social    → Msg Clear Social  ─┤
        #          ├─→ News Analyst      ⇄ tools_news      → Msg Clear News    ─┤ fan-in
        #          └─→ Fundamentals A.   ⇄ tools_fund.     → Msg Clear Fund.   ─┘
        #                                                                         │
        #                                                                 Bull Researcher
        for analyst_type in selected_analysts:
            analyst_name = f"{analyst_type.capitalize()} Analyst"
            tools_name = f"tools_{analyst_type}"
            clear_name = f"Msg Clear {analyst_type.capitalize()}"

            # 扇出：START → 每个分析师（并行启动）
            # LangGraph 会同时执行所有从 START 出发的边，实现分析师并行工作
            workflow.add_edge(START, analyst_name)

            # 条件边：分析师 → 工具节点 或 消息清理节点
            # should_continue_{type} 判断分析师是否需要调用工具：
            #   - 如果LLM响应中包含工具调用 → 路由到 tools_{type} 执行工具
            #   - 如果LLM响应中没有工具调用（分析完成）→ 路由到 Msg Clear 清理消息
            workflow.add_conditional_edges(
                analyst_name,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                [tools_name, clear_name],
            )
            # 工具执行完成后返回分析师节点，形成 "分析师 ⇄ 工具" 的循环调用
            # 直到分析师认为数据充足，不再调用工具为止
            workflow.add_edge(tools_name, analyst_name)

            # 扇入：所有 Msg Clear → 看涨研究员
            # LangGraph 的 fan-in 语义：只有当所有 Msg Clear 节点都执行完毕后，
            # Bull Researcher 才会开始执行，确保所有分析师报告都已就绪
            workflow.add_edge(clear_name, "Bull Researcher")

        # --- 阶段2 边：看涨/看跌研究员辩论 + 研究经理裁决 ---
        # 拓扑结构（辩论循环）：
        #   Bull Researcher ──→ Bear Researcher ──→ Bull Researcher ──→ ... ──→ Research Manager
        #        ↑                   │       ↑                   │
        #        └───────────────────┘       └───────────────────┘   (辩论未结束则继续)
        #
        # should_continue_debate 判断辩论是否继续：
        #   - 辩论轮数 < max_debate_rounds → 继续辩论（路由到对方研究员）
        #   - 辩论轮数 >= max_debate_rounds → 辩论结束（路由到研究经理裁决）
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",       # 辩论继续：交给看跌研究员反驳
                "Research Manager": "Research Manager",     # 辩论结束：交给研究经理裁决
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",       # 辩论继续：交回看涨研究员反驳
                "Research Manager": "Research Manager",     # 辩论结束：交给研究经理裁决
            },
        )
        # --- 阶段3 边：研究经理 → 交易员 ---
        # 研究经理生成投资计划后，交由交易员基于该计划做出具体交易决策
        workflow.add_edge("Research Manager", "Trader")

        # --- 阶段4 边：风险评估轮转辩论 ---
        # 拓扑结构（轮转辩论循环）：
        #   Trader → Risky Analyst ──→ Safe Analyst ──→ Neutral Analyst ──→ Risk Judge
        #                ↑                                       │
        #                └───────────────────────────────────────┘  (风险讨论未结束则继续)
        #
        # 辩论顺序：激进 → 保守 → 中性 → (循环回到激进) → ... → 风险经理
        # should_continue_risk_analysis 判断风险讨论是否继续：
        #   - 讨论轮数未达上限 → 继续轮转辩论
        #   - 讨论轮数达到上限 → 路由到风险经理做最终裁决
        workflow.add_edge("Trader", "Risky Analyst")  # 交易员决策进入风险讨论

        # 激进分析师 → 保守分析师（继续讨论）或 风险经理（讨论结束）
        workflow.add_conditional_edges(
            "Risky Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Safe Analyst": "Safe Analyst",       # 继续讨论：交由保守分析师从风险规避角度反驳
                "Risk Judge": "Risk Judge",           # 讨论结束：交由风险经理最终裁决
            },
        )
        # 保守分析师 → 中性分析师（继续讨论）或 风险经理（讨论结束）
        workflow.add_conditional_edges(
            "Safe Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst", # 继续讨论：交由中性分析师做客观平衡分析
                "Risk Judge": "Risk Judge",           # 讨论结束：交由风险经理最终裁决
            },
        )
        # 中性分析师 → 激进分析师（继续讨论，形成循环）或 风险经理（讨论结束）
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Risky Analyst": "Risky Analyst",     # 继续讨论：回到激进分析师，开始新一轮辩论
                "Risk Judge": "Risk Judge",           # 讨论结束：交由风险经理最终裁决
            },
        )

        # 风险经理裁决完成 → 工作流结束
        # 风险经理输出最终的风险评估报告和调整后的交易建议
        workflow.add_edge("Risk Judge", END)

        # ============================================================
        # 编译工作流图
        # ============================================================
        # compile() 将声明式的图定义编译为可执行的计算图，
        # 编译后的图支持 stream()（流式执行）和 invoke()（同步执行）
        return workflow.compile()
