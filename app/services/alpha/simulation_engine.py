"""
实时模拟交易引擎

职责：管理所有活跃的模拟交易实例，定期从 MongoDB 获取最新行情，驱动策略执行。

与回测的区别：
  - 回测：一次性用历史数据跑完，秒级出结果
  - 模拟：持续运行，每个交易日收盘后获取最新K线，像实盘一样逐日推进

架构：
  SimulationEngine（引擎，管理多个实例）
    └── SimulationContext（单个模拟交易的运行状态：持仓、资金、订单等）
          └── SimulationStrategyEngine（适配器，桥接 vnpy 策略和 SimulationContext）
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .simulation_adapter import SimulationStrategyEngine

logger = logging.getLogger(__name__)


class SimulationStatus(str, Enum):
    """模拟交易状态枚举"""
    STOPPED = "stopped"   # 已停止
    RUNNING = "running"   # 运行中
    PAUSED = "paused"     # 已暂停
    ERROR = "error"       # 出错


class SimulationContext:
    """单个模拟交易的运行上下文

    保存一个模拟交易实例的全部运行时状态：
    资金、持仓、订单、成交、盈亏曲线等。
    这个对象在内存中维护，同时定期持久化到 MongoDB。
    """

    def __init__(
        self,
        simulation_id: str,
        strategy_id: str,
        user_id: str,
        symbols: list[str],
        capital: float,
        strategy_params: dict,
    ):
        # 基本信息
        self.simulation_id = simulation_id       # 模拟交易唯一 ID
        self.strategy_id = strategy_id           # 关联的策略 ID
        self.user_id = user_id                   # 所属用户
        self.symbols = symbols                   # 交易标的列表
        self.capital = capital                   # 初始资金
        self.strategy_params = strategy_params or {}  # 策略参数
        self.status = SimulationStatus.RUNNING   # 初始状态：运行中

        # 资金和盈亏
        self.cash = capital                      # 可用现金（随买卖变化）
        self.realized_pnl = 0.0                  # 已实现盈亏（平仓时计算）
        self.total_commission = 0.0              # 累计手续费

        # 持仓、订单、成交、盈亏历史
        self.positions: dict[str, dict] = {}     # 持仓 {vt_symbol: {quantity, avg_cost, market_value}}
        self.orders: list[dict] = []             # 委托记录
        self.trades: list[dict] = []             # 成交记录
        self.pnl_history: list[dict] = []        # 每日盈亏曲线

        # 运行状态
        self.strategy = None                     # vnpy 策略实例
        self.started_at = datetime.utcnow()      # 启动时间
        self.last_update = datetime.utcnow()     # 最近更新时间
        self.last_bar_date: Optional[str] = None # 最近处理的K线日期（防止重复处理）
        self.error_message: Optional[str] = None # 错误信息

    def get_portfolio_value(self) -> float:
        """计算总资产 = 现金 + 持仓市值"""
        positions_value = sum(
            p.get("market_value", p["avg_cost"] * p["quantity"])
            for p in self.positions.values()
        )
        return self.cash + positions_value

    def get_total_pnl(self) -> float:
        """计算总盈亏 = 已实现盈亏 + 浮动盈亏"""
        return self.realized_pnl + (self.get_portfolio_value() - self.capital)

    def to_dict(self) -> dict:
        """序列化为前端可用的字典（用于 API 返回和 MongoDB 存储）"""
        return {
            "simulation_id": self.simulation_id,
            "strategy_id": self.strategy_id,
            "symbols": self.symbols,
            "capital": self.capital,
            "status": self.status.value,
            "current_cash": round(self.cash, 2),
            "positions_value": round(sum(
                p.get("market_value", p["avg_cost"] * p["quantity"])
                for p in self.positions.values()
            ), 2),
            "total_pnl": round(self.get_total_pnl(), 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "trade_count": len(self.trades),
            "started_at": self.started_at.isoformat(),
            "last_update": self.last_update.isoformat(),
            "error_message": self.error_message,
        }


class SimulationEngine:
    """模拟交易引擎 — 管理所有活跃的 SimulationContext 实例

    核心流程：
    1. start_simulation() — 沙箱验证策略代码 → 创建 SimulationContext → 初始化策略
    2. _run_initial_cycle() — 获取最新K线，驱动策略执行一次
    3. _periodic_update() — 创建 asyncio 后台任务，每 60 秒检查一次：
       - 如果在交易时间 → 跳过（等收盘后再处理）
       - 如果非交易时间且有新K线 → 驱动策略执行 → 持久化状态
    """

    def __init__(self):
        self._simulations: dict[str, SimulationContext] = {}  # 活跃的模拟实例 {id: context}
        self._lock = asyncio.Lock()       # 防止并发修改 _simulations
        self._running = False

    async def start_simulation(
        self,
        strategy_id: str,
        strategy_code: str,
        user_id: str,
        symbols: list[str],
        capital: float = 1_000_000,
        strategy_params: Optional[dict] = None,
    ) -> SimulationContext:
        """启动一个模拟交易实例

        步骤：
        1. 用 CodeValidator 沙箱执行策略代码，获取策略类
        2. 创建 SimulationContext（资金、持仓等初始状态）
        3. 转换股票代码格式 → 创建 SimulationStrategyEngine 适配器
        4. 实例化策略并调用 on_init() 初始化
        5. 获取最新K线驱动策略执行一次
        6. 启动后台定时任务持续更新
        """
        from .code_validator import CodeValidator
        from .data_bridge import AlphaDataBridge

        # 1. 沙箱执行策略代码，拿到策略类（超时30秒）
        strategy_class = CodeValidator().execute_strategy_class(strategy_code, timeout=30)

        # 2. 创建运行上下文
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        ctx = SimulationContext(
            simulation_id=simulation_id,
            strategy_id=strategy_id,
            user_id=user_id,
            symbols=symbols,
            capital=capital,
            strategy_params=strategy_params or {},
        )

        # 3. 转换股票代码：["002230"] → ["002230.SZSE"]
        vt_symbols = [AlphaDataBridge.code_to_vt_symbol(s) for s in symbols]

        # 4. 创建适配器并实例化策略
        # SimulationStrategyEngine 替代 vnpy 的 BacktestingEngine，
        # 把策略的买卖指令重定向到 SimulationContext 的持仓管理
        adapter = SimulationStrategyEngine(ctx, {})
        strategy = strategy_class(adapter, strategy_class.__name__, vt_symbols, ctx.strategy_params)
        strategy.on_init()  # 调用策略的初始化方法

        ctx.strategy = strategy

        # 注册到引擎的实例字典
        async with self._lock:
            self._simulations[simulation_id] = ctx

        logger.info(f"模拟交易启动: {simulation_id}, 策略: {strategy_id}, 标的: {symbols}")

        try:
            # 5. 立即获取最新K线，驱动策略执行一次
            await self._run_initial_cycle(ctx, vt_symbols)
            # 6. 启动后台定时任务（每60秒检查一次是否有新K线）
            asyncio.create_task(self._periodic_update(ctx, vt_symbols))
        except Exception as e:
            ctx.status = SimulationStatus.ERROR
            ctx.error_message = str(e)
            logger.error(f"模拟交易初始化失败: {e}")

        return ctx

    async def _run_initial_cycle(self, ctx: SimulationContext, vt_symbols: list[str]):
        """启动时获取最近一根K线并驱动策略执行

        让策略在启动时就能基于最新行情做出交易决策，
        而不是等到下一个交易日收盘。
        """
        bars = await self._fetch_latest_bars(vt_symbols)
        if bars:
            # 更新适配器中的当前行情，驱动策略处理
            adapter = SimulationStrategyEngine(ctx, bars)
            ctx.strategy.strategy_engine = adapter
            ctx.strategy.on_bars(bars)          # 调用策略的K线处理方法
            self._record_pnl(ctx, bars)         # 记录当日盈亏

    async def _periodic_update(self, ctx: SimulationContext, vt_symbols: list[str]):
        """后台定时任务：每60秒检查一次，非交易时间且有新K线时驱动策略

        逻辑：
        - 交易时间（9:30-15:00）不执行，等收盘后再处理
        - 同一天的K线只处理一次（通过 last_bar_date 去重）
        - 处理完新K线后持久化状态到 MongoDB
        """
        from app.utils.trading_time import is_trading_time

        while ctx.status == SimulationStatus.RUNNING:
            await asyncio.sleep(60)  # 每60秒检查一次

            if ctx.status != SimulationStatus.RUNNING:
                break

            # 交易时间内跳过，等收盘后再处理
            if is_trading_time():
                continue

            now = datetime.utcnow()
            today_str = now.strftime("%Y-%m-%d")
            # 今天已经处理过，跳过
            if ctx.last_bar_date == today_str:
                continue

            try:
                # 从 MongoDB 获取最新K线
                bars = await self._fetch_latest_bars(vt_symbols)
                if bars:
                    # 检查K线日期是否更新（可能还是昨天的数据）
                    bar_date = list(bars.values())[0].datetime.strftime("%Y-%m-%d") if bars else None
                    if bar_date == ctx.last_bar_date:
                        continue

                    # 驱动策略执行
                    adapter = SimulationStrategyEngine(ctx, bars)
                    ctx.strategy.strategy_engine = adapter
                    ctx.strategy.on_bars(bars)

                    # 记录盈亏并持久化
                    self._record_pnl(ctx, bars)
                    ctx.last_bar_date = bar_date
                    ctx.last_update = datetime.utcnow()

                    await self._persist_state(ctx)

            except Exception as e:
                logger.error(f"模拟交易更新失败 ({ctx.simulation_id}): {e}")
                ctx.error_message = str(e)

    async def _fetch_latest_bars(self, vt_symbols: list[str]) -> dict:
        """从 MongoDB 的 stock_daily_quotes 集合获取每只股票最新一根K线

        Returns:
            {vt_symbol: BarData} 字典
        """
        from app.core.database import get_mongo_db
        from app.services.alpha.data_bridge import AlphaDataBridge
        from vnpy.trader.object import BarData
        from vnpy.trader.constant import Interval

        db = get_mongo_db()
        bars = {}

        for vt_symbol in vt_symbols:
            code6 = AlphaDataBridge.vt_symbol_to_code(vt_symbol)  # "002230.SZSE" → "002230"
            exchange = AlphaDataBridge.code_to_exchange(code6)     # → Exchange.SZSE

            # 查询最新一条行情（按 trade_date 降序）
            row = await db["stock_daily_quotes"].find_one(
                {"symbol": code6},
                sort=[("trade_date", -1)],
            )

            if row:
                bars[vt_symbol] = AlphaDataBridge.df_row_to_bar_data(row, exchange)

        return bars

    def _record_pnl(self, ctx: SimulationContext, bars: dict):
        """记录当日盈亏快照到 pnl_history

        daily_pnl = 今日总资产 - 昨日总资产
        """
        portfolio_value = ctx.get_portfolio_value()
        entry = {
            "date": list(bars.values())[0].datetime.strftime("%Y-%m-%d") if bars else "",
            "cash": round(ctx.cash, 2),
            "positions_value": round(portfolio_value - ctx.cash, 2),
            "total_value": round(portfolio_value, 2),
            "daily_pnl": 0,
            "total_pnl": round(ctx.get_total_pnl(), 2),
        }

        # 如果有前一天的记录，计算日盈亏
        if ctx.pnl_history:
            prev = ctx.pnl_history[-1]["total_value"]
            entry["daily_pnl"] = round(portfolio_value - prev, 2)

        ctx.pnl_history.append(entry)

    async def _persist_state(self, ctx: SimulationContext):
        """将模拟交易状态持久化到 MongoDB

        写入两个集合：
        - alpha_simulations: 模拟交易的整体状态
        - alpha_sim_pnl: 每日盈亏记录（按日期 upsert，避免重复）
        """
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()

            # 更新模拟交易状态
            await db["alpha_simulations"].update_one(
                {"simulation_id": ctx.simulation_id},
                {"$set": ctx.to_dict()},
                upsert=True,  # 不存在则插入
            )

            # 更新当日盈亏记录
            if ctx.pnl_history:
                last = ctx.pnl_history[-1]
                await db["alpha_sim_pnl"].update_one(
                    {"simulation_id": ctx.simulation_id, "date": last["date"]},
                    {"$set": last},
                    upsert=True,
                )
        except Exception as e:
            logger.error(f"持久化模拟状态失败: {e}")

    async def stop_simulation(self, simulation_id: str, user_id: str) -> bool:
        """停止模拟交易（从内存中移除，状态持久化为 stopped）"""
        async with self._lock:
            ctx = self._simulations.get(simulation_id)
            if not ctx or ctx.user_id != user_id:
                return False
            ctx.status = SimulationStatus.STOPPED
            await self._persist_state(ctx)
            del self._simulations[simulation_id]
            return True

    async def pause_simulation(self, simulation_id: str, user_id: str) -> bool:
        """暂停模拟交易（保留在内存中，但不执行策略）"""
        ctx = self._simulations.get(simulation_id)
        if not ctx or ctx.user_id != user_id:
            return False
        ctx.status = SimulationStatus.PAUSED
        await self._persist_state(ctx)
        return True

    async def get_simulation(self, simulation_id: str, user_id: str) -> Optional[dict]:
        """查询单个模拟交易状态"""
        ctx = self._simulations.get(simulation_id)
        if not ctx or ctx.user_id != user_id:
            return None
        return ctx.to_dict()

    async def list_simulations(self, user_id: str) -> list[dict]:
        """查询用户所有活跃的模拟交易"""
        return [
            ctx.to_dict()
            for ctx in self._simulations.values()
            if ctx.user_id == user_id
        ]

    async def get_positions(self, simulation_id: str, user_id: str) -> list[dict]:
        """查询模拟交易的持仓列表"""
        ctx = self._simulations.get(simulation_id)
        if not ctx or ctx.user_id != user_id:
            return []
        return [
            {"vt_symbol": vs, **pos}
            for vs, pos in ctx.positions.items()
        ]

    async def get_orders(self, simulation_id: str, user_id: str, limit: int = 50) -> list[dict]:
        """查询模拟交易的委托记录（最近N条）"""
        ctx = self._simulations.get(simulation_id)
        if not ctx or ctx.user_id != user_id:
            return []
        return ctx.orders[-limit:]

    async def get_pnl_history(self, simulation_id: str, user_id: str) -> list[dict]:
        """查询模拟交易的每日盈亏曲线"""
        ctx = self._simulations.get(simulation_id)
        if not ctx or ctx.user_id != user_id:
            return []
        return ctx.pnl_history


# ---- 单例模式 ----
_simulation_engine: Optional[SimulationEngine] = None


def get_simulation_engine() -> SimulationEngine:
    """获取模拟交易引擎单例"""
    global _simulation_engine
    if _simulation_engine is None:
        _simulation_engine = SimulationEngine()
    return _simulation_engine
