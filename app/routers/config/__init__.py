"""
配置管理API路由（聚合器）

将配置管理端点拆分为多个子模块：
- _common: 共享导入、工具函数、请求模型
- _providers: LLM 厂家 CRUD + 测试 + 自定义端点
- _llm_configs: LLM 配置 + 数据源配置 CRUD + 测试
- _market_categories: 市场分类 + 数据源分组管理
- _settings: 系统设置、导出/导入、迁移、默认值
- _model_catalog: 模型目录 CRUD
- _database: 数据库配置 CRUD + 测试
"""

from fastapi import APIRouter

from app.routers.config._common import router as common_router
from app.routers.config._providers import router as providers_router
from app.routers.config._providers import custom_endpoint_router
from app.routers.config._llm_configs import router as llm_configs_router
from app.routers.config._market_categories import router as market_categories_router
from app.routers.config._settings import router as settings_router
from app.routers.config._model_catalog import router as model_catalog_router
from app.routers.config._database import router as database_router

router = APIRouter(tags=["config"])
router.include_router(common_router)
router.include_router(providers_router)
router.include_router(custom_endpoint_router)
router.include_router(llm_configs_router)
router.include_router(market_categories_router)
router.include_router(settings_router)
router.include_router(model_catalog_router)
router.include_router(database_router)
