"""
配置管理 - 共享导入、工具函数和请求模型
"""

import logging
import re
from copy import deepcopy
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse

from app.routers.auth_db import get_current_user
from app.models.user import User
from app.models.config import (
    SystemConfigResponse, LLMConfigRequest, DataSourceConfigRequest,
    DatabaseConfigRequest, ConfigTestRequest, ConfigTestResponse,
    LLMConfig, DataSourceConfig, DatabaseConfig,
    LLMProvider, LLMProviderRequest, LLMProviderResponse,
    MarketCategory, MarketCategoryRequest, DataSourceGrouping,
    DataSourceGroupingRequest, DataSourceOrderRequest,
    ModelCatalog, ModelInfo
)
from app.services.config_service import config_service
from app.services.vendor_config_service import vendor_config_service
from app.models.vendor_config import VendorType
from datetime import datetime
from app.utils.timezone import now_tz

from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType
from app.services.config_provider import provider as config_provider


logger = logging.getLogger("webapi")


# ===== 请求模型 =====

class SetDefaultRequest(BaseModel):
    """设置默认配置请求"""
    name: str


class CustomLLMEndpointRequest(BaseModel):
    """自定义 OpenAI 兼容大模型端点（如自部署 vLLM）"""
    provider_name: str
    model_name: str
    base_url: str
    api_key: Optional[str] = None
    provider_display_name: str = ""
    description: str = ""
    api_key_optional: bool = True
    test_connection: bool = False
    set_as_default: bool = False
    max_tokens: int = 4000
    temperature: float = 0.7
    timeout: int = 180


class ModelCatalogRequest(BaseModel):
    """模型目录请求"""
    provider: str
    provider_name: str
    models: List[Dict[str, Any]]


# ===== 配置重载端点 =====

router = APIRouter(prefix="/config", tags=["配置管理"])


@router.post("/reload", summary="重新加载配置")
async def reload_config(current_user: dict = Depends(get_current_user)):
    """
    重新加载配置并桥接到环境变量

    用于配置更新后立即生效，无需重启服务
    """
    try:
        from app.core.config_bridge import reload_bridged_config

        success = reload_bridged_config()

        if success:
            await log_operation(
                user_id=str(current_user.get("user_id", "")),
                username=current_user.get("username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="重载配置",
                details={"action": "reload_config"},
                ip_address="",
                user_agent=""
            )

            return {
                "success": True,
                "message": "配置重载成功",
                "data": {
                    "reloaded_at": now_tz().isoformat()
                }
            }
        else:
            return {
                "success": False,
                "message": "配置重载失败，请查看日志"
            }
    except Exception as e:
        logger.error(f"配置重载失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"配置重载失败: {str(e)}"
        )


# ===== 系统配置获取端点 =====

@router.get("/system", response_model=SystemConfigResponse)
async def get_system_config(
    current_user: User = Depends(get_current_user)
):
    """获取系统配置"""
    try:
        config = await config_service.get_system_config()
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="系统配置不存在"
            )

        return SystemConfigResponse(
            config_name=config.config_name,
            config_type=config.config_type,
            llm_configs=_sanitize_llm_configs(config.llm_configs),
            default_llm=config.default_llm,
            data_source_configs=_sanitize_datasource_configs(config.data_source_configs),
            default_data_source=config.default_data_source,
            database_configs=_sanitize_database_configs(config.database_configs),
            system_settings=_sanitize_kv(config.system_settings),
            created_at=config.created_at,
            updated_at=config.updated_at,
            version=config.version,
            is_active=config.is_active
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统配置失败: {str(e)}"
        )


# ===== 脱敏工具函数 =====

def _sanitize_llm_configs(items):
    try:
        return [LLMConfig(**{**i.model_dump(), "api_key": None}) for i in items]
    except Exception:
        return items

def _sanitize_datasource_configs(items):
    """
    脱敏数据源配置，返回缩略的 API Key

    逻辑：
    1. 如果数据库中有有效的 API Key，返回缩略版本
    2. 如果数据库中没有，尝试从环境变量读取并返回缩略版本
    3. 如果都没有，返回 None
    """
    try:
        from app.utils.api_key_utils import (
            is_valid_api_key,
            truncate_api_key,
            get_env_api_key_for_datasource
        )

        result = []
        for item in items:
            data = item.model_dump()

            # 处理 API Key
            db_key = data.get("api_key")
            if is_valid_api_key(db_key):
                # 数据库中有有效的 API Key，返回缩略版本
                data["api_key"] = truncate_api_key(db_key)
            else:
                # 数据库中没有有效的 API Key，尝试从环境变量读取
                ds_type = data.get("type")
                if isinstance(ds_type, str):
                    env_key = get_env_api_key_for_datasource(ds_type)
                    if env_key:
                        # 环境变量中有有效的 API Key，返回缩略版本
                        data["api_key"] = truncate_api_key(env_key)
                    else:
                        data["api_key"] = None
                else:
                    data["api_key"] = None

            # 处理 API Secret（同样的逻辑）
            db_secret = data.get("api_secret")
            if is_valid_api_key(db_secret):
                data["api_secret"] = truncate_api_key(db_secret)
            else:
                data["api_secret"] = None

            result.append(DataSourceConfig(**data))

        return result
    except Exception as e:
        print(f"⚠️ 脱敏数据源配置失败: {e}")
        return items

def _sanitize_database_configs(items):
    try:
        return [DatabaseConfig(**{**i.model_dump(), "password": None}) for i in items]
    except Exception:
        return items

def _sanitize_kv(d: Dict[str, Any]) -> Dict[str, Any]:
    """对字典中的可能敏感键进行脱敏（仅用于响应）。"""
    try:
        if not isinstance(d, dict):
            return d
        sens_patterns = ("key", "secret", "password", "token", "client_secret")
        redacted = {}
        for k, v in d.items():
            if isinstance(k, str) and any(p in k.lower() for p in sens_patterns):
                redacted[k] = None
            else:
                redacted[k] = v
        return redacted
    except Exception:
        return d
