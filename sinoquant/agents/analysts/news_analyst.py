"""
新闻分析师（News Analyst）模块

该模块负责创建新闻分析师节点，是 SinoQuant 多智能体辩论工作流中的核心分析师之一。
新闻分析师通过调用统一新闻工具获取股票相关新闻数据，然后基于真实新闻生成分析报告。

核心设计：预获取机制（Pre-Fetch Mechanism）
================================================================
新闻分析师面临的核心挑战是：DashScope 和 DeepSeek 等模型的 function calling
能力不稳定，经常出现不调用工具而直接编造新闻分析的情况。为此，本模块实现了
预处理机制，在调用 LLM 之前就获取好新闻数据：

  标准流程（适用于 OpenAI、Gemini 等模型）
  ┌─────────────────────────────────────────────────────┐
  │ 绑定工具 → LLM 自主调用工具 → 获取新闻 → 生成报告  │
  └─────────────────────────────────────────────────────┘

  预处理流程（适用于 DashScope/DeepSeek 模型）
  ┌─────────────────────────────────────────────────────┐
  │ 1. 检测模型类型（DashScope/DeepSeek）               │
  │ 2. 预先调用统一新闻工具获取新闻数据                  │
  │ 3. 将新闻数据直接注入提示词                          │
  │ 4. 调用 LLM 基于注入的新闻生成报告（跳过工具绑定）   │
  └─────────────────────────────────────────────────────┘

  强制补救流程（LLM 不调用工具时的后备方案）
  ┌─────────────────────────────────────────────────────┐
  │ LLM 未调用工具 → 代码层强制获取新闻 → 注入提示词    │
  │ → 重新调用 LLM 生成报告                             │
  └─────────────────────────────────────────────────────┘

其他关键机制：
- 统一新闻工具（get_stock_news_unified）：一个工具覆盖所有股票类型的新闻获取
- 死循环修复：通过工具调用计数器防止无限循环
- 思维链内容清洗：在外层包装器中移除 <think/> 和 DSML 标签
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from datetime import datetime

# 导入统一日志系统和分析模块日志装饰器
from sinoquant.utils.logging_init import get_logger
from sinoquant.utils.tool_logging import log_analyst_module
# 导入统一新闻工具 —— 封装了多个新闻数据源（东财、新浪等），
# 自动根据股票类型选择合适的数据源
from sinoquant.tools.unified_news_tool import create_unified_news_tool
# 导入股票工具类
from sinoquant.utils.stock_utils import StockUtils
# 导入Google工具调用处理器
from sinoquant.agents.utils.google_tool_handler import GoogleToolCallHandler
# 导入消息过滤工具
from sinoquant.agents.utils.agent_utils import filter_messages_for_analyst

# 当前分析师允许的工具名集合
_NEWS_TOOLS = {"get_stock_news_unified", "get_realtime_stock_news"}

logger = get_logger("analysts.news")


def create_news_analyst(llm, toolkit):
    """
    新闻分析师工厂函数

    创建并返回新闻分析师节点函数。采用闭包模式捕获 LLM 实例和工具集。

    与其他分析师的关键差异：
    1. 预获取机制 —— 对 DashScope/DeepSeek 模型，在调用 LLM 前先获取新闻数据
    2. 统一新闻工具 —— 使用 create_unified_news_tool() 创建，而非直接从 toolkit 获取
    3. 强制补救 —— 当 LLM 不调用工具时，代码层强制获取新闻并注入提示词

    Args:
        llm: LangChain 兼容的 LLM 实例
        toolkit: 数据工具集实例

    Returns:
        Callable: 新闻分析师节点函数
    """
    # 导入思维链内容清洗工具
    from sinoquant.utils.text_utils import remove_thinking_content

    @log_analyst_module("news")
    def _news_analyst_node_inner(state):
        """
        新闻分析师节点核心逻辑

        执行流程（含预处理和补救机制）：
        1. 死循环修复 —— 检查工具调用计数器
        2. 股票类型检测与公司名称解析
        3. 创建统一新闻工具
        4. 预处理分支（DashScope/DeepSeek）：
           - 在 LLM 调用前先获取新闻数据
           - 将新闻数据直接注入提示词
           - 跳过工具绑定，直接生成报告
        5. 标准流程（其他模型）：
           - 绑定工具 → LLM 调用 → 检查是否调用了工具
           - 如果没有调用工具 → 强制获取新闻并注入提示词
        6. 返回清洁的 AIMessage（不包含 tool_calls），避免死循环

        Args:
            state: LangGraph 工作流状态字典

        Returns:
            dict: 更新后的状态，包含 messages、news_report、news_tool_call_count
        """
        start_time = datetime.now()

        # 🔧 工具调用计数器 - 防止无限循环
        tool_call_count = state.get("news_tool_call_count", 0)
        max_tool_calls = 3  # 最大工具调用次数
        logger.info(f"🔧 [死循环修复] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[新闻分析师] 开始分析 {ticker} 的新闻，交易日期: {current_date}")
        session_id = state.get("session_id", "未知会话")
        logger.info(f"[新闻分析师] 会话ID: {session_id}，开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 获取市场信息
        market_info = StockUtils.get_market_info(ticker)
        logger.info(f"[新闻分析师] 股票类型: {market_info['market_name']}")
        
        # 获取公司名称 —— 内嵌函数，逻辑与其他分析师的 _get_company_name() 相同
        # 这里使用内嵌函数而非模块级函数，是因为新闻分析师模块的风格选择
        def _get_company_name(ticker: str, market_info: dict) -> str:
            """根据股票代码获取公司名称（内嵌辅助函数）"""
            try:
                if market_info['is_china']:
                    # 中国A股：使用统一接口获取股票信息
                    from sinoquant.dataflows.interface import get_china_stock_info_unified
                    stock_info = get_china_stock_info_unified(ticker)
                    
                    # 解析股票名称
                    if "股票名称:" in stock_info:
                        company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                        logger.debug(f"📊 [DEBUG] 从统一接口获取中国股票名称: {ticker} -> {company_name}")
                        return company_name
                    else:
                        logger.warning(f"⚠️ [DEBUG] 无法从统一接口解析股票名称: {ticker}")
                        return f"股票代码{ticker}"
                        
            except Exception as e:
                logger.error(f"❌ [DEBUG] 获取公司名称失败: {e}")
                return f"股票{ticker}"
        
        company_name = _get_company_name(ticker, market_info)
        logger.info(f"[新闻分析师] 公司名称: {company_name}")
        
        # 🔧 使用统一新闻工具，简化工具调用
        # 统一新闻工具通过 create_unified_news_tool() 工厂函数创建，
        # 内部封装了多个新闻数据源（东方财富、新浪财经等），
        # 自动根据股票类型（A股/美股/港股）选择合适的数据源
        # 与直接使用 toolkit 的工具不同，统一新闻工具支持 model_info 参数，
        # 用于根据模型类型调整新闻获取策略
        logger.info(f"[新闻分析师] 使用统一新闻工具，自动识别A股股票并获取相应新闻")
   # 创建统一新闻工具 —— 注意：工具名称被显式设置为 "get_stock_news_unified"
   # 这是因为 LangChain 的 function calling 需要工具名称与提示词中的一致
        unified_news_tool = create_unified_news_tool(toolkit)
        unified_news_tool.name = "get_stock_news_unified"
        
        tools = [unified_news_tool]
        logger.info(f"[新闻分析师] 已加载统一新闻工具: get_stock_news_unified")

        system_message = (
            """您是一位专业的财经新闻分析师，负责分析最新的市场新闻和事件对股票价格的潜在影响。

您的主要职责包括：
1. 获取和分析最新的实时新闻（优先15-30分钟内的新闻）
2. 评估新闻事件的紧急程度和市场影响
3. 识别可能影响股价的关键信息
4. 分析新闻的时效性和可靠性
5. 提供基于新闻的交易建议和价格影响评估

重点关注的新闻类型：
- 财报发布和业绩指导
- 重大合作和并购消息
- 政策变化和监管动态
- 突发事件和危机管理
- 行业趋势和技术突破
- 管理层变动和战略调整

分析要点：
- 新闻的时效性（发布时间距离现在多久）
- 新闻的可信度（来源权威性）
- 市场影响程度（对股价的潜在影响）
- 投资者情绪变化（正面/负面/中性）
- 与历史类似事件的对比

📊 新闻影响分析要求：
- 评估新闻对股价的短期影响（1-3天）和市场情绪变化
- 分析新闻的利好/利空程度和可能的市场反应
- 评估新闻对公司基本面和长期投资价值的影响
- 识别新闻中的关键信息点和潜在风险
- 对比历史类似事件的市场反应
- 不允许回复'无法评估影响'或'需要更多信息'

请特别注意：
⚠️ 如果新闻数据存在滞后（超过2小时），请在分析中明确说明时效性限制
✅ 优先分析最新的、高相关性的新闻事件
📊 提供新闻对市场情绪和投资者信心的影响评估
💰 必须包含基于新闻的市场反应预期和投资建议
🎯 聚焦新闻内容本身的解读，不涉及技术指标分析

请撰写详细的中文分析报告，并在报告末尾附上Markdown表格总结关键发现。"""
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "您是一位专业的财经新闻分析师。"
                    "\n🚨 CRITICAL REQUIREMENT - 绝对强制要求："
                    "\n"
                    "\n❌ 禁止行为："
                    "\n- 绝对禁止在没有调用工具的情况下直接回答"
                    "\n- 绝对禁止基于推测或假设生成任何分析内容"
                    "\n- 绝对禁止跳过工具调用步骤"
                    "\n- 绝对禁止说'我无法获取实时数据'等借口"
                    "\n"
                    "\n✅ 强制执行步骤："
                    "\n1. 您的第一个动作必须是调用 get_stock_news_unified 工具"
                    "\n2. 该工具会自动识别A股股票并获取相应新闻"
                    "\n3. 只有在成功获取新闻数据后，才能开始分析"
                    "\n4. 您的回答必须基于工具返回的真实数据"
                    "\n"
                    "\n🔧 工具调用格式示例："
                    "\n调用: get_stock_news_unified(stock_code='{ticker}', max_news=10)"
                    "\n"
                    "\n⚠️ 如果您不调用工具，您的回答将被视为无效并被拒绝。"
                    "\n⚠️ 您必须先调用工具获取数据，然后基于数据进行分析。"
                    "\n⚠️ 没有例外，没有借口，必须调用工具。"
                    "\n"
                    "\n您可以访问以下工具：{tool_names}。"
                    "\n{system_message}"
                    "\n供您参考，当前日期是{current_date}。我们正在查看公司{ticker}。"
                    "\n请按照上述要求执行，用中文撰写所有分析内容。",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)
        
        # 获取模型信息用于统一新闻工具的特殊处理
        model_info = ""
        try:
            if hasattr(llm, 'model_name'):
                model_info = f"{llm.__class__.__name__}:{llm.model_name}"
            else:
                model_info = llm.__class__.__name__
        except:
            model_info = "Unknown"
        
        logger.info(f"[新闻分析师] 准备调用LLM进行新闻分析，模型: {model_info}")
        
        # 🚨 DashScope/DeepSeek 预处理机制
        # 这些模型的 function calling 能力不稳定，经常不调用工具而直接编造内容。
        # 解决方案：在调用 LLM 之前，先通过代码层获取新闻数据，
        # 然后将数据直接注入提示词，让 LLM 基于真实数据生成报告。
        # 这样完全绕过了 function calling 机制，消除了工具调用失败的风险。
        pre_fetched_news = None
        if 'DashScope' in llm.__class__.__name__ or 'DeepSeek' in llm.__class__.__name__:
            logger.warning(f"[新闻分析师] 🚨 检测到{llm.__class__.__name__}模型，启动预处理强制新闻获取...")
            try:
                # 强制预先获取新闻数据
                logger.info(f"[新闻分析师] 🔧 预处理：强制调用统一新闻工具...")
                logger.info(f"[新闻分析师] 📊 调用参数: stock_code={ticker}, max_news=10, model_info={model_info}")

                pre_fetched_news = unified_news_tool(stock_code=ticker, max_news=10, model_info=model_info)

                logger.info(f"[新闻分析师] 📋 预处理返回结果长度: {len(pre_fetched_news) if pre_fetched_news else 0} 字符")
                logger.info(f"[新闻分析师] 📄 预处理返回结果预览 (前500字符): {pre_fetched_news[:500] if pre_fetched_news else 'None'}")

                if pre_fetched_news and len(pre_fetched_news.strip()) > 100:
                    logger.info(f"[新闻分析师] ✅ 预处理成功获取新闻: {len(pre_fetched_news)} 字符")

                    # 预处理成功：将新闻数据直接注入增强提示词，跳过工具绑定
                    # 这是预处理流程的核心 —— 不使用 bind_tools()，直接用纯文本调用 LLM
                    enhanced_prompt = f"""
您是一位专业的财经新闻分析师。请基于以下已获取的最新新闻数据，对股票 {ticker}（{company_name}）进行详细分析：

=== 最新新闻数据 ===
{pre_fetched_news}

=== 分析要求 ===
{system_message}

请基于上述真实新闻数据撰写详细的中文分析报告。注意：新闻数据已经提供，您无需再调用任何工具。
"""

                    logger.info(f"[新闻分析师] 🔄 使用预获取新闻数据直接生成分析...")
                    logger.info(f"[新闻分析师] 📝 增强提示词长度: {len(enhanced_prompt)} 字符")

                    llm_start_time = datetime.now()
                    result = llm.invoke([{"role": "user", "content": enhanced_prompt}])

                    llm_end_time = datetime.now()
                    llm_time_taken = (llm_end_time - llm_start_time).total_seconds()
                    logger.info(f"[新闻分析师] LLM调用完成（预处理模式），耗时: {llm_time_taken:.2f}秒")

                    # 直接返回结果，跳过后续的工具调用检测
                    if hasattr(result, 'content') and result.content:
                        report = result.content
                        logger.info(f"[新闻分析师] ✅ 预处理模式成功，报告长度: {len(report)} 字符")
                        logger.info(f"[新闻分析师] 📄 报告预览 (前300字符): {report[:300]}")

                        # 跳转到最终处理 —— 创建清洁的 AIMessage（不含 tool_calls）
                        # 这确保 LangGraph 工作流能正确判断分析已完成，避免重复调用分析师节点
                        from langchain_core.messages import AIMessage
                        clean_message = AIMessage(content=report)

                        end_time = datetime.now()
                        time_taken = (end_time - start_time).total_seconds()
                        logger.info(f"[新闻分析师] 新闻分析完成（预处理模式），总耗时: {time_taken:.2f}秒")
                        # 🔧 更新工具调用计数器
                        return {
                            "messages": [clean_message],
                            "news_report": report,
                            "news_tool_call_count": tool_call_count + 1
                        }
                    else:
                        logger.warning(f"[新闻分析师] ⚠️ LLM返回结果为空，回退到标准模式")

                else:
                    # 预处理获取新闻失败或内容过短（<100字符），回退到标准流程
                    # 标准流程会绑定工具让 LLM 尝试调用，如果仍然失败还有强制补救
                    logger.warning(f"[新闻分析师] ⚠️ 预处理获取新闻失败或内容过短（{len(pre_fetched_news) if pre_fetched_news else 0}字符），回退到标准模式")
                    if pre_fetched_news:
                        logger.warning(f"[新闻分析师] 📄 失败的新闻内容: {pre_fetched_news}")

            except Exception as e:
                # 预处理过程中出现异常（如网络超时、数据源不可用），回退到标准流程
                logger.error(f"[新闻分析师] ❌ 预处理失败: {e}，回退到标准模式")
                import traceback
                logger.error(f"[新闻分析师] 📋 异常堆栈: {traceback.format_exc()}")

        # ===== 标准流程（非预处理路径） =====
        # 对于 OpenAI、Gemini 等 function calling 能力稳定的模型，
        # 使用标准的工具绑定方式让 LLM 自主决定是否调用工具
        llm_start_time = datetime.now()
        chain = prompt | llm.bind_tools(tools)
        logger.info(f"[新闻分析师] 开始LLM调用，分析 {ticker} 的新闻")
        # 修复：传递字典而不是直接传递消息列表，以便 ChatPromptTemplate 能正确处理所有变量
        result = chain.invoke({"messages": state["messages"]})
        
        llm_end_time = datetime.now()
        llm_time_taken = (llm_end_time - llm_start_time).total_seconds()
        logger.info(f"[新闻分析师] LLM调用完成，耗时: {llm_time_taken:.2f}秒")

        # Google 模型使用统一的工具调用处理器
        # Gemini 的 function calling 返回格式与 OpenAI 不同，需要专门适配
        if GoogleToolCallHandler.is_google_model(llm):
            logger.info(f"📊 [新闻分析师] 检测到Google模型，使用统一工具调用处理器")
            
            # 创建分析提示词
            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="新闻分析",
                specific_requirements="重点关注新闻事件对股价的影响、市场情绪变化、政策影响等。"
            )
            
            # 处理Google模型工具调用
            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result,
                llm=llm,
                tools=tools,
                state=state,
                analysis_prompt_template=analysis_prompt_template,
                analyst_name="新闻分析师"
            )
        else:
            # 非Google模型的处理逻辑
            logger.info(f"[新闻分析师] 非Google模型 ({llm.__class__.__name__})，使用标准处理逻辑")

            # 检查工具调用情况
            current_tool_calls = len(result.tool_calls) if hasattr(result, 'tool_calls') else 0
            logger.info(f"[新闻分析师] LLM调用了 {current_tool_calls} 个工具")
            logger.debug(f"📊 [DEBUG] 累计工具调用次数: {tool_call_count}/{max_tool_calls}")

            if current_tool_calls == 0:
                # 强制补救机制：LLM 没有调用任何工具
                # 这是标准流程中的后备方案，与预处理机制的目的相同：
                # 确保 LLM 基于真实新闻数据生成报告，而非编造内容
                logger.warning(f"[新闻分析师] ⚠️ {llm.__class__.__name__} 没有调用任何工具，启动补救机制...")
                logger.warning(f"[新闻分析师] 📄 LLM原始响应内容 (前500字符): {result.content[:500] if hasattr(result, 'content') else 'No content'}")

                try:
                    # 强制获取新闻数据 —— 在代码层直接调用统一新闻工具，绕过 LLM 的 function calling
                    logger.info(f"[新闻分析师] 🔧 强制调用统一新闻工具获取新闻数据...")
                    logger.info(f"[新闻分析师] 📊 调用参数: stock_code={ticker}, max_news=10")

                    forced_news = unified_news_tool(stock_code=ticker, max_news=10, model_info=model_info)

                    logger.info(f"[新闻分析师] 📋 强制获取返回结果长度: {len(forced_news) if forced_news else 0} 字符")
                    logger.info(f"[新闻分析师] 📄 强制获取返回结果预览 (前500字符): {forced_news[:500] if forced_news else 'None'}")

                    if forced_news and len(forced_news.strip()) > 100:
                        logger.info(f"[新闻分析师] ✅ 强制获取新闻成功: {len(forced_news)} 字符")

                        # 将强制获取的新闻数据注入提示词，重新调用 LLM 生成报告
                        # 这里使用纯文本调用（不绑定工具），确保 LLM 只生成文本输出
                        forced_prompt = f"""
您是一位专业的财经新闻分析师。请基于以下最新获取的新闻数据，对股票 {ticker}（{company_name}）进行详细的新闻分析：

=== 最新新闻数据 ===
{forced_news}

=== 分析要求 ===
{system_message}

请基于上述真实新闻数据撰写详细的中文分析报告。
"""

                        logger.info(f"[新闻分析师] 🔄 基于强制获取的新闻数据重新生成完整分析...")
                        logger.info(f"[新闻分析师] 📝 强制提示词长度: {len(forced_prompt)} 字符")

                        forced_result = llm.invoke([{"role": "user", "content": forced_prompt}])

                        if hasattr(forced_result, 'content') and forced_result.content:
                            report = forced_result.content
                            logger.info(f"[新闻分析师] ✅ 强制补救成功，生成基于真实数据的报告，长度: {len(report)} 字符")
                            logger.info(f"[新闻分析师] 📄 报告预览 (前300字符): {report[:300]}")
                        else:
                            logger.warning(f"[新闻分析师] ⚠️ 强制补救LLM返回为空，使用原始结果")
                            report = result.content if hasattr(result, 'content') else ""
                    else:
                        logger.warning(f"[新闻分析师] ⚠️ 统一新闻工具获取失败或内容过短（{len(forced_news) if forced_news else 0}字符），使用原始结果")
                        if forced_news:
                            logger.warning(f"[新闻分析师] 📄 失败的新闻内容: {forced_news}")
                        report = result.content if hasattr(result, 'content') else ""

                except Exception as e:
                    logger.error(f"[新闻分析师] ❌ 强制补救过程失败: {e}")
                    import traceback
                    logger.error(f"[新闻分析师] 📋 异常堆栈: {traceback.format_exc()}")
                    report = result.content if hasattr(result, 'content') else ""
            else:
                # 有工具调用 → 手动执行工具 + 手动调用 LLM 生成报告
                # 不依赖 LangGraph 自动工具循环（非 Google 模型下该循环
                # 会导致 LLM 反复输出 DSML 伪造工具调用，报告始终为空）
                logger.info(f"[新闻分析师] 🔧 检测到工具调用，手动执行工具并生成报告")

                try:
                    from langchain_core.messages import ToolMessage, HumanMessage

                    tool_messages = []
                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')

                        logger.debug(f"[新闻分析师] 执行工具: {tool_name}, 参数: {tool_args}")

                        tool_result = None
                        for tool in tools:
                            current_tool_name = getattr(tool, 'name', None) or getattr(tool, '__name__', None)
                            if current_tool_name == tool_name:
                                try:
                                    tool_result = tool.invoke(tool_args)
                                    logger.debug(f"[新闻分析师] 工具执行成功，结果长度: {len(str(tool_result))}")
                                    break
                                except Exception as tool_error:
                                    logger.error(f"[新闻分析师] 工具执行失败: {tool_error}")
                                    tool_result = f"工具执行失败: {str(tool_error)}"

                        if tool_result is None:
                            tool_result = f"未找到工具: {tool_name}"

                        tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

                    analysis_prompt = f"""现在请基于上述工具获取的新闻数据，生成详细的新闻分析报告。

**分析对象：**
- 公司名称：{company_name}
- 股票代码：{ticker}
- 分析日期：{current_date}

**输出格式要求（必须严格遵守）：**

请按照以下专业格式输出报告，不要使用emoji符号：

# **{company_name}（{ticker}）新闻分析报告**
**分析日期：{current_date}**

---

## 一、新闻概况

[概述近期关于该公司的新闻报道数量、整体倾向]

---

## 二、重要新闻分析

### 1. [新闻标题1]
[分析新闻内容及其对股价的影响]

### 2. [新闻标题2]
[分析新闻内容及其对股价的影响]

### 3. [新闻标题3]
[分析新闻内容及其对股价的影响]

---

## 三、新闻事件对股价的影响评估

[评估近期新闻事件对股价的综合影响：利好/利空/中性]

---

## 四、投资建议

- **建议方向**：[看多/看空/中性]
- **影响持续时间**：[短期/中期/长期]

---

## 五、关键发现总结

| 新闻事件 | 影响方向 | 影响程度 | 持续时间 |
|---------|---------|---------|---------|
| [事件1] | [利好/利空/中性] | [强/中/弱] | [短/中/长] |

要求：
- 基于工具返回的真实新闻数据进行分析
- 报告长度不少于500字
- 使用中文撰写
- 不要使用emoji符号
- 严禁输出任何XML/HTML标签或工具调用格式，只输出报告正文"""

                    messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
                    final_result = llm.invoke(messages)
                    report = final_result.content

                    logger.info(f"[新闻分析师] 生成完整新闻分析报告，长度: {len(report)}")

                    # 返回包含工具调用和最终分析的完整消息序列
                    from langchain_core.messages import AIMessage
                    clean_message = AIMessage(content=report)
                    return {
                        "messages": [result] + tool_messages + [clean_message],
                        "news_report": report,
                        "news_tool_call_count": tool_call_count + 1
                    }

                except Exception as e:
                    logger.error(f"[新闻分析师] 工具执行或分析生成失败: {e}")
                    import traceback
                    traceback.print_exc()
                    report = f"新闻分析师调用了工具但分析生成失败: {[tc.get('name', 'unknown') for tc in result.tool_calls]}"
                    from langchain_core.messages import AIMessage
                    clean_message = AIMessage(content=report)
                    return {
                        "messages": [clean_message],
                        "news_report": report,
                        "news_tool_call_count": tool_call_count + 1
                    }
        
        total_time_taken = (datetime.now() - start_time).total_seconds()
        logger.info(f"[新闻分析师] 新闻分析完成，总耗时: {total_time_taken:.2f}秒")

        # 🔧 修复死循环问题：返回清洁的 AIMessage，不包含 tool_calls
        # 如果返回包含 tool_calls 的 AIMessage，LangGraph 工作流会认为
        # 工具尚未执行完毕，会再次调用分析师节点，导致无限循环。
        # 因此，无论走哪条路径，最终都创建一个不含 tool_calls 的清洁消息。
        # 这确保工作流图能正确判断分析已完成，避免重复调用
        from langchain_core.messages import AIMessage
        clean_message = AIMessage(content=report)

        logger.info(f"[新闻分析师] ✅ 返回清洁消息，报告长度: {len(report)} 字符")

        # 🔧 更新工具调用计数器
        return {
            "messages": [clean_message],
            "news_report": report,
            "news_tool_call_count": tool_call_count + 1
        }

    def news_analyst_node(state):
        """
        新闻分析师节点外层包装器 —— 思维链内容清洗

        在内层函数返回后，对 news_report 进行思维链/DSML 标签清洗。
        移除 <think/> 推理标签和 <｜tool_calls＞ 等伪工具调用格式，
        确保下游分析师和管理器接收到的报告是干净的纯文本。
        """
        result = _news_analyst_node_inner(state)
        if "news_report" in result:
            original = result["news_report"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [新闻分析师] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["news_report"] = cleaned
        return result

    return news_analyst_node
