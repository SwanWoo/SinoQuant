"""
source — 数据源管理器的拆分子模块

提供以下 mixin:
  - CacheMixin            缓存读写
  - SourceSelectionMixin   数据源选择、优先级、适配器获取
  - StockDataMixin         股票行情数据
  - StockInfoMixin         股票基本信息
  - FundamentalsMixin      基本面数据
  - NewsMixin              新闻数据

以及重构后的 DataSourceManager（在 data_source_manager.py 中定义）。
"""

from sinoquant.dataflows.source._cache import CacheMixin
from sinoquant.dataflows.source._source_selection import SourceSelectionMixin
from sinoquant.dataflows.source._stock_data import StockDataMixin
from sinoquant.dataflows.source._stock_info import StockInfoMixin
from sinoquant.dataflows.source._fundamentals import FundamentalsMixin
from sinoquant.dataflows.source._news import NewsMixin

__all__ = [
    "CacheMixin",
    "SourceSelectionMixin",
    "StockDataMixin",
    "StockInfoMixin",
    "FundamentalsMixin",
    "NewsMixin",
]
