"""
配置管理 - LLM 厂家管理（CRUD + 测试 + 自定义端点）
"""

import re
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from urllib.parse import urlparse, urlunparse

from app.routers.auth_db import get_current_user
from app.models.user import User
from app.models.config import (
    LLMProvider, LLMProviderRequest, LLMProviderResponse,
    LLMConfig, ConfigTestResponse
)
from app.services.config_service import config_service
from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType
from app.services.config_provider import provider as config_provider

from app.routers.config._common import (
    logger, CustomLLMEndpointRequest
)


router = APIRouter(prefix="/config/llm/providers", tags=["配置管理"])


# ===== 工具函数 =====

def _normalize_openai_compatible_base_url(raw_url: str) -> str:
    """
    规范化 OpenAI 兼容端点。
    - 必须是 http/https
    - 无版本号时自动补 /v1（兼容常见 vLLM 部署）
    """
    if not raw_url or not raw_url.strip():
        raise HTTPException(status_code=400, detail="base_url 不能为空")

    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="base_url 必须是有效的 http/https 地址")

    path = (parsed.path or "").rstrip("/")
    if not path:
        path = "/v1"
    elif not re.search(r"/v\d+$", path):
        path = f"{path}/v1"

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _normalize_provider_name(raw_name: str) -> str:
    if not raw_name or not raw_name.strip():
        raise HTTPException(status_code=400, detail="provider_name 不能为空")

    normalized = raw_name.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z0-9_]{2,64}", normalized):
        raise HTTPException(
            status_code=400,
            detail="provider_name 仅支持小写字母、数字、下划线，长度 2-64"
        )
    return normalized


# ========== 大模型厂家管理 ==========

@router.get("", response_model=List[LLMProviderResponse])
async def get_llm_providers(
    current_user: User = Depends(get_current_user)
):
    """获取所有大模型厂家"""
    try:
        from app.utils.api_key_utils import (
            is_valid_api_key,
            truncate_api_key,
            get_env_api_key_for_provider
        )

        providers = await config_service.get_llm_providers()
        result = []

        for provider in providers:
            # 处理 API Key：优先使用数据库配置，如果数据库没有则检查环境变量
            db_key_valid = is_valid_api_key(provider.api_key)
            if db_key_valid:
                # 数据库中有有效的 API Key，返回缩略版本
                api_key_display = truncate_api_key(provider.api_key)
            else:
                # 数据库中没有有效的 API Key，尝试从环境变量读取
                env_key = get_env_api_key_for_provider(provider.name)
                if env_key:
                    # 环境变量中有有效的 API Key，返回缩略版本
                    api_key_display = truncate_api_key(env_key)
                else:
                    api_key_display = None

            # 处理 API Secret（同样的逻辑）
            db_secret_valid = is_valid_api_key(provider.api_secret)
            if db_secret_valid:
                api_secret_display = truncate_api_key(provider.api_secret)
            else:
                # 注意：API Secret 通常不在环境变量中，所以这里只检查数据库
                api_secret_display = None

            result.append(
                LLMProviderResponse(
                    id=str(provider.id),
                    name=provider.name,
                    display_name=provider.display_name,
                    description=provider.description,
                    website=provider.website,
                    api_doc_url=provider.api_doc_url,
                    logo_url=provider.logo_url,
                    is_active=provider.is_active,
                    supported_features=provider.supported_features,
                    default_base_url=provider.default_base_url,
                    # 返回缩略的 API Key（前6位 + "..." + 后6位）
                    api_key=api_key_display,
                    api_secret=api_secret_display,
                    extra_config={
                        **provider.extra_config,
                        "has_api_key": bool(api_key_display),
                        "has_api_secret": bool(api_secret_display)
                    },
                    created_at=provider.created_at,
                    updated_at=provider.updated_at
                )
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取厂家列表失败: {str(e)}"
        )


@router.post("", response_model=dict)
async def add_llm_provider(
    request: LLMProviderRequest,
    current_user: User = Depends(get_current_user)
):
    """添加大模型厂家（方案A：REST不接受密钥，强制清洗）"""
    try:
        sanitized = request.model_dump()
        if 'api_key' in sanitized:
            sanitized['api_key'] = ""
        provider = LLMProvider(**sanitized)
        provider_id = await config_service.add_llm_provider(provider)

        # 审计日志（忽略异常）
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="add_llm_provider",
                details={"provider_id": str(provider_id), "name": request.name},
                success=True,
            )
        except Exception:
            pass
        return {
            "success": True,
            "message": "厂家添加成功",
            "data": {"id": str(provider_id)}
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加厂家失败: {str(e)}"
        )


@router.put("/{provider_id}", response_model=dict)
async def update_llm_provider(
    provider_id: str,
    request: LLMProviderRequest,
    current_user: User = Depends(get_current_user)
):
    """更新大模型厂家"""
    try:
        from app.utils.api_key_utils import should_skip_api_key_update

        update_data = request.model_dump(exclude_unset=True)

        # 处理 API Key 的更新逻辑
        # 1. 如果 API Key 是空字符串，表示用户想清空密钥 -> 保存空字符串
        # 2. 如果 API Key 是占位符或截断的密钥（如 "sk-99054..."），则删除该字段（不更新）
        # 3. 如果 API Key 是有效的完整密钥，则更新
        if 'api_key' in update_data:
            api_key = update_data.get('api_key', '')
            # 如果应该跳过更新（占位符或截断的密钥），则删除该字段
            if should_skip_api_key_update(api_key):
                del update_data['api_key']
            # 如果是空字符串，保留（表示清空）
            # 如果是有效的完整密钥，保留（表示更新）

        if 'api_secret' in update_data:
            api_secret = update_data.get('api_secret', '')
            # 同样的逻辑处理 API Secret
            if should_skip_api_key_update(api_secret):
                del update_data['api_secret']

        success = await config_service.update_llm_provider(provider_id, update_data)

        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="update_llm_provider",
                    details={"provider_id": provider_id, "changed_keys": list(request.model_dump().keys())},
                    success=True,
                )
            except Exception:
                pass
            return {
                "success": True,
                "message": "厂家更新成功",
                "data": {}
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="厂家不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新厂家失败: {str(e)}"
        )


@router.delete("/{provider_id}", response_model=dict)
async def delete_llm_provider(
    provider_id: str,
    current_user: User = Depends(get_current_user)
):
    """删除大模型厂家"""
    try:
        success = await config_service.delete_llm_provider(provider_id)

        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="delete_llm_provider",
                    details={"provider_id": provider_id},
                    success=True,
                )
            except Exception:
                pass
            return {
                "success": True,
                "message": "厂家删除成功",
                "data": {}
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="厂家不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除厂家失败: {str(e)}"
        )


@router.patch("/{provider_id}/toggle", response_model=dict)
async def toggle_llm_provider(
    provider_id: str,
    request: dict,
    current_user: User = Depends(get_current_user)
):
    """切换大模型厂家状态"""
    try:
        is_active = request.get("is_active", True)
        success = await config_service.toggle_llm_provider(provider_id, is_active)

        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="toggle_llm_provider",
                    details={"provider_id": provider_id, "is_active": bool(is_active)},
                    success=True,
                )
            except Exception:
                pass
            return {
                "success": True,
                "message": f"厂家已{'启用' if is_active else '禁用'}",
                "data": {}
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="厂家不存在"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"切换厂家状态失败: {str(e)}"
        )


@router.post("/{provider_id}/fetch-models", response_model=dict)
async def fetch_provider_models(
    provider_id: str,
    current_user: User = Depends(get_current_user)
):
    """从厂家 API 获取模型列表"""
    try:
        result = await config_service.fetch_provider_models(provider_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"获取模型列表失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取模型列表失败: {str(e)}"
        )


@router.post("/migrate-env", response_model=dict)
async def migrate_env_to_providers(
    current_user: User = Depends(get_current_user)
):
    """将环境变量配置迁移到厂家管理"""
    try:
        result = await config_service.migrate_env_to_providers()
        # 审计日志（忽略异常）
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="migrate_env_to_providers",
                details={
                    "migrated_count": result.get("migrated_count", 0),
                    "skipped_count": result.get("skipped_count", 0)
                },
                success=bool(result.get("success", False)),
            )
        except Exception:
            pass

        return {
            "success": result["success"],
            "message": result["message"],
            "data": {
                "migrated_count": result.get("migrated_count", 0),
                "skipped_count": result.get("skipped_count", 0)
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"环境变量迁移失败: {str(e)}"
        )


@router.post("/init-aggregators", response_model=dict)
async def init_aggregator_providers(
    current_user: User = Depends(get_current_user)
):
    """初始化聚合渠道厂家配置（302.AI、OpenRouter等）"""
    try:
        result = await config_service.init_aggregator_providers()

        # 审计日志（忽略异常）
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="init_aggregator_providers",
                details={
                    "added_count": result.get("added", 0),
                    "skipped_count": result.get("skipped", 0)
                },
                success=bool(result.get("success", False)),
            )
        except Exception:
            pass

        return {
            "success": result["success"],
            "message": result["message"],
            "data": {
                "added_count": result.get("added", 0),
                "skipped_count": result.get("skipped", 0)
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"初始化聚合渠道失败: {str(e)}"
        )


@router.post("/{provider_id}/test", response_model=dict)
async def test_provider_api(
    provider_id: str,
    current_user: User = Depends(get_current_user)
):
    """测试厂家API密钥"""
    try:
        logger.info(f"🧪 收到API测试请求 - provider_id: {provider_id}")
        result = await config_service.test_provider_api(provider_id)
        logger.info(f"🧪 API测试结果: {result}")
        return result
    except Exception as e:
        logger.error(f"测试厂家API失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"测试厂家API失败: {str(e)}"
        )


# ========== 自定义端点管理 ==========

# 注意：自定义端点的路由需要放在 /config/llm/custom-endpoint，
# 所以这里创建一个单独的 router

custom_endpoint_router = APIRouter(prefix="/config/llm", tags=["配置管理"])


@custom_endpoint_router.post("/custom-endpoint", response_model=dict)
async def upsert_custom_llm_endpoint(
    request: CustomLLMEndpointRequest,
    current_user: User = Depends(get_current_user)
):
    """
    一键配置自定义 OpenAI 兼容大模型端点（适配自部署 vLLM）。
    会自动：
    1. 创建/更新厂家配置（llm_providers）
    2. 创建/更新模型配置（llm_configs）
    """
    from app.utils.api_key_utils import is_valid_api_key, should_skip_api_key_update

    provider_name = _normalize_provider_name(request.provider_name)
    normalized_base_url = _normalize_openai_compatible_base_url(request.base_url)
    api_key_provided = request.api_key is not None
    api_key = request.api_key.strip() if api_key_provided else ""

    if api_key_provided:
        if should_skip_api_key_update(api_key):
            raise HTTPException(status_code=400, detail="API Key 不能使用占位符或截断值")
        if api_key and not is_valid_api_key(api_key):
            raise HTTPException(status_code=400, detail="API Key 格式无效（长度需大于10）")

    provider_display_name = request.provider_display_name.strip() or f"自定义-{provider_name}"
    provider_description = request.description.strip() or "用户自定义 OpenAI 兼容端点（如 vLLM）"
    api_key_configured = bool(api_key)

    # 1) Upsert 厂家配置
    providers = await config_service.get_llm_providers()
    existing_provider = next((p for p in providers if p.name == provider_name), None)

    extra_config = dict((existing_provider.extra_config if existing_provider else {}) or {})
    extra_config.update({
        "source": "manual",
        "api_compatible": "openai",
        "deployment_type": "vllm",
        "api_key_optional": bool(request.api_key_optional),
        "test_model": request.model_name
    })

    provider_id: str
    if existing_provider:
        api_key_configured = api_key_configured or bool(getattr(existing_provider, "api_key", None))
        update_data = {
            "display_name": provider_display_name,
            "description": provider_description,
            "is_active": True,
            "default_base_url": normalized_base_url,
            "supported_features": ["chat", "completion", "streaming", "function_calling"],
            "extra_config": extra_config
        }
        # 仅在显式传入 api_key 时才更新该字段
        # - 传空字符串: 清空密钥（无鉴权 vLLM）
        # - 传有效密钥: 覆盖原值
        if api_key_provided:
            update_data["api_key"] = api_key if api_key else ""

        updated = await config_service.update_llm_provider(str(existing_provider.id), update_data)
        if not updated:
            raise HTTPException(status_code=500, detail="更新自定义厂家配置失败")
        provider_id = str(existing_provider.id)
    else:
        if (not api_key) and (not request.api_key_optional):
            raise HTTPException(status_code=400, detail="该配置要求提供 API Key")

        provider = LLMProvider(
            name=provider_name,
            display_name=provider_display_name,
            description=provider_description,
            is_active=True,
            supported_features=["chat", "completion", "streaming", "function_calling"],
            default_base_url=normalized_base_url,
            api_key=api_key if api_key else "",
            extra_config=extra_config
        )
        provider_id = str(await config_service.add_llm_provider(provider))

    # 2) Upsert 模型配置
    llm_config = LLMConfig(
        provider=provider_name,
        model_name=request.model_name,
        model_display_name=request.model_name,
        api_key=api_key if api_key_provided and api_key else "",
        api_base=normalized_base_url,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        timeout=request.timeout,
        enabled=True,
        description=provider_description
    )

    saved = await config_service.update_llm_config(llm_config)
    if not saved:
        raise HTTPException(status_code=500, detail="保存模型配置失败")

    default_set = False
    if request.set_as_default:
        default_set = await config_service.set_default_llm(request.model_name)

    test_result = None
    if request.test_connection:
        test_result = await config_service.test_llm_config(llm_config)

    # 让配置缓存失效，确保新配置立即可见
    try:
        config_provider.invalidate()
    except Exception:
        pass

    # 审计日志（忽略异常）
    try:
        await log_operation(
            user_id=str(getattr(current_user, "id", "")),
            username=getattr(current_user, "username", "unknown"),
            action_type=ActionType.CONFIG_MANAGEMENT,
            action="upsert_custom_llm_endpoint",
            details={
                "provider_name": provider_name,
                "model_name": request.model_name,
                "base_url": normalized_base_url,
                "set_as_default": bool(request.set_as_default),
                "test_connection": bool(request.test_connection)
            },
            success=True,
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": "自定义大模型端点配置成功",
        "data": {
            "provider_id": provider_id,
            "provider_name": provider_name,
            "model_name": request.model_name,
            "api_base": normalized_base_url,
            "api_key_configured": bool(api_key_configured),
            "api_key_optional": bool(request.api_key_optional),
            "set_as_default": bool(default_set),
            "test_result": test_result
        }
    }
