"""
新闻数据获取 Mixin

提供多数据源的新闻数据查询，支持自动降级。
"""

import time
from typing import Dict, List, Any

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()


class NewsMixin:
    """新闻数据获取 mixin"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_news_data(
        self, symbol: str = None, hours_back: int = 24, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        获取新闻数据的统一接口，支持多数据源和自动降级
        优先级：MongoDB → Tushare → AKShare

        Args:
            symbol: 股票代码，为空则获取市场新闻
            hours_back: 回溯小时数
            limit: 返回数量限制

        Returns:
            List[Dict]: 新闻数据列表
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(
            f"📰 [数据来源: {self.current_source.value}] 开始获取新闻数据: "
            f"{symbol or '市场新闻'}, 回溯{hours_back}小时",
            extra={
                "symbol": symbol,
                "hours_back": hours_back,
                "limit": limit,
                "data_source": self.current_source.value,
                "event_type": "news_fetch_start",
            },
        )

        start_time = time.time()

        try:
            if self.current_source == ChinaDataSource.MONGODB:
                result = self._get_mongodb_news(symbol, hours_back, limit)
            elif self.current_source == ChinaDataSource.TUSHARE:
                result = self._get_tushare_news(symbol, hours_back, limit)
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_news(symbol, hours_back, limit)
            else:
                logger.warning(f"⚠️ 数据源 {self.current_source.value} 不支持新闻数据")
                result = []

            duration = time.time() - start_time
            result_count = len(result) if result else 0

            if result and result_count > 0:
                logger.info(
                    f"✅ [数据来源: {self.current_source.value}] 成功获取新闻数据: "
                    f"{symbol or '市场新闻'} ({result_count}条, 耗时{duration:.2f}秒)",
                    extra={
                        "symbol": symbol,
                        "data_source": self.current_source.value,
                        "news_count": result_count,
                        "duration": duration,
                        "event_type": "news_fetch_success",
                    },
                )
                return result
            else:
                logger.warning(
                    f"⚠️ [数据来源: {self.current_source.value}] "
                    f"未获取到新闻数据: {symbol or '市场新闻'}，尝试降级",
                    extra={
                        "symbol": symbol,
                        "data_source": self.current_source.value,
                        "duration": duration,
                        "event_type": "news_fetch_fallback",
                    },
                )
                return self._try_fallback_news(symbol, hours_back, limit)

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"❌ [数据来源: {self.current_source.value}异常] "
                f"获取新闻数据失败: {symbol or '市场新闻'} - {e}",
                extra={
                    "symbol": symbol,
                    "data_source": self.current_source.value,
                    "duration": duration,
                    "error": str(e),
                    "event_type": "news_fetch_exception",
                },
                exc_info=True,
            )
            return self._try_fallback_news(symbol, hours_back, limit)

    # ------------------------------------------------------------------
    # 各数据源新闻获取
    # ------------------------------------------------------------------

    def _get_mongodb_news(
        self, symbol: str, hours_back: int, limit: int
    ) -> List[Dict[str, Any]]:
        """从MongoDB获取新闻数据"""
        try:
            from sinoquant.dataflows.cache.mongodb_cache_adapter import (
                get_mongodb_cache_adapter,
            )
            adapter = get_mongodb_cache_adapter()

            news_data = adapter.get_news_data(
                symbol, hours_back=hours_back, limit=limit
            )

            if news_data and len(news_data) > 0:
                logger.info(
                    f"✅ [数据来源: MongoDB-新闻] 成功获取: "
                    f"{symbol or '市场新闻'} ({len(news_data)}条)"
                )
                return news_data
            else:
                logger.warning(
                    f"⚠️ [数据来源: MongoDB] 未找到新闻: "
                    f"{symbol or '市场新闻'}，降级到其他数据源"
                )
                return self._try_fallback_news(symbol, hours_back, limit)

        except Exception as e:
            logger.error(f"❌ [数据来源: MongoDB] 获取新闻失败: {e}")
            return self._try_fallback_news(symbol, hours_back, limit)

    def _get_tushare_news(
        self, symbol: str, hours_back: int, limit: int
    ) -> List[Dict[str, Any]]:
        """从Tushare获取新闻数据"""
        try:
            logger.warning(f"⚠️ [数据来源: Tushare] Tushare新闻功能暂时不可用")
            return []
        except Exception as e:
            logger.error(f"❌ [数据来源: Tushare] 获取新闻失败: {e}")
            return []

    def _get_akshare_news(
        self, symbol: str, hours_back: int, limit: int
    ) -> List[Dict[str, Any]]:
        """从AKShare获取新闻数据"""
        try:
            logger.warning(f"⚠️ [数据来源: AKShare] AKShare新闻功能暂时不可用")
            return []
        except Exception as e:
            logger.error(f"❌ [数据来源: AKShare] 获取新闻失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 降级
    # ------------------------------------------------------------------

    def _try_fallback_news(
        self, symbol: str, hours_back: int, limit: int
    ) -> List[Dict[str, Any]]:
        """新闻数据降级处理"""
        try:
            from sinoquant.config.runtime_settings import local_data_only_enabled
            if local_data_only_enabled():
                logger.warning(
                    f"🔒 [本地数据模式] 禁止外部API降级，{symbol or '市场'}的新闻数据在MongoDB中不可用"
                )
                return []
        except Exception:
            pass

        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.error(
            f"🔄 {self.current_source.value}失败，尝试备用数据源获取新闻..."
        )

        fallback_order = self._get_data_source_priority_order(symbol)

        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 尝试备用数据源获取新闻: {source.value}")

                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_news(symbol, hours_back, limit)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_news(symbol, hours_back, limit)
                    else:
                        continue

                    if result and len(result) > 0:
                        logger.info(
                            f"✅ [数据来源: 备用数据源] "
                            f"降级成功获取新闻: {source.value}"
                        )
                        return result
                    else:
                        logger.warning(
                            f"⚠️ 备用数据源{source.value}未返回新闻"
                        )

                except Exception as e:
                    logger.error(f"❌ 备用数据源{source.value}异常: {e}")
                    continue

        logger.warning(
            f"⚠️ [数据来源: 所有数据源失败] 无法获取新闻: "
            f"{symbol or '市场新闻'}"
        )
        return []
