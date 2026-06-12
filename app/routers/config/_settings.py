"""
配置管理 - 系统设置、导出/导入、迁移、默认值设置
"""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.routers.auth_db import get_current_user
from app.models.user import User
from app.services.config_service import config_service
from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType
from app.services.config_provider import provider as config_provider
from app.utils.timezone import now_tz

from app.routers.config._common import (
    logger, SetDefaultRequest, _sanitize_kv
)


router = APIRouter(prefix="/config", tags=["配置管理"])


# ========== 系统设置 ==========

@router.get("/settings", response_model=Dict[str, Any])
async def get_system_settings(
    current_user: User = Depends(get_current_user)
):
    """获取系统设置"""
    try:
        effective = await config_provider.get_effective_system_settings()
        return _sanitize_kv(effective)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统设置失败: {str(e)}"
        )


@router.get("/settings/meta", response_model=dict)
async def get_system_settings_meta(
    current_user: User = Depends(get_current_user)
):
    """获取系统设置的元数据（敏感性、可编辑性、来源、是否有值）。
    返回结构：{success, data: {items: [{key,sensitive,editable,source,has_value}]}, message}
    """
    try:
        meta_map = await config_provider.get_system_settings_meta()
        items = [
            {"key": k, **v} for k, v in meta_map.items()
        ]
        return {"success": True, "data": {"items": items}, "message": ""}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统设置元数据失败: {str(e)}"
        )


@router.put("/settings", response_model=dict)
async def update_system_settings(
    settings: Dict[str, Any],
    current_user: User = Depends(get_current_user)
):
    """更新系统设置"""
    try:
        # 打印接收到的设置（用于调试）
        logger.info(f"📝 接收到的系统设置更新请求，包含 {len(settings)} 项")
        if 'quick_analysis_model' in settings:
            logger.info(f"  ✓ quick_analysis_model: {settings['quick_analysis_model']}")
        else:
            logger.warning(f"  ⚠️  未包含 quick_analysis_model")
        if 'deep_analysis_model' in settings:
            logger.info(f"  ✓ deep_analysis_model: {settings['deep_analysis_model']}")
        else:
            logger.warning(f"  ⚠️  未包含 deep_analysis_model")

        success = await config_service.update_system_settings(settings)
        if success:
            # 审计日志（忽略日志异常，不影响主流程）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="update_system_settings",
                    details={"changed_keys": list(settings.keys())},
                    success=True,
                )
            except Exception:
                pass
            # 失效缓存
            try:
                config_provider.invalidate()
            except Exception:
                pass
            return {"message": "系统设置更新成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="系统设置更新失败"
            )
    except HTTPException:
        raise
    except Exception as e:
        # 审计失败记录（忽略日志异常）
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="update_system_settings",
                details={"changed_keys": list(settings.keys())},
                success=False,
                error_message=str(e),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新系统设置失败: {str(e)}"
        )


# ========== 导出/导入 ==========

@router.post("/export", response_model=dict)
async def export_config(
    current_user: User = Depends(get_current_user)
):
    """导出配置"""
    try:
        config_data = await config_service.export_config()
        # 审计日志（忽略异常）
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.DATA_EXPORT,
                action="export_config",
                details={"size": len(str(config_data))},
                success=True,
            )
        except Exception:
            pass
        return {
            "message": "配置导出成功",
            "data": config_data,
            "exported_at": now_tz().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导出配置失败: {str(e)}"
        )


@router.post("/import", response_model=dict)
async def import_config(
    config_data: Dict[str, Any],
    current_user: User = Depends(get_current_user)
):
    """导入配置"""
    try:
        success = await config_service.import_config(config_data)
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.DATA_IMPORT,
                    action="import_config",
                    details={"keys": list(config_data.keys())[:10]},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "配置导入成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="配置导入失败"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导入配置失败: {str(e)}"
        )


# ========== 迁移 ==========

@router.post("/migrate-legacy", response_model=dict)
async def migrate_legacy_config(
    current_user: User = Depends(get_current_user)
):
    """迁移传统配置"""
    try:
        success = await config_service.migrate_legacy_config()
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="migrate_legacy_config",
                    details={},
                    success=True,
                )
            except Exception:
                pass
            return {"message": "传统配置迁移成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="传统配置迁移失败"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"迁移传统配置失败: {str(e)}"
        )


# ========== 默认值设置（兼容路径） ==========

@router.post("/default/llm", response_model=dict)
async def set_default_llm(
    request: SetDefaultRequest,
    current_user: User = Depends(get_current_user)
):
    """设置默认大模型"""
    try:
        # 开源版本：所有用户都可以修改配置

        success = await config_service.set_default_llm(request.name)
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="set_default_llm",
                    details={"name": request.name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": f"默认大模型已设置为: {request.name}"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="设置默认大模型失败，请检查模型名称是否正确"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置默认大模型失败: {str(e)}"
        )


@router.post("/default/datasource", response_model=dict)
async def set_default_data_source(
    request: SetDefaultRequest,
    current_user: User = Depends(get_current_user)
):
    """设置默认数据源"""
    try:
        # 开源版本：所有用户都可以修改配置

        success = await config_service.set_default_data_source(request.name)
        if success:
            # 审计日志（忽略异常）
            try:
                await log_operation(
                    user_id=str(getattr(current_user, "id", "")),
                    username=getattr(current_user, "username", "unknown"),
                    action_type=ActionType.CONFIG_MANAGEMENT,
                    action="set_default_datasource",
                    details={"name": request.name},
                    success=True,
                )
            except Exception:
                pass
            return {"message": f"默认数据源已设置为: {request.name}"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="设置默认数据源失败，请检查数据源名称是否正确"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置默认数据源失败: {str(e)}"
        )


# ========== 可用模型列表 ==========

@router.get("/models", response_model=List[Dict[str, Any]])
async def get_available_models(
    current_user: User = Depends(get_current_user)
):
    """获取可用的模型列表"""
    try:
        models = await config_service.get_available_models()
        return models
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取模型列表失败: {str(e)}"
        )
