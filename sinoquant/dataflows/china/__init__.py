"""
中国A股数据模块

将 OptimizedChinaDataProvider 拆分为多个 mixin 子模块：
- _financial_data_parser: 财务数据解析
- _financial_scoring: 评分与分析
- _fundamentals_report: 基本面报告生成
- _financial_cache: 缓存方法
"""

from ..optimized_china_data import (
    OptimizedChinaDataProvider,
    get_optimized_china_data_provider,
    get_china_stock_data_cached,
    get_china_fundamentals_cached,
)

__all__ = [
    "OptimizedChinaDataProvider",
    "get_optimized_china_data_provider",
    "get_china_stock_data_cached",
    "get_china_fundamentals_cached",
]
