"""
数据桥接服务：将 MongoDB 行情数据转换为 vnpy AlphaLab 格式

职责：在两个数据世界之间做转换——
  - MongoDB 里的 stock_daily_quotes 集合（项目自己的行情存储）
  - vnpy AlphaLab 的 parquet 文件（vnpy 回测引擎读取的格式）

核心方法：
  - df_row_to_bar_data(): MongoDB 文档 → vnpy BarData 对象
  - sync_stock_to_lab(): 从 MongoDB 批量读取 → 转换 → 写入 AlphaLab
  - prepare_backtest_data(): 回测前确保所有标的数据已就绪

A股交易所判断规则：
  - 0/3 开头 → 深交所（SZSE）：主板、创业板
  - 6 开头 → 上交所（SSE）：主板、科创板

日期格式注意：
  MongoDB 中 trade_date 格式为 "YYYYMMDD"（无连字符），如 "20260520"
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _import_vnpy():
    """延迟导入 vnpy 模块（避免启动时依赖 vnpy）"""
    from vnpy.trader.object import BarData
    from vnpy.trader.constant import Exchange, Interval
    return BarData, Exchange, Interval


# ---- A股代码前缀 → 交易所映射 ----
SZSE_PREFIXES = ("0", "3")      # 深交所：0=主板，3=创业板
SSE_PREFIXES = ("6", "688")     # 上交所：6=主板，688=科创板

# ---- A股默认合约参数（手续费、最小变动价位等）----
A_STOCK_DEFAULT_CONTRACT = {
    "long_rate": 0.0003,    # 买入佣金率 0.03%
    "short_rate": 0.0013,   # 卖出佣金率 0.13%（含印花税 0.1%）
    "size": 1,              # 合约乘数（A股为1）
    "pricetick": 0.01,      # 最小变动价位 1 分钱
}


class AlphaDataBridge:

    def __init__(self, lab):
        """
        Args:
            lab: vnpy AlphaLab 实例，管理回测数据文件和策略配置
        """
        self.lab = lab

    @staticmethod
    def code_to_exchange(code6: str):  # -> Exchange
        """6位股票代码 → vnpy Exchange 枚举

        "002230" → Exchange.SZSE（深交所）
        "600519" → Exchange.SSE（上交所）
        """
        _, Exchange, _ = _import_vnpy()
        if code6.startswith(SZSE_PREFIXES):
            return Exchange.SZSE
        return Exchange.SSE

    @staticmethod
    def code_to_vt_symbol(code6: str) -> str:
        """6位股票代码 → vnpy 合约代码

        "002230" → "002230.SZSE"
        "600519" → "600519.SSE"
        """
        code6 = code6.zfill(6)  # 补零到6位（如 "1234" → "001234"）
        exchange = AlphaDataBridge.code_to_exchange(code6)
        return f"{code6}.{exchange.value}"

    @staticmethod
    def vt_symbol_to_code(vt_symbol: str) -> str:
        """vnpy 合约代码 → 6位股票代码（反向转换）

        "002230.SZSE" → "002230"
        """
        return vt_symbol.split(".")[0]

    @staticmethod
    def df_row_to_bar_data(row: dict, exchange):  # exchange: Exchange, -> BarData
        """MongoDB 文档（单行行情） → vnpy BarData 对象

        这是最核心的转换方法，把 MongoDB 中的行情记录转为 vnpy 引擎能识别的 K线对象。

        MongoDB 行情文档格式示例：
        {
            "symbol": "002230",
            "trade_date": "20260520",    # 注意：YYYYMMDD 格式，无连字符
            "open": 48.88,
            "high": 48.94,
            "low": 47.88,
            "close": 48.02,
            "volume": 268276,            # 注意：字段名是 volume，不是 vol
            "amount": 129707083.76
        }
        """
        BarData, _, Interval = _import_vnpy()
        code6 = str(row.get("symbol", "")).zfill(6)

        # 处理日期：MongoDB 中可能是 "20260520" 或 "2026-05-20" 格式
        trade_date = row.get("trade_date") or row.get("date")
        if isinstance(trade_date, str):
            s = trade_date.replace("-", "")[:8]  # 去掉连字符，取前8位
            dt = datetime.strptime(s, "%Y%m%d")
        else:
            dt = trade_date  # 可能已经是 datetime 对象

        return BarData(
            symbol=code6,                                  # 股票代码
            exchange=exchange,                             # 交易所
            datetime=dt,                                   # K线日期
            interval=Interval.DAILY,                       # 日线
            open_price=float(row.get("open", 0) or 0),    # 开盘价
            high_price=float(row.get("high", 0) or 0),    # 最高价
            low_price=float(row.get("low", 0) or 0),      # 最低价
            close_price=float(row.get("close", 0) or 0),  # 收盘价
            volume=float(row.get("volume", row.get("vol", 0)) or 0),  # 成交量（兼容 vol 字段名）
            turnover=float(row.get("amount", 0) or 0),    # 成交额
            open_interest=0,                               # 持仓量（A股无此概念）
            gateway_name="DB",                             # 数据来源标识
        )

    async def sync_stock_to_lab(
        self,
        code6: str,
        db,
        interval=None,  # Interval = Interval.DAILY,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """从 MongoDB 同步单只股票的历史行情到 AlphaLab

        步骤：
        1. 从 stock_daily_quotes 集合查询行情数据
        2. 逐行转为 vnpy BarData 对象
        3. 调用 lab.save_bar_data() 写入 AlphaLab 的 parquet 文件
        4. 如果该合约没有配置手续费参数，自动添加默认配置

        Args:
            code6: 6位股票代码
            db: MongoDB 数据库实例（motor 异步客户端）
            interval: K线周期，默认日线
            start: 起始日期
            end: 结束日期

        Returns:
            同步的K线条数
        """
        if interval is None:
            _, _, Interval = _import_vnpy()
            interval = Interval.DAILY
        vt_symbol = self.code_to_vt_symbol(code6)
        exchange = self.code_to_exchange(code6)

        # 构建查询条件
        query = {"symbol": code6}
        if start or end:
            query["trade_date"] = {}
            if start:
                query["trade_date"]["$gte"] = start.strftime("%Y%m%d")
            if end:
                query["trade_date"]["$lte"] = end.strftime("%Y%m%d")

        # 从 MongoDB 读取行情（按日期升序）
        cursor = db["stock_daily_quotes"].find(query).sort("trade_date", 1)
        rows = await cursor.to_list(length=None)

        if not rows:
            logger.warning(f"未找到 {code6} 的历史数据")
            return 0

        # 转换为 vnpy BarData 并保存到 AlphaLab
        bars = [self.df_row_to_bar_data(r, exchange) for r in rows]
        self.lab.save_bar_data(bars)

        # 确保 AlphaLab 中有该合约的交易参数配置（手续费、最小变动价位等）
        contract = self.lab.load_contract_setttings()  # 注意：AlphaLab 方法名是 double t
        if vt_symbol not in contract:
            self.lab.add_contract_setting(vt_symbol, **A_STOCK_DEFAULT_CONTRACT)

        logger.info(f"同步 {vt_symbol} 完成: {len(bars)} 条K线")
        return len(bars)

    async def sync_stocks_to_lab(
        self,
        codes: list[str],
        db,
        interval=None,  # Interval = Interval.DAILY,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict[str, int]:
        """批量同步多只股票的历史行情

        Args:
            codes: 股票代码列表
            db: MongoDB 数据库实例
            interval: K线周期
            start: 起始日期
            end: 结束日期

        Returns:
            {股票代码: 同步条数} 字典
        """
        if interval is None:
            _, _, Interval = _import_vnpy()
            interval = Interval.DAILY
        results = {}
        for code in codes:
            try:
                count = await self.sync_stock_to_lab(code, db, interval, start, end)
                results[code] = count
            except Exception as e:
                logger.error(f"同步 {code} 失败: {e}")
                results[code] = 0
        return results

    async def prepare_backtest_data(
        self,
        vt_symbols: list[str],
        db,
        start: datetime,
        end: datetime,
    ) -> None:
        """回测前确保所有标的的数据已在 AlphaLab 中

        先检查 AlphaLab 是否已有数据，没有才从 MongoDB 同步，
        避免重复同步浪费时间和带宽。

        Args:
            vt_symbols: vnpy 合约代码列表（如 ["002230.SZSE", "600519.SSE"]）
            db: MongoDB 数据库实例
            start: 回测起始日期
            end: 回测结束日期
        """
        _, _, Interval = _import_vnpy()
        for vt_symbol in vt_symbols:
            code6 = self.vt_symbol_to_code(vt_symbol)
            exchange = self.code_to_exchange(code6)

            # 检查 AlphaLab 中是否已有该标的的数据
            existing = self.lab.load_bar_data(vt_symbol, Interval.DAILY, start, end)
            if existing and len(existing) > 0:
                logger.info(f"{vt_symbol} 已有 {len(existing)} 条数据，跳过同步")
                continue

            # 没有数据，从 MongoDB 同步
            await self.sync_stock_to_lab(code6, db, Interval.DAILY, start, end)


def get_alpha_data_bridge(lab):
    """工厂函数：获取 AlphaDataBridge 实例"""
    return AlphaDataBridge(lab)
