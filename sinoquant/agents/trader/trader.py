import functools
import time
import json
import re

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


def create_trader(llm, memory):
    """创建交易员（Trader）节点工厂函数。

    交易员在多智能体辩论框架中负责基于研究经理的投资计划，结合各分析师报告，
    生成最终的结构化交易决策。其核心职责包括：
    1. 接收研究经理（Research Manager）制定的投资计划
    2. 综合市场、情绪、新闻、基本面四维分析报告
    3. 生成包含 action/target_price/confidence/risk_score/reasoning 的结构化决策
    4. 利用金融情境记忆从过去的交易错误中学习
    5. 根据股票所属市场自动处理货币单位（A股使用人民币）

    参数:
        llm: 语言模型实例，用于生成交易决策
        memory: 金融情境记忆系统，用于检索与当前市场状况相似的历史交易经验；
                若为 None 则跳过历史记忆检索

    返回:
        functools.partial: 绑定了 name="Trader" 的交易员节点函数
    """
    # 导入思维链清洗工具，用于移除 LLM 输出中的推理过程标签
    from sinoquant.utils.text_utils import remove_thinking_content

    def _trader_node_inner(state, name):
        """交易员核心逻辑：基于投资计划生成结构化交易决策。

        处理流程：
        1. 从工作流状态中提取投资计划和各分析师报告
        2. 检测股票所属市场（A股/美股/港股等），确定货币单位
        3. 检索金融情境记忆，获取类似情境下的历史交易经验
        4. 构建 system + user 消息对，调用 LLM 生成交易决策
        5. 从 LLM 输出中提取结构化决策（---DECISION--- 块或文本回退提取）

        参数:
            state: LangGraph 工作流状态字典，包含：
                - company_of_interest: 目标股票代码
                - investment_plan: 研究经理制定的投资计划
                - market_report: 市场技术分析报告
                - sentiment_report: 社交媒体情绪分析报告
                - news_report: 新闻分析报告
                - fundamentals_report: 基本面分析报告
            name: 交易员节点名称标识（默认为 "Trader"）

        返回:
            dict: 包含 messages、trader_investment_plan、structured_decision、sender
        """
        # 提取目标股票代码
        company_name = state["company_of_interest"]
        # 研究经理制定的投资计划，作为交易员决策的核心依据
        investment_plan = state["investment_plan"]
        # 各分析师的研究报告，为交易员提供多维度参考信息
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        # 使用统一的股票类型检测工具，判断股票所属市场
        # 这决定了货币单位的选择（A股=人民币，美股=美元等）
        from sinoquant.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(company_name)
        is_china = market_info['is_china']

        # 货币处理：A股统一使用人民币作为货币单位
        # 注意：当前实现中无论市场类型都使用人民币，这是基于项目主要面向 A 股市场的设计
        currency = '人民币'
        currency_symbol = '¥'

        logger.debug(f"💰 [DEBUG] ===== 交易员节点开始 =====")
        logger.debug(f"💰 [DEBUG] 交易员检测股票类型: {company_name} -> {market_info['market_name']}, 货币: {currency}")
        logger.debug(f"💰 [DEBUG] 货币符号: {currency_symbol}")
        logger.debug(f"💰 [DEBUG] 市场详情: 中国A股={is_china}")
        logger.debug(f"💰 [DEBUG] 基本面报告长度: {len(fundamentals_report)}")
        logger.debug(f"💰 [DEBUG] 基本面报告前200字符: {fundamentals_report[:200]}...")

        # 将所有分析师报告拼接为综合市场情境描述
        # 用于与金融情境记忆系统进行相似度匹配
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"

        # 检查memory是否可用，检索最多 2 条与当前市场状况相似的历史交易经验
        if memory is not None:
            logger.warning(f"⚠️ [DEBUG] memory可用，获取历史记忆")
            past_memories = memory.get_memories(curr_situation, n_matches=2)
            past_memory_str = ""
            for i, rec in enumerate(past_memories, 1):
                past_memory_str += rec["recommendation"] + "\n\n"
        else:
            logger.warning(f"⚠️ [DEBUG] memory为None，跳过历史记忆检索")
            past_memories = []
            past_memory_str = "暂无历史记忆数据可参考。"

        # 构建 user 消息：将研究经理的投资计划作为交易员决策的核心输入
        # 投资计划中已包含研究经理对辩论的评估、目标价格分析和战略行动建议
        context = {
            "role": "user",
            "content": f"Based on a comprehensive analysis by a team of analysts, here is an investment plan tailored for {company_name}. This plan incorporates insights from current technical market trends, macroeconomic indicators, and social media sentiment. Use this plan as a foundation for evaluating your next trading decision.\n\nProposed Investment Plan: {investment_plan}\n\nLeverage these insights to make an informed and strategic decision.",
        }

        # ===== 构建 LLM 消息列表（system + user） =====
        # system 消息定义了交易员的角色、输出格式要求和约束条件
        # 关键要素：
        # 1. 货币单位强制使用人民币（适配 A 股市场）
        # 2. 公司名称必须与基本面报告一致，防止幻觉问题
        # 3. 强制要求提供具体目标价位，不允许模糊回答
        # 4. 要求输出结构化决策块（---DECISION---），便于系统自动提取
        # 5. 嵌入金融情境记忆，帮助交易员从历史交易错误中学习
        messages = [
            {
                "role": "system",
                "content": f"""您是一位专业的交易员，负责分析市场数据并做出投资决策。基于您的分析，请提供具体的买入、卖出或持有建议。

⚠️ 重要提醒：当前分析的股票代码是 {company_name}，请使用正确的货币单位：{currency}（{currency_symbol}）

🔴 严格要求：
- 股票代码 {company_name} 的公司名称必须严格按照基本面报告中的真实数据
- 绝对禁止使用错误的公司名称或混淆不同的股票
- 所有分析必须基于提供的真实数据，不允许假设或编造
- **必须提供具体的目标价位，不允许设置为null或空值**

请在您的分析中包含以下关键信息：
1. **投资建议**: 明确的买入/持有/卖出决策
2. **目标价位**: 基于分析的合理目标价格({currency}) - 🚨 强制要求提供具体数值
   - 买入建议：提供目标价位和预期涨幅
   - 持有建议：提供合理价格区间（如：{currency_symbol}XX-XX）
   - 卖出建议：提供止损价位和目标卖出价
3. **置信度**: 对决策的信心程度(0-1之间)
4. **风险评分**: 投资风险等级(0-1之间，0为低风险，1为高风险)
5. **详细推理**: 支持决策的具体理由

🎯 目标价位计算指导：
- 基于基本面分析中的估值数据（P/E、P/B、DCF等）
- 参考技术分析的支撑位和阻力位
- 考虑行业平均估值水平
- 结合市场情绪和新闻影响
- 即使市场情绪过热，也要基于合理估值给出目标价

特别注意：
- 使用人民币（¥）作为价格单位
- 目标价位必须与当前股价的货币单位保持一致
- 必须使用基本面报告中提供的正确公司名称
- **绝对不允许说"无法确定目标价"或"需要更多信息"**

请用中文撰写分析内容，并始终以'最终交易建议: **买入/持有/卖出**'结束您的回应以确认您的建议。

🚨 **必须在分析末尾、最终交易建议之后，添加以下结构化决策数据块**（用于系统自动提取）：
```
---DECISION---
{{"action": "买入/持有/卖出", "target_price": 数字, "confidence": 0-1数字, "risk_score": 0-1数字, "reasoning": "一句话决策理由"}}
---DECISION---
```

请不要忘记利用过去决策的经验教训来避免重复错误。以下是类似情况下的交易反思和经验教训: {past_memory_str}""",
            },
            context,
        ]

        logger.debug(f"💰 [DEBUG] 准备调用LLM，系统提示包含货币: {currency}")
        logger.debug(f"💰 [DEBUG] 系统提示中的关键部分: 目标价格({currency})")

        # 调用 LLM 生成交易决策
        # LLM 将基于投资计划、分析师报告和历史记忆，输出包含结构化决策块的交易建议
        result = llm.invoke(messages)
        content = result.content

        logger.debug(f"💰 [DEBUG] LLM调用完成")
        logger.debug(f"💰 [DEBUG] 交易员回复长度: {len(content)}")
        logger.debug(f"💰 [DEBUG] 交易员回复前500字符: {content[:500]}...")
        logger.debug(f"💰 [DEBUG] ===== 交易员节点结束 =====")

        # 从 LLM 输出中提取结构化决策
        # 优先尝试从 ---DECISION--- 块中解析 JSON，失败则回退到文本提取模式
        structured_decision = _extract_decision_from_output(content, market_info)

        # 返回更新后的工作流状态：
        # - messages: LLM 原始响应消息（用于对话历史传递）
        # - trader_investment_plan: 交易员的完整分析文本（含自然语言推理和结构化决策块）
        # - structured_decision: 解析后的结构化决策字典（action/target_price/confidence/risk_score/reasoning）
        # - sender: 节点发送者标识，用于在多智能体流程中追踪消息来源
        return {
            "messages": [result],
            "trader_investment_plan": content,
            "structured_decision": structured_decision,
            "sender": name,
        }

    def trader_node(state, name="Trader"):
        """交易员节点的外层包装函数，负责思维链内容清洗。

        该函数是对 _trader_node_inner 的包装，增加了对 LLM 输出的后处理：
        - 移除 LLM 生成内容中的思维链标签（如 DeepSeek v4 的 <think/> 或 DSML 标签）
        - 确保最终输出到下游节点的交易计划中不包含推理过程的中间内容
        - 这是所有分析师和管理者节点的通用模式，防止思维链泄露影响报告可读性

        参数:
            state: LangGraph 工作流状态字典
            name: 交易员节点名称标识（默认为 "Trader"）

        返回:
            dict: 清洗后的工作流状态更新（含 structured_decision）
        """
        result = _trader_node_inner(state, name)
        # 对交易员投资计划进行思维链内容清洗
        # remove_thinking_content 会移除 <think|>...</think|> 等推理过程标签
        if "trader_investment_plan" in result:
            original = result["trader_investment_plan"]
            cleaned = remove_thinking_content(original)
            if cleaned != original:
                logger.info(f"🧹 [Trader] 清洗报告: 移除思维链/DSML标签 ({len(original)}→{len(cleaned)}字符)")
            result["trader_investment_plan"] = cleaned
        return result

    # 使用 functools.partial 将 name 参数绑定为 "Trader"，
    # 使返回的函数符合 LangGraph 节点的要求（仅接受 state 参数）
    return functools.partial(trader_node, name="Trader")


def _extract_decision_from_output(content: str, market_info: dict) -> dict:
    """从 Trader 的 LLM 输出中提取结构化决策（无需额外 LLM 调用）。

    提取策略采用两级回退机制：
    1. 优先从 ---DECISION--- 块中解析 JSON（结构化提取，精度高）
    2. 若 JSON 解析失败，回退到从自然语言文本中提取（文本提取，精度较低）

    结构化决策包含以下字段：
    - action: 交易动作（买入/持有/卖出）
    - target_price: 目标价格（浮点数，已清洗货币符号）
    - confidence: 置信度（0-1 之间，默认 0.7）
    - risk_score: 风险评分（0-1 之间，默认 0.5）
    - reasoning: 决策理由（一句话摘要）

    参数:
        content: LLM 生成的交易决策文本
        market_info: 股票市场信息字典（含 is_china、market_name 等）

    返回:
        dict: 结构化决策字典
    """

    # ===== 第一级：从 ---DECISION--- 块中提取 JSON =====
    # ---DECISION--- 块是 system 提示词中要求 LLM 在输出末尾添加的结构化数据格式
    # 格式为：---DECISION--- {JSON} ---DECISION---
    # 这种方式提取精度最高，因为 LLM 直接输出了结构化数据
    decision_match = re.search(r'---DECISION---\s*(\{.*\})\s*---DECISION---', content, re.DOTALL)
    if decision_match:
        try:
            decision = json.loads(decision_match.group(1))

            # 验证并规范化 action 字段
            # LLM 可能返回中文（买入/卖出/持有）或英文（buy/sell/hold），
            # 需要统一映射为中文标准格式
            action = decision.get("action", "持有")
            if action not in ["买入", "持有", "卖出"]:
                # 英文-中文动作映射表，覆盖常见的中英文变体
                action_map = {
                    "buy": "买入", "hold": "持有", "sell": "卖出",
                    "BUY": "买入", "HOLD": "持有", "SELL": "卖出",
                    "购买": "买入", "保持": "持有", "出售": "卖出",
                }
                action = action_map.get(action, "持有")

            # 处理目标价格字段
            # LLM 可能返回带货币符号的字符串（如 "¥25.50"）或纯数字
            # 需要清洗货币符号并转换为浮点数
            target_price = decision.get("target_price")
            if target_price is not None:
                try:
                    if isinstance(target_price, str):
                        # 移除常见货币符号：$（美元）、¥/￥（人民币）、元（中文单位）
                        target_price = float(target_price.replace('$', '').replace('¥', '').replace('￥', '').replace('元', '').strip())
                    elif isinstance(target_price, (int, float)):
                        target_price = float(target_price)
                    else:
                        target_price = None
                except (ValueError, TypeError):
                    target_price = None

            return {
                "action": action,
                "target_price": target_price,
                "confidence": float(decision.get("confidence", 0.7)),
                "risk_score": float(decision.get("risk_score", 0.5)),
                "reasoning": decision.get("reasoning", "基于综合分析的投资建议"),
            }
        except (json.JSONDecodeError, Exception) as e:
            # JSON 解析失败时回退到文本提取模式
            logger.warning(f"⚠️ [Trader] DECISION JSON 解析失败: {e}, 回退到文本提取")

    # ===== 第二级：回退到文本提取（兜底方案） =====
    # 当 LLM 未生成 ---DECISION--- 块或 JSON 格式错误时，
    # 从自然语言文本中通过正则表达式提取决策信息
    return _extract_decision_from_text(content)


def _extract_decision_from_text(text: str) -> dict:
    """从自然语言文本中提取决策信息（兜底方案）。

    当 LLM 未生成 ---DECISION--- 结构化块或 JSON 解析失败时使用此函数。
    通过正则表达式从文本中提取 action 和 target_price，
    confidence 和 risk_score 使用默认值（因为无法从文本中可靠提取）。

    提取策略：
    1. action 提取：优先从"最终交易建议"行提取，避免上下文中同时出现
       买入/卖出关键词时产生误判；卖出优先于买入（防止"建议卖出A并买入B"误判为买入）
    2. target_price 提取：按优先级依次尝试四种正则模式匹配

    参数:
        text: LLM 生成的自然语言交易决策文本

    返回:
        dict: 结构化决策字典（action/target_price/confidence/risk_score/reasoning）
    """
    # ===== 提取交易动作（action） =====
    # 优先从"最终交易建议"行提取 action，避免文本中同时出现买入/卖出时误判
    action = '持有'
    final_match = re.search(r'最终交易建议[：:]*\s*\*{0,2}(买入|卖出|持有)\*{0,2}', text)
    if final_match:
        action = final_match.group(1)
    else:
        # 回退：全文匹配，卖出优先于买入（避免"建议卖出XX并买入YY"误判为买入）
        # 优先级：卖出 > 买入 > 持有（卖出信号更值得关注，避免漏判风险提示）
        if re.search(r'卖出|SELL', text, re.IGNORECASE):
            action = '卖出'
        elif re.search(r'买入|BUY', text, re.IGNORECASE):
            action = '买入'
        elif re.search(r'持有|HOLD', text, re.IGNORECASE):
            action = '持有'

    # ===== 提取目标价格（target_price） =====
    # 按优先级依次尝试四种正则匹配模式：
    # 1. "目标价位/目标价格"后跟数字（最精确，优先级最高）
    # 2. "目标"后跟数字（次精确）
    # 3. 货币符号（¥/$）后跟数字（较宽松）
    # 4. 数字后跟"元"（中文价格表述）
    target_price = None
    price_patterns = [
        r'目标价[位格]?[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',
        r'目标[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',
        r'[¥\$](\d+(?:\.\d+)?)',
        r'(\d+(?:\.\d+)?)元',
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                target_price = float(match.group(1))
                break
            except ValueError:
                continue

    # 文本提取模式下使用默认的 confidence 和 risk_score
    # 因为无法从自然语言文本中可靠地提取这些数值
    return {
        "action": action,
        "target_price": target_price,
        "confidence": 0.7,   # 默认置信度
        "risk_score": 0.5,   # 默认风险评分
        "reasoning": "基于综合分析的投资建议",
    }
