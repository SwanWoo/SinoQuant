"""
模拟交易策略引擎适配器

职责：实现 vnpy BacktestingEngine 的核心接口，让策略代码能在模拟交易环境中运行。

关键区别：
  - 回测模式下，策略通过 BacktestingEngine 发单 → 引擎撮合 → 生成成交
  - 模拟模式下，策略通过本适配器发单 → 直接以当前收盘价模拟成交 → 更新 SimulationContext

A股交易费用说明：
  - 买入佣金：0.03%（long_rate）
  - 卖出佣金：0.13%（short_rate，含印花税 0.1%）
"""

import logging

logger = logging.getLogger(__name__)


class SimulationStrategyEngine:
    """模拟交易策略引擎适配器

    策略代码调用 self.buy()/self.sell() 时，vnpy 内部会调用 strategy_engine.send_order()。
    本适配器重写了 send_order()，不做撮合，直接以当前K线收盘价成交，
    并更新 SimulationContext 的资金和持仓。
    """

    def __init__(self, context, current_bars: dict):
        """
        Args:
            context: SimulationContext 实例，管理资金、持仓等状态
            current_bars: 当前K线行情 {vt_symbol: BarData}，用于确定成交价格
        """
        self.context = context
        self.current_bars = current_bars
        self._order_count = 0  # 订单计数器，用于生成唯一订单 ID

    def send_order(self, strategy, vt_symbol, direction, offset, price, volume):
        """模拟发送订单 — 以当前K线收盘价立即成交

        这是策略调 self.buy()/self.sell() 时 vnpy 底层调用的方法。
        模拟模式下不做撮合，直接按收盘价成交。

        Args:
            strategy: 策略实例
            vt_symbol: 合约代码（如 "002230.SZSE"）
            direction: 买卖方向（Direction.LONG / Direction.SHORT）
            offset: 开平标志（Offset.OPEN / Offset.CLOSE）
            price: 委托价格（模拟模式下忽略，用收盘价）
            volume: 委托数量

        Returns:
            订单 ID 列表
        """
        from vnpy.trader.constant import Direction

        self._order_count += 1
        orderid = f"SIM.{self._order_count}"

        # 获取当前行情，确定成交价
        bar = self.current_bars.get(vt_symbol)
        if not bar:
            return []  # 没有行情数据，无法成交

        fill_price = bar.close_price   # 以收盘价成交
        fill_volume = volume

        trade_info = {
            "orderid": orderid,
            "vt_symbol": vt_symbol,
            "direction": direction.value if hasattr(direction, "value") else str(direction),
            "offset": offset.value if hasattr(offset, "value") else str(offset),
            "order_price": price,           # 策略委托价
            "fill_price": fill_price,       # 实际成交价（收盘价）
            "volume": fill_volume,
            "timestamp": bar.datetime.isoformat() if hasattr(bar.datetime, "isoformat") else str(bar.datetime),
        }

        # ---- 计算手续费和更新资金/持仓 ----
        from vnpy.trader.constant import Direction as Dir
        from app.services.alpha.data_bridge import A_STOCK_DEFAULT_CONTRACT

        size = 1
        rates = A_STOCK_DEFAULT_CONTRACT  # {long_rate: 0.0003, short_rate: 0.0013}
        turnover = fill_price * fill_volume * size  # 成交金额

        if direction == Dir.LONG:
            # 买入：扣减现金 + 佣金，增加持仓
            commission = turnover * rates["long_rate"]       # 买入佣金 0.03%
            self.context.cash -= turnover + commission       # 现金减少（买股票 + 佣金）
            self.context.positions[vt_symbol] = self.context.positions.get(vt_symbol, {"quantity": 0, "avg_cost": 0})
            pos = self.context.positions[vt_symbol]
            # 计算新的持仓均价（加权平均）
            total_cost = pos["avg_cost"] * pos["quantity"] + fill_price * fill_volume
            pos["quantity"] += fill_volume
            pos["avg_cost"] = total_cost / pos["quantity"] if pos["quantity"] > 0 else 0
        else:
            # 卖出：增加现金 - 佣金，减少持仓，计算已实现盈亏
            commission = turnover * rates["short_rate"]      # 卖出佣金 0.13%（含印花税）
            self.context.cash += turnover - commission       # 现金增加（卖股票 - 佣金）
            if vt_symbol in self.context.positions:
                pos = self.context.positions[vt_symbol]
                pnl = (fill_price - pos["avg_cost"]) * fill_volume  # 本次卖出盈亏
                self.context.realized_pnl += pnl              # 累计已实现盈亏
                pos["quantity"] -= fill_volume
                if pos["quantity"] <= 0:
                    del self.context.positions[vt_symbol]     # 清仓后移除持仓

        # 记录委托和成交
        self.context.orders.append(trade_info)
        self.context.trades.append({**trade_info, "commission": commission})
        self.context.total_commission += commission

        return [orderid]

    def cancel_order(self, strategy, vt_orderid):
        """取消订单（模拟模式下不需要实现，因为都是即时成交）"""
        pass

    def get_signal(self):
        """获取信号 DataFrame（模拟模式下不需要预生成信号，策略自行计算）"""
        import polars as pl
        return pl.DataFrame()

    def write_log(self, msg, strategy=None):
        """策略写日志"""
        logger.info(f"[模拟交易] {msg}")

    def get_cash_available(self) -> float:
        """获取可用资金"""
        return self.context.cash

    def get_holding_value(self) -> float:
        """获取持仓市值（按当前收盘价计算）"""
        total = 0
        for vt_symbol, pos_info in self.context.positions.items():
            bar = self.current_bars.get(vt_symbol)
            if bar:
                total += bar.close_price * pos_info["quantity"]
        return total
