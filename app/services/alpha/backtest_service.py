"""
vnpy 回测执行服务

职责：将用户提交的量化策略代码，放到 vnpy BacktestingEngine 中跑历史回测，
     并将回测结果（统计指标、每日盈亏、成交记录、委托记录）写入 MongoDB。

关键约束：
  - vnpy 的 BacktestingEngine 是同步阻塞的，不能直接在 async 函数中调用，
    因此用 ThreadPoolExecutor 把回测任务丢到后台线程跑。
  - 线程内部必须用同步版 pymongo（MongoClient），不能用 motor，
    因为 motor 依赖 asyncio 事件循环，而线程里没有事件循环。
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from bson import ObjectId

logger = logging.getLogger(__name__)


def _serialize_doc(doc: dict) -> dict:
    """递归序列化 MongoDB 文档，把 ObjectId / datetime 转成字符串。

    MongoDB 返回的文档里常包含 ObjectId 和 datetime，
    FastAPI 的 jsonable_encoder 处理不了这两种类型，必须手动转。
    """
    if not doc:
        return doc
    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)                # ObjectId → 字符串
        elif isinstance(v, datetime):
            result[k] = v.isoformat()         # datetime → ISO 格式字符串
        elif isinstance(v, list):
            result[k] = [
                _serialize_doc(i) if isinstance(i, dict)
                else str(i) if isinstance(i, ObjectId)
                else i
                for i in v
            ]
        elif isinstance(v, dict):
            result[k] = _serialize_doc(v)     # 递归处理嵌套 dict
        else:
            result[k] = v
    return result


class BacktestService:

    def __init__(self):
        self._lab = None
        self._lab_path = "data/alpha_lab"              # AlphaLab 数据目录
        self._thread_pool = ThreadPoolExecutor(max_workers=2)  # 最多同时跑 2 个回测

    @property
    def lab(self):
        """懒加载 vnpy AlphaLab 实例（用于管理回测数据和策略文件）"""
        if self._lab is None:
            from vnpy.alpha import AlphaLab
            self._lab = AlphaLab(self._lab_path)
        return self._lab

    async def run_backtest(
        self,
        strategy_id: str,          # 策略 ID（关联 alpha_strategies 集合）
        strategy_code: str,        # 策略源代码（Python 字符串）
        symbols: list[str],        # 股票代码列表，如 ["002230", "600519"]
        start_date: str,           # 回测起始日期，格式 "YYYY-MM-DD"
        end_date: str,             # 回测结束日期，格式 "YYYY-MM-DD"
        user_id: str,              # 用户 ID
        capital: float = 1_000_000,  # 初始资金，默认 100 万
        strategy_params: Optional[dict] = None,  # 策略参数（传给策略类的 setting）
    ) -> dict:
        """提交回测任务（非阻塞，在线程池中执行同步的 vnpy 引擎）

        流程：
        1. 在 MongoDB 中创建回测记录（状态=running）
        2. 异步准备历史行情数据（从 MongoDB 下载到 AlphaLab 的 parquet 文件）
        3. 提交到线程池执行回测
        4. 回测完成后更新 MongoDB 记录（状态=completed/failed）
        """
        from app.core.database import get_mongo_db
        from .code_validator import CodeValidator
        from .data_bridge import AlphaDataBridge

        db = get_mongo_db()
        backtest_id = f"bt_{uuid.uuid4().hex[:12]}"   # 生成唯一回测 ID
        now = datetime.utcnow()

        # ---- 第1步：在数据库中创建回测记录 ----
        doc = {
            "backtest_id": backtest_id,
            "strategy_id": strategy_id,
            "user_id": user_id,
            "status": "running",                       # 初始状态：运行中
            "parameters": {
                "symbols": symbols,
                "start_date": start_date,
                "end_date": end_date,
                "capital": capital,
                "strategy_params": strategy_params or {},
            },
            "statistics": None,                        # 回测统计指标（完成后填充）
            "daily_pnl": [],                           # 每日盈亏曲线
            "trades": [],                              # 成交记录
            "orders": [],                              # 委托记录
            "logs": [],                                # 引擎日志
            "error_message": None,                     # 错误信息
            "started_at": now,
            "completed_at": None,
            "duration_seconds": 0,
        }
        await db["alpha_backtests"].insert_one(doc)

        # ---- 第2步：转换股票代码格式 ----
        # 数据库里是 "002230"，vnpy 需要 "002230.SZ" 格式
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        vt_symbols = [AlphaDataBridge.code_to_vt_symbol(s) for s in symbols]

        # ---- 第3步：异步准备回测数据 ----
        # 从 MongoDB 的 stock_daily_quotes 集合读取历史行情，
        # 转换为 vnpy BarData 格式，写入 AlphaLab 的 parquet 文件
        # 这一步必须在 async 上下文中完成（用 motor 访问 MongoDB）
        try:
            bridge = AlphaDataBridge(self.lab)
            await bridge.prepare_backtest_data(vt_symbols, db, start_dt, end_dt)
        except Exception as e:
            logger.warning(f"数据准备失败（将依赖已有数据）: {e}")

        # ---- 第4步：定义在线程池中执行的回测函数 ----
        def _run():
            try:
                import polars as pl

                # 4a. 沙箱执行策略代码，拿到策略类
                validator = CodeValidator()
                strategy_class = validator.execute_strategy_class(strategy_code, timeout=30)

                from vnpy.trader.constant import Interval
                from vnpy.alpha import BacktestingEngine

                # 4b. 创建回测引擎并设置参数
                engine = BacktestingEngine(self.lab)
                engine.set_parameters(
                    vt_symbols=vt_symbols,              # 交易标的列表
                    interval=Interval.DAILY,            # 日线级别回测
                    start=start_dt,                     # 起始日期
                    end=end_dt,                         # 结束日期
                    capital=int(capital),               # 初始资金（vnpy 要求整数）
                )

                # 4c. 创建空的信号 DataFrame
                # vnpy 回测引擎要求传入 signal_df，这里先创建空结构，
                # 实际信号由策略类的 on_bar 方法在回测过程中生成
                signal_df = pl.DataFrame(schema={
                    "datetime": pl.Datetime,
                    "vt_symbol": pl.Utf8,
                    "signal": pl.Float64,
                })

                # 4d. 加载策略并运行回测
                engine.add_strategy(strategy_class, strategy_params or {}, signal_df)
                engine.load_data()                      # 从 parquet 文件加载历史数据
                engine.run_backtesting()                 # 执行回测（逐根K线调用策略的 on_bar）

                # 4e. 计算回测结果
                daily_df = engine.calculate_result()    # 计算每日盈亏 DataFrame
                # 如果没有产生任何交易，calculate_result 返回 None
                if daily_df is None:
                    engine.daily_df = None              # 必须置空，否则后续统计会报错
                statistics = engine.calculate_statistics()  # 计算统计指标（年化收益、最大回撤等）

                # 4f. 序列化每日盈亏数据
                # vnpy 返回的 DataFrame 中包含 numpy 类型（np.int64, np.float64），
                # MongoDB 不能直接存储，必须转为 Python 原生类型
                import numpy as np
                daily_pnl = []
                if daily_df is not None:
                    for row in daily_df.iter_rows(named=True):
                        daily_pnl.append({
                            k: (int(v) if isinstance(v, np.integer)       # np.int64 → int
                                else float(v) if isinstance(v, np.floating)  # np.float64 → float
                                else str(v) if not isinstance(v, (int, float, type(None)))
                                else v)
                            for k, v in row.items()
                        })

                # 4g. 提取成交记录
                trades = []
                for trade in engine.get_all_trades():
                    trades.append({
                        "symbol": trade.vt_symbol,                  # 成交合约（如 002230.SZ）
                        "direction": trade.direction.value if trade.direction else "",  # 买/卖
                        "offset": trade.offset.value if trade.offset else "",            # 开/平
                        "price": trade.price,                       # 成交价格
                        "volume": trade.volume,                     # 成交数量
                        "datetime": str(trade.datetime) if trade.datetime else "",       # 成交时间
                    })

                # 4h. 提取委托记录
                orders = []
                for order in engine.get_all_orders():
                    orders.append({
                        "symbol": order.vt_symbol,                  # 委托合约
                        "direction": order.direction.value if order.direction else "",
                        "offset": order.offset.value if order.offset else "",
                        "price": order.price,                       # 委托价格
                        "volume": order.volume,                     # 委托数量
                        "traded": order.traded,                     # 已成交数量
                        "status": order.status.value,               # 委托状态
                        "datetime": str(order.datetime) if order.datetime else "",
                    })

                # 4i. 序列化统计指标
                # statistics 字典中可能包含 date / numpy 类型 / inf，
                # 都需要转为 JSON/MongoDB 兼容类型
                import math
                from datetime import date as date_type
                import numpy as np
                for key, value in statistics.items():
                    if isinstance(value, date_type):
                        statistics[key] = value.isoformat()         # date → "YYYY-MM-DD"
                    elif isinstance(value, (np.integer,)):
                        statistics[key] = int(value)                # np.int64 → int
                    elif isinstance(value, (np.floating,)):
                        statistics[key] = float(np.nan_to_num(value))  # np.float64 → float，NaN → 0
                    elif isinstance(value, float):
                        if value in (math.inf, -math.inf):
                            statistics[key] = 0                     # inf → 0

                completed = datetime.utcnow()
                duration = (completed - now).total_seconds()

                # 4j. 构建更新数据
                update = {
                    "status": "completed",
                    "statistics": statistics,       # 回测统计指标
                    "daily_pnl": daily_pnl,         # 每日盈亏曲线
                    "trades": trades,                # 成交记录
                    "orders": orders,                # 委托记录
                    "logs": engine.logs[:100],       # 引擎日志（最多保留100条）
                    "completed_at": completed,
                    "duration_seconds": duration,
                }

                # 4k. 更新 MongoDB 记录
                # 注意：线程内部必须用同步 pymongo（MongoClient），不能用 motor
                from pymongo import MongoClient
                from app.core.config import settings
                client = MongoClient(settings.MONGO_URI)
                client[settings.MONGO_DB]["alpha_backtests"].update_one(
                    {"backtest_id": backtest_id}, {"$set": update}
                )
                client.close()

                return {**doc, **update}

            except Exception as e:
                logger.error(f"回测执行失败: {e}", exc_info=True)
                error_update = {
                    "status": "failed",
                    "error_message": str(e),
                    "completed_at": datetime.utcnow(),
                    "duration_seconds": (datetime.utcnow() - now).total_seconds(),
                }
                # 失败也要更新数据库记录
                from pymongo import MongoClient
                from app.core.config import settings
                client = MongoClient(settings.MONGO_URI)
                client[settings.MONGO_DB]["alpha_backtests"].update_one(
                    {"backtest_id": backtest_id}, {"$set": error_update}
                )
                client.close()
                return {**doc, **error_update}

        # ---- 第5步：提交到线程池（非阻塞，立即返回） ----
        self._thread_pool.submit(_run)
        return _serialize_doc(doc)

    async def get_backtest_result(self, backtest_id: str) -> Optional[dict]:
        """查询单次回测结果"""
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        doc = await db["alpha_backtests"].find_one({"backtest_id": backtest_id})
        return _serialize_doc(doc) if doc else None

    async def list_backtests(
        self,
        user_id: str,
        strategy_id: Optional[str] = None,   # 可选，按策略过滤
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]:
        """查询用户的回测历史列表（按时间倒序）"""
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        query = {"user_id": user_id}
        if strategy_id:
            query["strategy_id"] = strategy_id
        cursor = (
            db["alpha_backtests"]
            .find(query)
            .sort("started_at", -1)           # 最新的排在前面
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_serialize_doc(d) for d in docs]


# ---- 单例模式 ----
_backtest_service: Optional[BacktestService] = None


def get_backtest_service() -> BacktestService:
    """获取回测服务单例（全局只创建一个实例，复用线程池）"""
    global _backtest_service
    if _backtest_service is None:
        _backtest_service = BacktestService()
    return _backtest_service
