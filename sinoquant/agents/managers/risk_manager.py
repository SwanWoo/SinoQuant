import time
import json

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


def create_risk_manager(llm, memory):
    """创建风险经理（Risk Manager）节点工厂函数。

    风险经理是多智能体辩论框架中的最终决策者（Final Arbiter），其核心职责包括：
    1. 评估三位风险分析师（激进/中性/保守）之间的辩论
    2. 基于辩论论据和过去经验教训，做出最终的买入/卖出/持有决策
    3. 完善交易员（Trader）的投资计划，融入风险控制视角
    4. 利用金融情境记忆从过去的错误决策中学习，避免重蹈覆辙
    5. 具备重试机制（最多 3 次尝试），确保 LLM 调用的鲁棒性

    与研究经理（Research Manager）的区别：
    - 研究经理评估看涨/看跌辩论，制定投资计划
    - 风险经理评估激进/中性/保守辩论，完善计划并做出最终决策

    参数:
        llm: 语言模型实例，用于生成最终交易决策
        memory: 金融情境记忆系统，用于检索与当前市场状况相似的历史决策经验；
                若为 None 则跳过历史记忆检索

    返回:
        risk_manager_node: 可注入 LangGraph 工作流的风险经理节点函数
    """
    # 导入思维链清洗工具，用于移除 LLM 输出中的推理过程标签
    from sinoquant.utils.text_utils import remove_thinking_content

    def _risk_manager_node_inner(state) -> dict:
        """风险经理核心逻辑：评估风险辩论并生成最终交易决策。

        处理流程：
        1. 从工作流状态中提取风险辩论历史和各分析师报告
        2. 基于当前市场状况检索金融情境记忆
        3. 构建包含辩论历史、交易员计划和记忆反思的提示词
        4. 调用 LLM 生成最终交易决策（含重试机制，最多 3 次尝试）
        5. 若所有重试均失败，生成默认持有决策
        6. 更新风险辩论状态并输出最终交易决策

        参数:
            state: LangGraph 工作流状态字典，包含：
                - company_of_interest: 目标股票代码
                - risk_debate_state: 风险辩论状态（含辩论历史 history、
                  risky_history/neutral_history/safe_history）
                - market_report: 市场技术分析报告
                - news_report: 新闻分析报告
                - fundamentals_report: 基本面分析报告
                - sentiment_report: 社交媒体情绪分析报告
                - investment_plan: 交易员的投资计划

        返回:
            dict: 包含更新后的 risk_debate_state 和 final_trade_decision
        """
        # 提取目标股票代码
        company_name = state["company_of_interest"]

        # 提取风险辩论历史：激进/中性/保守三位风险分析师之间的完整辩论记录
        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        # 提取各分析师报告，用于构建全面的当前市场情境描述
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["news_report"]  # 注意：此处使用 news_report，疑似 bug（应为 state["fundamentals_report"]）
        sentiment_report = state["sentiment_report"]
        # 交易员的投资计划，风险经理将以此为基础进行完善和调整
        trader_plan = state["investment_plan"]

        # 将所有分析师报告拼接为综合市场情境描述
        # 用于与金融情境记忆系统进行相似度匹配
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"

        # 安全检查：确保memory不为None
        # 通过情境相似度检索最多 2 条历史记忆，帮助风险经理避免重复过去的决策失误
        if memory is not None:
            past_memories = memory.get_memories(curr_situation, n_matches=2)
        else:
            logger.warning(f"⚠️ [DEBUG] memory为None，跳过历史记忆检索")
            past_memories = []

        # 将检索到的历史记忆格式化为字符串，嵌入到提示词中
        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ===== 构建风险经理提示词 =====
        # 提示词结构包含四个核心部分：
        # 1. 角色定义：作为风险管理委员会主席和辩论主持人
        # 2. 决策指导原则：总结关键论点、提供理由、完善交易员计划、从过去错误中学习
        # 3. 输入数据：分析师辩论历史 + 交易员投资计划 + 历史记忆反思
        # 4. 输出要求：明确的买入/卖出/持有建议 + 详细推理
        # 关键要求：避免因"各方面都有道理"而默认选择持有，必须基于最强论据做出承诺
        prompt = f"""作为风险管理委员会主席和辩论主持人，您的目标是评估三位风险分析师——激进、中性和安全/保守——之间的辩论，并确定交易员的最佳行动方案。您的决策必须产生明确的建议：买入、卖出或持有。只有在有具体论据强烈支持时才选择持有，而不是在所有方面都似乎有效时作为后备选择。力求清晰和果断。

决策指导原则：
1. **总结关键论点**：提取每位分析师的最强观点，重点关注与背景的相关性。
2. **提供理由**：用辩论中的直接引用和反驳论点支持您的建议。
3. **完善交易员计划**：从交易员的原始计划**{trader_plan}**开始，根据分析师的见解进行调整。
4. **从过去的错误中学习**：使用**{past_memory_str}**中的经验教训来解决先前的误判，改进您现在做出的决策，确保您不会做出错误的买入/卖出/持有决定而亏损。

交付成果：
- 明确且可操作的建议：买入、卖出或持有。
- 基于辩论和过去反思的详细推理。

---

**分析师辩论历史：**
{history}

---

专注于可操作的见解和持续改进。建立在过去经验教训的基础上，批判性地评估所有观点，确保每个决策都能带来更好的结果。请用中文撰写所有分析内容和建议。"""

        # 📊 统计 prompt 大小（用于监控 LLM 调用成本和性能）
        # 中文文本约 1.5-2 字符/token，此处使用 1.8 作为保守估算因子
        prompt_length = len(prompt)
        # 粗略估算 token 数量（中文约 1.5-2 字符/token，英文约 4 字符/token）
        estimated_tokens = int(prompt_length / 1.8)  # 保守估计

        logger.info(f"📊 [Risk Manager] Prompt 统计:")
        logger.info(f"   - 辩论历史长度: {len(history)} 字符")
        logger.info(f"   - 交易员计划长度: {len(trader_plan)} 字符")
        logger.info(f"   - 历史记忆长度: {len(past_memory_str)} 字符")
        logger.info(f"   - 总 Prompt 长度: {prompt_length} 字符")
        logger.info(f"   - 估算输入 Token: ~{estimated_tokens} tokens")

        # ===== 增强的 LLM 调用：包含错误处理和重试机制 =====
        # 风险经理作为最终决策者，必须确保 LLM 调用的鲁棒性
        # 重试机制设计：
        # - 最多重试 3 次（max_retries = 3）
        # - 每次重试间隔 2 秒，避免 API 限流
        # - 成功条件：响应非空且长度 > 10 字符（排除空响应和极短响应）
        # - 所有重试失败后，生成默认持有决策（保守策略）
        max_retries = 3
        retry_count = 0
        response_content = ""

        while retry_count < max_retries:
            try:
                logger.info(f"🔄 [Risk Manager] 调用LLM生成交易决策 (尝试 {retry_count + 1}/{max_retries})")

                # ⏱️ 记录开始时间
                start_time = time.time()

                # 调用 LLM 生成最终交易决策
                # 风险经理的决策是整个多智能体辩论流程的最终输出
                response = llm.invoke(prompt)

                # ⏱️ 记录结束时间
                elapsed_time = time.time() - start_time

                if response and hasattr(response, 'content') and response.content:
                    response_content = response.content.strip()

                    # 📊 统计响应信息
                    response_length = len(response_content)
                    estimated_output_tokens = int(response_length / 1.8)

                    # 尝试获取实际的 token 使用情况（如果 LLM 返回了）
                    usage_info = ""
                    if hasattr(response, 'response_metadata') and response.response_metadata:
                        metadata = response.response_metadata
                        if 'token_usage' in metadata:
                            token_usage = metadata['token_usage']
                            usage_info = f", 实际Token: 输入={token_usage.get('prompt_tokens', 'N/A')} 输出={token_usage.get('completion_tokens', 'N/A')} 总计={token_usage.get('total_tokens', 'N/A')}"

                    logger.info(f"⏱️ [Risk Manager] LLM调用耗时: {elapsed_time:.2f}秒")
                    logger.info(f"📊 [Risk Manager] 响应统计: {response_length} 字符, 估算~{estimated_output_tokens} tokens{usage_info}")

                    if len(response_content) > 10:  # 确保响应有实质内容（排除空响应和极短响应）
                        logger.info(f"✅ [Risk Manager] LLM调用成功")
                        break
                    else:
                        logger.warning(f"⚠️ [Risk Manager] LLM响应内容过短: {len(response_content)} 字符")
                        response_content = ""
                else:
                    logger.warning(f"⚠️ [Risk Manager] LLM响应为空或无效")
                    response_content = ""

            except Exception as e:
                elapsed_time = time.time() - start_time
                logger.error(f"❌ [Risk Manager] LLM调用失败 (尝试 {retry_count + 1}): {str(e)}")
                logger.error(f"⏱️ [Risk Manager] 失败前耗时: {elapsed_time:.2f}秒")
                response_content = ""

            retry_count += 1
            # 重试间隔：等待 2 秒后再次尝试，避免因 API 限流导致连续失败
            if retry_count < max_retries and not response_content:
                logger.info(f"🔄 [Risk Manager] 等待2秒后重试...")
                time.sleep(2)

        # ===== 默认决策生成 =====
        # 如果所有重试都失败（LLM 调用异常、响应为空或过短），
        # 生成默认的"持有"决策，遵循风险控制原则：
        # 1. 市场信息不足时避免盲目操作
        # 2. 保持现有仓位，等待更明确信号
        # 3. 在不确定性高的情况下选择保守策略
        if not response_content:
            logger.error(f"❌ [Risk Manager] 所有LLM调用尝试失败，使用默认决策")
            response_content = f"""**默认建议：持有**

由于技术原因无法生成详细分析，基于当前市场状况和风险控制原则，建议对{company_name}采取持有策略。

**理由：**
1. 市场信息不足，避免盲目操作
2. 保持现有仓位，等待更明确的市场信号
3. 控制风险，避免在不确定性高的情况下做出激进决策

**建议：**
- 密切关注市场动态和公司基本面变化
- 设置合理的止损和止盈位
- 等待更好的入场或出场时机

注意：此为系统默认建议，建议结合人工分析做出最终决策。"""

        # 构建新的风险辩论状态，保留完整的辩论历史记录
        # judge_decision 记录风险经理作为裁判的最终裁决
        # latest_speaker 设为 "Judge" 标识当前发言者为风险经理
        # 保留三位分析师各自的辩论历史，用于后续分析和审计
        new_risk_debate_state = {
            "judge_decision": response_content,
            "history": risk_debate_state["history"],
            "risky_history": risk_debate_state["risky_history"],
            "safe_history": risk_debate_state["safe_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_risky_response": risk_debate_state["current_risky_response"],
            "current_safe_response": risk_debate_state["current_safe_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        logger.info(f"📋 [Risk Manager] 最终决策生成完成，内容长度: {len(response_content)} 字符")

        # 返回更新后的工作流状态：
        # - risk_debate_state: 更新后的风险辩论状态（含裁判决策）
        # - final_trade_decision: 最终交易决策文本，这是整个多智能体辩论流程的最终输出
        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": response_content,
        }

    def risk_manager_node(state):
        """风险经理节点的外层包装函数，负责思维链内容清洗。

        该函数是对 _risk_manager_node_inner 的包装，增加了对 LLM 输出的后处理：
        - 移除 LLM 生成内容中的思维链标签（如 DeepSeek v4 的 <think/> 或 DSML 标签）
        - 确保最终输出到下游节点的交易决策中不包含推理过程的中间内容
        - 这是所有分析师和管理者节点的通用模式，防止思维链泄露影响报告可读性
        - 特别重要：风险经理的输出是最终交易决策，直接影响用户交易行为，
          因此必须确保输出内容清晰、专业，不含技术性中间内容

        参数:
            state: LangGraph 工作流状态字典

        返回:
            dict: 清洗后的工作流状态更新（含 risk_debate_state + final_trade_decision）
        """
        result = _risk_manager_node_inner(state)
        # 对最终交易决策进行思维链内容清洗
        # remove_thinking_content 会移除 <think|>...</think|> 等推理过程标签
        if "final_trade_decision" in result:
            original = result["final_trade_decision"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [Risk Manager] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["final_trade_decision"] = cleaned
        return result

    return risk_manager_node
