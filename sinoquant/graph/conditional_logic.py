# SinaQuant/graph/conditional_logic.py
# LangGraph 条件路由逻辑模块
# 控制多智能体工作流中各节点的执行顺序和条件跳转：
#   1. 分析师节点：决定继续工具调用还是生成报告
#   2. 投资辩论：控制看涨/看跌研究员的轮转和辩论终止
#   3. 风险讨论：控制激进/保守/中性分析师的轮转和讨论终止

from sinoquant.agents.utils.agent_states import AgentState

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


class ConditionalLogic:
    """条件路由控制器

    管理三类条件边（conditional edges）的路由逻辑：
    - should_continue_{analyst}：分析师节点的工具调用循环控制
    - should_continue_debate：投资辩论的轮转与终止控制
    - should_continue_risk_analysis：风险讨论的轮转与终止控制

    核心设计原则：
    - 工具调用计数器防止死循环（LLM 可能反复调用工具而不生成报告）
    - 报告长度检测提前终止（已有报告则无需继续调用工具）
    - 辩论/讨论轮次受配置参数控制（max_debate_rounds / max_risk_discuss_rounds）
    """

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """初始化条件路由控制器

        Args:
            max_debate_rounds: 投资辩论轮次，实际发言次数 = 2 × max_debate_rounds
                               （看涨和看跌各发言 max_debate_rounds 次）
            max_risk_discuss_rounds: 风险讨论轮次，实际发言次数 = 3 × max_risk_discuss_rounds
                                     （激进、保守、中性各发言 max_risk_discuss_rounds 次）
        """
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    def should_continue_market(self, state: AgentState):
        """判断市场分析师是否应继续工具调用还是生成报告

        路由逻辑（按优先级）：
        1. 工具调用次数 ≥ 3 → 强制结束，路由到 Msg Clear Market
        2. 已有市场报告且长度 > 100 → 分析完成，路由到 Msg Clear Market
        3. 最后一条消息包含 tool_calls → 需要执行工具，路由到 tools_market
        4. 默认 → 路由到 Msg Clear Market（生成报告或结束分析）

        Args:
            state: 当前 LangGraph 状态，包含 messages、market_report、market_tool_call_count

        Returns:
            下一个节点名称："tools_market" 或 "Msg Clear Market"
        """
        from sinoquant.utils.logging_init import get_logger
        logger = get_logger("agents")

        messages = state["messages"]
        last_message = messages[-1]  # 取最后一条消息用于判断

        # 死循环修复：检查工具调用次数，防止 LLM 反复调用工具而不生成报告
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 3  # 最多允许调用3次工具（通常1次即可获取所有数据）

        # 检查是否已经有市场分析报告
        market_report = state.get("market_report", "")

        logger.info(f"🔀 [条件判断] should_continue_market")
        logger.info(f"🔀 [条件判断] - 消息数量: {len(messages)}")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(market_report)}")
        logger.info(f"🔧 [死循环修复] - 工具调用次数: {tool_call_count}/{max_tool_calls}")
        logger.info(f"🔀 [条件判断] - 最后消息类型: {type(last_message).__name__}")
        logger.info(f"🔀 [条件判断] - 是否有tool_calls: {hasattr(last_message, 'tool_calls')}")
        if hasattr(last_message, 'tool_calls'):
            logger.info(f"🔀 [条件判断] - tool_calls数量: {len(last_message.tool_calls) if last_message.tool_calls else 0}")
            if last_message.tool_calls:
                for i, tc in enumerate(last_message.tool_calls):
                    logger.info(f"🔀 [条件判断] - tool_call[{i}]: {tc.get('name', 'unknown')}")

        # 优先级1：工具调用次数达到上限 → 强制结束
        if tool_call_count >= max_tool_calls:
            logger.warning(f"🔧 [死循环修复] 达到最大工具调用次数，强制结束: Msg Clear Market")
            return "Msg Clear Market"

        # 优先级2：已有报告内容且长度 > 100 → 分析完成
        if market_report and len(market_report) > 100:
            logger.info(f"🔀 [条件判断] ✅ 报告已完成，返回: Msg Clear Market")
            return "Msg Clear Market"

        # 优先级3：LLM 返回了 tool_calls → 需要执行工具获取数据
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            logger.info(f"🔀 [条件判断] 🔧 检测到tool_calls，返回: tools_market")
            return "tools_market"

        # 默认：无工具调用，LLM 已生成文本回复 → 路由到 Msg Clear 结束分析师分支
        logger.info(f"🔀 [条件判断] ✅ 无tool_calls，返回: Msg Clear Market")
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """判断社交媒体分析师是否应继续工具调用还是生成报告

        路由逻辑与市场分析师相同，使用 sentiment_tool_call_count 和 sentiment_report

        Args:
            state: 当前 LangGraph 状态

        Returns:
            下一个节点名称："tools_social" 或 "Msg Clear Social"
        """
        from sinoquant.utils.logging_init import get_logger
        logger = get_logger("agents")

        messages = state["messages"]
        last_message = messages[-1]

        # 死循环修复：检查工具调用次数
        tool_call_count = state.get("sentiment_tool_call_count", 0)
        max_tool_calls = 3

        # 检查是否已经有情绪分析报告
        sentiment_report = state.get("sentiment_report", "")

        logger.info(f"🔀 [条件判断] should_continue_social")
        logger.info(f"🔀 [条件判断] - 消息数量: {len(messages)}")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(sentiment_report)}")
        logger.info(f"🔧 [死循环修复] - 工具调用次数: {tool_call_count}/{max_tool_calls}")

        # 优先级1：工具调用次数达到上限 → 强制结束
        if tool_call_count >= max_tool_calls:
            logger.warning(f"🔧 [死循环修复] 达到最大工具调用次数，强制结束: Msg Clear Social")
            return "Msg Clear Social"

        # 优先级2：已有报告内容 → 分析完成
        if sentiment_report and len(sentiment_report) > 100:
            logger.info(f"🔀 [条件判断] ✅ 报告已完成，返回: Msg Clear Social")
            return "Msg Clear Social"

        # 优先级3：有工具调用 → 执行工具
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            logger.info(f"🔀 [条件判断] 🔧 检测到tool_calls，返回: tools_social")
            return "tools_social"

        # 默认：结束分析
        logger.info(f"🔀 [条件判断] ✅ 无tool_calls，返回: Msg Clear Social")
        return "Msg Clear Social"

    def should_continue_news(self, state: AgentState):
        """判断新闻分析师是否应继续工具调用还是生成报告

        路由逻辑与其他分析师相同，使用 news_tool_call_count 和 news_report

        Args:
            state: 当前 LangGraph 状态

        Returns:
            下一个节点名称："tools_news" 或 "Msg Clear News"
        """
        from sinoquant.utils.logging_init import get_logger
        logger = get_logger("agents")

        messages = state["messages"]
        last_message = messages[-1]

        # 死循环修复：检查工具调用次数
        tool_call_count = state.get("news_tool_call_count", 0)
        max_tool_calls = 3

        # 检查是否已经有新闻分析报告
        news_report = state.get("news_report", "")

        logger.info(f"🔀 [条件判断] should_continue_news")
        logger.info(f"🔀 [条件判断] - 消息数量: {len(messages)}")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(news_report)}")
        logger.info(f"🔧 [死循环修复] - 工具调用次数: {tool_call_count}/{max_tool_calls}")

        # 优先级1：工具调用次数达到上限 → 强制结束
        if tool_call_count >= max_tool_calls:
            logger.warning(f"🔧 [死循环修复] 达到最大工具调用次数，强制结束: Msg Clear News")
            return "Msg Clear News"

        # 优先级2：已有报告内容 → 分析完成
        if news_report and len(news_report) > 100:
            logger.info(f"🔀 [条件判断] ✅ 报告已完成，返回: Msg Clear News")
            return "Msg Clear News"

        # 优先级3：有工具调用 → 执行工具
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            logger.info(f"🔀 [条件判断] 🔧 检测到tool_calls，返回: tools_news")
            return "tools_news"

        # 默认：结束分析
        logger.info(f"🔀 [条件判断] ✅ 无tool_calls，返回: Msg Clear News")
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """判断基本面分析师是否应继续工具调用还是生成报告

        与其他分析师相比，基本面分析师的特殊之处：
        - max_tool_calls = 1（一次工具调用即可获取所有基本面数据）
        - 优先检查已有报告（防止 LLM 在拿到数据后仍尝试重复调用工具）
        - 检查 tool_calls 时会验证调用次数是否已达上限

        Args:
            state: 当前 LangGraph 状态

        Returns:
            下一个节点名称："tools_fundamentals" 或 "Msg Clear Fundamentals"
        """
        from sinoquant.utils.logging_init import get_logger
        logger = get_logger("agents")

        messages = state["messages"]
        last_message = messages[-1]

        # 死循环修复: 添加工具调用次数检查
        tool_call_count = state.get("fundamentals_tool_call_count", 0)
        max_tool_calls = 1  # 一次工具调用就能获取所有数据

        # 检查是否已经有基本面报告
        fundamentals_report = state.get("fundamentals_report", "")

        logger.info(f"🔀 [条件判断] should_continue_fundamentals")
        logger.info(f"🔀 [条件判断] - 消息数量: {len(messages)}")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(fundamentals_report)}")
        logger.info(f"🔧 [死循环修复] - 工具调用次数: {tool_call_count}/{max_tool_calls}")
        logger.info(f"🔀 [条件判断] - 最后消息类型: {type(last_message).__name__}")
        
        # 🔍 [调试日志] 打印最后一条消息的详细内容
        logger.info(f"🤖 [条件判断] 最后一条消息详细内容:")
        logger.info(f"🤖 [条件判断] - 消息类型: {type(last_message).__name__}")
        if hasattr(last_message, 'content'):
            content_preview = last_message.content[:300] + "..." if len(last_message.content) > 300 else last_message.content
            logger.info(f"🤖 [条件判断] - 内容预览: {content_preview}")
        
        # 🔍 [调试日志] 打印tool_calls的详细信息
        logger.info(f"🔀 [条件判断] - 是否有tool_calls: {hasattr(last_message, 'tool_calls')}")
        if hasattr(last_message, 'tool_calls'):
            logger.info(f"🔀 [条件判断] - tool_calls数量: {len(last_message.tool_calls) if last_message.tool_calls else 0}")
            if last_message.tool_calls:
                logger.info(f"🔧 [条件判断] 检测到 {len(last_message.tool_calls)} 个工具调用:")
                for i, tc in enumerate(last_message.tool_calls):
                    logger.info(f"🔧 [条件判断] - 工具调用 {i+1}: {tc.get('name', 'unknown')} (ID: {tc.get('id', 'unknown')})")
                    if 'args' in tc:
                        logger.info(f"🔧 [条件判断] - 参数: {tc['args']}")
            else:
                logger.info(f"🔧 [条件判断] tool_calls为空列表")
        else:
            logger.info(f"🔧 [条件判断] 无tool_calls属性")

        # ✅ 优先级1: 如果已经有报告内容，说明分析已完成，不再循环
        if fundamentals_report and len(fundamentals_report) > 100:
            logger.info(f"🔀 [条件判断] ✅ 报告已完成，返回: Msg Clear Fundamentals")
            return "Msg Clear Fundamentals"

        # ✅ 优先级2: 如果有tool_calls，去执行工具
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            # 检查是否超过最大调用次数
            if tool_call_count >= max_tool_calls:
                logger.warning(f"🔧 [死循环修复] 工具调用次数已达上限({tool_call_count}/{max_tool_calls})，但仍有tool_calls，强制结束")
                return "Msg Clear Fundamentals"

            logger.info(f"🔀 [条件判断] 🔧 检测到tool_calls，返回: tools_fundamentals")
            return "tools_fundamentals"

        # ✅ 优先级3: 没有tool_calls，正常结束
        logger.info(f"🔀 [条件判断] ✅ 无tool_calls，返回: Msg Clear Fundamentals")
        return "Msg Clear Fundamentals"

    def should_continue_debate(self, state: AgentState) -> str:
        """控制投资辩论（看涨 vs 看跌）的轮转与终止

        辩论流程：
        1. Bull Researcher 发言 → current_response 设为 "Bull..."
        2. Bear Researcher 发言 → current_response 设为 "Bear..."
        3. 交替进行直到 count >= 2 × max_debate_rounds
        4. 达到上限后路由到 Research Manager 做最终裁决

        Args:
            state: 当前 LangGraph 状态，包含 investment_debate_state

        Returns:
            下一个节点名称："Bull Researcher"、"Bear Researcher" 或 "Research Manager"
        """
        current_count = state["investment_debate_state"]["count"]  # 当前发言总次数
        max_count = 2 * self.max_debate_rounds  # 最大发言次数（双方各 max_debate_rounds 次）
        current_speaker = state["investment_debate_state"]["current_response"]  # 当前发言人标识

        # 🔍 详细日志
        logger.info(f"🔍 [投资辩论控制] 当前发言次数: {current_count}, 最大次数: {max_count} (配置轮次: {self.max_debate_rounds})")
        logger.info(f"🔍 [投资辩论控制] 当前发言者: {current_speaker}")

        # 辩论次数达到上限 → 结束辩论，交由研究经理裁决
        if current_count >= max_count:
            logger.info(f"✅ [投资辩论控制] 达到最大次数，结束辩论 -> Research Manager")
            return "Research Manager"

        # 交替轮转：看涨发完 → 看跌发言，看跌发完 → 看涨发言
        next_speaker = "Bear Researcher" if current_speaker.startswith("Bull") else "Bull Researcher"
        logger.info(f"🔄 [投资辩论控制] 继续辩论 -> {next_speaker}")
        return next_speaker

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """控制风险讨论（激进 vs 保守 vs 中性）的轮转与终止

        讨论流程（三人轮转）：
        1. Risky Analyst → Safe Analyst → Neutral Analyst → Risky Analyst → ...
        2. 每轮3次发言，共 max_risk_discuss_rounds 轮
        3. 达到 3 × max_risk_discuss_rounds 后路由到 Risk Judge 做最终裁决

        Args:
            state: 当前 LangGraph 状态，包含 risk_debate_state

        Returns:
            下一个节点名称："Risky Analyst"、"Safe Analyst"、"Neutral Analyst" 或 "Risk Judge"
        """
        current_count = state["risk_debate_state"]["count"]  # 当前发言总次数
        max_count = 3 * self.max_risk_discuss_rounds  # 最大发言次数（三人各 max_risk_discuss_rounds 次）
        latest_speaker = state["risk_debate_state"]["latest_speaker"]  # 最后发言人标识

        # 🔍 详细日志
        logger.info(f"🔍 [风险讨论控制] 当前发言次数: {current_count}, 最大次数: {max_count} (配置轮次: {self.max_risk_discuss_rounds})")
        logger.info(f"🔍 [风险讨论控制] 最后发言者: {latest_speaker}")

        # 讨论次数达到上限 → 结束讨论，交由风险经理裁决
        if current_count >= max_count:
            logger.info(f"✅ [风险讨论控制] 达到最大次数，结束讨论 -> Risk Judge")
            return "Risk Judge"

        # 三人轮转调度：Risky → Safe → Neutral → Risky → ...
        if latest_speaker.startswith("Risky"):
            next_speaker = "Safe Analyst"
        elif latest_speaker.startswith("Safe"):
            next_speaker = "Neutral Analyst"
        else:
            next_speaker = "Risky Analyst"

        logger.info(f"🔄 [风险讨论控制] 继续讨论 -> {next_speaker}")
        return next_speaker
