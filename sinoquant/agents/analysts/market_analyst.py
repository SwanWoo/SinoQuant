"""
市场分析师（Market Analyst）模块

该模块负责创建市场技术分析师节点，是 SinoQuant 多智能体辩论工作流中的核心分析师之一。
市场分析师通过调用统一市场数据工具获取股票行情数据，然后基于真实数据生成技术分析报告。

核心流程：
1. 股票类型检测 —— 根据股票代码格式（如 6位纯数字=A股，字母开头=美股等）判断市场类型
2. 公司名称解析 —— 通过数据源获取公司中文名称，用于报告标题和正文
3. 工具绑定与 LLM 调用 —— 将统一市场数据工具绑定到 LLM，让模型自主决定是否调用
4. 工具执行与报告生成 —— 执行 LLM 请求的工具调用，将工具结果反馈给 LLM 生成最终报告
5. 思维链内容清洗 —— 在外层包装器中清洗 LLM 输出中的思维链/DSML 标签

降级方案（Fallback）：
- 公司名称获取：统一接口 → 数据源管理器直接获取 → 使用股票代码兜底
- 工具执行失败：返回错误信息而非崩溃，确保工作流继续运行
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
import traceback

# 导入分析模块日志装饰器
from sinoquant.utils.tool_logging import log_analyst_module

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")

# 导入Google工具调用处理器 —— Google 模型（如 Gemini）的工具调用格式与 OpenAI 不同，
# 需要使用专门的处理器来适配
from sinoquant.agents.utils.google_tool_handler import GoogleToolCallHandler

# 导入消息过滤工具 —— 从工作流状态的消息历史中过滤出当前分析师相关的消息
from sinoquant.agents.utils.agent_utils import filter_messages_for_analyst

# 当前分析师允许的工具名集合，用于权限控制和日志记录
_MARKET_TOOLS = {"get_stock_market_data_unified", "get_china_stock_data"}


def _get_company_name(ticker: str, market_info: dict) -> str:
    """
    公司名称解析辅助函数

    根据股票代码和市场信息获取公司中文名称。该函数对A股和非A股采用不同的获取策略：
    - A股：通过统一数据接口 get_china_stock_info_unified() 获取，包含降级方案
    - 非A股：直接返回股票代码作为兜底

    降级方案链路：
    1. 优先使用统一接口 get_china_stock_info_unified() 解析"股票名称:"字段
    2. 若统一接口失败，尝试通过数据源管理器直接获取 name 字段
    3. 所有方案均失败时，返回"股票代码{ticker}"作为兜底

    Args:
        ticker: 股票代码（如 "000001"、"AAPL"）
        market_info: 市场信息字典，由 StockUtils.get_market_info() 返回，
                     包含 is_china、market_name、currency_name 等字段

    Returns:
        str: 公司名称（如 "平安银行"），获取失败时返回兜底名称
    """
    try:
        if market_info['is_china']:
            # 中国A股：使用统一接口获取股票信息
            # 统一接口内部会按 Tushare → AKShare → BaoStock 的优先级尝试数据源
            from sinoquant.dataflows.interface import get_china_stock_info_unified
            stock_info = get_china_stock_info_unified(ticker)

            logger.debug(f"📊 [市场分析师] 获取股票信息返回: {stock_info[:200] if stock_info else 'None'}...")

            # 解析股票名称 —— 统一接口返回的格式为 "股票名称:XXX\\n..."
            # 通过分割字符串提取公司名称
            if stock_info and "股票名称:" in stock_info:
                company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                logger.info(f"✅ [市场分析师] 成功获取中国股票名称: {ticker} -> {company_name}")
                return company_name
            else:
                # 降级方案：尝试直接从数据源管理器获取
                logger.warning(f"⚠️ [市场分析师] 无法从统一接口解析股票名称: {ticker}，尝试降级方案")
                try:
                    from sinoquant.dataflows.data_source_manager import get_china_stock_info_unified as get_info_dict
                    info_dict = get_info_dict(ticker)
                    if info_dict and info_dict.get('name'):
                        company_name = info_dict['name']
                        logger.info(f"✅ [市场分析师] 降级方案成功获取股票名称: {ticker} -> {company_name}")
                        return company_name
                except Exception as e:
                    logger.error(f"❌ [市场分析师] 降级方案也失败: {e}")

                logger.error(f"❌ [市场分析师] 所有方案都无法获取股票名称: {ticker}")
                return f"股票代码{ticker}"

    except Exception as e:
        logger.error(f"❌ [DEBUG] 获取公司名称失败: {e}")
        return f"股票{ticker}"


def create_market_analyst(llm, toolkit):
    """
    市场分析师工厂函数

    创建并返回市场分析师节点函数。该函数采用闭包模式，捕获 LLM 实例和工具集，
    返回的 market_analyst_node 可直接作为 LangGraph 图节点使用。

    架构设计：
    - 外层 market_analyst_node()：负责思维链内容清洗，确保报告不包含 <think/> 等标签
    - 内层 _market_analyst_node_inner()：执行核心分析逻辑

    两层分离的原因：
    DeepSeek v4 等模型会在输出中包含 <think/> 思维链标签，
    需要在返回结果前统一清洗，避免污染下游分析师和管理器的输入。

    Args:
        llm: LangChain 兼容的 LLM 实例（如 ChatDeepSeek、ChatDashScope 等）
        toolkit: 数据工具集实例，提供 get_stock_market_data_unified 等工具方法

    Returns:
        Callable: 市场分析师节点函数，接受 state 字典，返回更新后的 state 字典
    """
    # 导入思维链内容清洗工具 —— 用于移除 DeepSeek 等模型输出中的 <think/> 和 DSML 标签
    from sinoquant.utils.text_utils import remove_thinking_content

    def _market_analyst_node_inner(state):
        """
        市场分析师节点核心逻辑

        执行流程：
        1. 死循环修复 —— 检查工具调用计数器，防止 LLM 重复调用工具导致无限循环
        2. 股票类型检测 —— 通过 StockUtils 判断股票所属市场和货币类型
        3. 公司名称解析 —— 调用 _get_company_name() 获取公司中文名称
        4. 工具绑定 —— 将 get_stock_market_data_unified 统一工具绑定到 LLM
        5. LLM 调用 —— 通过 prompt | llm.bind_tools(tools) 链式调用
        6. 分支处理：
           a. Google 模型 → 使用 GoogleToolCallHandler 统一处理器
           b. 非Google 模型 → 标准处理：
              - 无工具调用 → 直接使用 LLM 回复作为报告
              - 有工具调用 → 执行工具 → 将工具结果和原始消息一起传给 LLM 生成报告
        7. 返回更新后的 state（包含 messages、market_report、计数器）

        Args:
            state: LangGraph 工作流状态字典，包含以下关键字段：
                - company_of_interest: 股票代码
                - trade_date: 交易日期
                - messages: 消息历史列表
                - market_tool_call_count: 工具调用计数器

        Returns:
            dict: 更新后的状态，包含 messages、market_report、market_tool_call_count
        """
        logger.debug(f"📈 [DEBUG] ===== 市场分析师节点开始 =====")

        # 🔧 死循环修复：工具调用计数器
        # 某些 LLM（尤其是较弱的模型）可能在收到工具结果后仍然重复调用工具，
        # 导致 LangGraph 工作流在同一节点上无限循环。
        # 通过计数器限制最大工具调用次数（3次），超过后不再执行工具。
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 3  # 最大工具调用次数
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 3  # 最大工具调用次数
        logger.info(f"🔧 [死循环修复] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.debug(f"📈 [DEBUG] 输入参数: ticker={ticker}, date={current_date}")
        logger.debug(f"📈 [DEBUG] 当前状态中的消息数量: {len(state.get('messages', []))}")
        logger.debug(f"📈 [DEBUG] 现有市场报告: {state.get('market_report', 'None')}")

        # 根据股票代码格式选择数据源
        # StockUtils.get_market_info() 通过正则匹配股票代码格式来判断市场类型：
        # - 6位纯数字（以0/3开头=深市，以6开头=沪市）→ A股
        # - 字母开头 → 美股
        # - 5位数字 → 港股等
        from sinoquant.utils.stock_utils import StockUtils

        market_info = StockUtils.get_market_info(ticker)

        logger.debug(f"📈 [DEBUG] 股票类型检查: {ticker} -> {market_info['market_name']} ({market_info['currency_name']})")

        # 获取公司名称
        company_name = _get_company_name(ticker, market_info)
        logger.debug(f"📈 [DEBUG] 公司名称: {ticker} -> {company_name}")

        # 统一使用 get_stock_market_data_unified 工具
        # 该工具内部会自动识别A股股票并调用相应的数据源
        # 统一工具的设计目的是：分析师无需关心底层数据源差异（Tushare/AKShare/BaoStock），
        # 只需调用一个工具即可获取所有市场的行情数据
        logger.info(f"📊 [市场分析师] 使用统一市场数据工具，自动识别A股股票")
        tools = [toolkit.get_stock_market_data_unified]

        # 安全地获取工具名称用于调试
        tool_names_debug = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names_debug.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names_debug.append(tool.__name__)
            else:
                tool_names_debug.append(str(tool))
        logger.info(f"📊 [市场分析师] 绑定的工具: {tool_names_debug}")
        logger.info(f"📊 [市场分析师] 目标市场: {market_info['market_name']}")

        # 🔥 优化：将输出格式要求放在系统提示的开头，确保LLM遵循格式
        # 系统提示词（System Prompt）的设计要点：
        # 1. 明确角色定位：专业股票技术分析师
        # 2. 注入上下文变量：公司名称、股票代码、市场类型、货币单位
        # 3. 严格的工作流程要求：先调用工具获取数据，再生成报告，不要重复调用
        # 4. 输出格式模板：确保报告结构统一，便于下游管理器汇总
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是一位专业的股票技术分析师，与其他分析师协作。\n"
                    "\n"
                    "📋 **分析对象：**\n"
                    "- 公司名称：{company_name}\n"
                    "- 股票代码：{ticker}\n"
                    "- 所属市场：{market_name}\n"
                    "- 计价货币：{currency_name}（{currency_symbol}）\n"
                    "- 分析日期：{current_date}\n"
                    "\n"
                    "🔧 **工具使用：**\n"
                    "你可以使用以下工具：{tool_names}\n"
                    "⚠️ 重要工作流程：\n"
                    "1. 如果消息历史中没有工具结果，立即调用 get_stock_market_data_unified 工具\n"
                    "   - ticker: {ticker}\n"
                    "   - start_date: {current_date}\n"
                    "   - end_date: {current_date}\n"
                    "   注意：系统会自动扩展到365天历史数据，你只需要传递当前分析日期即可\n"
                    "2. 如果消息历史中已经有工具结果（ToolMessage），立即基于工具数据生成最终分析报告\n"
                    "3. 不要重复调用工具！一次工具调用就足够了！\n"
                    "4. 接收到工具数据后，必须立即生成完整的技术分析报告，不要再调用任何工具\n"
                    "\n"
                    "📝 **输出格式要求（必须严格遵守）：**\n"
                    "\n"
                    "## 📊 股票基本信息\n"
                    "- 公司名称：{company_name}\n"
                    "- 股票代码：{ticker}\n"
                    "- 所属市场：{market_name}\n"
                    "\n"
                    "## 📈 技术指标分析\n"
                    "[在这里分析移动平均线、MACD、RSI、布林带等技术指标，提供具体数值]\n"
                    "\n"
                    "## 📉 价格趋势分析\n"
                    "[在这里分析价格趋势，考虑{market_name}市场特点]\n"
                    "\n"
                    "## 💭 投资建议\n"
                    "[在这里给出明确的投资建议：买入/持有/卖出]\n"
                    "\n"
                    "⚠️ **重要提醒：**\n"
                    "- 必须使用上述格式输出，不要自创标题格式\n"
                    "- 所有价格数据使用{currency_name}（{currency_symbol}）表示\n"
                    "- 确保在分析中正确使用公司名称\"{company_name}\"和股票代码\"{ticker}\"\n"
                    "- 不要在标题中使用\"技术分析报告\"等自创标题\n"
                    "- 如果你有明确的技术面投资建议（买入/持有/卖出），请在投资建议部分明确标注\n"
                    "- 不要使用'最终交易建议'前缀，因为最终决策需要综合所有分析师的意见\n"
                    "\n"
                    "请使用中文，基于真实数据进行分析。",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # 安全地获取工具名称，处理函数和工具对象
        tool_names = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names.append(tool.__name__)
            else:
                tool_names.append(str(tool))

        # 🔥 设置所有模板变量
        prompt = prompt.partial(tool_names=", ".join(tool_names))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)
        prompt = prompt.partial(company_name=company_name)
        prompt = prompt.partial(market_name=market_info['market_name'])
        prompt = prompt.partial(currency_name=market_info['currency_name'])
        prompt = prompt.partial(currency_symbol=market_info['currency_symbol'])

        # 添加详细日志
        logger.info(f"📊 [市场分析师] LLM类型: {llm.__class__.__name__}")
        logger.info(f"📊 [市场分析师] LLM模型: {getattr(llm, 'model_name', 'unknown')}")
        logger.info(f"📊 [市场分析师] 消息历史数量: {len(state['messages'])}")
        logger.info(f"📊 [市场分析师] 公司名称: {company_name}")
        logger.info(f"📊 [市场分析师] 股票代码: {ticker}")

        # 打印提示词模板信息
        logger.info("📊 [市场分析师] ========== 提示词模板信息 ==========")
        logger.info(f"📊 [市场分析师] 模板变量已设置: company_name={company_name}, ticker={ticker}, market={market_info['market_name']}")
        logger.info("📊 [市场分析师] ==========================================")

        # 打印实际传递给LLM的消息
        logger.info(f"📊 [市场分析师] ========== 传递给LLM的消息 ==========")
        for i, msg in enumerate(state["messages"]):
            msg_type = type(msg).__name__
            # 🔥 修复：更安全地提取消息内容
            if hasattr(msg, 'content'):
                msg_content = str(msg.content)[:500]  # 增加到500字符以便查看完整内容
            elif isinstance(msg, tuple) and len(msg) >= 2:
                # 处理旧格式的元组消息 ("human", "content")
                msg_content = f"[元组消息] 类型={msg[0]}, 内容={str(msg[1])[:500]}"
            else:
                msg_content = str(msg)[:500]
            logger.info(f"📊 [市场分析师] 消息[{i}] 类型={msg_type}, 内容={msg_content}")
        logger.info(f"📊 [市场分析师] ========== 消息列表结束 ==========")

        # 构建 LLM 链：提示词模板 | 绑定工具的 LLM
        # bind_tools() 将工具定义注入 LLM 的 function calling 机制，
        # LLM 会在需要时主动请求调用工具，而非直接回答
        chain = prompt | llm.bind_tools(tools)

        logger.info(f"📊 [市场分析师] 开始调用LLM...")
        # 修复：传递字典而不是直接传递消息列表，以便 ChatPromptTemplate 能正确处理所有变量
        result = chain.invoke({"messages": state["messages"]})
        logger.info(f"📊 [市场分析师] LLM调用完成")

        # 打印LLM响应
        logger.info(f"📊 [市场分析师] ========== LLM响应开始 ==========")
        logger.info(f"📊 [市场分析师] 响应类型: {type(result).__name__}")
        logger.info(f"📊 [市场分析师] 响应内容: {str(result.content)[:1000]}...")
        if hasattr(result, 'tool_calls') and result.tool_calls:
            logger.info(f"📊 [市场分析师] 工具调用: {result.tool_calls}")
        logger.info(f"📊 [市场分析师] ========== LLM响应结束 ==========")

        # 使用统一的Google工具调用处理器
        # Google 模型（Gemini）的工具调用返回格式与 OpenAI 不同，
        # 需要通过 GoogleToolCallHandler 进行格式适配和统一处理
        if GoogleToolCallHandler.is_google_model(llm):
            logger.info(f"📊 [市场分析师] 检测到Google模型，使用统一工具调用处理器")
            
            # 创建分析提示词
            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="市场分析",
                specific_requirements="重点关注市场数据、价格走势、交易量变化等市场指标。"
            )
            
            # 处理Google模型工具调用
            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result,
                llm=llm,
                tools=tools,
                state=state,
                analysis_prompt_template=analysis_prompt_template,
                analyst_name="市场分析师"
            )

            # 🔧 更新工具调用计数器
            return {
                "messages": [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }
        else:
            # 非Google模型的处理逻辑（OpenAI 兼容模型：DeepSeek、DashScope 等）
            # 这些模型使用标准的 OpenAI function calling 格式
            logger.info(f"📊 [市场分析师] 非Google模型 ({llm.__class__.__name__})，使用标准处理逻辑")
            logger.info(f"📊 [市场分析师] 检查LLM返回结果...")
            logger.info(f"📊 [市场分析师] - 是否有tool_calls: {hasattr(result, 'tool_calls')}")
            if hasattr(result, 'tool_calls'):
                logger.info(f"📊 [市场分析师] - tool_calls数量: {len(result.tool_calls)}")
                if result.tool_calls:
                    for i, tc in enumerate(result.tool_calls):
                        logger.info(f"📊 [市场分析师] - tool_call[{i}]: {tc.get('name', 'unknown')}")

            # 处理市场分析报告 —— 根据工具调用情况分两条路径
            if len(result.tool_calls) == 0:
                # 路径1：LLM 没有请求工具调用，直接使用其回复作为报告
                # 这种情况通常发生在消息历史中已有工具结果，LLM 基于已有数据生成报告
                report = result.content
                logger.info(f"📊 [市场分析师] ✅ 直接回复（无工具调用），长度: {len(report)}")
                logger.debug(f"📊 [DEBUG] 直接回复内容预览: {report[:200]}...")
            else:
                # 路径2：LLM 请求了工具调用 → 执行工具 → 将结果反馈给 LLM 生成完整报告
                # 这是正常的首轮调用流程：LLM 发现消息历史中没有行情数据，请求获取
                logger.info(f"📊 [市场分析师] 🔧 检测到工具调用: {[call.get('name', 'unknown') for call in result.tool_calls]}")

                try:
                    # 执行工具调用：遍历 LLM 请求的所有工具调用，逐个执行
                    from langchain_core.messages import ToolMessage, HumanMessage

                    tool_messages = []
                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')

                        logger.debug(f"📊 [DEBUG] 执行工具: {tool_name}, 参数: {tool_args}")

                        # 找到对应的工具并执行
                        # 通过工具名称匹配找到工具对象，然后调用 invoke() 执行
                        tool_result = None
                        for tool in tools:
                            # 安全地获取工具名称进行比较
                            current_tool_name = None
                            if hasattr(tool, 'name'):
                                current_tool_name = tool.name
                            elif hasattr(tool, '__name__'):
                                current_tool_name = tool.__name__

                            if current_tool_name == tool_name:
                                try:
                                    if tool_name == "get_china_stock_data":
                                        # 中国股票数据工具
                                        tool_result = tool.invoke(tool_args)
                                    else:
                                        # 其他工具
                                        tool_result = tool.invoke(tool_args)
                                    logger.debug(f"📊 [DEBUG] 工具执行成功，结果长度: {len(str(tool_result))}")
                                    break
                                except Exception as tool_error:
                                    logger.error(f"❌ [DEBUG] 工具执行失败: {tool_error}")
                                    tool_result = f"工具执行失败: {str(tool_error)}"

                        if tool_result is None:
                            tool_result = f"未找到工具: {tool_name}"

                        # 创建工具消息 —— ToolMessage 是 LangChain 的标准消息类型，
                        # 用于将工具执行结果反馈给 LLM，tool_call_id 必须与原始调用匹配
                        tool_message = ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_id
                        )
                        tool_messages.append(tool_message)

                    # 基于工具结果生成完整分析报告
                    # 🔥 重要：分析提示词必须包含公司名称和输出格式要求，确保LLM生成正确的报告标题
                    # 这里的 analysis_prompt 作为 HumanMessage 追加到消息序列中，
                    # 指导 LLM 基于工具返回的真实数据生成结构化的技术分析报告
                    analysis_prompt = f"""现在请基于上述工具获取的数据，生成详细的技术分析报告。

**分析对象：**
- 公司名称：{company_name}
- 股票代码：{ticker}
- 所属市场：{market_info['market_name']}
- 计价货币：{market_info['currency_name']}（{market_info['currency_symbol']}）

**输出格式要求（必须严格遵守）：**

请按照以下专业格式输出报告，不要使用emoji符号（如📊📈📉💭等），使用纯文本标题：

# **{company_name}（{ticker}）技术分析报告**
**分析日期：[当前日期]**
**数据来源：Tushare金融数据库**

---

## 一、股票基本信息

- **公司名称**：{company_name}
- **股票代码**：{ticker}
- **所属市场**：{market_info['market_name']}
- **当前价格**：[从工具数据中获取] {market_info['currency_symbol']}
- **涨跌幅**：[从工具数据中获取]
- **成交量**：[从工具数据中获取]

---

## 二、技术指标分析

### 1. 移动平均线（MA）分析

[分析MA5、MA10、MA20、MA60等均线系统，包括：]
- 当前各均线数值
- 均线排列形态（多头/空头）
- 价格与均线的位置关系
- 均线交叉信号

### 2. MACD指标分析

[分析MACD指标，包括：]
- DIF、DEA、MACD柱状图当前数值
- 金叉/死叉信号
- 背离现象
- 趋势强度判断

### 3. RSI相对强弱指标

[分析RSI指标，包括：]
- RSI当前数值
- 超买/超卖区域判断
- 背离信号
- 趋势确认

### 4. 布林带（BOLL）分析

[分析布林带指标，包括：]
- 上轨、中轨、下轨数值
- 价格在布林带中的位置
- 带宽变化趋势
- 突破信号

---

## 三、价格趋势分析

### 1. 短期趋势（5-10个交易日）

[分析短期价格走势，包括支撑位、压力位、关键价格区间]

### 2. 中期趋势（20-60个交易日）

[分析中期价格走势，结合均线系统判断趋势方向]

### 3. 成交量分析

[分析成交量变化，量价配合情况]

---

## 四、投资建议

### 1. 综合评估

[基于上述技术指标，给出综合评估]

### 2. 操作建议

- **投资评级**：买入/持有/卖出
- **目标价位**：[给出具体价格区间] {market_info['currency_symbol']}
- **止损位**：[给出止损价格] {market_info['currency_symbol']}
- **风险提示**：[列出主要风险因素]

### 3. 关键价格区间

- **支撑位**：[具体价格]
- **压力位**：[具体价格]
- **突破买入价**：[具体价格]
- **跌破卖出价**：[具体价格]

---

**重要提醒：**
- 必须严格按照上述格式输出，使用标准的Markdown标题（#、##、###）
- 不要使用emoji符号（📊📈📉💭等）
- 所有价格数据使用{market_info['currency_name']}（{market_info['currency_symbol']}）表示
- 确保在分析中正确使用公司名称"{company_name}"和股票代码"{ticker}"
- 报告标题必须是：# **{company_name}（{ticker}）技术分析报告**
- 报告必须基于工具返回的真实数据进行分析
- 包含具体的技术指标数值和专业分析
- 提供明确的投资建议和风险提示
- 报告长度不少于800字
- 使用中文撰写
- 使用表格展示数据时，确保格式规范"""

                    # 构建完整的消息序列：
                    # 历史消息 + LLM的工具调用请求(AIMessage) + 工具执行结果(ToolMessage) + 分析提示(HumanMessage)
                    # LLM 会基于这个完整的消息序列生成最终的技术分析报告
                    messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]

                    # 生成最终分析报告
                    final_result = llm.invoke(messages)
                    report = final_result.content

                    logger.info(f"📊 [市场分析师] 生成完整分析报告，长度: {len(report)}")

                    # 返回包含工具调用和最终分析的完整消息序列
                    # messages 中包含：AIMessage(工具调用请求) + ToolMessage(工具结果) + AIMessage(最终报告)
                    # 这样下游节点（如 ResearchManager）可以在消息历史中看到完整的分析过程
                    # 🔧 更新工具调用计数器
                    return {
                        "messages": [result] + tool_messages + [final_result],
                        "market_report": report,
                        "market_tool_call_count": tool_call_count + 1
                    }

                except Exception as e:
                    logger.error(f"❌ [市场分析师] 工具执行或分析生成失败: {e}")
                    traceback.print_exc()

                    # 降级处理：工具执行或分析生成失败时，返回错误信息而非崩溃
                    # 确保工作流可以继续运行，下游管理器会收到错误提示
                    report = f"市场分析师调用了工具但分析生成失败: {[call.get('name', 'unknown') for call in result.tool_calls]}"

                    # 🔧 更新工具调用计数器
                    return {
                        "messages": [result],
                        "market_report": report,
                        "market_tool_call_count": tool_call_count + 1
                    }

            # 🔧 更新工具调用计数器
            return {
                "messages": [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }

    def market_analyst_node(state):
        """
        市场分析师节点外层包装器 —— 思维链内容清洗

        该函数是对 _market_analyst_node_inner() 的薄包装，唯一职责是：
        在内层函数返回结果后，对 market_report 进行思维链内容清洗。

        思维链内容清洗的必要性：
        - DeepSeek v4 等模型在启用 DSML（DeepSeek Markup Language）推理模式后，
          会在输出中包含 <think/> 标签包裹的推理过程
        - 这些标签对下游的分析师和管理器来说是噪声，需要在返回前统一移除
        - remove_thinking_content() 会移除 <think/>、<｜tool_calls＞ 等非报告内容

        Args:
            state: LangGraph 工作流状态字典

        Returns:
            dict: 清洗后的状态，market_report 中不包含思维链/DSML 标签
        """
        result = _market_analyst_node_inner(state)
        if "market_report" in result:
            original = result["market_report"]
            # 调用 remove_thinking_content() 清洗思维链标签和 DSML 伪工具调用格式
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [市场分析师] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["market_report"] = cleaned
        return result

    return market_analyst_node
