#!/usr/bin/env python3
"""
数据源管理器 (Facade)

统一管理中国股票数据源的选择和切换，支持Tushare、AKShare、BaoStock等。

实际逻辑拆分到 sinoquant.dataflows.source 包中的各个 mixin 模块：
  - _cache.py              缓存读写
  - _source_selection.py   数据源选择、优先级、适配器获取
  - _stock_data.py         股票行情数据
  - _stock_info.py         股票基本信息
  - _fundamentals.py       基本面数据
  - _news.py               新闻数据
"""

import warnings
from enum import Enum
from typing import Dict, List, Optional, Any

import pandas as pd

warnings.filterwarnings("ignore")

# 导入统一日志系统
from sinoquant.utils.logging_init import setup_dataflow_logging

logger = setup_dataflow_logging()

# 导入统一数据源编码
from sinoquant.constants import DataSourceCode


class ChinaDataSource(Enum):
    """
    中国股票数据源枚举

    注意：这个枚举与 sinoquant.constants.DataSourceCode 保持同步
    值使用统一的数据源编码
    """
    MONGODB = DataSourceCode.MONGODB
    TUSHARE = DataSourceCode.TUSHARE
    AKSHARE = DataSourceCode.AKSHARE
    BAOSTOCK = DataSourceCode.BAOSTOCK


# ---------------------------------------------------------------------------
# 导入所有 mixin
# ---------------------------------------------------------------------------
from sinoquant.dataflows.source._cache import CacheMixin
from sinoquant.dataflows.source._source_selection import SourceSelectionMixin
from sinoquant.dataflows.source._stock_data import StockDataMixin
from sinoquant.dataflows.source._stock_info import StockInfoMixin
from sinoquant.dataflows.source._fundamentals import FundamentalsMixin
from sinoquant.dataflows.source._news import NewsMixin


class DataSourceManager(
    CacheMixin,
    SourceSelectionMixin,
    StockDataMixin,
    StockInfoMixin,
    FundamentalsMixin,
    NewsMixin,
):
    """数据源管理器 — 通过多继承组合所有 mixin"""

    ChinaDataSource = ChinaDataSource  # 让 mixin 可以通过 self.ChinaDataSource 访问枚举

    def __init__(self):
        """初始化数据源管理器"""
        # 检查是否启用MongoDB缓存
        self.use_mongodb_cache = self._check_mongodb_enabled()

        self.default_source = self._get_default_source()
        self.available_sources = self._check_available_sources()
        self.current_source = self.default_source

        # 初始化统一缓存管理器
        self.cache_manager = None
        self.cache_enabled = False
        try:
            from sinoquant.dataflows.cache import get_cache
            self.cache_manager = get_cache()
            self.cache_enabled = True
            logger.info("✅ 统一缓存管理器已启用")
        except Exception as e:
            logger.warning(f"⚠️ 统一缓存管理器初始化失败: {e}")

        logger.info("📊 数据源管理器初始化完成")
        logger.info(
            f"   MongoDB缓存: {'✅ 已启用' if self.use_mongodb_cache else '❌ 未启用'}"
        )
        logger.info(
            f"   统一缓存: {'✅ 已启用' if self.cache_enabled else '❌ 未启用'}"
        )
        logger.info(f"   默认数据源: {self.default_source.value}")
        logger.info(
            f"   可用数据源: {[s.value for s in self.available_sources]}"
        )


# ---------------------------------------------------------------------------
# 全局单例 & 便捷函数
# ---------------------------------------------------------------------------
_data_source_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    """获取全局数据源管理器实例"""
    global _data_source_manager
    if _data_source_manager is None:
        _data_source_manager = DataSourceManager()
    return _data_source_manager


def get_china_stock_data_unified(symbol: str, start_date: str, end_date: str) -> str:
    """
    统一的中国股票数据获取接口
    自动使用配置的数据源，支持备用数据源

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        str: 格式化的股票数据
    """
    logger.info(
        f"🔍 [股票代码追踪] data_source_manager.get_china_stock_data_unified "
        f"接收到的股票代码: '{symbol}' (类型: {type(symbol)})"
    )
    logger.info(f"🔍 [股票代码追踪] 股票代码长度: {len(str(symbol))}")
    logger.info(f"🔍 [股票代码追踪] 股票代码字符: {list(str(symbol))}")

    manager = get_data_source_manager()
    logger.info(
        f"🔍 [股票代码追踪] 调用 manager.get_stock_data，"
        f"传入参数: symbol='{symbol}', start_date='{start_date}', end_date='{end_date}'"
    )
    result = manager.get_stock_data(symbol, start_date, end_date)

    # 兜底兼容：保证 result 为字符串
    if isinstance(result, tuple):
        logger.warning(
            "⚠️ [股票代码追踪] manager.get_stock_data 返回了 tuple，自动兼容处理"
        )
        result = result[0] if len(result) > 0 else ""
    if result is None:
        result = ""
    if not isinstance(result, str):
        result = str(result)

    if result:
        lines = result.split("\n")
        data_lines = [line for line in lines if "2025-" in line and symbol in line]
        logger.info(
            f"🔍 [股票代码追踪] 返回结果统计: "
            f"总行数={len(lines)}, 数据行数={len(data_lines)}, "
            f"结果长度={len(result)}字符"
        )
        logger.info(f"🔍 [股票代码追踪] 返回结果前500字符: {result[:500]}")
        if len(data_lines) > 0:
            logger.info(
                f"🔍 [股票代码追踪] 数据行示例: "
                f"第1行='{data_lines[0][:100]}', "
                f"最后1行='{data_lines[-1][:100]}'"
            )
    else:
        logger.info("🔍 [股票代码追踪] 返回结果: None")

    return result


def get_china_stock_info_unified(symbol: str) -> Dict:
    """
    统一的中国股票信息获取接口

    Args:
        symbol: 股票代码

    Returns:
        Dict: 股票基本信息
    """
    manager = get_data_source_manager()
    return manager.get_stock_info(symbol)


# ==================== 兼容性接口 ====================


def get_stock_data_service() -> DataSourceManager:
    """
    获取股票数据服务实例（兼容 stock_data_service 接口）

    ⚠️ 此函数为兼容性接口，实际返回 DataSourceManager 实例
    推荐直接使用 get_data_source_manager()
    """
    return get_data_source_manager()
