# SinaQuant/graph/signal_processing.py
"""
信号处理模块 —— 从交易分析文本中提取结构化投资决策

本模块是 SinaQuant 多智能体交易系统中的关键后处理环节。在分析师（市场、基本面、新闻、
社交媒体）经过辩论和研究经理综合后，交易员（Trader）和风控经理（Risk Manager）会生成
自由文本形式的交易建议。SignalProcessor 的职责是将这些非结构化的文本转化为结构化决策，
包含：操作方向（买入/持有/卖出）、目标价格、置信度、风险评分和决策理由。

处理流程采用三级回退策略（Fallback Strategy）：
    1. 主路径：调用 LLM 将文本解析为 JSON 格式的结构化决策
    2. 二级回退：LLM 解析失败时，使用正则表达式从文本中提取决策（_extract_simple_decision）
    3. 终极回退：所有方法均失败时，返回默认的保守持有决策（_get_default_decision）

目标价格提取（Target Price Extraction）是本模块最复杂的部分：
    - 首先从 LLM 返回的 JSON 中直接获取
    - 若 JSON 中缺失，则使用多种中文正则模式从原文中匹配
    - 若正则也无法匹配，则通过智能价格推算（Smart Price Estimation）根据当前价格和
      涨跌幅信息进行估算
"""

from langchain_openai import ChatOpenAI

# 导入统一日志系统和图处理模块日志装饰器
from sinoquant.utils.logging_init import get_logger
from sinoquant.utils.tool_logging import log_graph_module
from sinoquant.utils.text_utils import remove_thinking_content
logger = get_logger("graph.signal_processing")


class SignalProcessor:
    """
    信号处理器 —— 从交易分析文本中提取结构化投资决策

    SignalProcessor 是 SinaQuant 交易图（Trading Graph）中的决策提取组件。当交易员和
    风控经理完成分析后，其输出通常是自然语言文本，而下游系统需要结构化的决策数据来执行
    交易或生成报告。本类负责这一文本到结构化数据的转换过程。

    结构化决策（Structured Decision）包含以下五个核心字段：
        - action: 操作方向，必须是"买入"、"持有"或"卖出"之一
        - target_price: 目标价格，与股票交易货币一致（A股为人民币，港股为港币，美股为美元）
        - confidence: 置信度，0-1之间的浮点数，表示对决策的信心程度
        - risk_score: 风险评分，0-1之间的浮点数，数值越高表示风险越大
        - reasoning: 决策理由，详细阐述投资逻辑和分析要点

    处理策略采用三级回退机制，确保在任何情况下都能返回有效的结构化决策：
        1. LLM 解析（process_signal 主路径）
        2. 正则表达式提取（_extract_simple_decision）
        3. 默认保守决策（_get_default_decision）
    """

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """
        初始化信号处理器

        Args:
            quick_thinking_llm: 快速推理 LLM 实例，用于将交易信号文本解析为结构化 JSON。
                通常使用响应速度较快的模型（如 deepseek-chat、qwen-flash 等），
                因为信号处理是交易流程中的中间步骤，不需要最强大的模型，
                但需要较快的响应速度以减少整体延迟。
        """
        self.quick_thinking_llm = quick_thinking_llm

    @log_graph_module("signal_processing")
    def process_signal(self, full_signal: str, stock_symbol: str = None) -> dict:
        """
        处理完整交易信号，提取结构化投资决策信息

        这是信号处理的主入口方法。通过调用 LLM 将交易员/风控经理的自然语言分析报告
        解析为包含 action、target_price、confidence、risk_score、reasoning 五个字段
        的结构化决策字典。

        处理流程：
            1. 输入验证 —— 检查信号文本是否为空或无效
            2. 市场识别 —— 根据股票代码判断所属市场（A股/港股/美股）和计价货币
            3. LLM 调用 —— 使用精心设计的中文 prompt 引导 LLM 输出 JSON 格式的决策
            4. JSON 解析 —— 从 LLM 响应中提取并验证 JSON 数据
            5. 字段标准化 —— 将操作方向映射为中文，提取和验证目标价格
            6. 价格回退 —— 若 JSON 中无目标价格，依次尝试正则提取和智能推算
            7. 异常回退 —— 若 LLM 调用或 JSON 解析失败，使用正则提取或返回默认决策

        Args:
            full_signal: 完整的交易信号文本，通常来自交易员或风控经理的输出
            stock_symbol: 股票代码（如 "000001.SZ"、"0700.HK"、"AAPL"），
                用于判断市场类型和计价货币，影响目标价格的单位和推算逻辑

        Returns:
            dict: 结构化决策字典，包含以下字段：
                - action (str): 操作方向，"买入"/"持有"/"卖出"
                - target_price (float|None): 目标价格，单位与股票交易货币一致
                - confidence (float): 置信度，0-1之间
                - risk_score (float): 风险评分，0-1之间
                - reasoning (str): 决策理由，去除思考链标签后的清洁文本
        """

        # ==================== 第一级：输入验证 ====================
        # 检查信号文本是否存在且为有效字符串，空信号直接返回保守持有决策
        if not full_signal or not isinstance(full_signal, str) or len(full_signal.strip()) == 0:
            logger.error(f"❌ [SignalProcessor] 输入信号为空或无效: {repr(full_signal)}")
            return {
                'action': '持有',
                'target_price': None,
                'confidence': 0.5,
                'risk_score': 0.5,
                'reasoning': '输入信号无效，默认持有建议'
            }

        # 清理信号文本首尾空白，再次验证清理后是否为空
        full_signal = full_signal.strip()
        if len(full_signal) == 0:
            logger.error(f"❌ [SignalProcessor] 信号内容为空")
            return {
                'action': '持有',
                'target_price': None,
                'confidence': 0.5,
                'risk_score': 0.5,
                'reasoning': '信号内容为空，默认持有建议'
            }

        # ==================== 第二级：市场识别与货币判断 ====================
        # 根据股票代码识别所属市场（A股/港股/美股等），确定计价货币
        # 这一步至关重要，因为目标价格必须与交易货币一致：
        #   - A股（如 000001.SZ）使用人民币（CNY/¥）
        #   - 港股（如 0700.HK）使用港币（HKD/HK$）
        #   - 美股（如 AAPL）使用美元（USD/$）
        from sinoquant.utils.stock_utils import StockUtils

        market_info = StockUtils.get_market_info(stock_symbol)
        is_china = market_info['is_china']
        is_hk = market_info['is_hk']
        currency = market_info['currency_name']
        currency_symbol = market_info['currency_symbol']

        logger.info(f"🔍 [SignalProcessor] 处理信号: 股票={stock_symbol}, 市场={market_info['market_name']}, 货币={currency}",
                   extra={'stock_symbol': stock_symbol, 'market': market_info['market_name'], 'currency': currency})

        # ==================== 第三级：构建 LLM Prompt ====================
        # 设计中文 prompt，引导 LLM 从交易分析报告中提取结构化决策
        # Prompt 的关键设计要点：
        #   1. 要求 LLM 输出 JSON 格式，便于程序解析
        #   2. 强制 action 字段使用中文（买入/持有/卖出），避免英文混入
        #   3. 明确指出股票的市场和货币类型，防止目标价格单位错误
        #   4. 要求 reasoning 保留详细分析内容，不要过度精简
        messages = [
            (
                "system",
                f"""您是一位专业的金融分析助手，负责从交易员的分析报告中提取结构化的投资决策信息。

请从提供的分析报告中提取以下信息，并以JSON格式返回：

{{
    "action": "买入/持有/卖出",
    "target_price": 数字({currency}价格，**必须提供具体数值，不能为null**),
    "confidence": 数字(0-1之间，如果没有明确提及则为0.7),
    "risk_score": 数字(0-1之间，如果没有明确提及则为0.5),
    "reasoning": "投资决策的详细理由，包含关键分析要点和风险评估"
}}

请确保：
1. action字段必须是"买入"、"持有"或"卖出"之一（绝对不允许使用英文buy/hold/sell）
2. target_price必须是具体的数字,target_price应该是合理的{currency}价格数字（使用{currency_symbol}符号）
3. confidence和risk_score应该在0-1之间
4. reasoning应为详细阐述（200-500字），包括：核心分析发现、主要支撑因素、潜在风险提示
5. 所有内容必须使用中文，不允许任何英文投资建议

特别注意：
- 股票代码 {stock_symbol or '未知'} 是{market_info['market_name']}，使用{currency}计价
- 目标价格必须与股票的交易货币一致（{currency_symbol}）
- 请在reasoning中保留尽可能多的分析细节和判断依据

如果某些信息在报告中没有明确提及，请使用合理的默认值。""",
            ),
            ("human", full_signal),
        ]

        # ==================== 第四级：消息验证 ====================
        # 确保 messages 列表和 human 消息内容不为空
        if not messages or len(messages) == 0:
            logger.error(f"❌ [SignalProcessor] messages为空")
            return self._get_default_decision()
        
        # 验证human消息内容
        human_content = messages[1][1] if len(messages) > 1 else ""
        if not human_content or len(human_content.strip()) == 0:
            logger.error(f"❌ [SignalProcessor] human消息内容为空")
            return self._get_default_decision()

        logger.debug(f"🔍 [SignalProcessor] 准备调用LLM，消息数量: {len(messages)}, 信号长度: {len(full_signal)}")

        # ==================== 第五级：LLM 调用与结果解析 ====================
        try:
            # 调用快速推理 LLM，获取结构化决策的文本响应
            response = self.quick_thinking_llm.invoke(messages).content
            logger.debug(f"🔍 [SignalProcessor] LLM响应: {response[:200]}...")

            # 尝试解析JSON响应
            import json
            import re

            # 从 LLM 响应中提取 JSON 部分
            # LLM 可能在 JSON 前后添加说明文字，使用正则提取第一个完整的 JSON 对象
            # re.DOTALL 使 . 匹配换行符，确保跨行 JSON 也能正确提取
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_text = json_match.group()
                logger.debug(f"🔍 [SignalProcessor] 提取的JSON: {json_text}")
                decision_data = json.loads(json_text)

                # ==================== 第六级：操作方向标准化 ====================
                # LLM 有时仍会输出英文操作词或同义词，需要统一映射为中文标准值
                action = decision_data.get('action', '持有')
                if action not in ['买入', '持有', '卖出']:
                    # 中文投资建议映射（Chinese Investment Advice Mapping）
                    # 覆盖英文 buy/hold/sell、中文同义词（购买/保持/出售）、
                    # 以及其他变体（purchase/keep/dispose）
                    action_map = {
                        'buy': '买入', 'hold': '持有', 'sell': '卖出',
                        'BUY': '买入', 'HOLD': '持有', 'SELL': '卖出',
                        '购买': '买入', '保持': '持有', '出售': '卖出',
                        'purchase': '买入', 'keep': '持有', 'dispose': '卖出'
                    }
                    action = action_map.get(action, '持有')
                    if action != decision_data.get('action', '持有'):
                        logger.debug(f"🔍 [SignalProcessor] 投资建议映射: {decision_data.get('action')} -> {action}")

                # ==================== 第七级：目标价格提取与回退 ====================
                # 目标价格提取是本模块最复杂的部分，采用多级回退策略：
                #   1. 优先从 LLM 返回的 JSON 中直接获取 target_price
                #   2. 若 JSON 中缺失，从 reasoning 和原文中使用正则模式提取
                #   3. 若正则也无法匹配，调用智能价格推算方法估算
                target_price = decision_data.get('target_price')
                if target_price is None or target_price == "null" or target_price == "":
                    # 将 reasoning 和原始信号合并，扩大价格提取的搜索范围
                    # LLM 可能将价格信息放在 reasoning 字段中而非 target_price 字段
                    reasoning = decision_data.get('reasoning', '')
                    full_text = f"{reasoning} {full_signal}"  # 扩大搜索范围

                    # ==================== 增强的中文价格匹配模式 ====================
                    # 这些正则模式覆盖了中文投资分析报告中常见的价格表述方式，
                    # 按匹配精度从高到低排列（目标价 > 估值 > 货币符号）：
                    price_patterns = [
                        r'目标价[位格]?[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',  # "目标价位: 45.50" / "目标价: ¥45.50" / "目标价格45.50"
                        r'目标[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "目标: 45.50" / "目标¥45.50"
                        r'价格[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "价格: 45.50" / "价格¥45.50"
                        r'价位[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "价位: 45.50"
                        r'合理[价位格]?[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)', # "合理价位: 45.50" / "合理估值: 45.50"
                        r'估值[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "估值: 45.50" —— 注意：估值不等于目标价，但常被混用
                        r'[¥\$](\d+(?:\.\d+)?)',                      # "¥45.50" 或 "$190" —— 直接货币符号前缀
                        r'(\d+(?:\.\d+)?)元',                         # "45.50元" —— 人民币后缀
                        r'(\d+(?:\.\d+)?)美元',                       # "190美元" —— 美元后缀
                        r'建议[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',        # "建议: 45.50" —— 建议买入价位
                        r'预期[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',        # "预期: 45.50" —— 预期价格
                        r'看[到至]\s*[¥\$]?(\d+(?:\.\d+)?)',          # "看到45.50" / "看至45.50" —— 看涨到某价位
                        r'上涨[到至]\s*[¥\$]?(\d+(?:\.\d+)?)',        # "上涨到45.50" / "上涨至¥45.50"
                        r'(\d+(?:\.\d+)?)\s*[¥\$]',                  # "45.50¥" / "190$" —— 货币符号后缀（较少见）
                    ]
                    
                    # 按优先级依次尝试每个价格匹配模式，找到第一个匹配即停止
                    # 高精度模式（如"目标价位"）排在前面，避免被低精度模式（如"¥45.50"）误匹配
                    for pattern in price_patterns:
                        price_match = re.search(pattern, full_text, re.IGNORECASE)
                        if price_match:
                            try:
                                target_price = float(price_match.group(1))
                                logger.debug(f"🔍 [SignalProcessor] 从文本中提取到目标价格: {target_price} (模式: {pattern})")
                                break
                            except (ValueError, IndexError):
                                continue

                    # 如果所有正则模式均未能提取到价格，则调用智能价格推算
                    # 智能推算会根据当前价格和涨跌幅信息估算目标价
                    if target_price is None or target_price == "null" or target_price == "":
                        target_price = self._smart_price_estimation(full_text, action, is_china)
                        if target_price:
                            logger.debug(f"🔍 [SignalProcessor] 智能推算目标价格: {target_price}")
                        else:
                            target_price = None
                            logger.warning(f"🔍 [SignalProcessor] 未能提取到目标价格，设置为None")
                else:
                    # LLM 返回了 target_price，但需要确保其类型正确
                    # LLM 可能返回字符串格式（如 "$45.50"、"45.50元"）或数值类型
                    try:
                        if isinstance(target_price, str):
                            # 清理字符串格式的价格
                            clean_price = target_price.replace('$', '').replace('¥', '').replace('￥', '').replace('元', '').replace('美元', '').strip()
                            target_price = float(clean_price) if clean_price and clean_price.lower() not in ['none', 'null', ''] else None
                        elif isinstance(target_price, (int, float)):
                            target_price = float(target_price)
                        logger.debug(f"🔍 [SignalProcessor] 处理后的目标价格: {target_price}")
                    except (ValueError, TypeError):
                        target_price = None
                        logger.warning(f"🔍 [SignalProcessor] 价格转换失败，设置为None")

                # ==================== 组装最终结构化决策结果 ====================
                # reasoning 使用 remove_thinking_content() 去除 LLM 思考链标签
                # （如 DeepSeek 的 <think /> 标签），确保输出文本干净
                result = {
                    'action': action,
                    'target_price': target_price,
                    'confidence': float(decision_data.get('confidence', 0.7)),
                    'risk_score': float(decision_data.get('risk_score', 0.5)),
                    'reasoning': remove_thinking_content(full_signal),
                }
                logger.info(f"🔍 [SignalProcessor] 处理结果: {result}",
                           extra={'action': result['action'], 'target_price': result['target_price'],
                                 'confidence': result['confidence'], 'stock_symbol': stock_symbol})
                return result
            else:
                # LLM 响应中未找到 JSON 格式，回退到正则表达式提取
                return self._extract_simple_decision(response)

        except Exception as e:
            # LLM 调用或解析过程出现异常（网络错误、JSON 解析错误等）
            # 回退到正则表达式提取，使用原始信号文本而非 LLM 响应
            logger.error(f"信号处理错误: {e}", exc_info=True, extra={'stock_symbol': stock_symbol})
            return self._extract_simple_decision(full_signal)

    def _smart_price_estimation(self, text: str, action: str, is_china: bool) -> float:
        """
        智能价格推算 —— 当无法从文本中直接提取目标价格时的估算方法

        当所有正则模式均未能匹配到明确的目标价格时，此方法尝试通过以下信息推算：
            1. 当前价格：从文本中提取"当前价"、"现价"、"股价"等表述中的价格
            2. 涨跌幅：从文本中提取"上涨X%"、"涨幅X%"等表述中的百分比

        推算逻辑：
            - 有当前价格 + 有涨跌幅：根据操作方向加减涨跌幅计算目标价
              - 买入：当前价 * (1 + 涨跌幅)，即预期继续上涨
              - 卖出：当前价 * (1 - 涨跌幅)，即预期继续下跌
            - 有当前价格 + 无涨跌幅：使用默认涨跌幅估算
              - 买入：A股默认+15%，非A股默认+12%
              - 卖出：A股默认-5%，非A股默认-8%
              - 持有：直接使用当前价格（预期不变）
            - 无当前价格：返回 None，无法推算

        注意：A股的默认涨跌幅偏大（涨15% vs 12%，跌5% vs 8%），因为 A 股市场
        波动性相对较大，涨跌停板为 10%/20%，因此给予更大的估算空间。

        Args:
            text: 包含价格和涨跌信息的文本
            action: 当前操作方向（"买入"/"持有"/"卖出"）
            is_china: 是否为 A 股，影响默认涨跌幅的取值

        Returns:
            float|None: 推算的目标价格，无法推算时返回 None
        """
        import re
        
        # 尝试从文本中提取当前价格和涨跌幅信息
        current_price = None
        percentage_change = None

        # ==================== 提取当前价格 ====================
        # 匹配中文分析报告中常见的当前价格表述
        
        current_price_patterns = [
            r'当前价[格位]?[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',  # "当前价格: 45.50" / "当前价位¥45.50"
            r'现价[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',            # "现价: 45.50"
            r'股价[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',            # "股价: 45.50"
            r'价格[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',            # "价格: 45.50"（最宽泛，匹配优先级最低）
        ]
        
        for pattern in current_price_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    current_price = float(match.group(1))
                    break
                except ValueError:
                    continue
        
        # ==================== 提取涨跌幅信息 ====================
        # 匹配中文分析报告中常见的涨跌幅表述
        # 注意：此处仅提取上涨幅度的百分比，下跌信息暂未单独处理
        percentage_patterns = [
            r'上涨\s*(\d+(?:\.\d+)?)%',        # "上涨5.2%"
            r'涨幅\s*(\d+(?:\.\d+)?)%',         # "涨幅5.2%"
            r'增长\s*(\d+(?:\.\d+)?)%',         # "增长5.2%"
            r'(\d+(?:\.\d+)?)%\s*的?上涨',      # "5.2%的上涨" / "5.2%上涨"
        ]
        
        for pattern in percentage_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    percentage_change = float(match.group(1)) / 100
                    break
                except ValueError:
                    continue
        
        # ==================== 基于提取的信息推算目标价 ====================
        # 情况一：同时拥有当前价格和涨跌幅，直接计算
        if current_price and percentage_change:
            if action == '买入':
                return round(current_price * (1 + percentage_change), 2)
            elif action == '卖出':
                return round(current_price * (1 - percentage_change), 2)
        
        # 情况二：有当前价格但无涨跌幅，使用默认涨跌幅估算
        # A 股的默认涨跌幅大于非 A 股，因为 A 股市场波动性更大
        if current_price:
            if action == '买入':
                # 买入建议默认10-20%涨幅
                multiplier = 1.15 if is_china else 1.12
                return round(current_price * multiplier, 2)
            elif action == '卖出':
                # 卖出建议默认5-10%跌幅
                multiplier = 0.95 if is_china else 0.92
                return round(current_price * multiplier, 2)
            else:  # 持有
                # 持有建议表示预期价格不变，直接使用当前价格
                return current_price

        # 情况三：无法提取任何价格信息，返回 None
        # 后续逻辑将把 target_price 设为 None，表示价格不可用
        return None

    def _extract_simple_decision(self, text: str) -> dict:
        """
        正则表达式回退决策提取 —— 当 LLM 解析失败时的备用方案

        当 LLM 调用失败（网络错误、超时）或 LLM 响应中不包含有效 JSON 时，
        使用纯正则表达式从文本中提取决策信息。相比 LLM 解析，此方法的
        优点是不依赖外部服务、响应快、确定性高；缺点是理解能力有限，
        可能误匹配或遗漏复杂的表述方式。

        提取策略：
            1. 操作方向（action）：优先匹配"最终交易建议"行，其次全文搜索
               - 卖出优先于买入（保守原则：若同时出现买入和卖出，倾向卖出）
            2. 目标价格（target_price）：使用价格正则模式提取，与主路径相同的模式
            3. 价格回退：若正则也无法提取价格，调用智能价格推算
            4. 置信度和风险评分：无法从正则中提取，使用保守默认值
               - confidence: 0.7（中等偏高，避免过于保守）
               - risk_score: 0.5（中等风险，中性评估）

        Args:
            text: 待提取的文本，可能是 LLM 响应或原始信号文本

        Returns:
            dict: 结构化决策字典，字段与 process_signal() 返回值一致
        """
        import re

        # ==================== 提取操作方向 ====================
        # 默认为"持有"（最保守的选择）
        action = '持有'  # 默认
        # 优先从"最终交易建议"行提取 action
        # 匹配格式如 "最终交易建议：**买入**" 或 "最终交易建议: 卖出"
        final_match = re.search(r'最终交易建议[：:]*\s*\*{0,2}(买入|卖出|持有)\*{0,2}', text)
        if final_match:
            action = final_match.group(1)
        else:
            # 全文匹配回退策略：卖出优先于买入（保守原则）
            # 若文本中同时出现"买入"和"卖出"，优先取"卖出"，
            # 因为在不确定时保守比激进更安全
            if re.search(r'卖出|SELL', text, re.IGNORECASE):
                action = '卖出'
            elif re.search(r'买入|BUY', text, re.IGNORECASE):
                action = '买入'
            elif re.search(r'持有|HOLD', text, re.IGNORECASE):
                action = '持有'

        # ==================== 提取目标价格 ====================
        # 使用精简版价格匹配模式（比主路径少一些模式，但覆盖最常见的表述）
        target_price = None
        price_patterns = [
            r'目标价[位格]?[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',  # "目标价位: 45.50"
            r'\*\*目标价[位格]?\*\*[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',  # "**目标价位**: 45.50"（Markdown 加粗格式）
            r'目标[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "目标: 45.50"
            r'价格[：:]?\s*[¥\$]?(\d+(?:\.\d+)?)',         # "价格: 45.50"
            r'[¥\$](\d+(?:\.\d+)?)',                      # "¥45.50" 或 "$190"
            r'(\d+(?:\.\d+)?)元',                         # "45.50元"
        ]

        for pattern in price_patterns:
            price_match = re.search(pattern, text)
            if price_match:
                try:
                    target_price = float(price_match.group(1))
                    break
                except ValueError:
                    continue

        # 若正则也无法提取价格，调用智能价格推算
        # 注意：此方法默认 is_china=True（假设为 A 股），因为在此回退路径中
        # 无法获取股票代码信息。这是一个保守假设，对港股/美股可能不够准确。
        if target_price is None:
            is_china = True  # 默认假设是A股，实际应该从上下文获取
            target_price = self._smart_price_estimation(text, action, is_china)

        return {
            'action': action,
            'target_price': target_price,
            'confidence': 0.7,
            'risk_score': 0.5,
            'reasoning': remove_thinking_content(text)
        }

    def _get_default_decision(self) -> dict:
        """
        返回默认的保守投资决策 —— 终极回退方案

        当所有其他方法都失败时（输入为空、LLM 调用异常、正则提取也无法工作），
        返回此默认决策。选择"持有"作为默认操作方向，这是最保守的选择：
            - 不买入：避免在信息不足时追高
            - 不卖出：避免在信息不足时恐慌抛售
            - 持有：保持现状，等待更好的决策时机

        默认值的含义：
            - action: "持有" —— 保守中立的操作建议
            - target_price: None —— 没有足够信息推算目标价格
            - confidence: 0.5 —— 中等置信度，表示决策依据不充分
            - risk_score: 0.5 —— 中等风险评分，未做具体风险评估
            - reasoning: "输入数据无效，默认持有建议" —— 明确说明决策来源

        Returns:
            dict: 默认结构化决策字典
        """
        return {
            'action': '持有',
            'target_price': None,
            'confidence': 0.5,
            'risk_score': 0.5,
            'reasoning': '输入数据无效，默认持有建议'
        }
