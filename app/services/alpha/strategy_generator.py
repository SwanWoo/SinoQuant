"""
LLM 策略生成服务

职责：根据多智能体分析报告（市场/情绪/新闻/基本面），调用 LLM 生成可执行的 vnpy 量化策略代码。

流程：
  1. 将分析报告填充到提示词模板
  2. 调用 LLM（DeepSeek/Qwen 等）生成 Python 策略代码
  3. 从 LLM 返回的 markdown 中提取纯 Python 代码
  4. 用 CodeValidator 做安全验证
  5. 存入 MongoDB alpha_strategies 集合

LLM 选择逻辑：
  按实用性排序尝试多个模型（deepseek-chat → qwen3.5-flash → ...），
  找到第一个有 API Key 的可用模型。模型名来自循环变量，不是 provider_info 的字段。
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from bson import ObjectId
from app.core.database import get_mongo_db
from .code_validator import CodeValidator

logger = logging.getLogger(__name__)

# ---- 策略生成提示词模板 ----
# {market_report} 等占位符在运行时替换为实际分析报告
STRATEGY_GENERATION_PROMPT = """你是一位专业的量化交易策略开发专家。根据以下分析报告和交易决策，生成一个可执行的 vnpy AlphaStrategy 子类 Python 策略代码。

## 可用的策略 API

策略类必须继承 `AlphaStrategy`，并实现以下方法：
- `on_init(self)`: 策略初始化
- `on_bars(self, bars: dict[str, BarData])`: 每根K线触发，bars 的 key 是 vt_symbol（如 "000001.SZSE"）
- `on_trade(self, trade)`: 成交回调

可用属性和方法：
- `self.vt_symbols: list[str]` — 当前标的列表
- `self.get_pos(vt_symbol) -> float` — 当前持仓量
- `self.get_target(vt_symbol) -> float` — 目标持仓量
- `self.set_target(vt_symbol, target) -> None` — 设置目标持仓量
- `self.execute_trading(bars, price_add) -> None` — 按目标持仓自动下单（price_add 为滑点比例，如 0.01）
- `self.buy(vt_symbol, price, volume) -> list[str]` — 买入开仓
- `self.sell(vt_symbol, price, volume) -> list[str]` — 卖出平仓
- `self.short(vt_symbol, price, volume) -> list[str]` — 卖出开仓（A股不适用）
- `self.cover(vt_symbol, price, volume) -> list[str]` — 买入平仓（A股不适用）
- `self.get_cash_available() -> float` — 可用资金
- `self.get_holding_value() -> float` — 持仓市值
- `self.get_portfolio_value() -> float` — 总资产

BarData 对象属性：
- `bar.open_price, bar.high_price, bar.low_price, bar.close_price` — OHLC
- `bar.volume, bar.turnover` — 成交量和成交额
- `bar.vt_symbol, bar.datetime` — 标识和时间

## 策略设计建议

1. 策略逻辑在 `on_bars()` 中实现，基于K线数据自行计算信号
2. 使用 `self.set_target()` 设置目标持仓，然后调用 `self.execute_trading(bars, price_add=0.01)` 执行
3. 可以定义类属性作为策略参数（如 `lookback = 20`、`threshold = 0.02`）
4. A股只能做多，不要使用 `self.short()` 和 `self.cover()`
5. 不要使用 `self.get_signal()` — 策略自行计算信号
6. 可以使用 numpy、pandas 进行计算
7. 持仓量为股数（不是手数），最小交易单位 100 股

## 硬性要求

- 必须从 vnpy.alpha 导入 AlphaStrategy
- 只能使用以下导入模块: vnpy.alpha, vnpy.trader.object, vnpy.trader.constant, numpy, pandas, collections, math, datetime, typing
- 不要使用 os, subprocess, open, exec, eval 等危险操作
- 只输出 Python 代码，不要输出解释文字

## 分析报告

### 市场分析报告
{market_report}

### 情绪分析报告
{sentiment_report}

### 新闻分析报告
{news_report}

### 基本面分析报告
{fundamentals_report}

### 最终交易决策
{trade_decision}

请根据以上信息，生成一个完整的量化交易策略类。代码必须可以直接执行。"""


class StrategyGeneratorService:

    def __init__(self):
        self._validator = CodeValidator()  # 代码验证器
        self._llm = None                   # LLM 实例（延迟初始化）
        self._llm_model_info = ""          # 当前使用的模型信息（如 "deepseek:deepseek-chat"）

    async def _get_llm(self):
        """获取 LLM 实例（延迟初始化，自动选择第一个有 API Key 的模型）

        按实用性排序尝试多个模型，找到第一个可用的。
        重要：模型名来自 model_candidates 列表中的循环变量，
        不是 provider_info.get("model_name")（那个 key 不存在）。
        """
        if self._llm is not None:
            return self._llm, self._llm_model_info

        try:
            from app.services.analysis._analysis_config import get_provider_and_url_by_model_sync
            from sinoquant.llm_adapters import create_openai_compatible_llm

            # 按实用性排序，依次尝试
            model_candidates = [
                "deepseek-chat",        # DeepSeek V3，性价比最高
                "qwen3.5-flash",        # 通义千问 Flash，速度快
                "qwen3-max-preview",    # 通义千问 Max，能力强
                "glm-4",                # 智谱 GLM-4
                "deepseek-reasoner",    # DeepSeek R1 推理模型
            ]
            provider_info = None
            for model_name in model_candidates:
                provider_info = get_provider_and_url_by_model_sync(model_name)
                if provider_info and provider_info.get("api_key"):
                    break  # 找到第一个有 API Key 的模型

            if not provider_info or not provider_info.get("api_key"):
                raise RuntimeError("没有可用的 LLM 配置，请在 Web 界面配置至少一个启用的 LLM")

            # 从 provider_info 获取连接参数
            provider = provider_info.get("provider", "dashscope")
            model = model_name                    # 用循环变量，不用 provider_info 的 model_name
            api_key = provider_info.get("api_key")
            base_url = provider_info.get("backend_url")

            # 创建 OpenAI 兼容的 LLM 实例
            self._llm = create_openai_compatible_llm(
                provider=provider,
                model=model,
                api_key=api_key,
                temperature=0.3,         # 偏保守，减少随机性
                max_tokens=4096,         # 策略代码一般不超过 4096 token
                base_url=base_url,
            )
            self._llm_model_info = f"{provider}:{model}"
            return self._llm, self._llm_model_info
        except Exception as e:
            logger.error(f"初始化 LLM 失败: {e}")
            raise

    async def generate_strategy(
        self,
        symbol: str,                                    # 股票代码
        market_reports: dict,                           # 分析报告字典
        user_id: str,                                   # 用户 ID
        analysis_task_id: Optional[str] = None,         # 关联的分析任务 ID
        model_name: Optional[str] = None,               # 指定模型名（可选）
    ) -> dict:
        """调用 LLM 生成策略代码，验证并存储

        流程：
        1. 填充提示词模板
        2. 调用 LLM 生成代码
        3. 从 LLM 返回的 markdown 中提取纯 Python 代码
        4. 用 CodeValidator 验证安全性
        5. 存入 MongoDB alpha_strategies 集合
        """
        db = get_mongo_db()
        strategy_id = f"strat_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow()

        # 1. 填充提示词：把分析报告插入模板的占位符
        prompt = STRATEGY_GENERATION_PROMPT.format(
            market_report=market_reports.get("market_report", "暂无"),
            sentiment_report=market_reports.get("sentiment_report", "暂无"),
            news_report=market_reports.get("news_report", "暂无"),
            fundamentals_report=market_reports.get("fundamentals_report", "暂无"),
            trade_decision=json.dumps(
                market_reports.get("trade_decision", {}),
                ensure_ascii=False,
                indent=2,
            ),
        )

        try:
            # 2. 调用 LLM
            llm, model_info = await self._get_llm()
            logger.info(f"调用 LLM ({model_info}) 生成策略...")

            response = await llm.ainvoke(prompt)
            code = response.content if hasattr(response, "content") else str(response)

            # 3. 从 LLM 返回的 markdown 中提取纯 Python 代码
            code = self._extract_code(code)
            if not code:
                raise ValueError("LLM 未生成有效的 Python 代码")

        except Exception as e:
            # LLM 调用失败，存入错误记录
            logger.error(f"LLM 生成失败: {e}")
            doc = {
                "strategy_id": strategy_id,
                "user_id": user_id,
                "name": f"策略-{symbol}",
                "symbol": symbol,
                "status": "error",
                "validation_errors": [f"LLM生成失败: {str(e)}"],
                "code": "",
                "description": "",
                "parameters": {},
                "analysis_task_id": analysis_task_id,
                "llm_model_info": model_name or "",
                "created_at": now,
                "updated_at": now,
            }
            await db["alpha_strategies"].insert_one(doc)
            return self._serialize_doc(doc)

        # 4. 安全验证
        valid, errors = self._validator.validate(code)

        # 提取元数据
        name = self._extract_class_name(code) or f"策略-{symbol}"
        description = self._extract_description(code, market_reports)

        # 5. 存入数据库
        doc = {
            "strategy_id": strategy_id,
            "user_id": user_id,
            "name": f"{name}-{symbol}",
            "symbol": symbol,
            "status": "validated" if valid else "error",  # 验证通过则标记为 validated
            "validation_errors": errors,
            "code": code,
            "description": description,
            "parameters": self._extract_parameters(code),
            "analysis_task_id": analysis_task_id,
            "llm_model_info": model_info,
            "created_at": now,
            "updated_at": now,
        }
        await db["alpha_strategies"].insert_one(doc)

        logger.info(f"策略生成完成: {strategy_id}, 验证: {'通过' if valid else '失败'}")
        return self._serialize_doc(doc)

    # ---- 查询方法 ----

    async def get_strategy(self, strategy_id: str) -> Optional[dict]:
        """查询单个策略"""
        db = get_mongo_db()
        doc = await db["alpha_strategies"].find_one({"strategy_id": strategy_id})
        return self._serialize_doc(doc) if doc else None

    async def list_strategies(
        self, user_id: str, limit: int = 50, skip: int = 0
    ) -> list[dict]:
        """查询用户的策略列表（按创建时间倒序）"""
        db = get_mongo_db()
        cursor = (
            db["alpha_strategies"]
            .find({"user_id": user_id})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [self._serialize_doc(d) for d in docs]

    async def delete_strategy(self, strategy_id: str, user_id: str) -> bool:
        """删除策略及其关联的回测记录"""
        db = get_mongo_db()
        result = await db["alpha_strategies"].delete_one(
            {"strategy_id": strategy_id, "user_id": user_id}
        )
        if result.deleted_count > 0:
            # 同时删除该策略的所有回测记录
            await db["alpha_backtests"].delete_many({"strategy_id": strategy_id})
        return result.deleted_count > 0

    async def update_strategy_code(
        self, strategy_id: str, code: str, user_id: str
    ) -> Optional[dict]:
        """更新策略代码（用户手动编辑后保存）"""
        db = get_mongo_db()
        existing = await db["alpha_strategies"].find_one(
            {"strategy_id": strategy_id, "user_id": user_id}
        )
        if not existing:
            return None

        # 重新验证新代码
        valid, errors = self._validator.validate(code)
        update = {
            "code": code,
            "status": "validated" if valid else "error",
            "validation_errors": errors,
            "updated_at": datetime.utcnow(),
        }
        if valid:
            update["parameters"] = self._extract_parameters(code)

        await db["alpha_strategies"].update_one(
            {"strategy_id": strategy_id}, {"$set": update}
        )
        doc = await db["alpha_strategies"].find_one({"strategy_id": strategy_id})
        return self._serialize_doc(doc) if doc else None

    async def validate_strategy(
        self, strategy_id: str, user_id: str
    ) -> tuple[bool, list[str]]:
        """重新验证策略代码安全性"""
        db = get_mongo_db()
        doc = await db["alpha_strategies"].find_one(
            {"strategy_id": strategy_id, "user_id": user_id}
        )
        if not doc or not doc.get("code"):
            return False, ["策略不存在或代码为空"]

        valid, errors = self._validator.validate(doc["code"])
        await db["alpha_strategies"].update_one(
            {"strategy_id": strategy_id},
            {"$set": {"status": "validated" if valid else "error", "validation_errors": errors, "updated_at": datetime.utcnow()}},
        )
        return valid, errors

    def get_validator(self) -> CodeValidator:
        """获取代码验证器实例"""
        return self._validator

    # ---- 工具方法 ----

    @staticmethod
    def _serialize_doc(doc: dict) -> dict:
        """递归序列化 MongoDB 文档（ObjectId → str, datetime → isoformat）"""
        if not doc:
            return doc
        result = {}
        for k, v in doc.items():
            if isinstance(v, ObjectId):
                result[k] = str(v)
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, list):
                result[k] = [
                    StrategyGeneratorService._serialize_doc(i) if isinstance(i, dict) else str(i) if isinstance(i, ObjectId) else i
                    for i in v
                ]
            elif isinstance(v, dict):
                result[k] = StrategyGeneratorService._serialize_doc(v)
            else:
                result[k] = v
        return result

    @staticmethod
    def _extract_code(text: str) -> str:
        """从 LLM 返回的 markdown 中提取纯 Python 代码

        依次尝试：
        1. ```python ... ``` 代码块
        2. ``` ... ``` 代码块
        3. 找 class 关键字开始的位置
        """
        if "```python" in text:
            start = text.index("```python") + 9
            end = text.index("```", start) if "```" in text[start:] else len(text)
            return text[start:end].strip()
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            return text[start:end].strip()
        if "class " in text and "AlphaStrategy" in text:
            start = text.index("class ")
            return text[start:].strip()
        return ""

    @staticmethod
    def _extract_class_name(code: str) -> str:
        """从策略代码中提取类名（如 "MACDStrategy"）"""
        import re
        match = re.search(r"class\s+(\w+)\s*\(.*?AlphaStrategy", code)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_parameters(code: str) -> dict:
        """从策略代码中提取类属性作为策略参数

        例如代码中有 lookback = 20, threshold = 0.02，
        会被提取为 {"lookback": 20, "threshold": 0.02}
        """
        import re
        params = {}
        for match in re.finditer(r"(\w+)\s*=\s*([\d.]+|['\"].*?['\"])", code):
            name, value = match.group(1), match.group(2)
            if name not in ("self", "super", "True", "False", "None"):
                try:
                    params[name] = eval(value)
                except Exception:
                    params[name] = value
        return params

    @staticmethod
    def _extract_description(code: str, reports: dict) -> str:
        """根据交易决策生成策略描述"""
        decision = reports.get("trade_decision", {})
        action = decision.get("action", "")
        reasoning = decision.get("reasoning", "")
        if action or reasoning:
            return f"基于分析决策({action})生成的量化策略"
        return "LLM 自动生成的量化交易策略"


# ---- 单例模式 ----
_strategy_generator: Optional[StrategyGeneratorService] = None


def get_strategy_generator_service() -> StrategyGeneratorService:
    """获取策略生成服务单例"""
    global _strategy_generator
    if _strategy_generator is None:
        _strategy_generator = StrategyGeneratorService()
    return _strategy_generator
