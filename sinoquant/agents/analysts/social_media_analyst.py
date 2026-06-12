"""
社交媒体分析师（Social Media Analyst）模块

该模块负责创建社交媒体分析师节点，是 SinoQuant 多智能体辩论工作流中的核心分析师之一。
社交媒体分析师通过调用统一情绪分析工具获取投资者情绪数据，然后基于真实数据生成情绪分析报告。

核心功能：
1. 社交媒体情绪分析 —— 分析中国主要财经平台（雪球、东方财富股吧等）的投资者讨论
2. 情绪量化评估 —— 提供情绪指数评分（1-10分）和预期价格波动幅度
3. 情绪趋势分析 —— 识别情绪极端点和可能的反转信号
4. 交易时机建议 —— 基于情绪变化提供交易建议

与其他分析师的差异：
- 社交媒体分析师使用 get_stock_sentiment_unified 统一情绪分析工具
- 相比基本面/新闻分析师，社交媒体分析师没有强制工具调用机制
  （因为情绪数据的优先级低于基本面和新闻数据）
- 仍保留了 Google 工具调用处理器和思维链内容清洗
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json

# 导入统一日志系统和分析模块日志装饰器
from sinoquant.utils.logging_init import get_logger
from sinoquant.utils.tool_logging import log_analyst_module
logger = get_logger("analysts.social_media")

# 导入Google工具调用处理器 —— 用于适配 Gemini 等模型的工具调用格式
from sinoquant.agents.utils.google_tool_handler import GoogleToolCallHandler

# 导入消息过滤工具
from sinoquant.agents.utils.agent_utils import filter_messages_for_analyst

# 当前分析师允许的工具名集合
# 社交媒体分析师只使用统一情绪分析工具
_SOCIAL_TOOLS = {"get_stock_sentiment_unified"}


def _get_company_name_for_social_media(ticker: str, market_info: dict) -> str:
    """
    社交媒体分析师专用：公司名称解析辅助函数

    逻辑与其他分析师的 _get_company_name() 相同，包含降级方案链路：
    统一接口 → 数据源管理器 → 股票代码兜底

    Args:
        ticker: 股票代码
        market_info: 市场信息字典

    Returns:
        str: 公司名称
    """
    try:
        if market_info['is_china']:
            # 中国A股：使用统一接口获取股票信息
            from sinoquant.dataflows.interface import get_china_stock_info_unified
            stock_info = get_china_stock_info_unified(ticker)

            logger.debug(f"📊 [社交媒体分析师] 获取股票信息返回: {stock_info[:200] if stock_info else 'None'}...")

            # 解析股票名称
            if stock_info and "股票名称:" in stock_info:
                company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                logger.info(f"✅ [社交媒体分析师] 成功获取中国股票名称: {ticker} -> {company_name}")
                return company_name
            else:
                # 降级方案：尝试直接从数据源管理器获取
                logger.warning(f"⚠️ [社交媒体分析师] 无法从统一接口解析股票名称: {ticker}，尝试降级方案")
                try:
                    from sinoquant.dataflows.data_source_manager import get_china_stock_info_unified as get_info_dict
                    info_dict = get_info_dict(ticker)
                    if info_dict and info_dict.get('name'):
                        company_name = info_dict['name']
                        logger.info(f"✅ [社交媒体分析师] 降级方案成功获取股票名称: {ticker} -> {company_name}")
                        return company_name
                except Exception as e:
                    logger.error(f"❌ [社交媒体分析师] 降级方案也失败: {e}")

                logger.error(f"❌ [社交媒体分析师] 所有方案都无法获取股票名称: {ticker}")
                return f"股票代码{ticker}"

    except Exception as e:
        logger.error(f"❌ [社交媒体分析师] 获取公司名称失败: {e}")
        return f"股票{ticker}"


def create_social_media_analyst(llm, toolkit):
    """
    社交媒体分析师工厂函数

    创建并返回社交媒体分析师节点函数。采用闭包模式捕获 LLM 实例和工具集。

    与基本面/新闻分析师相比，社交媒体分析师的实现相对简单：
    1. 没有预处理机制 —— 不需要像新闻分析师那样在 LLM 调用前预获取数据
    2. 没有强制工具调用 —— 不需要像基本面分析师那样多层级降级方案
    3. 仍然保留了 Google 工具调用处理器和思维链内容清洗

    Args:
        llm: LangChain 兼容的 LLM 实例
        toolkit: 数据工具集实例，提供 get_stock_sentiment_unified 工具

    Returns:
        Callable: 社交媒体分析师节点函数
    """
    # 导入思维链内容清洗工具
    from sinoquant.utils.text_utils import remove_thinking_content

    @log_analyst_module("social_media")
    def _social_media_analyst_node_inner(state):
        """
        社交媒体分析师节点核心逻辑

        执行流程（相对简化）：
        1. 死循环修复 —— 检查工具调用计数器（max=3）
        2. 股票类型检测与公司名称解析
        3. 工具绑定 —— 将 get_stock_sentiment_unified 统一情绪分析工具绑定到 LLM
        4. LLM 调用 —— 通过 prompt | llm.bind_tools(tools) 链式调用
        5. 分支处理：
           a. Google 模型 → GoogleToolCallHandler 统一处理器
           b. 非Google 模型 → 检查是否有工具调用：
              - 有工具调用 → 返回结果（依赖工作流自动执行工具）
              - 无工具调用 → 直接使用 LLM 回复作为报告（无强制补救）
        6. 返回更新后的 state

        注意：社交媒体分析师在 LLM 不调用工具时，不像基本面/新闻分析师那样
        有强制补救机制。这是因为情绪数据的优先级相对较低，如果 LLM 已经有
        足够的上下文信息，可以直接生成分析。

        Args:
            state: LangGraph 工作流状态字典

        Returns:
            dict: 更新后的状态，包含 messages、sentiment_report、sentiment_tool_call_count
        """
        # 🔧 死循环修复：工具调用计数器
        tool_call_count = state.get("sentiment_tool_call_count", 0)
        max_tool_calls = 3  # 最大工具调用次数
        logger.info(f"🔧 [死循环修复] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # 获取股票市场信息
        from sinoquant.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)

        # 获取公司名称
        company_name = _get_company_name_for_social_media(ticker, market_info)
        logger.info(f"[社交媒体分析师] 公司名称: {company_name}")

        # 统一使用 get_stock_sentiment_unified 工具
        # 该工具内部会自动识别A股股票并调用相应的情绪数据源
        # 对于A股，它会从东方财富股吧、雪球等平台获取投资者讨论和情绪数据
        logger.info(f"[社交媒体分析师] 使用统一情绪分析工具，自动识别A股股票")
        tools = [toolkit.get_stock_sentiment_unified]

        system_message = (
            """您是一位专业的中国市场社交媒体和投资情绪分析师，负责分析中国投资者对特定股票的讨论和情绪变化。

您的主要职责包括：
1. 分析中国主要财经平台的投资者情绪（如雪球、东方财富股吧等）
2. 监控财经媒体和新闻对股票的报道倾向
3. 识别影响股价的热点事件和市场传言
4. 评估散户与机构投资者的观点差异
5. 分析政策变化对投资者情绪的影响
6. 评估情绪变化对股价的潜在影响

重点关注平台：
- 财经新闻：财联社、新浪财经、东方财富、腾讯财经
- 投资社区：雪球、东方财富股吧、同花顺
- 社交媒体：微博财经大V、知乎投资话题
- 专业分析：各大券商研报、财经自媒体

分析要点：
- 投资者情绪的变化趋势和原因
- 关键意见领袖(KOL)的观点和影响力
- 热点事件对股价预期的影响
- 政策解读和市场预期变化
- 散户情绪与机构观点的差异

📊 情绪影响分析要求：
- 量化投资者情绪强度（乐观/悲观程度）和情绪变化趋势
- 评估情绪变化对短期市场反应的影响（1-5天）
- 分析散户情绪与市场走势的相关性
- 识别情绪极端点和可能的情绪反转信号
- 提供基于情绪分析的市场预期和投资建议
- 评估市场情绪对投资者信心和决策的影响程度
- 不允许回复'无法评估情绪影响'或'需要更多数据'

💰 必须包含：
- 情绪指数评分（1-10分）
- 预期价格波动幅度
- 基于情绪的交易时机建议

请撰写详细的中文分析报告，并在报告末尾附上Markdown表格总结关键发现。
注意：由于中国社交媒体API限制，如果数据获取受限，请明确说明并提供替代分析建议。"""
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "您是一位有用的AI助手，与其他助手协作。"
                    " 使用提供的工具来推进回答问题。"
                    " 如果您无法完全回答，没关系；具有不同工具的其他助手"
                    " 将从您停下的地方继续帮助。执行您能做的以取得进展。"
                    " 如果您或任何其他助手有最终交易提案：**买入/持有/卖出**或可交付成果，"
                    " 请在您的回应前加上最终交易提案：**买入/持有/卖出**，以便团队知道停止。"
                    " 您可以访问以下工具：{tool_names}。\n{system_message}"
                    "供您参考，当前日期是{current_date}。我们要分析的当前公司是{ticker}。请用中文撰写所有分析内容。",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        # 安全地获取工具名称，处理函数和工具对象
        tool_names = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names.append(tool.__name__)
            else:
                tool_names.append(str(tool))

        prompt = prompt.partial(tool_names=", ".join(tool_names))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | llm.bind_tools(tools)

        # 修复：传递字典而不是直接传递消息列表，以便 ChatPromptTemplate 能正确处理所有变量
        result = chain.invoke({"messages": state["messages"]})

        # 使用统一的Google工具调用处理器
        if GoogleToolCallHandler.is_google_model(llm):
            logger.info(f"📊 [社交媒体分析师] 检测到Google模型，使用统一工具调用处理器")
            
            # 创建分析提示词
            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="社交媒体情绪分析",
                specific_requirements="重点关注投资者情绪、社交媒体讨论热度、舆论影响等。"
            )
            
            # 处理Google模型工具调用
            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result,
                llm=llm,
                tools=tools,
                state=state,
                analysis_prompt_template=analysis_prompt_template,
                analyst_name="社交媒体分析师"
            )
        else:
            # 非Google模型的处理逻辑（OpenAI 兼容模型：DeepSeek、DashScope 等）
            # 与市场分析师相同：手动执行工具 + 手动调用 LLM 生成报告
            # 不依赖 LangGraph 的自动工具循环（该循环在非 Google 模型下
            # 会导致 LLM 反复输出 DSML 伪造工具调用，报告始终为空）
            logger.info(f"[社交媒体分析师] 非Google模型 ({llm.__class__.__name__})，使用手动工具执行路径")

            if len(result.tool_calls) == 0:
                # LLM 没有调用工具，直接使用其文本回复作为报告
                report = result.content
                logger.info(f"[社交媒体分析师] ✅ 直接回复（无工具调用），长度: {len(report)}")
            else:
                # LLM 请求了工具调用 → 手动执行工具 → 基于 结果生成完整报告
                logger.info(f"[社交媒体分析师] 🔧 检测到工具调用: {[tc.get('name', 'unknown') for tc in result.tool_calls]}")

                try:
                    from langchain_core.messages import ToolMessage, HumanMessage

                    tool_messages = []
                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')

                        logger.debug(f"[社交媒体分析师] 执行工具: {tool_name}, 参数: {tool_args}")

                        tool_result = None
                        for tool in tools:
                            current_tool_name = getattr(tool, 'name', None) or getattr(tool, '__name__', None)
                            if current_tool_name == tool_name:
                                try:
                                    tool_result = tool.invoke(tool_args)
                                    logger.debug(f"[社交媒体分析师] 工具执行成功，结果长度: {len(str(tool_result))}")
                                    break
                                except Exception as tool_error:
                                    logger.error(f"[社交媒体分析师] 工具执行失败: {tool_error}")
                                    tool_result = f"工具执行失败: {str(tool_error)}"

                        if tool_result is None:
                            tool_result = f"未找到工具: {tool_name}"

                        tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

                    # 基于工具结果生成完整情绪分析报告
                    analysis_prompt = f"""现在请基于上述工具获取的数据，生成详细的社交媒体情绪分析报告。

**分析对象：**
- 公司名称：{company_name}
- 股票代码：{ticker}

**输出格式要求（必须严格遵守）：**

请按照以下专业格式输出报告，不要使用emoji符号：

# **{company_name}（{ticker}）社交媒体情绪分析报告**
**分析日期：{current_date}**

---

## 一、投资者情绪概况

- **情绪指数评分**：[1-10分，10为极度乐观]
- **情绪倾向**：[乐观/中性/悲观]
- **讨论热度**：[高/中/低]

---

## 二、各平台情绪分析

### 1. 东方财富股吧情绪
[分析散户投资者讨论和情绪倾向]

### 2. 雪球社区情绪
[分析专业投资者观点和情绪]

### 3. 财经媒体情绪
[分析财经新闻报道倾向]

---

## 三、关键情绪信号

[识别情绪极端点、反转信号、热点事件影响]

---

## 四、情绪对股价的影响评估

[评估情绪变化对短期（1-5天）股价走势的影响]

---

## 五、交易建议

- **建议方向**：[看多/看空/中性]
- **预期价格波动幅度**：[百分比]
- **交易时机建议**：[具体建议]

---

## 六、关键发现总结

| 指标 | 数值/判断 |
|------|-----------|
| 情绪指数 | [1-10] |
| 情绪倾向 | [乐观/中性/悲观] |
| 讨论热度 | [高/中/低] |
| 预期波动 | [百分比] |

要求：
- 基于工具返回的真实数据进行分析
- 报告长度不少于500字
- 使用中文撰写
- 不要使用emoji符号
- 严禁输出任何XML/HTML标签或工具调用格式，只输出报告正文"""

                    messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
                    final_result = llm.invoke(messages)
                    report = final_result.content

                    logger.info(f"[社交媒体分析师] 生成完整情绪分析报告，长度: {len(report)}")

                    return {
                        "messages": [result] + tool_messages + [final_result],
                        "sentiment_report": report,
                        "sentiment_tool_call_count": tool_call_count + 1
                    }

                except Exception as e:
                    logger.error(f"[社交媒体分析师] 工具执行或分析生成失败: {e}")
                    import traceback
                    traceback.print_exc()
                    report = f"社交媒体分析师调用了工具但分析生成失败: {[tc.get('name', 'unknown') for tc in result.tool_calls]}"

                    return {
                        "messages": [result],
                        "sentiment_report": report,
                        "sentiment_tool_call_count": tool_call_count + 1
                    }

        # 🔧 更新工具调用计数器
        return {
            "messages": [result],
            "sentiment_report": report,
            "sentiment_tool_call_count": tool_call_count + 1
        }

    def social_media_analyst_node(state):
        """
        社交媒体分析师节点外层包装器 —— 思维链内容清洗

        在内层函数返回后，对 sentiment_report 进行思维链/DSML 标签清洗。
        移除 <think/> 推理标签和 <｜tool_calls＞ 等伪工具调用格式。
        """
        result = _social_media_analyst_node_inner(state)
        if "sentiment_report" in result:
            original = result["sentiment_report"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [社交媒体分析师] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["sentiment_report"] = cleaned
        return result

    return social_media_analyst_node
