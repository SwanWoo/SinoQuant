"""
配置管理服务 (Facade)

ConfigService 通过 mixin 继承的方式将功能分散到多个子模块中，
保持所有现有导入路径不变。

原始单文件（4,354 行）已拆分为以下 mixin 模块：
- _market_categories.py    市场分类 & 数据源分组管理
- _system_config.py         系统配置 CRUD / 导入导出 / 迁移
- _llm_config_tester.py     LLM 配置 API 测试
- _data_source_tester.py    数据源 & 数据库配置测试
- _database_config.py       数据库配置 CRUD
- _model_catalog.py         模型目录 CRUD
- _llm_provider_management.py  LLM 厂家 CRUD & 环境变量迁移
- _provider_api_tester.py   厂家 API 连接测试
- _provider_model_fetcher.py   厂家模型列表获取
"""

import logging
from typing import Optional

from app.core.database import get_mongo_db

# Import all mixin classes
from app.services.config._market_categories import MarketCategoryMixin
from app.services.config._system_config import SystemConfigMixin
from app.services.config._llm_config_tester import LLMConfigTesterMixin
from app.services.config._data_source_tester import DataSourceTesterMixin
from app.services.config._database_config import DatabaseConfigMixin
from app.services.config._model_catalog import ModelCatalogMixin
from app.services.config._llm_provider_management import LLMProviderMixin
from app.services.config._provider_api_tester import ProviderApiTesterMixin
from app.services.config._provider_model_fetcher import ProviderModelFetcherMixin

logger = logging.getLogger(__name__)


class ConfigService(
    MarketCategoryMixin,
    SystemConfigMixin,
    LLMConfigTesterMixin,
    DataSourceTesterMixin,
    DatabaseConfigMixin,
    ModelCatalogMixin,
    LLMProviderMixin,
    ProviderApiTesterMixin,
    ProviderModelFetcherMixin,
):
    """配置管理服务类"""

    def __init__(self, db_manager=None):
        self.db = None
        self.db_manager = db_manager

    async def _get_db(self):
        """获取数据库连接"""
        if self.db is None:
            if self.db_manager and self.db_manager.mongo_db is not None:
                # 如果有DatabaseManager实例，直接使用
                self.db = self.db_manager.mongo_db
            else:
                # 否则使用全局函数
                self.db = get_mongo_db()
        return self.db


# 创建全局实例
config_service = ConfigService()
