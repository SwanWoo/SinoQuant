"""
基本面分析师（Fundamentals Analyst）模块 - 统一工具架构版本

该模块负责创建基本面分析师节点，是 SinoQuant 多智能体辩论工作流中的核心分析师之一。
基本面分析师通过调用统一基本面数据工具获取股票财务数据，然后基于真实数据生成基本面分析报告。

核心设计：强制工具调用机制（Forced Tool Call）
================================================================
基本面分析师面临的核心挑战是：某些 LLM（尤其是较弱的模型或 DashScope 系模型）
可能在收到工具绑定后仍然不调用工具，而是直接编造分析内容。为此，本模块实现了
一套多层次降级方案来确保获取真实数据：

  正常流程（Normal Flow）
  ┌─────────────────────────────────────────────────────┐
  │ LLM 自主调用工具 → 执行工具 → 基于数据生成报告     │
  └─────────────────────────────────────────────────────┘

  降级方案1：强制工具调用（Forced Tool Call）
  ┌─────────────────────────────────────────────────────┐
  │ LLM 未调用工具 → 代码层强制执行工具 → 将数据注入   │
  │ 提示词 → 重新调用 LLM 生成报告                      │
  └─────────────────────────────────────────────────────┘

  降级方案2：无工具链重试（No-Tool-Chain Retry）
  ┌─────────────────────────────────────────────────────┐
  │ 强制生成报告后 LLM 仍输出 DSML 幻觉 → 移除工具绑定 │
  │ 用纯文本提示词重新调用 LLM 生成报告                 │
  └─────────────────────────────────────────────────────┘

  降级方案3：模板化报告（Template Fallback）
  ┌─────────────────────────────────────────────────────┐
  │ 所有 LLM 调用均失败 → 直接将工具返回的原始数据拼接  │
  │ 成模板化报告返回                                     │
  └─────────────────────────────────────────────────────┘

其他关键机制：
- 死循环修复：通过 tool_call_count 计数器限制最大工具调用次数，防止 LLM 重复调用工具
- DashScope 特殊处理：为阿里百炼模型创建新的 LLM 实例，避免工具缓存导致的问题
- 思维链内容清洗：在外层包装器中移除 <think/> 和 DSML 标签
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

# 导入分析模块日志装饰器
from sinoquant.utils.tool_logging import log_analyst_module

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")

# 导入消息过滤工具
from sinoquant.agents.utils.agent_utils import filter_messages_for_analyst

# 当前分析师允许的工具名集合 —— 基本面分析师只使用统一工具
# 不再像旧版那样绑定多个工具（如 get_income_stmt、get_balance_sheet 等），
# 而是通过 get_stock_fundamentals_unified 一次获取所有基本面数据
_FUNDAMENTALS_TOOLS = {"get_stock_fundamentals_unified"}

# 导入Google工具调用处理器
from sinoquant.agents.utils.google_tool_handler import GoogleToolCallHandler


def _get_company_name_for_fundamentals(ticker: str, market_info: dict) -> str:
    """
    基本面分析师专用：公司名称解析辅助函数

    逻辑与市场分析师的 _get_company_name() 相同，但独立定义以避免模块间耦合。
    降级方案链路：统一接口 → 数据源管理器 → 股票代码兜底

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

            logger.debug(f"📊 [基本面分析师] 获取股票信息返回: {stock_info[:200] if stock_info else 'None'}...")

            # 解析股票名称
            if stock_info and "股票名称:" in stock_info:
                company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                logger.info(f"✅ [基本面分析师] 成功获取中国股票名称: {ticker} -> {company_name}")
                return company_name
            else:
                # 降级方案：尝试直接从数据源管理器获取
                logger.warning(f"⚠️ [基本面分析师] 无法从统一接口解析股票名称: {ticker}，尝试降级方案")
                try:
                    from sinoquant.dataflows.data_source_manager import get_china_stock_info_unified as get_info_dict
                    info_dict = get_info_dict(ticker)
                    if info_dict and info_dict.get('name'):
                        company_name = info_dict['name']
                        logger.info(f"✅ [基本面分析师] 降级方案成功获取股票名称: {ticker} -> {company_name}")
                        return company_name
                except Exception as e:
                    logger.error(f"❌ [基本面分析师] 降级方案也失败: {e}")

                logger.error(f"❌ [基本面分析师] 所有方案都无法获取股票名称: {ticker}")
                return f"股票代码{ticker}"

    except Exception as e:
        logger.error(f"❌ [基本面分析师] 获取公司名称失败: {e}")
        return f"股票{ticker}"


def create_fundamentals_analyst(llm, toolkit):
    """
    基本面分析师工厂函数

    创建并返回基本面分析师节点函数。采用闭包模式捕获 LLM 实例和工具集。

    与市场分析师相比，基本面分析师的核心差异在于：
    1. 强制工具调用机制 —— 当 LLM 不主动调用工具时，代码层会强制执行
    2. 多层次降级方案 —— 从正常流程到模板化报告，共4级降级
    3. DashScope 特殊处理 —— 为阿里百炼模型创建新实例以避免工具缓存问题
    4. 工具调用计数器 —— 基本面分析只需一次工具调用即可获取所有数据

    Args:
        llm: LangChain 兼容的 LLM 实例
        toolkit: 数据工具集实例，提供 get_stock_fundamentals_unified 工具

    Returns:
        Callable: 基本面分析师节点函数，接受 state 字典，返回更新后的 state 字典
    """
    # 导入思维链内容清洗工具
    from sinoquant.utils.text_utils import remove_thinking_content

    @log_analyst_module("fundamentals")
    def _fundamentals_analyst_node_inner(state):
        """
        基本面分析师节点核心逻辑

        执行流程（含降级方案）：
        1. 死循环修复 —— 检查工具调用计数器（max=1），防止重复调用
        2. 股票类型检测与公司名称解析
        3. 工具绑定与 LLM 调用
        4. 分支处理：
           a. Google 模型 → GoogleToolCallHandler 统一处理
           b. 非 Google 模型 → 标准处理逻辑，含三层降级：
              - 正常流程：LLM 调用了工具，返回工具调用请求等待执行
              - 强制生成报告：LLM 在已有工具结果时仍尝试调用工具，强制生成文本报告
              - 强制工具调用：LLM 完全不调用工具，代码层直接执行工具并注入数据
              - 模板化报告：LLM 输出全是 DSML 幻觉，使用原始数据拼接模板

        Args:
            state: LangGraph 工作流状态字典

        Returns:
            dict: 更新后的状态，包含 messages、fundamentals_report、fundamentals_tool_call_count
        """
        logger.debug(f"📊 [DEBUG] ===== 基本面分析师节点开始 =====")

        # 🔧 死循环修复：工具调用计数器
        # 基本面分析的最大工具调用次数设为 1，因为统一工具一次调用即可获取所有数据
        # 检查消息历史中是否有 ToolMessage，如果有则说明工具已执行过
        messages = state.get("messages", [])
        tool_message_count = sum(1 for msg in messages if isinstance(msg, ToolMessage))

        tool_call_count = state.get("fundamentals_tool_call_count", 0)
        max_tool_calls = 1  # 最大工具调用次数：一次工具调用就能获取所有数据

        # 如果有新的 ToolMessage，更新计数器
        if tool_message_count > tool_call_count:
            tool_call_count = tool_message_count
            logger.info(f"🔧 [工具调用计数] 检测到新的工具结果，更新计数器: {tool_call_count}")

        logger.info(f"🔧 [工具调用计数] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # 🔧 基本面分析数据范围：固定获取10天数据（处理周末/节假日/数据延迟）
        # 基本面分析主要依赖财务数据（PE、PB、ROE等），只需要当前股价
        # 获取10天数据是为了保证能拿到数据，但实际分析只使用最近2天
        # 注意：与市场分析师的365天历史数据不同，基本面分析不需要长周期行情
        # 参考文档：docs/ANALYST_DATA_CONFIGURATION.md
        # 基本面分析主要依赖财务数据（PE、PB、ROE等），只需要当前股价
        # 获取10天数据是为了保证能拿到数据，但实际分析只使用最近2天
        from datetime import datetime, timedelta
        try:
            end_date_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_date_dt = end_date_dt - timedelta(days=10)
            start_date = start_date_dt.strftime("%Y-%m-%d")
            logger.info(f"📅 [基本面分析师] 数据范围: {start_date} 至 {current_date} (固定10天)")
        except Exception as e:
            # 如果日期解析失败，使用默认10天前
            logger.warning(f"⚠️ [基本面分析师] 日期解析失败，使用默认范围: {e}")
            start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        logger.debug(f"📊 [DEBUG] 输入参数: ticker={ticker}, date={current_date}")
        logger.debug(f"📊 [DEBUG] 当前状态中的消息数量: {len(state.get('messages', []))}")
        logger.debug(f"📊 [DEBUG] 现有基本面报告: {state.get('fundamentals_report', 'None')}")

        # 获取股票市场信息
        from sinoquant.utils.stock_utils import StockUtils
        logger.info(f"📊 [基本面分析师] 正在分析股票: {ticker}")

        # 添加详细的股票代码追踪日志
        logger.info(f"🔍 [股票代码追踪] 基本面分析师接收到的原始股票代码: '{ticker}' (类型: {type(ticker)})")
        logger.info(f"🔍 [股票代码追踪] 股票代码长度: {len(str(ticker))}")
        logger.info(f"🔍 [股票代码追踪] 股票代码字符: {list(str(ticker))}")

        market_info = StockUtils.get_market_info(ticker)
        logger.info(f"🔍 [股票代码追踪] StockUtils.get_market_info 返回的市场信息: {market_info}")

        logger.debug(f"📊 [DEBUG] 股票类型检查: {ticker} -> {market_info['market_name']} ({market_info['currency_name']}")
        logger.debug(f"📊 [DEBUG] 详细市场信息: is_china={market_info['is_china']}")
        logger.debug(f"📊 [DEBUG] 工具配置检查: online_tools={toolkit.config['online_tools']}")

        # 获取公司名称
        company_name = _get_company_name_for_fundamentals(ticker, market_info)
        logger.debug(f"📊 [DEBUG] 公司名称: {ticker} -> {company_name}")

        # 统一使用 get_stock_fundamentals_unified 工具
        # 该工具内部会自动识别A股股票并调用相应的数据源
        # 对于A股，它会自动获取价格数据和基本面数据，无需LLM调用多个工具
        # 这是"统一工具架构"的核心：一个工具覆盖所有股票类型和所有基本面数据
        logger.info(f"📊 [基本面分析师] 使用统一基本面分析工具，自动识别A股股票")
        tools = [toolkit.get_stock_fundamentals_unified]

        # 安全地获取工具名称用于调试
        tool_names_debug = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names_debug.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names_debug.append(tool.__name__)
            else:
                tool_names_debug.append(str(tool))
        logger.info(f"📊 [基本面分析师] 绑定的工具: {tool_names_debug}")
        logger.info(f"📊 [基本面分析师] 目标市场: {market_info['market_name']}")

        # 统一的系统提示，适用于所有股票类型
        # 提示词中包含大量"强制要求"和"禁止"语句，这是为了对抗 LLM 不调用工具的倾向
        # 某些模型（尤其是 DashScope/通义千问）在收到工具绑定后仍可能直接编造回答
        # 这些强制指令是为了提高工具调用率
        system_message = (
            f"你是一位专业的股票基本面分析师。"
            f"⚠️ 绝对强制要求：你必须调用工具获取真实数据！不允许任何假设或编造！"
            f"任务：分析{company_name}（股票代码：{ticker}，{market_info['market_name']}）"
            f"🔴 立即调用 get_stock_fundamentals_unified 工具"
            f"参数：ticker='{ticker}', start_date='{start_date}', end_date='{current_date}', curr_date='{current_date}'"
            "📊 分析要求："
            "- 基于真实数据进行深度基本面分析"
            f"- 计算并提供合理价位区间（使用{market_info['currency_name']}{market_info['currency_symbol']}）"
            "- 分析当前股价是否被低估或高估"
            "- 提供基于基本面的目标价位建议"
            "- 包含PE、PB、PEG等估值指标分析"
            "- 结合市场特点进行分析"
            "🌍 语言和货币要求："
            "- 所有分析内容必须使用中文"
            "- 投资建议必须使用中文：买入、持有、卖出"
            "- 绝对不允许使用英文：buy、hold、sell"
            f"- 货币单位使用：{market_info['currency_name']}（{market_info['currency_symbol']}）"
            "🚫 严格禁止："
            "- 不允许说'我将调用工具'"
            "- 不允许假设任何数据"
            "- 不允许编造公司信息"
            "- 不允许直接回答而不调用工具"
            "- 不允许回复'无法确定价位'或'需要更多信息'"
            "- 不允许使用英文投资建议（buy/hold/sell）"
            "- 不允许输出任何XML/HTML标签（如 <｜tool_calls>、<｜invoke>、<｜parameter> 等）"
            "- 工具调用请使用标准的 function calling 机制，不要在文本内容中伪造工具调用格式"
            "✅ 你必须："
            "- 立即调用统一基本面分析工具"
            "- 等待工具返回真实数据"
            "- 基于真实数据进行分析"
            "- 提供具体的价位区间和目标价"
            "- 使用中文投资建议（买入/持有/卖出）"
            "现在立即开始调用工具！不要说任何其他话！"
        )

        # 系统提示模板
        system_prompt = (
            "🔴 强制要求：你必须调用工具获取真实数据！"
            "🚫 绝对禁止：不允许假设、编造或直接回答任何问题！"
            "🚫 绝对禁止：不允许在输出内容中出现任何 XML/HTML 标签（如 <｜tool_calls>、<｜invoke>、<｜parameter> 等）！"
            "🚫 绝对禁止：不允许在文本中伪造工具调用格式！工具调用只能通过标准 function calling 机制！"
            "✅ 工作流程："
            "1. 【第一次调用】如果消息历史中没有工具结果（ToolMessage），立即调用 get_stock_fundamentals_unified 工具"
            "2. 【收到数据后】如果消息历史中已经有工具结果（ToolMessage），🚨 绝对禁止再次调用工具！🚨"
            "3. 【生成报告】收到工具数据后，必须立即生成完整的基本面分析报告，包含："
            "   - 公司基本信息和财务数据分析"
            "   - PE、PB、PEG等估值指标分析"
            "   - 当前股价是否被低估或高估的判断"
            "   - 合理价位区间和目标价位建议"
            "   - 基于基本面的投资建议（买入/持有/卖出）"
            "4. 🚨 重要：工具只需调用一次！一次调用返回所有需要的数据！不要重复调用！🚨"
            "5. 🚨 如果你已经看到ToolMessage，说明工具已经返回数据，直接生成报告，不要再调用工具！🚨"
            "6. 🚨 生成报告时只输出报告正文，不要输出思考过程或任何XML格式文本！🚨"
            "可用工具：{tool_names}。\n{system_message}"
            "当前日期：{current_date}。"
            "分析目标：{company_name}（股票代码：{ticker}）。"
            "请确保在分析中正确区分公司名称和股票代码。"
        )

        # 创建提示模板
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])

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
        prompt = prompt.partial(company_name=company_name)

        # DashScope 特殊处理：阿里百炼模型存在工具缓存问题
        # 当同一 LLM 实例多次调用 bind_tools() 时，工具定义可能不会正确更新，
        # 导致 LLM 无法识别新绑定的工具。解决方案是为每次分析创建新的 LLM 实例。
        # 这里通过检查类名中是否包含 "DashScope" 来判断是否为阿里百炼模型。
        if hasattr(llm, '__class__') and 'DashScope' in llm.__class__.__name__:
            logger.debug(f"📊 [DEBUG] 检测到阿里百炼模型，创建新实例以避免工具缓存")
            from sinoquant.llm_adapters import ChatDashScopeOpenAI

            # 获取原始 LLM 的 base_url 和 api_key
            original_base_url = getattr(llm, 'openai_api_base', None)
            original_api_key = getattr(llm, 'openai_api_key', None)

            fresh_llm = ChatDashScopeOpenAI(
                model=llm.model_name,
                api_key=original_api_key,  # 🔥 传递原始 LLM 的 API Key
                base_url=original_base_url if original_base_url else None,  # 传递 base_url
                temperature=llm.temperature,
                max_tokens=getattr(llm, 'max_tokens', 2000)
            )

            if original_base_url:
                logger.debug(f"📊 [DEBUG] 新实例使用原始 base_url: {original_base_url}")
            if original_api_key:
                logger.debug(f"📊 [DEBUG] 新实例使用原始 API Key（来自数据库配置）")
        else:
            fresh_llm = llm

        logger.debug(f"📊 [DEBUG] 创建LLM链，工具数量: {len(tools)}")
        # 安全地获取工具名称用于调试
        debug_tool_names = []
        for tool in tools:
            if hasattr(tool, 'name'):
                debug_tool_names.append(tool.name)
            elif hasattr(tool, '__name__'):
                debug_tool_names.append(tool.__name__)
            else:
                debug_tool_names.append(str(tool))
        logger.debug(f"📊 [DEBUG] 绑定的工具列表: {debug_tool_names}")
        logger.debug(f"📊 [DEBUG] 创建工具链，让模型自主决定是否调用工具")

        # 添加详细日志
        logger.info(f"📊 [基本面分析师] LLM类型: {fresh_llm.__class__.__name__}")
        logger.info(f"📊 [基本面分析师] LLM模型: {getattr(fresh_llm, 'model_name', 'unknown')}")
        logger.info(f"📊 [基本面分析师] 消息历史数量: {len(state['messages'])}")

        try:
            chain = prompt | fresh_llm.bind_tools(tools)
            logger.info(f"📊 [基本面分析师] ✅ 工具绑定成功，绑定了 {len(tools)} 个工具")
        except Exception as e:
            logger.error(f"📊 [基本面分析师] ❌ 工具绑定失败: {e}")
            raise e

        logger.info(f"📊 [基本面分析师] 开始调用LLM...")

        # 添加详细的股票代码追踪日志
        logger.info(f"🔍 [股票代码追踪] LLM调用前，ticker参数: '{ticker}'")
        logger.info(f"🔍 [股票代码追踪] 传递给LLM的消息数量: {len(state['messages'])}")

        # 🔥 打印提交给大模型的完整内容
        logger.info("=" * 80)
        logger.info("📝 [提示词调试] 开始打印提交给大模型的完整内容")
        logger.info("=" * 80)

        # 1. 打印系统提示词
        logger.info("📋 [提示词调试] 1️⃣ 系统提示词 (System Message):")
        logger.info("-" * 80)
        logger.info(system_message)
        logger.info("-" * 80)

        # 2. 打印完整的提示模板
        logger.info("📋 [提示词调试] 2️⃣ 完整提示模板 (Prompt Template):")
        logger.info("-" * 80)
        logger.info(f"工具名称: {', '.join(tool_names)}")
        logger.info(f"当前日期: {current_date}")
        logger.info(f"股票代码: {ticker}")
        logger.info(f"公司名称: {company_name}")
        logger.info("-" * 80)

        # 3. 打印消息历史
        logger.info("📋 [提示词调试] 3️⃣ 消息历史 (Message History):")
        logger.info("-" * 80)
        for i, msg in enumerate(state['messages']):
            msg_type = type(msg).__name__
            if hasattr(msg, 'content'):
                # 🔥 调试模式：打印完整内容，不截断
                content_full = str(msg.content)
                logger.info(f"消息 {i+1} [{msg_type}]:")
                logger.info(f"  内容长度: {len(content_full)} 字符")
                logger.info(f"  内容: {content_full}")
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                logger.info(f"  工具调用: {[tc.get('name', 'unknown') for tc in msg.tool_calls]}")
            if hasattr(msg, 'name'):
                logger.info(f"  工具名称: {msg.name}")
            logger.info("-" * 40)
        logger.info("-" * 80)

        # 4. 打印绑定的工具信息
        logger.info("📋 [提示词调试] 4️⃣ 绑定的工具 (Bound Tools):")
        logger.info("-" * 80)
        for i, tool in enumerate(tools):
            tool_name = getattr(tool, 'name', None) or getattr(tool, '__name__', 'unknown')
            tool_desc = getattr(tool, 'description', 'No description')
            logger.info(f"工具 {i+1}: {tool_name}")
            logger.info(f"  描述: {tool_desc}")
            if hasattr(tool, 'args_schema'):
                logger.info(f"  参数: {tool.args_schema}")
            logger.info("-" * 40)
        logger.info("-" * 80)

        logger.info("=" * 80)
        logger.info("📝 [提示词调试] 完整内容打印结束，开始调用LLM")
        logger.info("=" * 80)

        # 修复：传递字典而不是直接传递消息列表，以便 ChatPromptTemplate 能正确处理所有变量
        result = chain.invoke({"messages": state["messages"]})
        logger.info(f"📊 [基本面分析师] LLM调用完成")
        
        # 🔍 [调试日志] 打印AIMessage的详细内容
        logger.info(f"🤖 [基本面分析师] AIMessage详细内容:")
        logger.info(f"🤖 [基本面分析师] - 消息类型: {type(result).__name__}")
        logger.info(f"🤖 [基本面分析师] - 内容长度: {len(result.content) if hasattr(result, 'content') else 0}")
        if hasattr(result, 'content') and result.content:
            # 🔥 调试模式：打印完整内容，不截断
            logger.info(f"🤖 [基本面分析师] - 完整内容:")
            logger.info(f"{result.content}")
        
        # 🔍 [调试日志] 打印tool_calls的详细信息
        # 详细记录 LLM 返回结果
        logger.info(f"📊 [基本面分析师] ===== LLM返回结果分析 =====")
        logger.info(f"📊 [基本面分析师] - 结果类型: {type(result).__name__}")
        logger.info(f"📊 [基本面分析师] - 是否有tool_calls属性: {hasattr(result, 'tool_calls')}")

        if hasattr(result, 'content'):
            content_preview = str(result.content)[:200] if result.content else "None"
            logger.info(f"📊 [基本面分析师] - 内容长度: {len(str(result.content)) if result.content else 0}")
            logger.info(f"📊 [基本面分析师] - 内容预览: {content_preview}...")

        if hasattr(result, 'tool_calls'):
            logger.info(f"📊 [基本面分析师] - tool_calls数量: {len(result.tool_calls)}")
            if result.tool_calls:
                logger.info(f"🔧 [基本面分析师] 检测到 {len(result.tool_calls)} 个工具调用:")
                for i, tc in enumerate(result.tool_calls):
                    logger.info(f"🔧 [基本面分析师] - 工具调用 {i+1}: {tc.get('name', 'unknown')} (ID: {tc.get('id', 'unknown')})")
                    if 'args' in tc:
                        logger.info(f"🔧 [基本面分析师] - 参数: {tc['args']}")
            else:
                logger.info(f"🔧 [基本面分析师] tool_calls为空列表")
        else:
            logger.info(f"🔧 [基本面分析师] 无tool_calls属性")

        logger.info(f"📊 [基本面分析师] ===== LLM返回结果分析结束 =====")

        # 使用统一的Google工具调用处理器
        if GoogleToolCallHandler.is_google_model(fresh_llm):
            logger.info(f"📊 [基本面分析师] 检测到Google模型，使用统一工具调用处理器")
            
            # 创建分析提示词
            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="基本面分析",
                specific_requirements="重点关注财务数据、盈利能力、估值指标、行业地位等基本面因素。"
            )
            
            # 处理Google模型工具调用
            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result,
                llm=fresh_llm,
                tools=tools,
                state=state,
                analysis_prompt_template=analysis_prompt_template,
                analyst_name="基本面分析师"
            )

            return {"fundamentals_report": report}
        else:
            # 非Google模型的处理逻辑
            logger.debug(f"📊 [DEBUG] 非Google模型 ({fresh_llm.__class__.__name__})，使用标准处理逻辑")
            
            # 检查工具调用情况
            current_tool_calls = len(result.tool_calls) if hasattr(result, 'tool_calls') else 0
            logger.debug(f"📊 [DEBUG] 当前消息的工具调用数量: {current_tool_calls}")
            logger.debug(f"📊 [DEBUG] 累计工具调用次数: {tool_call_count}/{max_tool_calls}")

            if current_tool_calls > 0:
                # LLM 请求了工具调用 → 手动执行工具 + 手动调用 LLM 生成报告
                # 不依赖 LangGraph 自动工具循环（非 Google 模型下该循环
                # 会导致 LLM 反复输出 DSML 伪造工具调用，报告始终为空）
                logger.info(f"[基本面分析师] 🔧 检测到工具调用，手动执行工具并生成报告: {[tc.get('name', 'unknown') for tc in result.tool_calls]}")

                try:
                    from langchain_core.messages import HumanMessage

                    tool_messages = []
                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')

                        logger.debug(f"[基本面分析师] 执行工具: {tool_name}, 参数: {tool_args}")

                        tool_result = None
                        for tool in tools:
                            current_tool_name = getattr(tool, 'name', None) or getattr(tool, '__name__', None)
                            if current_tool_name == tool_name:
                                try:
                                    tool_result = tool.invoke(tool_args)
                                    logger.debug(f"[基本面分析师] 工具执行成功，结果长度: {len(str(tool_result))}")
                                    break
                                except Exception as tool_error:
                                    logger.error(f"[基本面分析师] 工具执行失败: {tool_error}")
                                    tool_result = f"工具执行失败: {str(tool_error)}"

                        if tool_result is None:
                            tool_result = f"未找到工具: {tool_name}"

                        tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

                    currency_info = f"{market_info['currency_name']}（{market_info['currency_symbol']}）"

                    analysis_prompt = f"""现在请基于上述工具获取的数据，生成详细的基本面分析报告。

**分析对象：**
- 公司名称：{company_name}
- 股票代码：{ticker}
- 所属市场：{market_info['market_name']}
- 计价货币：{currency_info}
- 分析日期：{current_date}

**输出格式要求（必须严格遵守）：**

请按照以下专业格式输出报告，不要使用emoji符号：

# **{company_name}（{ticker}）基本面分析报告**
**分析日期：{current_date}**

---

## 一、公司基本信息

- **公司名称**：{company_name}
- **股票代码**：{ticker}

---

## 二、财务数据分析

### 1. 营收与利润分析
[分析最近几个报告期的营业收入、净利润及其增长趋势]

### 2. 资产负债分析
[分析总资产、负债率等关键财务指标]

---

## 三、估值分析

### 1. PE（市盈率）分析
[当前PE、行业PE对比、历史PE分位]

### 2. PB（市净率）分析
[当前PB、行业PB对比]

---

## 四、投资建议

- **估值判断**：[被低估/合理/被高估]
- **合理价位区间**：[价格区间] {market_info['currency_symbol']}
- **投资建议**：[买入/持有/卖出]

---

## 五、关键财务指标总结

| 指标 | 数值 | 评价 |
|------|------|------|
| 营收增长率 | [百分比] | [增长/下降] |
| 净利润率 | [百分比] | [高/中/低] |
| PE | [数值] | [高估/合理/低估] |
| PB | [数值] | [高估/合理/低估] |

要求：
- 基于工具返回的真实数据进行分析
- 报告长度不少于800字
- 使用中文撰写
- 不要使用emoji符号
- 严禁输出任何XML/HTML标签或工具调用格式，只输出报告正文"""

                    messages_list = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
                    final_result = fresh_llm.invoke(messages_list)
                    report = final_result.content

                    cleaned_report = remove_thinking_content(str(report))
                    logger.info(f"[基本面分析师] 生成完整基本面分析报告，原始: {len(str(report))}字符, 清洗后: {len(cleaned_report)}字符")

                    if not cleaned_report or len(cleaned_report) < 50:
                        logger.warning(f"[基本面分析师] 报告清洗后过短，使用工具数据模板化报告")
                        tool_data_preview = "\n".join(str(tm.content)[:2000] for tm in tool_messages)
                        cleaned_report = (
                            f"## {company_name}（{ticker}）基本面分析报告\n\n"
                            f"### 基础数据\n\n{tool_data_preview}\n\n"
                            f"### 投资建议\n"
                            f"由于AI模型未能基于数据生成规范的分析报告，以上为工具返回的原始数据。"
                        )

                    from langchain_core.messages import AIMessage
                    clean_message = AIMessage(content=cleaned_report)
                    return {
                        "messages": [result] + tool_messages + [clean_message],
                        "fundamentals_report": cleaned_report,
                        "fundamentals_tool_call_count": tool_call_count + 1
                    }

                except Exception as e:
                    logger.error(f"[基本面分析师] 工具执行或分析生成失败: {e}")
                    import traceback
                    traceback.print_exc()
                    fallback_report = f"基本面分析师调用了工具但分析生成失败: {[tc.get('name', 'unknown') for tc in result.tool_calls]}"
                    from langchain_core.messages import AIMessage
                    clean_message = AIMessage(content=fallback_report)
                    return {
                        "messages": [clean_message],
                        "fundamentals_report": fallback_report,
                        "fundamentals_tool_call_count": tool_call_count + 1
                    }
            else:
                # 没有工具调用 —— 这是强制工具调用机制的核心场景
                # LLM 没有主动调用工具，需要通过多种检查决定是否强制调用<tool_call>、<function_call> 等\n"
                # LLM 没有主动调用工具，需要通过多种检查决定是否强制调用
                logger.info(f"📊 [基本面分析师] ===== 强制工具调用检查开始 =====")
                logger.debug(f"📊 [DEBUG] 检测到模型未调用工具，检查是否需要强制调用")

                # 方案1：检查消息历史中是否已经有工具返回的数据
                # 如果历史消息中有 ToolMessage，说明工具已经在之前的轮次中被执行过
                messages = state.get("messages", [])
                logger.info(f"🔍 [消息历史] 当前消息总数: {len(messages)}")

                # 统计各类消息数量
                ai_message_count = sum(1 for msg in messages if isinstance(msg, AIMessage))
                tool_message_count = sum(1 for msg in messages if isinstance(msg, ToolMessage))
                logger.info(f"🔍 [消息历史] AIMessage数量: {ai_message_count}, ToolMessage数量: {tool_message_count}")

                # 记录最近几条消息的类型
                recent_messages = messages[-5:] if len(messages) >= 5 else messages
                logger.info(f"🔍 [消息历史] 最近{len(recent_messages)}条消息类型: {[type(msg).__name__ for msg in recent_messages]}")

                has_tool_result = any(isinstance(msg, ToolMessage) for msg in messages)
                logger.info(f"🔍 [检查结果] 是否有工具返回结果: {has_tool_result}")

                # 方案2：检查 AIMessage 是否已有分析内容
                # 如果 LLM 返回的内容长度超过 500 字符，认为是有效的分析报告
                # （虽然 LLM 没有调用工具，但可能基于消息历史中的已有数据生成了分析）
                has_analysis_content = False
                if hasattr(result, 'content') and result.content:
                    content_length = len(str(result.content))
                    logger.info(f"🔍 [内容检查] LLM返回内容长度: {content_length}字符")
                    # 如果内容长度超过500字符，认为是有效的分析内容
                    if content_length > 500:
                        has_analysis_content = True
                        logger.info(f"✅ [内容检查] LLM已返回有效分析内容 (长度: {content_length}字符 > 500字符阈值)")
                    else:
                        logger.info(f"⚠️ [内容检查] LLM返回内容较短 (长度: {content_length}字符 < 500字符阈值)")
                else:
                    logger.info(f"⚠️ [内容检查] LLM未返回内容或内容为空")

                # 方案3：统计工具调用次数
                tool_call_count = sum(1 for msg in messages if isinstance(msg, ToolMessage))
                logger.info(f"🔍 [统计] 历史工具调用次数: {tool_call_count}")

                logger.info(f"🔍 [重复调用检查] 汇总 - 工具结果数: {tool_call_count}, 已有工具结果: {has_tool_result}, 已有分析内容: {has_analysis_content}")
                logger.info(f"📊 [基本面分析师] ===== 强制工具调用检查结束 =====")

                # 决策点：如果已经有工具结果或已有分析内容，跳过强制调用
                # 只有在完全没有数据且没有分析内容时，才进入强制工具调用分支
                if has_tool_result or has_analysis_content:
                    logger.info(f"🚫 [决策] ===== 跳过强制工具调用 =====")
                    if has_tool_result:
                        logger.info(f"⚠️ [决策原因] 检测到已有 {tool_call_count} 次工具调用结果，避免重复调用")
                    if has_analysis_content:
                        logger.info(f"⚠️ [决策原因] LLM已返回有效分析内容，无需强制工具调用")

                    # 直接使用 LLM 返回的内容作为报告
                    report = str(result.content) if hasattr(result, 'content') else ""
                    cleaned_report = remove_thinking_content(report)
                    logger.info(f"📊 [返回结果] LLM原始内容长度: {len(report)}字符, 清洗后: {len(cleaned_report)}字符")

                    # 安全网：清洗后过短说明 LLM 输出了 DSML/幻觉内容
                    # 这种情况下 remove_thinking_content() 会移除大部分内容，
                    # 剩余内容不足 50 字符。需要回退到强制生成报告模式。
                    if not cleaned_report or len(cleaned_report) < 50:
                        logger.warning(f"⚠️ [基本面分析师] LLM输出经清洗后过短({len(cleaned_report)}字符)，回退到强制生成报告模式")
                        force_system_prompt = (
                            f"你是专业的股票基本面分析师。"
                            f"基于消息历史中的数据，生成{company_name}（代码：{ticker}）的基本面分析报告。\n\n"
                            f"报告必须包含：\n"
                            f"1. 公司基本信息和财务数据分析\n"
                            f"2. PE、PB、PEG等估值指标分析\n"
                            f"3. 当前股价是否被低估或高估的判断\n"
                            f"4. 合理价位区间和目标价位建议\n"
                            f"5. 基于基本面的投资建议（买入/持有/卖出）\n\n"
                            f"要求：使用中文，基于消息历史中的真实数据，分析详细专业。\n"
                            f"🚫 严禁输出任何 XML/HTML 标签或工具调用格式，只输出报告正文。"
                        )
                        force_prompt = ChatPromptTemplate.from_messages([
                            ("system", force_system_prompt),
                            MessagesPlaceholder(variable_name="messages"),
                        ])
                        force_chain = force_prompt | fresh_llm
                        force_result = force_chain.invoke({"messages": messages})
                        cleaned_report = remove_thinking_content(
                            str(force_result.content) if hasattr(force_result, 'content') else ""
                        )
                        logger.info(f"✅ [强制生成] 报告长度: {len(cleaned_report)}字符")

                    logger.info(f"📊 [返回结果] 报告预览(前200字符): {cleaned_report[:200]}...")
                    logger.info(f"✅ [决策] 基本面分析完成")

                    # 🔧 保持工具调用计数器不变（已在开始时根据ToolMessage更新）
                    return {
                        "fundamentals_report": cleaned_report,
                        "messages": [result],
                        "fundamentals_tool_call_count": tool_call_count
                    }

                # 强制工具调用：LLM 完全不调用工具且没有有效分析内容
                # 代码层直接执行统一基本面工具，获取真实数据，
                # 然后将数据注入提示词，让 LLM 基于真实数据生成报告
                logger.info(f"🔧 [决策] ===== 执行强制工具调用 =====")
                logger.info(f"🔧 [决策原因] 未检测到工具结果或分析内容，需要获取基本面数据")
                logger.info(f"🔧 [决策] 启用强制工具调用模式")

                # 强制调用统一基本面分析工具 —— 直接在代码层调用，绕过 LLM 的 function calling
                try:
                    logger.debug(f"📊 [DEBUG] 强制调用 get_stock_fundamentals_unified...")
                    # 安全地查找统一基本面分析工具
                    unified_tool = None
                    for tool in tools:
                        tool_name = None
                        if hasattr(tool, 'name'):
                            tool_name = tool.name
                        elif hasattr(tool, '__name__'):
                            tool_name = tool.__name__

                        if tool_name == 'get_stock_fundamentals_unified':
                            unified_tool = tool
                            break
                    if unified_tool:
                        logger.info(f"🔍 [工具调用] 找到统一工具，准备强制调用")
                        logger.info(f"🔍 [工具调用] 传入参数 - ticker: '{ticker}', start_date: {start_date}, end_date: {current_date}")

                        combined_data = unified_tool.invoke({
                            'ticker': ticker,
                            'start_date': start_date,
                            'end_date': current_date,
                            'curr_date': current_date
                        })
                        # 工具返回的数据可能包含价格行情、财务指标、估值数据等

                        logger.info(f"✅ [工具调用] 统一工具调用成功")
                        logger.info(f"📊 [工具调用] 返回数据长度: {len(combined_data)}字符")
                        logger.debug(f"📊 [DEBUG] 统一工具数据获取成功，长度: {len(combined_data)}字符")
                        # 将统一工具返回的数据写入日志，便于排查与分析
                        try:
                            if isinstance(combined_data, (dict, list)):
                                import json
                                _preview = json.dumps(combined_data, ensure_ascii=False, default=str)
                                _full = _preview
                            else:
                                _preview = str(combined_data)
                                _full = _preview

                            # 预览信息控制长度，避免日志过长
                            _preview_truncated = (_preview[:6000] + ("..." if len(_preview) > 2000 else ""))
                            logger.info(f"📦 [基本面分析师] 统一工具返回数据预览(前6000字符):\n{_preview_truncated}")
                            # 完整数据写入DEBUG级别
                            logger.debug(f"🧾 [基本面分析师] 统一工具返回完整数据:\n{_full}")
                        except Exception as _log_err:
                            logger.warning(f"⚠️ [基本面分析师] 记录统一工具数据时出错: {_log_err}")
                    else:
                        combined_data = "统一基本面分析工具不可用"
                        logger.debug(f"📊 [DEBUG] 统一工具未找到")
                except Exception as e:
                    combined_data = f"统一基本面分析工具调用失败: {e}"
                    logger.debug(f"📊 [DEBUG] 统一工具调用异常: {e}")
                
                currency_info = f"{market_info['currency_name']}（{market_info['currency_symbol']}）"
                
                # 生成基于真实数据的分析报告 —— 将工具数据注入提示词，让 LLM 基于真实数据生成
                # 注意：这里使用 analysis_prompt_template | fresh_llm（不绑定工具），
                # 因为我们已经手动获取了数据，不需要 LLM 再调用工具
                analysis_prompt = f"""基于以下真实数据，对{company_name}（股票代码：{ticker}）进行详细的基本面分析：

{combined_data}

请提供：
1. 公司基本信息分析（{company_name}，股票代码：{ticker}）
2. 财务状况评估
3. 盈利能力分析
4. 估值分析（使用{currency_info}）
5. 投资建议（买入/持有/卖出）

要求：
- 基于提供的真实数据进行分析
- 正确使用公司名称"{company_name}"和股票代码"{ticker}"
- 价格使用{currency_info}
- 投资建议使用中文
- 分析要详细且专业
- 🚫 严禁输出XML/HTML标签、tool_calls、invoke、parameter等工具调用格式"""

                try:
                    # 创建简单的分析链
                    analysis_prompt_template = ChatPromptTemplate.from_messages([
                        ("system", "你是专业的股票基本面分析师，基于提供的真实数据进行分析。禁止输出任何XML标签或工具调用格式，只输出报告正文。"),
                        ("human", "{analysis_request}")
                    ])
                    
                    analysis_chain = analysis_prompt_template | fresh_llm
                    analysis_result = analysis_chain.invoke({"analysis_request": analysis_prompt})
                    
                    if hasattr(analysis_result, 'content'):
                        report = analysis_result.content
                    else:
                        report = str(analysis_result)

                    logger.info(f"📊 [基本面分析师] 强制工具调用完成，报告长度: {len(report)}")

                    # 降级方案3：模板化报告（Template Fallback）
                    # 如果 LLM 输出全是伪造的工具调用格式（DSML幻觉），
                    # remove_thinking_content() 清洗后内容过短（<50字符），
                    # 则直接将工具返回的原始数据拼接成模板化报告返回。
                    # 这是最后的兜底方案，保证至少返回了基础数据。
                    cleaned = remove_thinking_content(str(report))
                    if not cleaned or len(cleaned) < 50:
                        logger.warning(f"⚠️ [基本面分析师] LLM输出经清洗后为空或过短，使用原始数据模板化报告")
                        report = (
                            f"## {company_name}（{ticker}）基本面分析报告\n\n"
                            f"### 基础数据\n\n{combined_data}\n\n"
                            f"### 免责声明\n"
                            f"由于AI模型未能基于数据生成规范的报告，以上为基础数据原始内容。"
                            f"建议使用其他模型（如 deepseek-chat）重试分析。"
                        )

                except Exception as e:
                    logger.error(f"❌ [DEBUG] 强制工具调用分析失败: {e}")
                    report = f"基本面分析失败：{str(e)}"

                # 🔧 保持工具调用计数器不变（已在开始时根据ToolMessage更新）
                return {
                    "fundamentals_report": report,
                    "fundamentals_tool_call_count": tool_call_count
                }

        # 这里不应该到达，但作为备用
        logger.debug(f"📊 [DEBUG] 返回状态: fundamentals_report长度={len(result.content) if hasattr(result, 'content') else 0}")
        # 🔧 保持工具调用计数器不变（已在开始时根据ToolMessage更新）
        return {
            "messages": [result],
            "fundamentals_report": result.content if hasattr(result, 'content') else str(result),
            "fundamentals_tool_call_count": tool_call_count
        }

    def fundamentals_analyst_node(state):
        """
        基本面分析师节点外层包装器 —— 思维链内容清洗

        与市场分析师的包装器逻辑相同：在内层函数返回后，
        对 fundamentals_report 进行思维链/DSML 标签清洗。

        清洗的必要性：DeepSeek v4 等模型的输出可能包含：
        - <think/> 标签包裹的推理过程
        - <｜tool_calls＞ 等 DSML 伪工具调用格式
        这些内容对下游分析师和管理器来说是噪声，必须移除。
        """
        result = _fundamentals_analyst_node_inner(state)
        if "fundamentals_report" in result:
            original = result["fundamentals_report"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [基本面分析师] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["fundamentals_report"] = cleaned
        return result

    return fundamentals_analyst_node
