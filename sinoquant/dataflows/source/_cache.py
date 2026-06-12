"""
缓存相关操作的 Mixin
"""

import pandas as pd
from typing import Optional

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()


class CacheMixin:
    """缓存操作 mixin — 提供缓存读写和安全取值方法"""

    # ------------------------------------------------------------------
    # 公开缓存方法
    # ------------------------------------------------------------------

    def _get_cached_data(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        max_age_hours: int = 24,
    ) -> Optional[pd.DataFrame]:
        """
        从缓存获取数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            max_age_hours: 最大缓存时间（小时）

        Returns:
            DataFrame: 缓存的数据，如果没有则返回None
        """
        if not self.cache_enabled or not self.cache_manager:
            return None

        try:
            cache_key = self.cache_manager.find_cached_stock_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                max_age_hours=max_age_hours,
            )

            if cache_key:
                cached_data = self.cache_manager.load_stock_data(cache_key)
                if (
                    cached_data is not None
                    and hasattr(cached_data, "empty")
                    and not cached_data.empty
                ):
                    logger.debug(
                        f"📦 从缓存获取{symbol}数据: {len(cached_data)}条"
                    )
                    return cached_data
        except Exception as e:
            logger.warning(f"⚠️ 从缓存读取数据失败: {e}")

        return None

    def _save_to_cache(
        self,
        symbol: str,
        data: pd.DataFrame,
        start_date: str = None,
        end_date: str = None,
    ):
        """
        保存数据到缓存

        Args:
            symbol: 股票代码
            data: 数据
            start_date: 开始日期
            end_date: 结束日期
        """
        if not self.cache_enabled or not self.cache_manager:
            return

        try:
            if data is not None and hasattr(data, "empty") and not data.empty:
                self.cache_manager.save_stock_data(symbol, data, start_date, end_date)
                logger.debug(f"💾 保存{symbol}数据到缓存: {len(data)}条")
        except Exception as e:
            logger.warning(f"⚠️ 保存数据到缓存失败: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _get_volume_safely(self, data) -> float:
        """
        安全获取成交量数据（统一版本）

        支持多种列名: volume, vol, turnover, trade_volume

        Args:
            data: 股票数据DataFrame

        Returns:
            float: 成交量，如果获取失败返回0
        """
        try:
            volume_columns = ["volume", "vol", "turnover", "trade_volume"]

            for col in volume_columns:
                if col in data.columns:
                    logger.info(f"✅ 找到成交量列: {col}")
                    return data[col].sum()

            # 如果都没找到，记录警告并返回0
            logger.warning(f"⚠️ 未找到成交量列，可用列: {list(data.columns)}")
            return 0

        except Exception as e:
            logger.error(f"❌ 获取成交量失败: {e}")
            return 0
