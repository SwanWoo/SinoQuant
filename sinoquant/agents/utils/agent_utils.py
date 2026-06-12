"""
Agent 工具集与消息处理工具模块

本模块为 SinoQuant 多智能体交易系统提供三个核心能力：

1. 消息清理节点工厂 (create_msg_delete)：
   为 LangGraph 并行分支生成消息清理节点，解决 Anthropic 模型对空消息列表的兼容性问题。

2. 消息过滤器 (filter_messages_for_analyst)：
   在并行分析师分支间隔离工具调用，防止跨分析师工具调用污染导致幻觉问题。

3. 工具集类 (Toolkit)：
   封装所有 @tool 装饰的数据访问方法，供各分析师 Agent 调用，包括基本面分析、
   市场数据、新闻获取、情绪分析等统一数据接口。
"""

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from typing import List
from typing import Annotated
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import RemoveMessage  # 仅导入，不实际使用（并行分支中 RemoveMessage 不可靠）
from langchain_core.tools import tool
from datetime import date, timedelta, datetime
import functools
import pandas as pd
import os
from dateutil.relativedelta import relativedelta
from langchain_openai import ChatOpenAI
import sinoquant.dataflows.interface as interface  # 数据流统一接口层
from sinoquant.default_config import DEFAULT_CONFIG
from langchain_core.messages import HumanMessage

# 导入统一日志系统和工具日志装饰器
from sinoquant.utils.logging_init import get_logger
from sinoquant.utils.tool_logging import log_tool_call, log_analysis_step

# 导入日志模块（与上面 logging_init 重复导入，统一使用 logging_manager 的实例）
from sinoquant.utils.logging_manager import get_logger
logger = get_logger('agents')


def create_msg_delete(analyst_name: str = ""):
    """创建消息清理节点（Msg Clear Node）的工厂函数。

    在 LangGraph 的并行分支架构中，每个分析师分支执行完毕后需要清理消息，
    防止该分支的中间消息（如工具调用结果）泄漏到下游节点。

    设计决策：为什么使用 "Continue" 占位消息而非 RemoveMessage？
    ====================================================================
    1. 并行分支的 ID 冲突问题：
       LangGraph 并行分支共享消息列表，但各分支的 Msg Clear 节点看到的
       messages 状态不一致。分支 A 的 Msg Clear 节点可能看不到分支 B
       新增的消息 ID，使用 RemoveMessage 会导致 "ID doesn't exist" 错误。

    2. tool_calls ↔ ToolMessage 配对破坏：
       如果一个 AIMessage 包含多个 tool_calls，删除其中部分 ToolMessage
       会破坏配对关系，导致 LLM 重新处理时出现不可预期的行为。

    3. Anthropic 模型兼容性：
       Anthropic Claude 模型要求 messages 列表不能为空，且不能以 AIMessage
       结尾。添加 HumanMessage("Continue") 既满足此约束，又作为分支汇合后的
       对话延续信号。

    因此，本函数采用"只添加占位消息，不执行删除"的策略。

    Args:
        analyst_name: 分析师名称（用于日志和调试，当前实现中未使用，
                      但保留以支持未来可能的分支级别消息清理逻辑）

    Returns:
        delete_messages: 一个 LangGraph 节点函数，接收 state 字典，
                         返回包含占位 HumanMessage 的新消息列表
    """
    def delete_messages(state):
        """消息清理节点：添加 "Continue" 占位消息。

        不执行任何消息删除操作，仅添加一条 HumanMessage 占位消息，
        确保 Anthropic 模型的兼容性并标记分支执行完成。
        """
        placeholder = HumanMessage(content="Continue")
        return {"messages": [placeholder]}

    return delete_messages


def filter_messages_for_analyst(messages: list, allowed_tools: set) -> list:
    """过滤消息列表，移除其他分析师的 tool_call/ToolMessage，保留当前分析师的。

    核心问题：LangGraph 并行分支间的工具调用污染（Cross-Analyst Tool Call Contamination）
    ============================================================================
    在 SinoQuant 的 LangGraph 辩论工作流中，市场分析师、基本面分析师、新闻分析师、
    社交媒体分析师在并行分支中同时执行。LangGraph 的并行分支共享同一个 messages
    列表，这意味着：

    - 基本面分析师会看到市场分析师的 get_stock_market_data_unified 工具调用
    - 新闻分析师会看到基本面分析师的 get_stock_fundamentals_unified 工具调用
    - LLM 在看到不属于自己工具的名称后，可能产生幻觉（hallucination），试图调用
      不属于自己的工具，导致分析流程崩溃或结果偏差

    过滤策略：
    1. 保留所有 HumanMessage（跳过 create_msg_delete 创建的 "Continue" 占位消息）
    2. 保留不含 tool_calls 的纯文本 AIMessage（含 reasoning_content 等属性）
    3. 含 tool_calls 的 AIMessage：仅保留 allowed_tools 中的 tool_calls + 对应 ToolMessage
    4. 非当前分析师的 tool_calls 整条 AIMessage 中的非法部分被过滤，
       对应的 ToolMessage 也被移除
    5. 确保 tool_call ↔ ToolMessage 配对完整（避免孤立消息）

    DeepSeek reasoning_content 保留说明：
    DeepSeek 系列模型在思考模式下会在 AIMessage 中附加 reasoning_content 字段，
    包含模型的推理过程。重建 AIMessage 时必须显式复制此字段，因为 AIMessage
    构造函数不会自动从原消息继承非标准属性。

    Args:
        messages: LangGraph 状态中的完整消息列表（来自并行分支共享的 messages）
        allowed_tools: 当前分析师被允许使用的工具名称集合，如
                       {"get_stock_fundamentals_unified", "get_china_market_overview"}

    Returns:
        过滤后的消息列表，仅包含当前分析师可见的消息，保证：
        - 无跨分析师的工具调用泄漏
        - tool_call 与 ToolMessage 配对完整
        - DeepSeek reasoning_content 等关键属性保留
    """
    if not messages:
        return messages

    # =========================================================================
    # 第一步：扫描所有消息，建立 tool_call_id → 归属信息的映射表
    # =========================================================================
    # 遍历整个消息列表，找出每个 tool_call_id 对应的工具名称，
    # 判断是否属于当前分析师的 allowed_tools，同时关联对应的 ToolMessage。
    # 映射结构：tool_call_id -> {"allowed": bool, "tool_msg": ToolMessage|None}
    tool_call_info = {}  # id -> {"allowed": bool, "tool_msg": ToolMessage|None}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # 从 AIMessage 中提取每个 tool_call，记录其 ID 和是否属于允许的工具
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                if tc_id:
                    tool_call_info[tc_id] = {
                        "allowed": tc.get("name") in allowed_tools,
                        "tool_msg": None,  # ToolMessage 将在后续扫描中关联
                    }
        elif isinstance(msg, ToolMessage):
            # 将 ToolMessage 关联到对应的 tool_call_id
            if msg.tool_call_id in tool_call_info:
                # 正常情况：ToolMessage 匹配到先扫描到的 tool_call
                tool_call_info[msg.tool_call_id]["tool_msg"] = msg
            else:
                # 异常情况：ToolMessage 出现在对应 AIMessage 之前，
                # 或 AIMessage 中没有该 tool_call（不应发生，但做防御性处理），
                # 标记为不允许（allowed=False）以确保安全过滤
                tool_call_info[msg.tool_call_id] = {
                    "allowed": False,
                    "tool_msg": msg,
                }

    # 收集所有允许的 tool_call_id 集合，用于后续快速判断
    allowed_tool_call_ids = {tc_id for tc_id, info in tool_call_info.items() if info["allowed"]}

    # =========================================================================
    # 第二步：基于映射表重建消息链
    # =========================================================================
    # 逐条处理原始消息，按照过滤策略决定保留或丢弃：
    # - HumanMessage：保留（跳过 "Continue" 占位）
    # - 纯文本 AIMessage：保留
    # - 含 tool_calls 的 AIMessage：仅保留合法的 tool_calls，重建消息
    # - ToolMessage：不直接添加，而是在重建 AIMessage 后立即附加对应的 ToolMessage
    #   （确保 tool_call ↔ ToolMessage 配对完整，避免孤立消息）
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            # 跳过 create_msg_delete 创建的 "Continue" 占位消息
            # 这些消息仅用于 Anthropic 模型兼容性，不应出现在分析师的消息上下文中
            if msg.content == "Continue":
                continue
            result.append(msg)

        elif isinstance(msg, AIMessage):
            if not msg.tool_calls:
                # 保留原始 AIMessage（含 reasoning_content 等属性）
                # 纯文本的 AI 回复不涉及工具调用，不存在跨分析师污染风险
                result.append(msg)
            else:
                # 含 tool_calls 的 AIMessage：仅保留 allowed_tools 中的调用
                valid_calls = [tc for tc in msg.tool_calls if tc.get("id") in allowed_tool_call_ids]
                if valid_calls:
                    # 重建 AIMessage，仅包含合法的 tool_calls
                    # 注意：必须重建而非修改原消息，因为 LangChain 消息对象通常是不可变的
                    new_msg = AIMessage(
                        content=msg.content,
                        tool_calls=valid_calls,
                        id=msg.id,
                    )
                    # 保留 DeepSeek 思考模式的 reasoning_content
                    # DeepSeek v3/v4 在 DSML 标签中输出推理过程，存储在 reasoning_content 字段
                    # 如果不显式复制，构造函数会丢弃此字段，导致思考链丢失
                    if hasattr(msg, 'reasoning_content') and msg.reasoning_content:
                        new_msg.reasoning_content = msg.reasoning_content
                    # 保留 additional_kwargs（可能含 reasoning 等元信息）
                    # 某些 LLM 提供商将额外信息存储在此字段中
                    if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                        new_msg.additional_kwargs = msg.additional_kwargs
                    result.append(new_msg)
                    # 立即附加对应的 ToolMessage，确保 tool_call ↔ ToolMessage 配对完整
                    # 这样在过滤后的消息链中，每个 tool_call 紧跟其执行结果
                    for tc in valid_calls:
                        info = tool_call_info.get(tc.get("id"), {})
                        tool_msg = info.get("tool_msg")
                        if tool_msg:
                            result.append(tool_msg)
                # 如果 valid_calls 为空，说明该 AIMessage 中所有 tool_calls 都不属于
                # 当前分析师，整条消息被静默丢弃（连同其 ToolMessage）

        elif isinstance(msg, ToolMessage):
            # ToolMessage 不在此处添加——它们在上面的 AIMessage 处理逻辑中
            # 已被紧随对应的 tool_call 之后附加。
            # 跳过此处确保不会出现孤立的 ToolMessage（无对应 tool_call 的结果消息）
            pass

    return result


class Toolkit:
    """Agent 工具集类：封装所有 @tool 装饰的数据访问方法。

    本类为 SinoQuant 多智能体交易系统中的各分析师 Agent 提供统一的数据访问工具。
    所有工具方法均使用 @staticmethod + @tool 装饰器，确保：
    - 作为 LangChain Tool 对象可被 Agent 直接调用
    - 不依赖实例状态，所有配置通过类级别属性共享

    类级别配置模式（_config）：
    =============================
    - _config 是一个类变量，所有 Toolkit 实例共享同一份配置
    - 初始值从 DEFAULT_CONFIG 深拷贝，包含 research_depth（分析级别）等参数
    - 通过 update_config() 类方法更新，影响所有实例的行为
    - 配置优先级：数据库（Web UI）> 环境变量 > DEFAULT_CONFIG 默认值

    工具分类：
    =============================
    1. 统一工具（推荐使用）：
       - get_stock_fundamentals_unified: 基本面分析（支持数据深度策略）
       - get_stock_market_data_unified: 市场数据与技术指标（支持自动日期扩展）
       - get_stock_news_unified: 新闻数据（AKShare + Google 双源）
       - get_stock_sentiment_unified: 情绪分析（A股社交情绪占位）

    2. 遗留工具（已弃用，保留兼容）：
       - get_china_stock_data: 遗留数据访问器（@tool 已注释）
       - get_china_fundamentals: 遗留基本面访问器（@tool 已注释）
       - get_fundamentals_openai: 遗留 OpenAI 基本面搜索（@tool 已注释）

    3. 辅助工具：
       - get_chinese_social_sentiment: 中国社交媒体情绪
       - get_china_market_overview: 中国股市概览
       - get_google_news: Google 新闻搜索
       - get_realtime_stock_news: A股实时新闻
       - get_global_news_openai: 全球宏观经济新闻
    """

    # 类级别配置：所有实例共享，从 DEFAULT_CONFIG 初始化
    # 包含 research_depth（分析级别）、数据源优先级等系统配置
    _config = DEFAULT_CONFIG.copy()

    @classmethod
    def update_config(cls, config):
        """更新类级别配置，影响所有 Toolkit 实例。

        通常在应用启动时由 config_bridge 调用，将数据库中的供应商配置
        同步到环境变量后，更新工具集的分析级别等参数。

        Args:
            config: 配置字典，可包含 research_depth 等键
        """
        cls._config.update(config)

    @property
    def config(self):
        """访问当前配置（类级别共享）。"""
        return self._config

    def __init__(self, config=None):
        """初始化工具集实例。

        Args:
            config: 可选配置字典，传入时会更新类级别配置（影响所有实例）
        """
        if config:
            self.update_config(config)

    @staticmethod
    @tool
    def get_chinese_social_sentiment(
        ticker: Annotated[str, "股票代码，如 600036, 000001"],
        curr_date: Annotated[str, "当前日期，格式为 yyyy-mm-dd"],
    ) -> str:
        """获取中国社交媒体和财经平台的股票情绪分析。

        数据源：雪球、东方财富股吧、新浪财经等中国本土社交平台。
        通过 interface 层统一调用，支持多平台数据聚合。

        用途：供社交媒体分析师 Agent 使用，分析散户情绪和讨论热度。
        """
        try:
            # 这里可以集成多个中国平台的数据
            chinese_sentiment_results = interface.get_chinese_social_sentiment(ticker, curr_date)
            return chinese_sentiment_results
        except Exception as e:
            return f"中国社交媒体数据获取失败: {str(e)}"

    @staticmethod
    # @tool  # 已移除：请使用 get_stock_fundamentals_unified 或 get_stock_market_data_unified
    # 遗留数据访问器：保留代码兼容性，@tool 装饰器已注释，Agent 不会再自动调用此方法
    # 新代码应使用 get_stock_market_data_unified（市场数据）或 get_stock_fundamentals_unified（基本面）
    def get_china_stock_data(
        stock_code: Annotated[str, "中国股票代码，如 000001(平安银行), 600519(贵州茅台)"],
        start_date: Annotated[str, "开始日期，格式 yyyy-mm-dd"],
        end_date: Annotated[str, "结束日期，格式 yyyy-mm-dd"],
    ) -> str:
        """
        获取中国A股实时和历史数据，通过Tushare等高质量数据源提供专业的股票数据。
        支持实时行情、历史K线、技术指标等全面数据，自动使用最佳数据源。
        Args:
            stock_code (str): 中国股票代码，如 000001(平安银行), 600519(贵州茅台)
            start_date (str): 开始日期，格式 yyyy-mm-dd
            end_date (str): 结束日期，格式 yyyy-mm-dd
        Returns:
            str: 包含实时行情、历史数据、技术指标的完整股票分析报告
        """
        try:
            logger.debug(f"[DEBUG] ===== agent_utils.get_china_stock_data 开始调用 =====")
            logger.debug(f"[DEBUG] 参数: stock_code={stock_code}, start_date={start_date}, end_date={end_date}")

            from sinoquant.dataflows.interface import get_china_stock_data_unified
            logger.debug(f"[DEBUG] 成功导入统一数据源接口")

            logger.debug(f"[DEBUG] 正在调用统一数据源接口...")
            result = get_china_stock_data_unified(stock_code, start_date, end_date)

            logger.debug(f"[DEBUG] 统一数据源接口调用完成")
            logger.debug(f"[DEBUG] 返回结果类型: {type(result)}")
            logger.debug(f"[DEBUG] 返回结果长度: {len(result) if result else 0}")
            logger.debug(f"[DEBUG] 返回结果前200字符: {str(result)[:200]}...")
            logger.debug(f"[DEBUG] ===== agent_utils.get_china_stock_data 调用结束 =====")

            return result
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"[DEBUG] ===== agent_utils.get_china_stock_data 异常 =====")
            logger.error(f"[DEBUG] 错误类型: {type(e).__name__}")
            logger.error(f"[DEBUG] 错误信息: {str(e)}")
            logger.error(f"[DEBUG] 详细堆栈:")
            print(error_details)
            logger.error(f"[DEBUG] ===== 异常处理结束 =====")
            return f"中国股票数据获取失败: {str(e)}。请检查网络连接或稍后重试。"

    @staticmethod
    @tool
    def get_china_market_overview(
        curr_date: Annotated[str, "当前日期，格式 yyyy-mm-dd"],
    ) -> str:
        """获取中国股市整体概览，包括主要指数的实时行情。

        数据源：Tushare 专业数据源（正在从 TDX 迁移中）。
        涵盖上证指数、深证成指、创业板指、科创50等主要指数。

        用途：供市场分析师 Agent 了解大盘走势，为个股分析提供市场背景。

        注意：当前为迁移过渡版本，返回占位数据，完整功能待 Tushare 迁移完成后上线。
        """
        try:
            # 使用Tushare获取主要指数数据
            from sinoquant.dataflows.providers.china.tushare import get_tushare_adapter

            adapter = get_tushare_adapter()


            # 使用Tushare获取主要指数信息
            # 这里可以扩展为获取具体的指数数据
            return f"""# 中国股市概览 - {curr_date}

## 主要指数
- 上证指数: 数据获取中...
- 深证成指: 数据获取中...
- 创业板指: 数据获取中...
- 科创50: 数据获取中...

## 说明
市场概览功能正在从TDX迁移到Tushare，完整功能即将推出。
当前可以使用股票数据获取功能分析个股。

数据来源: Tushare专业数据源
更新时间: {curr_date}
"""

        except Exception as e:
            return f"中国市场概览获取失败: {str(e)}。正在从TDX迁移到Tushare数据源。"

    @staticmethod
    @tool
    def get_google_news(
        query: Annotated[str, "Query to search with"],
        curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    ):
        """通过 Google News 搜索获取最新新闻。

        数据源：Google News 搜索 API，通过 interface 层统一调用。
        默认回溯 7 天的新闻数据。

        用途：供新闻分析师 Agent 获取全球和个股相关新闻。
        在 get_stock_news_unified 中作为 AKShare 东方财富新闻的补充数据源。
        """
        google_news_results = interface.get_google_news(query, curr_date, 7)

        return google_news_results

    @staticmethod
    @tool
    def get_realtime_stock_news(
        ticker: Annotated[str, "股票代码，如 600036, 000001"],
        curr_date: Annotated[str, "当前日期，格式为 yyyy-mm-dd"],
    ) -> str:
        """获取A股股票的实时新闻分析，解决传统新闻源的滞后性问题。

        数据源：整合多个专业财经 API，提供 15-30 分钟内的最新新闻。
        支持多种新闻源轮询机制：优先使用实时新闻聚合器，失败时自动尝试备用新闻源。
        优先使用中文财经新闻源（如东方财富）。

        用途：供新闻分析师 Agent 获取高时效性的个股新闻，默认回溯 6 小时。
        与 get_stock_news_unified 的区别：本工具侧重实时性，后者侧重全面性。
        """
        from sinoquant.dataflows.realtime_news_utils import get_realtime_stock_news
        return get_realtime_stock_news(ticker, curr_date, hours_back=6)

    @staticmethod
    @tool
    def get_global_news_openai(
        curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    ):
        """通过 OpenAI 宏观经济新闻 API 获取全球宏观经济新闻。

        数据源：OpenAI 的宏观经济新闻搜索接口，通过 interface 层统一调用。

        用途：供新闻分析师 Agent 获取全球宏观经济动态，为个股分析提供宏观背景。
        包括央行政策、GDP 数据、贸易动态等影响市场的宏观事件。
        """
        openai_news_results = interface.get_global_news_openai(curr_date)

        return openai_news_results

    @staticmethod
    # @tool  # 已移除：请使用 get_stock_fundamentals_unified
    # 遗留 OpenAI 基本面搜索工具：保留代码兼容性，@tool 装饰器已注释
    # 新代码应使用 get_stock_fundamentals_unified，它整合了多数据源并支持数据深度策略
    def get_fundamentals_openai(
        ticker: Annotated[str, "中国A股股票代码"],
        curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    ):
        """获取中国A股股票的基本面信息，通过 OpenAI 搜索补充。

        遗留工具：此方法已弃用，请使用 get_stock_fundamentals_unified 替代。

        数据源：OpenAI 搜索 API，通过 interface 层统一调用。
        对于中国A股，会先通过 StockUtils 获取股票名称，然后用 "公司名(代码)" 格式搜索。

        注意：此方法仅依赖 OpenAI 搜索，不包含结构化财务数据，
        get_stock_fundamentals_unified 提供了更全面的 Tushare/AKShare 数据。
        """
        logger.debug(f"[DEBUG] get_fundamentals_openai 被调用: ticker={ticker}, date={curr_date}")

        # 检查是否为中国股票
        import re
        if re.match(r'^\d{6}$', str(ticker)):
            logger.debug(f"[DEBUG] 检测到中国A股代码: {ticker}")
            # 使用统一接口获取中国股票名称
            try:
                from sinoquant.dataflows.interface import get_china_stock_info_unified
                stock_info = get_china_stock_info_unified(ticker)

                # 解析股票名称
                if "股票名称:" in stock_info:
                    company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                else:
                    company_name = f"股票代码{ticker}"

                logger.debug(f"[DEBUG] 中国股票名称映射: {ticker} -> {company_name}")
            except Exception as e:
                logger.error(f"[DEBUG] 从统一接口获取股票名称失败: {e}")
                company_name = f"股票代码{ticker}"

            # 修改查询以包含正确的公司名称
            modified_query = f"{company_name}({ticker})"
            logger.debug(f"[DEBUG] 修改后的查询: {modified_query}")
        else:
            return f"错误：{ticker} 不是有效的中国A股代码格式，仅支持6位数字代码"

        try:
            openai_fundamentals_results = interface.get_fundamentals_openai(
                modified_query, curr_date
            )
            logger.debug(f"[DEBUG] OpenAI基本面分析结果长度: {len(openai_fundamentals_results) if openai_fundamentals_results else 0}")
            return openai_fundamentals_results
        except Exception as e:
            logger.error(f"[DEBUG] OpenAI基本面分析失败: {str(e)}")
            return f"基本面分析失败: {str(e)}"

    @staticmethod
    # @tool  # 已移除：请使用 get_stock_fundamentals_unified
    # 遗留基本面访问器：保留代码兼容性，@tool 装饰器已注释
    # 新代码应使用 get_stock_fundamentals_unified，它支持数据深度策略和多模块分析
    def get_china_fundamentals(
        ticker: Annotated[str, "中国A股股票代码，如600036"],
        curr_date: Annotated[str, "当前日期，格式为yyyy-mm-dd"],
    ):
        """获取中国A股股票的基本面信息，使用中国股票数据源。

        遗留工具：此方法已弃用，请使用 get_stock_fundamentals_unified 替代。

        数据源：Tushare/AKShare 统一数据源 + OptimizedChinaDataProvider 基本面分析器。
        先获取最近 30 天的股票数据，再通过 OptimizedChinaDataProvider 生成基本面报告。

        与 get_fundamentals_openai 的区别：
        - 本方法使用结构化财务数据（Tushare/AKShare）
        - get_fundamentals_openai 使用 OpenAI 搜索（非结构化）
        - get_stock_fundamentals_unified 整合了两者的优势
        """
        logger.debug(f"[DEBUG] get_china_fundamentals 被调用: ticker={ticker}, date={curr_date}")

        # 检查是否为中国股票
        import re
        if not re.match(r'^\d{6}$', str(ticker)):
            return f"错误：{ticker} 不是有效的中国A股代码格式"

        try:
            # 使用统一数据源接口获取股票数据（默认Tushare，支持备用数据源）
            from sinoquant.dataflows.interface import get_china_stock_data_unified
            logger.debug(f"[DEBUG] 正在获取 {ticker} 的股票数据...")

            # 获取最近30天的数据用于基本面分析
            from datetime import datetime, timedelta
            end_date = datetime.strptime(curr_date, '%Y-%m-%d')
            start_date = end_date - timedelta(days=30)

            stock_data = get_china_stock_data_unified(
                ticker,
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )

            logger.debug(f"[DEBUG] 股票数据获取完成，长度: {len(stock_data) if stock_data else 0}")

            if not stock_data or "获取失败" in stock_data or "错误" in stock_data:
                return f"无法获取股票 {ticker} 的基本面数据：{stock_data}"

            # 调用真正的基本面分析
            from sinoquant.dataflows.optimized_china_data import OptimizedChinaDataProvider

            # 创建分析器实例
            analyzer = OptimizedChinaDataProvider()

            # 生成真正的基本面分析报告
            fundamentals_report = analyzer._generate_fundamentals_report(ticker, stock_data)

            logger.debug(f"[DEBUG] 中国基本面分析报告生成完成")
            logger.debug(f"[DEBUG] get_china_fundamentals 结果长度: {len(fundamentals_report)}")

            return fundamentals_report

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"[DEBUG] get_china_fundamentals 失败:")
            logger.error(f"[DEBUG] 错误: {str(e)}")
            logger.error(f"[DEBUG] 堆栈: {error_details}")
            return f"中国股票基本面分析失败: {str(e)}"

    @staticmethod
    @tool
    @log_tool_call(tool_name="get_stock_fundamentals_unified", log_args=True)
    def get_stock_fundamentals_unified(
        ticker: Annotated[str, "A股股票代码"],
        start_date: Annotated[str, "开始日期，格式：YYYY-MM-DD"] = None,
        end_date: Annotated[str, "结束日期，格式：YYYY-MM-DD"] = None,
        curr_date: Annotated[str, "当前日期，格式：YYYY-MM-DD"] = None
    ) -> str:
        """获取A股股票基本面分析数据，支持基于分析级别的数据获取策略。

        这是基本面分析的核心统一工具，供基本面分析师 Agent 使用。

        数据深度策略（research_depth）：
        ========================================
        根据配置中的 research_depth 参数，自动调整数据获取的范围和深度：

        | 中文等级 | 数字等级 | data_depth | 分析模块 | 数据获取范围 |
        |---------|---------|------------|---------|------------|
        | 快速    | 1       | basic      | basic   | 基础财务指标 |
        | 基础    | 2       | standard   | standard| 标准财务分析 |
        | 标准    | 3       | standard   | standard| 标准财务分析（默认）|
        | 深度    | 4       | full       | full    | 完整基本面分析 |
        | 全面    | 5       | comprehensive | comprehensive | 综合基本面分析 |

        数据获取流程：
        1. 解析 research_depth 配置（支持中文、数字、字符串数字三种输入格式）
        2. 通过 StockUtils 自动识别股票类型（仅支持A股）
        3. 获取最新股价信息（最近 1-2 天数据，仅用于当前价格参考）
        4. 通过 OptimizedChinaDataProvider 获取基本面财务数据（核心数据）
        5. 组合为包含市场信息和数据深度标记的分析报告

        数据源：Tushare/AKShare 统一数据源（通过 interface 层调用，支持自动降级）

        Args:
            ticker: A股股票代码（如：000001、600036）
            start_date: 开始日期（可选，格式：YYYY-MM-DD），基本面分析通常只需近期数据
            end_date: 结束日期（可选，格式：YYYY-MM-DD）
            curr_date: 当前日期（可选，格式：YYYY-MM-DD），默认为当天

        Returns:
            str: 包含股价信息和基本面财务数据的格式化分析报告
        """
        logger.info(f"[统一基本面工具] 分析股票: {ticker}")

        # 获取分析级别配置，支持基于级别的数据获取策略
        research_depth = Toolkit._config.get('research_depth', '标准')
        logger.info(f"[分析级别] 当前分析级别: {research_depth}")

        # 数字等级到中文等级的映射
        # 支持 Web UI 下拉菜单传入的数字值（1-5）和配置文件中的中文值
        numeric_to_chinese = {
            1: "快速",    # 最快速分析，仅获取基础财务指标
            2: "基础",    # 基础分析，获取标准财务数据
            3: "标准",    # 标准分析（默认），平衡速度与深度
            4: "深度",    # 深度分析，获取完整基本面数据
            5: "全面"     # 最全面分析，获取综合基本面数据
        }

        # 标准化研究深度：支持三种输入格式
        # 1. 数字类型（int/float）：如 research_depth=3 -> "标准"
        # 2. 字符串数字：如 research_depth="3" -> "标准"
        # 3. 中文等级字符串：如 research_depth="标准" -> 直接使用
        if isinstance(research_depth, (int, float)):
            research_depth = int(research_depth)
            if research_depth in numeric_to_chinese:
                chinese_depth = numeric_to_chinese[research_depth]
                logger.info(f"[等级转换] 数字等级 {research_depth} -> 中文等级 '{chinese_depth}'")
                research_depth = chinese_depth
            else:
                logger.warning(f"无效的数字等级: {research_depth}，使用默认标准分析")
                research_depth = "标准"
        elif isinstance(research_depth, str):
            # 如果是字符串形式的数字，转换为整数
            if research_depth.isdigit():
                numeric_level = int(research_depth)
                if numeric_level in numeric_to_chinese:
                    chinese_depth = numeric_to_chinese[numeric_level]
                    logger.info(f"[等级转换] 字符串数字 '{research_depth}' -> 中文等级 '{chinese_depth}'")
                    research_depth = chinese_depth
                else:
                    logger.warning(f"无效的字符串数字等级: {research_depth}，使用默认标准分析")
                    research_depth = "标准"
            # 如果已经是中文等级，直接使用
            elif research_depth in ["快速", "基础", "标准", "深度", "全面"]:
                logger.info(f"[等级确认] 使用中文等级: '{research_depth}'")
            else:
                logger.warning(f"未知的研究深度: {research_depth}，使用默认标准分析")
                research_depth = "标准"
        else:
            logger.warning(f"无效的研究深度类型: {type(research_depth)}，使用默认标准分析")
            research_depth = "标准"

        # 根据分析级别调整数据获取策略
        # data_depth 决定 OptimizedChinaDataProvider 获取哪些分析模块
        # analysis_modules 决定基本面报告中包含哪些分析内容
        if research_depth == "快速":
            data_depth = "basic"
            logger.info(f"[分析级别] 快速分析模式：获取基础数据")
        elif research_depth == "基础":
            data_depth = "standard"
            logger.info(f"[分析级别] 基础分析模式：获取标准数据")
        elif research_depth == "标准":
            data_depth = "standard"
            logger.info(f"[分析级别] 标准分析模式：获取标准数据")
        elif research_depth == "深度":
            data_depth = "full"
            logger.info(f"[分析级别] 深度分析模式：获取完整数据")
        elif research_depth == "全面":
            data_depth = "comprehensive"
            logger.info(f"[分析级别] 全面分析模式：获取最全面数据")
        else:
            data_depth = "standard"
            logger.info(f"[分析级别] 未知级别，使用标准分析模式")

        # 添加详细的股票代码追踪日志
        logger.info(f"[股票代码追踪] 统一基本面工具接收到的原始股票代码: '{ticker}' (类型: {type(ticker)})")
        logger.info(f"[股票代码追踪] 股票代码长度: {len(str(ticker))}")

        # 保存原始ticker用于对比
        original_ticker = ticker

        try:
            from sinoquant.utils.stock_utils import StockUtils
            from datetime import datetime, timedelta

            # 自动识别股票类型
            market_info = StockUtils.get_market_info(ticker)
            is_china = market_info['is_china']

            logger.info(f"[股票代码追踪] StockUtils.get_market_info 返回的市场信息: {market_info}")
            logger.info(f"[统一基本面工具] 股票类型: {market_info['market_name']}")
            logger.info(f"[统一基本面工具] 货币: {market_info['currency_name']} ({market_info['currency_symbol']})")

            # 检查ticker是否在处理过程中发生了变化
            if str(ticker) != str(original_ticker):
                logger.warning(f"[股票代码追踪] 警告：股票代码发生了变化！原始: '{original_ticker}' -> 当前: '{ticker}'")

            # 设置默认日期
            if not curr_date:
                curr_date = datetime.now().strftime('%Y-%m-%d')

            # 根据数据深度级别设置不同的分析模块数量
            if data_depth == "basic":
                analysis_modules = "basic"
                logger.info(f"[基本面策略] 快速分析模式：获取基础财务指标")
            elif data_depth == "standard":
                analysis_modules = "standard"
                logger.info(f"[基本面策略] 标准分析模式：获取标准财务分析")
            elif data_depth == "full":
                analysis_modules = "full"
                logger.info(f"[基本面策略] 深度分析模式：获取完整基本面分析")
            elif data_depth == "comprehensive":
                analysis_modules = "comprehensive"
                logger.info(f"[基本面策略] 全面分析模式：获取综合基本面分析")
            else:
                analysis_modules = "standard"
                logger.info(f"[基本面策略] 默认模式：获取标准基本面分析")

            # 基本面分析策略：只获取当前价格和财务数据
            # 与市场数据分析不同，基本面分析不需要长期历史数据
            # 仅需最近几天数据确认当前价格，核心是财务指标和基本面数据
            days_to_fetch = 10  # 获取最近10天数据（确保至少包含1个交易周）
            days_to_analyze = 2  # 仅分析最近2天（基本面数据变化缓慢）

            logger.info(f"[基本面策略] 获取{days_to_fetch}天数据，分析最近{days_to_analyze}天")

            if not start_date:
                start_date = (datetime.now() - timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')

            if not end_date:
                end_date = curr_date

            result_data = []

            if is_china:
                # 中国A股：基本面分析优化策略
                # 分两步获取数据：
                # 第一步：获取最新股价（仅最近1-2天，用于当前价格参考）
                # 第二步：获取基本面财务数据（核心数据，由 OptimizedChinaDataProvider 生成）
                logger.info(f"[统一基本面工具] 处理A股数据，数据深度: {data_depth}...")
                logger.info(f"[股票代码追踪] 进入A股处理分支，ticker: '{ticker}'")

                try:
                    # 获取最新股价信息（只需要最近1-2天的数据）
                    from datetime import datetime, timedelta
                    recent_end_date = curr_date
                    recent_start_date = (datetime.strptime(curr_date, '%Y-%m-%d') - timedelta(days=2)).strftime('%Y-%m-%d')

                    from sinoquant.dataflows.interface import get_china_stock_data_unified
                    logger.info(f"[股票代码追踪] 调用 get_china_stock_data_unified（仅获取最新价格），传入参数: ticker='{ticker}', start_date='{recent_start_date}', end_date='{recent_end_date}'")
                    current_price_data = get_china_stock_data_unified(ticker, recent_start_date, recent_end_date)

                    logger.info(f"[基本面工具调试] A股价格数据返回长度: {len(current_price_data)}")
                    logger.info(f"[基本面工具调试] A股价格数据前500字符:\n{current_price_data[:500]}")

                    result_data.append(f"## A股当前价格信息\n{current_price_data}")
                except Exception as e:
                    logger.error(f"[基本面工具调试] A股价格数据获取失败: {e}")
                    result_data.append(f"## A股当前价格信息\n获取失败: {e}")
                    current_price_data = ""

                try:
                    # 获取基本面财务数据（这是基本面分析的核心）
                    from sinoquant.dataflows.optimized_china_data import OptimizedChinaDataProvider
                    analyzer = OptimizedChinaDataProvider()
                    logger.info(f"[股票代码追踪] 调用 OptimizedChinaDataProvider._generate_fundamentals_report，传入参数: ticker='{ticker}', analysis_modules='{analysis_modules}'")

                    # 传递分析模块参数到基本面分析方法
                    fundamentals_data = analyzer._generate_fundamentals_report(ticker, current_price_data, analysis_modules)

                    logger.info(f"[基本面工具调试] A股基本面数据返回长度: {len(fundamentals_data)}")
                    logger.info(f"[基本面工具调试] A股基本面数据前500字符:\n{fundamentals_data[:500]}")

                    result_data.append(f"## A股基本面财务数据\n{fundamentals_data}")
                except Exception as e:
                    logger.error(f"[基本面工具调试] A股基本面数据获取失败: {e}")
                    result_data.append(f"## A股基本面财务数据\n获取失败: {e}")
            else:
                return f"错误：{ticker} 不是有效的中国A股股票代码，本工具仅支持A股股票基本面分析。"

            # 组合所有数据
            combined_result = f"""# {ticker} 基本面分析数据

**股票类型**: {market_info['market_name']}
**货币**: {market_info['currency_name']} ({market_info['currency_symbol']})
**分析日期**: {curr_date}
**数据深度级别**: {data_depth}

{chr(10).join(result_data)}

---
*数据来源: A股数据源*
"""

            # 添加详细的数据获取日志
            logger.info(f"[统一基本面工具] ===== 数据获取完成摘要 =====")
            logger.info(f"[统一基本面工具] 股票代码: {ticker}")
            logger.info(f"[统一基本面工具] 股票类型: {market_info['market_name']}")
            logger.info(f"[统一基本面工具] 数据深度级别: {data_depth}")
            logger.info(f"[统一基本面工具] 获取的数据模块数量: {len(result_data)}")
            logger.info(f"[统一基本面工具] 总数据长度: {len(combined_result)} 字符")

            # 记录每个数据模块的详细信息
            for i, data_section in enumerate(result_data, 1):
                section_lines = data_section.split('\n')
                section_title = section_lines[0] if section_lines else "未知模块"
                section_length = len(data_section)
                logger.info(f"[统一基本面工具] 数据模块 {i}: {section_title} ({section_length} 字符)")

                if "获取失败" in data_section:
                    logger.warning(f"[统一基本面工具] 数据模块 {i} 包含错误信息")
                else:
                    logger.info(f"[统一基本面工具] 数据模块 {i} 获取成功")

            if data_depth in ["basic", "standard"]:
                logger.info(f"[统一基本面工具] 基础/标准级别策略: 仅获取核心价格数据和基础信息")
            elif data_depth in ["full", "detailed", "comprehensive"]:
                logger.info(f"[统一基本面工具] 完整/详细/全面级别策略: 获取价格数据 + 基本面数据")
            else:
                logger.info(f"[统一基本面工具] 默认策略: 获取完整数据")

            logger.info(f"[统一基本面工具] ===== 数据获取摘要结束 =====")

            return combined_result

        except Exception as e:
            error_msg = f"统一基本面分析工具执行失败: {str(e)}"
            logger.error(f"[统一基本面工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    @log_tool_call(tool_name="get_stock_market_data_unified", log_args=True)
    def get_stock_market_data_unified(
        ticker: Annotated[str, "A股股票代码"],
        start_date: Annotated[str, "开始日期，格式：YYYY-MM-DD。注意：系统会自动扩展到配置的回溯天数（通常为365天），你只需要传递分析日期即可"],
        end_date: Annotated[str, "结束日期，格式：YYYY-MM-DD。通常与start_date相同，传递当前分析日期即可"]
    ) -> str:
        """获取A股股票市场数据和技术指标数据，供市场分析师 Agent 使用。

        自动日期范围扩展机制：
        =============================
        技术指标（如 MACD、RSI、布林带等）需要足够的历史数据才能准确计算。
        例如 MACD 需要 26 天历史，布林带需要 20 天历史，长周期指标可能需要 200+ 天。
        因此系统会自动将用户传入的日期范围向前扩展到配置的回溯天数（通常为 365 天），
        确保技术指标计算有足够的历史数据支撑。

        使用方式：只需传递当前分析日期作为 start_date 和 end_date 即可，
        无需手动计算历史日期范围。

        数据源：Tushare/AKShare 统一数据源（通过 interface 层调用，支持自动降级），
        包含 OHLCV 行情数据、技术指标计算结果等。

        与 get_stock_fundamentals_unified 的区别：
        - 本工具侧重市场行情数据和技术指标（价格、成交量、均线、MACD 等）
        - get_stock_fundamentals_unified 侧重基本面财务数据（财报、财务比率等）

        Args:
            ticker: A股股票代码（如：000001、600036）
            start_date: 开始日期（格式：YYYY-MM-DD），传递当前分析日期即可
            end_date: 结束日期（格式：YYYY-MM-DD），传递当前分析日期即可

        Returns:
            str: 包含市场数据和技术分析的格式化报告
        """
        logger.info(f"[统一市场工具] 分析股票: {ticker}")

        try:
            from sinoquant.utils.stock_utils import StockUtils

            # 自动识别股票类型
            market_info = StockUtils.get_market_info(ticker)
            is_china = market_info['is_china']

            logger.info(f"[统一市场工具] 股票类型: {market_info['market_name']}")
            logger.info(f"[统一市场工具] 货币: {market_info['currency_name']} ({market_info['currency_symbol']})")

            result_data = []

            if is_china:
                # 中国A股：使用中国股票数据源
                logger.info(f"[统一市场工具] 处理A股市场数据...")

                try:
                    from sinoquant.dataflows.interface import get_china_stock_data_unified
                    stock_data = get_china_stock_data_unified(ticker, start_date, end_date)

                    logger.info(f"[市场工具调试] A股数据返回长度: {len(stock_data)}")
                    logger.info(f"[市场工具调试] A股数据前500字符:\n{stock_data[:500]}")

                    result_data.append(f"## A股市场数据\n{stock_data}")
                except Exception as e:
                    logger.error(f"[市场工具调试] A股数据获取失败: {e}")
                    result_data.append(f"## A股市场数据\n获取失败: {e}")
            else:
                return f"错误：{ticker} 不是有效的中国A股股票代码，本工具仅支持A股股票市场数据。"

            # 组合所有数据
            combined_result = f"""# {ticker} 市场数据分析

**股票类型**: {market_info['market_name']}
**货币**: {market_info['currency_name']} ({market_info['currency_symbol']})
**分析期间**: {start_date} 至 {end_date}

{chr(10).join(result_data)}

---
*数据来源: A股数据源*
"""

            logger.info(f"[统一市场工具] 数据获取完成，总长度: {len(combined_result)}")
            return combined_result

        except Exception as e:
            error_msg = f"统一市场数据工具执行失败: {str(e)}"
            logger.error(f"[统一市场工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    @log_tool_call(tool_name="get_stock_news_unified", log_args=True)
    def get_stock_news_unified(
        ticker: Annotated[str, "A股股票代码"],
        curr_date: Annotated[str, "当前日期，格式：YYYY-MM-DD"]
    ) -> str:
        """获取A股股票新闻数据，供新闻分析师 Agent 使用。

        双数据源策略（AKShare + Google News）：
        ========================================
        1. AKShare 东方财富新闻（主数据源）：
           - 通过 AKShareProvider.get_stock_news_sync() 获取
           - 来源：东方财富股吧等中国本土财经平台
           - 优势：中文原生、时效性好、覆盖 A 股相关新闻
           - 返回字段：新闻标题、发布时间、新闻链接

        2. Google News 中文搜索（补充数据源）：
           - 搜索关键词："{股票代码} 股票 公司 财报 新闻"
           - 优势：覆盖更广泛的新闻来源
           - 作为东方财富新闻的补充，当主数据源失败时提供备选

        两个数据源独立获取，互不影响，任一失败不影响另一个。
        最终将两个数据源的结果组合为统一的新闻分析报告。

        Args:
            ticker: A股股票代码（如：000001、600036）
            curr_date: 当前日期（格式：YYYY-MM-DD），新闻默认回溯 7 天

        Returns:
            str: 包含东方财富新闻和 Google 新闻的格式化分析报告
        """
        logger.info(f"[统一新闻工具] 分析股票: {ticker}")

        try:
            from sinoquant.utils.stock_utils import StockUtils
            from datetime import datetime, timedelta

            # 自动识别股票类型
            market_info = StockUtils.get_market_info(ticker)
            is_china = market_info['is_china']

            logger.info(f"[统一新闻工具] 股票类型: {market_info['market_name']}")

            # 计算新闻查询的日期范围
            end_date = datetime.strptime(curr_date, '%Y-%m-%d')
            start_date = end_date - timedelta(days=7)
            start_date_str = start_date.strftime('%Y-%m-%d')

            result_data = []

            if is_china:
                # 中国A股：双数据源新闻获取策略
                # 数据源1：AKShare 东方财富新闻（主数据源，中国本土财经平台）
                # 数据源2：Google News 中文搜索（补充数据源，覆盖更广泛新闻来源）
                logger.info(f"[统一新闻工具] 处理A股新闻...")

                # 1. 尝试获取AKShare东方财富新闻（主数据源）
                try:
                    # 清理股票代码：移除可能的后缀格式（如 .SH, .SZ, .SS, .HK 等）
                    # AKShare 的东方财富新闻接口需要纯6位数字代码
                    clean_ticker = ticker.replace('.SH', '').replace('.SZ', '').replace('.SS', '')\
                                   .replace('.HK', '').replace('.XSHE', '').replace('.XSHG', '')

                    logger.info(f"[统一新闻工具] 尝试获取东方财富新闻: {clean_ticker}")

                    # 通过 AKShare Provider 获取新闻
                    from sinoquant.dataflows.providers.china.akshare import AKShareProvider

                    provider = AKShareProvider()

                    # 获取东方财富新闻
                    news_df = provider.get_stock_news_sync(symbol=clean_ticker)

                    if news_df is not None and not news_df.empty:
                        # 格式化东方财富新闻
                        em_news_items = []
                        for _, row in news_df.iterrows():
                            # AKShare 返回的字段名
                            news_title = row.get('新闻标题', '') or row.get('标题', '')
                            news_time = row.get('发布时间', '') or row.get('时间', '')
                            news_url = row.get('新闻链接', '') or row.get('链接', '')

                            news_item = f"- **{news_title}** [{news_time}]({news_url})"
                            em_news_items.append(news_item)

                        # 添加到结果中
                        if em_news_items:
                            em_news_text = "\n".join(em_news_items)
                            result_data.append(f"## 东方财富新闻\n{em_news_text}")
                            logger.info(f"[统一新闻工具] 成功获取{len(em_news_items)}条东方财富新闻")
                except Exception as em_e:
                    logger.error(f"[统一新闻工具] 东方财富新闻获取失败: {em_e}")
                    result_data.append(f"## 东方财富新闻\n获取失败: {em_e}")

                # 2. 获取Google新闻作为补充（补充数据源）
                try:
                    # A股使用股票代码 + 中文关键词搜索，扩大搜索覆盖范围
                    # 搜索关键词格式："{股票代码} 股票 公司 财报 新闻"
                    clean_ticker = ticker.replace('.SH', '').replace('.SZ', '').replace('.SS', '')\
                                   .replace('.XSHE', '').replace('.XSHG', '')
                    search_query = f"{clean_ticker} 股票 公司 财报 新闻"
                    logger.info(f"[统一新闻工具] A股Google新闻搜索关键词: {search_query}")

                    from sinoquant.dataflows.interface import get_google_news
                    news_data = get_google_news(search_query, curr_date)
                    result_data.append(f"## Google新闻\n{news_data}")
                    logger.info(f"[统一新闻工具] 成功获取Google新闻")
                except Exception as google_e:
                    logger.error(f"[统一新闻工具] Google新闻获取失败: {google_e}")
                    result_data.append(f"## Google新闻\n获取失败: {google_e}")
            else:
                return f"错误：{ticker} 不是有效的中国A股股票代码，本工具仅支持A股股票新闻。"

            # 组合所有数据
            combined_result = f"""# {ticker} 新闻分析

**股票类型**: {market_info['market_name']}
**分析日期**: {curr_date}
**新闻时间范围**: {start_date_str} 至 {curr_date}

{chr(10).join(result_data)}

---
*数据来源: A股新闻源*
"""

            logger.info(f"[统一新闻工具] 数据获取完成，总长度: {len(combined_result)}")
            return combined_result

        except Exception as e:
            error_msg = f"统一新闻工具执行失败: {str(e)}"
            logger.error(f"[统一新闻工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    @log_tool_call(tool_name="get_stock_sentiment_unified", log_args=True)
    def get_stock_sentiment_unified(
        ticker: Annotated[str, "A股股票代码"],
        curr_date: Annotated[str, "当前日期，格式：YYYY-MM-DD"]
    ) -> str:
        """获取A股股票情绪分析数据，供社交媒体分析师 Agent 使用。

        当前状态：A股社交情绪数据源占位实现
        ========================================
        由于中文社交媒体（雪球、东方财富股吧、同花顺等）的情绪数据源
        尚未完全集成，当前返回基础占位分析报告。

        未来计划：
        - 集成雪球 API 获取讨论热度和情绪倾向
        - 集成东方财富股吧 API 获取散户情绪指标
        - 添加自然语言处理模型进行情绪打分
        - 整合同花顺等平台的资金流向和情绪指标

        与 get_chinese_social_sentiment 的区别：
        - 本工具是统一接口（unified），预留了数据深度策略扩展
        - get_chinese_social_sentiment 是独立工具，已实现数据源集成

        Args:
            ticker: A股股票代码（如：000001、600036）
            curr_date: 当前日期（格式：YYYY-MM-DD）

        Returns:
            str: 情绪分析报告（当前为占位数据，待完整集成）
        """
        logger.info(f"[统一情绪工具] 分析股票: {ticker}")

        try:
            from sinoquant.utils.stock_utils import StockUtils

            # 自动识别股票类型
            market_info = StockUtils.get_market_info(ticker)
            is_china = market_info['is_china']

            logger.info(f"[统一情绪工具] 股票类型: {market_info['market_name']}")

            result_data = []

            if is_china:
                # 中国A股：社交媒体情绪分析
                # 注意：当前为占位实现，返回中性情绪的基础分析报告
                # 完整的中文社交媒体情绪分析功能正在开发中
                logger.info(f"[统一情绪工具] 处理A股情绪...")

                try:
                    sentiment_summary = f"""
## 中文市场情绪分析

**股票**: {ticker} ({market_info['market_name']})
**分析日期**: {curr_date}

### 市场情绪概况
- 由于中文社交媒体情绪数据源暂未完全集成，当前提供基础分析
- 建议关注雪球、东方财富、同花顺等平台的讨论热度

### 情绪指标
- 整体情绪: 中性
- 讨论热度: 待分析
- 投资者信心: 待评估

*注：完整的中文社交媒体情绪分析功能正在开发中*
"""
                    result_data.append(sentiment_summary)
                except Exception as e:
                    result_data.append(f"## 中文市场情绪\n获取失败: {e}")
            else:
                return f"错误：{ticker} 不是有效的中国A股股票代码，本工具仅支持A股股票情绪分析。"

            # 组合所有数据
            combined_result = f"""# {ticker} 情绪分析

**股票类型**: {market_info['market_name']}
**分析日期**: {curr_date}

{chr(10).join(result_data)}

---
*数据来源: A股情绪数据源*
"""

            logger.info(f"[统一情绪工具] 数据获取完成，总长度: {len(combined_result)}")
            return combined_result

        except Exception as e:
            error_msg = f"统一情绪分析工具执行失败: {str(e)}"
            logger.error(f"[统一情绪工具] {error_msg}")
            return error_msg
