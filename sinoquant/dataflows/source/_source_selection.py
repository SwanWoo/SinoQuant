"""
数据源选择与适配器获取 Mixin

提供数据源优先级排序、可用性检查、适配器获取等功能。
通过 ``self.ChinaDataSource`` 访问枚举（在 facade 中赋值）。
"""

import os
from typing import List, Optional

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()
from sinoquant.constants import DataSourceCode


class SourceSelectionMixin:
    """数据源选择、优先级排序与适配器获取 mixin"""

    # ------------------------------------------------------------------
    # 数据源检查与选择
    # ------------------------------------------------------------------

    def _check_mongodb_enabled(self) -> bool:
        """检查是否启用MongoDB缓存"""
        from sinoquant.config.runtime_settings import use_app_cache_enabled
        return use_app_cache_enabled()

    def _get_data_source_priority_order(
        self, symbol: Optional[str] = None
    ) -> List:
        """
        从数据库获取数据源优先级顺序（用于降级）

        Args:
            symbol: 股票代码，用于识别市场类型（A股）

        Returns:
            按优先级排序的数据源列表（不包含MongoDB，因为MongoDB是最高优先级）
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        # 🔥 识别市场类型
        market_category = self._identify_market_category(symbol)

        try:
            # 🔥 从数据库读取数据源配置（使用同步客户端）
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config_collection = db.system_configs

            config_data = config_collection.find_one(
                {"is_active": True},
                sort=[("version", -1)],
            )

            if config_data and config_data.get("data_source_configs"):
                data_source_configs = config_data.get("data_source_configs", [])

                enabled_sources = []
                for ds in data_source_configs:
                    if not ds.get("enabled", True):
                        continue

                    market_categories = ds.get("market_categories", [])
                    if market_categories and market_category:
                        if market_category not in market_categories:
                            continue

                    enabled_sources.append(ds)

                enabled_sources.sort(
                    key=lambda x: x.get("priority", 0), reverse=True
                )

                source_mapping = {
                    DataSourceCode.TUSHARE: ChinaDataSource.TUSHARE,
                    DataSourceCode.AKSHARE: ChinaDataSource.AKSHARE,
                    DataSourceCode.BAOSTOCK: ChinaDataSource.BAOSTOCK,
                }

                result = []
                for ds in enabled_sources:
                    ds_type = ds.get("type", "").lower()
                    if ds_type in source_mapping:
                        source = source_mapping[ds_type]
                        if (
                            source != ChinaDataSource.MONGODB
                            and source in self.available_sources
                        ):
                            result.append(source)

                if result:
                    logger.info(
                        f"✅ [数据源优先级] 市场={market_category or '全部'}, "
                        f"从数据库读取: {[s.value for s in result]}"
                    )
                    return result
                else:
                    logger.warning(
                        f"⚠️ [数据源优先级] 市场={market_category or '全部'}, "
                        "数据库配置中没有可用的数据源，使用默认顺序"
                    )
            else:
                logger.warning(
                    "⚠️ [数据源优先级] 数据库中没有数据源配置，使用默认顺序"
                )
        except Exception as e:
            logger.warning(
                f"⚠️ [数据源优先级] 从数据库读取失败: {e}，使用默认顺序"
            )

        # 🔥 回退到默认顺序（兼容性）
        default_order = [
            ChinaDataSource.AKSHARE,
            ChinaDataSource.TUSHARE,
            ChinaDataSource.BAOSTOCK,
        ]
        return [s for s in default_order if s in self.available_sources]

    def _identify_market_category(self, symbol: Optional[str]) -> Optional[str]:
        """
        识别股票代码所属的市场分类

        Args:
            symbol: 股票代码

        Returns:
            市场分类ID（a_shares/us_stocks/hk_stocks），如果无法识别则返回None
        """
        if not symbol:
            return None

        try:
            from sinoquant.utils.stock_utils import StockUtils, StockMarket

            market = StockUtils.identify_stock_market(symbol)

            market_mapping = {
                StockMarket.CHINA_A: "a_shares",
            }

            category = market_mapping.get(market)
            if category:
                logger.debug(f"🔍 [市场识别] {symbol} → {category}")
            return category
        except Exception as e:
            logger.warning(f"⚠️ [市场识别] 识别失败: {e}")
            return None

    def _get_default_source(self):
        """获取默认数据源"""
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        if self.use_mongodb_cache:
            return ChinaDataSource.MONGODB

        env_source = os.getenv(
            "DEFAULT_CHINA_DATA_SOURCE", DataSourceCode.AKSHARE
        ).lower()

        source_mapping = {
            DataSourceCode.TUSHARE: ChinaDataSource.TUSHARE,
            DataSourceCode.AKSHARE: ChinaDataSource.AKSHARE,
            DataSourceCode.BAOSTOCK: ChinaDataSource.BAOSTOCK,
        }

        return source_mapping.get(env_source, ChinaDataSource.AKSHARE)

    # ------------------------------------------------------------------
    # 当前数据源 get / set
    # ------------------------------------------------------------------

    def get_current_source(self):
        """获取当前数据源"""
        return self.current_source

    def set_current_source(self, source) -> bool:
        """设置当前数据源"""
        if source in self.available_sources:
            self.current_source = source
            logger.info(f"✅ 数据源已切换到: {source.value}")
            return True
        else:
            logger.error(f"❌ 数据源不可用: {source.value}")
            return False

    # ------------------------------------------------------------------
    # 可用性检查
    # ------------------------------------------------------------------

    def _check_available_sources(self) -> list:
        """
        检查可用的数据源

        检查逻辑：
        1. 检查依赖包是否安装（技术可用性）
        2. 检查数据库配置中是否启用（业务可用性）

        Returns:
            可用且已启用的数据源列表
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]
        available = []

        # 🔥 从数据库读取数据源配置，获取启用状态
        enabled_sources_in_db = set()
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config_collection = db.system_configs

            config_data = config_collection.find_one(
                {"is_active": True},
                sort=[("version", -1)],
            )

            if config_data and config_data.get("data_source_configs"):
                data_source_configs = config_data.get("data_source_configs", [])

                for ds in data_source_configs:
                    if ds.get("enabled", True):
                        ds_type = ds.get("type", "").lower()
                        enabled_sources_in_db.add(ds_type)

                logger.info(
                    f"✅ [数据源配置] 从数据库读取到已启用的数据源: "
                    f"{enabled_sources_in_db}"
                )
            else:
                logger.warning(
                    "⚠️ [数据源配置] 数据库中没有数据源配置，"
                    "将检查所有已安装的数据源"
                )
                enabled_sources_in_db = {
                    "mongodb", "tushare", "akshare", "baostock",
                }
        except Exception as e:
            logger.warning(
                f"⚠️ [数据源配置] 从数据库读取失败: {e}，"
                "将检查所有已安装的数据源"
            )
            enabled_sources_in_db = {
                "mongodb", "tushare", "akshare", "baostock",
            }

        # 检查MongoDB（最高优先级）
        if self.use_mongodb_cache and "mongodb" in enabled_sources_in_db:
            try:
                from sinoquant.dataflows.cache.mongodb_cache_adapter import (
                    get_mongodb_cache_adapter,
                )
                adapter = get_mongodb_cache_adapter()
                if adapter.use_app_cache and adapter.db is not None:
                    available.append(ChinaDataSource.MONGODB)
                    logger.info(
                        "✅ MongoDB数据源可用且已启用（最高优先级）"
                    )
                else:
                    logger.warning("⚠️ MongoDB数据源不可用: 数据库未连接")
            except Exception as e:
                logger.warning(f"⚠️ MongoDB数据源不可用: {e}")
        elif self.use_mongodb_cache and "mongodb" not in enabled_sources_in_db:
            logger.info("ℹ️ MongoDB数据源已在数据库中禁用")

        # 从数据库读取数据源配置
        datasource_configs = self._get_datasource_configs_from_db()

        # 检查Tushare
        if "tushare" in enabled_sources_in_db:
            try:
                import tushare as ts  # noqa: F401
                token = (
                    datasource_configs.get("tushare", {}).get("api_key")
                    or os.getenv("TUSHARE_TOKEN")
                )
                if token:
                    available.append(ChinaDataSource.TUSHARE)
                    source = (
                        "数据库配置"
                        if datasource_configs.get("tushare", {}).get("api_key")
                        else "环境变量"
                    )
                    logger.info(
                        f"✅ Tushare数据源可用且已启用 (API Key来源: {source})"
                    )
                else:
                    logger.warning(
                        "⚠️ Tushare数据源不可用: "
                        "API Key未配置（数据库和环境变量均未找到）"
                    )
            except ImportError:
                logger.warning("⚠️ Tushare数据源不可用: 库未安装")
        else:
            logger.info("ℹ️ Tushare数据源已在数据库中禁用")

        # 检查AKShare
        if "akshare" in enabled_sources_in_db:
            try:
                import akshare as ak  # noqa: F401
                available.append(ChinaDataSource.AKSHARE)
                logger.info("✅ AKShare数据源可用且已启用")
            except ImportError:
                logger.warning("⚠️ AKShare数据源不可用: 库未安装")
        else:
            logger.info("ℹ️ AKShare数据源已在数据库中禁用")

        # 检查BaoStock
        if "baostock" in enabled_sources_in_db:
            try:
                import baostock as bs  # noqa: F401
                available.append(ChinaDataSource.BAOSTOCK)
                logger.info("✅ BaoStock数据源可用且已启用")
            except ImportError:
                logger.warning("⚠️ BaoStock数据源不可用: 库未安装")
        else:
            logger.info("ℹ️ BaoStock数据源已在数据库中禁用")

        return available

    def _get_datasource_configs_from_db(self) -> dict:
        """从数据库读取数据源配置（包括 API Key）"""
        try:
            from app.core.database import get_mongo_db_sync

            db = get_mongo_db_sync()
            config = db.system_configs.find_one({"is_active": True})
            if not config:
                return {}

            datasource_configs = config.get("data_source_configs", [])

            result = {}
            for ds_config in datasource_configs:
                name = ds_config.get("name", "").lower()
                result[name] = {
                    "api_key": ds_config.get("api_key", ""),
                    "api_secret": ds_config.get("api_secret", ""),
                    "config_params": ds_config.get("config_params", {}),
                }

            return result
        except Exception as e:
            logger.warning(f"⚠️ 从数据库读取数据源配置失败: {e}")
            return {}

    # ------------------------------------------------------------------
    # 适配器获取
    # ------------------------------------------------------------------

    def get_data_adapter(self):
        """获取当前数据源的适配器"""
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        if self.current_source == ChinaDataSource.MONGODB:
            return self._get_mongodb_adapter()
        elif self.current_source == ChinaDataSource.TUSHARE:
            return self._get_tushare_adapter()
        elif self.current_source == ChinaDataSource.AKSHARE:
            return self._get_akshare_adapter()
        elif self.current_source == ChinaDataSource.BAOSTOCK:
            return self._get_baostock_adapter()
        else:
            raise ValueError(f"不支持的数据源: {self.current_source}")

    def _get_mongodb_adapter(self):
        """获取MongoDB适配器"""
        try:
            from sinoquant.dataflows.cache.mongodb_cache_adapter import (
                get_mongodb_cache_adapter,
            )
            return get_mongodb_cache_adapter()
        except ImportError as e:
            logger.error(f"❌ MongoDB适配器导入失败: {e}")
            return None

    def _get_tushare_adapter(self):
        """获取Tushare提供器"""
        try:
            from sinoquant.dataflows.providers.china.tushare import (
                get_tushare_provider,
            )
            return get_tushare_provider()
        except ImportError as e:
            logger.error(f"❌ Tushare提供器导入失败: {e}")
            return None

    def _get_akshare_adapter(self):
        """获取AKShare适配器"""
        try:
            from sinoquant.dataflows.providers.china.akshare import (
                get_akshare_provider,
            )
            return get_akshare_provider()
        except ImportError as e:
            logger.error(f"❌ AKShare适配器导入失败: {e}")
            return None

    def _get_baostock_adapter(self):
        """获取BaoStock适配器"""
        try:
            from sinoquant.dataflows.providers.china.baostock import (
                get_baostock_provider,
            )
            return get_baostock_provider()
        except ImportError as e:
            logger.error(f"❌ BaoStock适配器导入失败: {e}")
            return None
