import time
import json

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


def create_research_manager(llm, memory):
    """创建研究经理（Research Manager）节点工厂函数。

    研究经理在多智能体辩论框架中担任辩论裁判（Debate Judge）的角色，其核心职责包括：
    1. 评估看涨（Bull）与看跌（Bear）分析师之间的辩论论点
    2. 基于辩论中最具说服力的证据，做出明确的投资立场（买入/卖出/持有）
    3. 为交易员（Trader）制定详细的投资计划，包括目标价格分析和战略行动建议
    4. 利用金融情境记忆（Financial Situation Memory）从过去的错误中学习，持续改进决策质量

    参数:
        llm: 语言模型实例，用于生成投资评估和计划
        memory: 金融情境记忆系统，用于检索与当前市场状况相似的历史决策经验；
                若为 None 则跳过历史记忆检索，仅基于当前信息做判断

    返回:
        research_manager_node: 可注入 LangGraph 工作流的研究经理节点函数
    """
    # 导入思维链清洗工具，用于移除 LLM 输出中的推理过程标签（如 DeepSeek DSML 标签），
    # 防止思维链内容泄露到最终报告中影响可读性
    from sinoquant.utils.text_utils import remove_thinking_content

    def _research_manager_node_inner(state) -> dict:
        """研究经理核心逻辑：评估辩论并生成投资计划。

        处理流程：
        1. 从工作流状态中提取辩论历史和各分析师报告
        2. 基于当前市场状况检索金融情境记忆（相似历史场景的决策经验）
        3. 构建包含辩论历史、分析报告和记忆反思的提示词
        4. 调用 LLM 生成辩论评估和投资计划
        5. 更新辩论状态并输出投资计划

        参数:
            state: LangGraph 工作流状态字典，包含：
                - investment_debate_state: 投资辩论状态（含辩论历史 history）
                - market_report: 市场技术分析报告
                - sentiment_report: 社交媒体情绪分析报告
                - news_report: 新闻分析报告
                - fundamentals_report: 基本面分析报告

        返回:
            dict: 包含更新后的 investment_debate_state 和 investment_plan
        """
        # 提取辩论历史：看涨与看跌分析师之间的完整辩论记录
        history = state["investment_debate_state"].get("history", "")
        # 提取四位分析师的研究报告，用于构建全面的当前市场情境描述
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        investment_debate_state = state["investment_debate_state"]

        # 将所有分析师报告拼接为综合市场情境描述，
        # 用于与金融情境记忆系统进行相似度匹配
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"

        # 安全检查：确保memory不为None
        # 通过情境相似度检索最多 2 条历史记忆，帮助研究经理避免重蹈覆辙
        if memory is not None:
            past_memories = memory.get_memories(curr_situation, n_matches=2)
        else:
            logger.warning(f"⚠️ [DEBUG] memory为None，跳过历史记忆检索")
            past_memories = []

        # 将检索到的历史记忆格式化为字符串，嵌入到提示词中
        # 每条记忆包含过往类似情境下的推荐意见和反思
        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ===== 构建研究经理提示词 =====
        # 提示词结构包含五个核心部分：
        # 1. 角色定义：作为投资组合经理和辩论主持人，要求批判性评估辩论
        # 2. 决策要求：必须做出明确的买入/卖出/持有立场，避免因"双方都有道理"而默认持有
        # 3. 投资计划框架：包含建议、理由、战略行动和目标价格分析
        # 4. 过去错误反思：嵌入金融情境记忆，帮助避免重复过去的决策失误
        # 5. 输入数据：综合分析报告（市场/情绪/新闻/基本面）+ 辩论历史
        prompt = f"""作为投资组合经理和辩论主持人，您的职责是批判性地评估这轮辩论并做出明确决策：支持看跌分析师、看涨分析师，或者仅在基于所提出论点有强有力理由时选择持有。

简洁地总结双方的关键观点，重点关注最有说服力的证据或推理。您的建议——买入、卖出或持有——必须明确且可操作。避免仅仅因为双方都有有效观点就默认选择持有；要基于辩论中最强有力的论点做出承诺。

此外，为交易员制定详细的投资计划。这应该包括：

您的建议：基于最有说服力论点的明确立场。
理由：解释为什么这些论点导致您的结论。
战略行动：实施建议的具体步骤。
📊 目标价格分析：基于所有可用报告（基本面、新闻、情绪），提供全面的目标价格区间和具体价格目标。考虑：
- 基本面报告中的基本估值
- 新闻对价格预期的影响
- 情绪驱动的价格调整
- 技术支撑/阻力位
- 风险调整价格情景（保守、基准、乐观）
- 价格目标的时间范围（1个月、3个月、6个月）
💰 您必须提供具体的目标价格 - 不要回复"无法确定"或"需要更多信息"。

考虑您在类似情况下的过去错误。利用这些见解来完善您的决策制定，确保您在学习和改进。以对话方式呈现您的分析，就像自然说话一样，不使用特殊格式。

以下是您对错误的过去反思：
\"{past_memory_str}\"

以下是综合分析报告：
市场研究：{market_research_report}

情绪分析：{sentiment_report}

新闻分析：{news_report}

基本面分析：{fundamentals_report}

以下是辩论：
辩论历史：
{history}

请用中文撰写所有分析内容和建议。"""

        # 📊 统计 prompt 大小（用于监控 LLM 调用成本和性能）
        # 中文文本约 1.5-2 字符/token，此处使用 1.8 作为保守估算因子
        prompt_length = len(prompt)
        estimated_tokens = int(prompt_length / 1.8)

        logger.info(f"📊 [Research Manager] Prompt 统计:")
        logger.info(f"   - 辩论历史长度: {len(history)} 字符")
        logger.info(f"   - 总 Prompt 长度: {prompt_length} 字符")
        logger.info(f"   - 估算输入 Token: ~{estimated_tokens} tokens")

        # ⏱️ 记录开始时间
        start_time = time.time()

        # 调用 LLM 生成辩论评估和投资计划
        # LLM 将基于辩论历史、分析报告和历史记忆，输出结构化的投资建议
        response = llm.invoke(prompt)

        # ⏱️ 记录结束时间
        elapsed_time = time.time() - start_time

        # 📊 统计响应信息
        response_length = len(response.content) if response and hasattr(response, 'content') else 0
        estimated_output_tokens = int(response_length / 1.8)

        logger.info(f"⏱️ [Research Manager] LLM调用耗时: {elapsed_time:.2f}秒")
        logger.info(f"📊 [Research Manager] 响应统计: {response_length} 字符, 估算~{estimated_output_tokens} tokens")

        # 构建新的投资辩论状态，保留原始辩论历史记录
        # judge_decision 记录研究经理作为裁判的最终裁决
        # current_response 用于在辩论流程中传递当前轮次的回复
        new_investment_debate_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": response.content,
            "count": investment_debate_state["count"],
        }

        # 返回更新后的工作流状态：
        # - investment_debate_state: 更新后的辩论状态（含裁判决策）
        # - investment_plan: 投资计划文本，将传递给下游的交易员（Trader）节点
        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

    def research_manager_node(state):
        """研究经理节点的外层包装函数，负责思维链内容清洗。

        该函数是对 _research_manager_node_inner 的包装，增加了对 LLM 输出的后处理：
        - 移除 LLM 生成内容中的思维链标签（如 DeepSeek v4 的 <think/> 或 DSML 标签）
        - 确保最终输出到下游节点的投资计划中不包含推理过程的中间内容
        - 这是所有分析师和管理者节点的通用模式，防止思维链泄露影响报告可读性

        参数:
            state: LangGraph 工作流状态字典

        返回:
            dict: 清洗后的工作流状态更新（investment_debate_state + investment_plan）
        """
        result = _research_manager_node_inner(state)
        # 对投资计划进行思维链内容清洗
        # remove_thinking_content 会移除 <think|>...</think|> 等推理过程标签
        if "investment_plan" in result:
            original = result["investment_plan"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [Research Manager] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["investment_plan"] = cleaned
        return result

    return research_manager_node
