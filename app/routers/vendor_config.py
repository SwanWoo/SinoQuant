"""
第三方厂商配置管理 API 路由

提供厂商配置的增删改查、测试、导入导出等接口
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.routers.auth_db import get_current_user
from app.models.user import User
from app.models.vendor_config import (
    VendorConfigRequest, VendorConfigUpdateRequest, VendorConfigResponse, VendorConfigListItem,
    VendorTestRequest, VendorTestResponse, VendorBulkImportRequest,
    VendorBulkImportResponse, VendorTypeInfo, VendorAuthTypeInfo,
    VendorType, ApiAuthType, VendorStatus
)
from app.services.vendor_config_service import vendor_config_service
from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType

router = APIRouter(prefix="/vendor-configs", tags=["厂商配置管理"])
logger = logging.getLogger("webapi")


def _get_user_id(current_user) -> str:
    """从 current_user（dict 或 User 模型）安全提取用户ID"""
    if isinstance(current_user, dict):
        return current_user.get("id", "")
    return _get_user_id(current_user)


# ========== 厂商类型和认证类型 ==========

@router.get("/types", response_model=List[VendorTypeInfo])
async def get_vendor_types(
    current_user: User = Depends(get_current_user)
):
    """获取支持的厂商类型列表"""
    return vendor_config_service.get_vendor_types()


@router.get("/auth-types", response_model=List[VendorAuthTypeInfo])
async def get_auth_types(
    current_user: User = Depends(get_current_user)
):
    """获取支持的认证类型列表"""
    return vendor_config_service.get_auth_types()


# ========== 厂商配置 CRUD ==========

@router.get("", response_model=List[VendorConfigResponse])
async def list_vendors(
    vendor_type: Optional[VendorType] = Query(None, description="按厂商类型筛选"),
    is_active: Optional[bool] = Query(None, description="按启用状态筛选"),
    status: Optional[VendorStatus] = Query(None, description="按状态筛选"),
    current_user: User = Depends(get_current_user)
):
    """
    获取厂商配置列表
    
    支持按厂商类型、启用状态、状态筛选
    """
    try:
        vendors = await vendor_config_service.get_vendors(
            vendor_type=vendor_type,
            is_active=is_active,
            status=status
        )
        return vendors
    except Exception as e:
        logger.error(f"获取厂商列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取厂商列表失败: {str(e)}"
        )


@router.get("/list", response_model=List[VendorConfigListItem])
async def list_vendor_items(
    vendor_type: Optional[VendorType] = Query(None, description="按厂商类型筛选"),
    current_user: User = Depends(get_current_user)
):
    """
    获取简化的厂商配置列表
    
    返回简化的列表项，适合下拉选择等场景
    """
    try:
        items = await vendor_config_service.get_vendor_list_items(vendor_type=vendor_type)
        return items
    except Exception as e:
        logger.error(f"获取厂商列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取厂商列表失败: {str(e)}"
        )


# ========== 用户级厂商配置 CRUD ==========

@router.get("/my", response_model=List[VendorConfigResponse])
async def list_my_vendors(
    vendor_type: Optional[VendorType] = Query(None, description="按厂商类型筛选"),
    is_active: Optional[bool] = Query(None, description="按启用状态筛选"),
    current_user: User = Depends(get_current_user)
):
    """
    获取当前用户可见的厂商配置列表（用户自有 + 全局配置）

    用户可以看到自己配置的厂商以及全局默认厂商
    """
    try:
        user_id = _get_user_id(current_user)
        vendors = await vendor_config_service.get_user_vendors(
            user_id=user_id,
            vendor_type=vendor_type,
            is_active=is_active,
        )
        return vendors
    except Exception as e:
        logger.error(f"获取用户厂商列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户厂商列表失败: {str(e)}"
        )


@router.post("/my", response_model=Dict[str, Any])
async def create_my_vendor(
    request: VendorConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """
    创建当前用户的厂商配置

    用户可以为自己的账号配置独立的 API Key 和 Base URL
    如果同名全局配置已存在，用户配置会优先使用
    """
    try:
        user_id = _get_user_id(current_user)
        success, message, vendor_id = await vendor_config_service.create_vendor(
            request=request,
            user_id=user_id  # user_id 同时作为作用域和审计字段
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )

        return {
            "success": True,
            "message": message,
            "data": {"id": vendor_id}
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建用户厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建用户厂商配置失败: {str(e)}"
        )


@router.put("/my/{vendor_id}", response_model=Dict[str, Any])
async def update_my_vendor(
    vendor_id: str,
    request: VendorConfigUpdateRequest,
    current_user: User = Depends(get_current_user)
):
    """
    更新当前用户的厂商配置

    只能更新属于当前用户的配置，全局配置不可通过此接口修改
    """
    try:
        user_id = _get_user_id(current_user)

        # 所有权校验
        from bson import ObjectId
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        existing = await db["vendor_configs"].find_one({"_id": ObjectId(vendor_id)})
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        if existing.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权修改此配置（不属于当前用户或为全局配置）"
            )

        success, message = await vendor_config_service.update_vendor(
            vendor_id=vendor_id,
            request=request,
            user_id=user_id
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )

        return {"success": True, "message": message}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新用户厂商配置失败: {str(e)}"
        )


@router.delete("/my/{vendor_id}", response_model=Dict[str, Any])
async def delete_my_vendor(
    vendor_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    删除当前用户的厂商配置

    只能删除属于当前用户的配置，全局配置不可通过此接口删除
    """
    try:
        user_id = _get_user_id(current_user)

        # 所有权校验
        from bson import ObjectId
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        existing = await db["vendor_configs"].find_one({"_id": ObjectId(vendor_id)})
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        if existing.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权删除此配置（不属于当前用户或为全局配置）"
            )

        success, message = await vendor_config_service.delete_vendor(vendor_id, user_id=user_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )

        return {"success": True, "message": message}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除用户厂商配置失败: {str(e)}"
        )


@router.post("/my/{vendor_id}/test", response_model=Dict[str, Any])
async def test_my_vendor(
    vendor_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    测试当前用户的厂商配置连接

    只能测试属于当前用户的配置
    """
    try:
        user_id = _get_user_id(current_user)

        # 所有权校验
        from bson import ObjectId
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        existing = await db["vendor_configs"].find_one({"_id": ObjectId(vendor_id)})
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        if existing.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权测试此配置（不属于当前用户或为全局配置）"
            )

        # 复用现有的测试逻辑
        from app.services.vendor_config_test_service import vendor_test_service
        result = await vendor_test_service.test_saved_vendor(vendor_id)

        return {
            "success": result.success,
            "message": result.message,
            "data": {
                "response_time_ms": result.response_time_ms,
                "details": result.details
            } if result.details else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试用户厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试用户厂商配置失败: {str(e)}"
        )


@router.get("/{vendor_id}", response_model=VendorConfigResponse)
async def get_vendor(
    vendor_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    获取单个厂商配置详情
    
    返回完整的厂商配置信息（敏感字段脱敏）
    """
    try:
        vendor = await vendor_config_service.get_vendor_by_id(vendor_id)
        if not vendor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="厂商不存在"
            )
        return vendor
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取厂商详情失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取厂商详情失败: {str(e)}"
        )


@router.post("", response_model=Dict[str, Any])
async def create_vendor(
    request: VendorConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """
    创建厂商配置
    
    创建新的第三方厂商配置
    """
    try:
        success, message, vendor_id = await vendor_config_service.create_vendor(
            request=request,
            user_id=_get_user_id(current_user)
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="create_vendor_config",
                details={
                    "vendor_id": vendor_id,
                    "name": request.name,
                    "vendor_type": request.vendor_type.value
                },
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": message,
            "data": {"id": vendor_id}
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建厂商配置失败: {str(e)}"
        )


@router.put("/{vendor_id}", response_model=Dict[str, Any])
async def update_vendor(
    vendor_id: str,
    request: VendorConfigUpdateRequest,
    current_user: User = Depends(get_current_user)
):
    """
    更新厂商配置
    
    更新指定厂商的配置信息
    """
    try:
        success, message = await vendor_config_service.update_vendor(
            vendor_id=vendor_id,
            request=request,
            user_id=_get_user_id(current_user)
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="update_vendor_config",
                details={
                    "vendor_id": vendor_id,
                    "name": request.name or "unknown",
                    "vendor_type": request.vendor_type.value if request.vendor_type else "unknown"
                },
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新厂商配置失败: {str(e)}"
        )


@router.delete("/{vendor_id}", response_model=Dict[str, Any])
async def delete_vendor(
    vendor_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    删除厂商配置
    
    删除指定的厂商配置
    """
    try:
        success, message = await vendor_config_service.delete_vendor(vendor_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message
            )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="delete_vendor_config",
                details={"vendor_id": vendor_id},
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除厂商配置失败: {str(e)}"
        )


@router.patch("/{vendor_id}/toggle", response_model=Dict[str, Any])
async def toggle_vendor_status(
    vendor_id: str,
    request: Dict[str, bool],
    current_user: User = Depends(get_current_user)
):
    """
    切换厂商启用状态
    
    启用或禁用指定的厂商配置
    """
    try:
        is_active = request.get("is_active", True)
        
        success, message = await vendor_config_service.toggle_vendor_status(
            vendor_id=vendor_id,
            is_active=is_active
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="toggle_vendor_status",
                details={
                    "vendor_id": vendor_id,
                    "is_active": is_active
                },
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换厂商状态失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"切换厂商状态失败: {str(e)}"
        )


@router.post("/{vendor_id}/set-default", response_model=Dict[str, Any])
async def set_default_vendor(
    vendor_id: str,
    request: Dict[str, str],
    current_user: User = Depends(get_current_user)
):
    """
    设置默认厂商
    
    将指定厂商设置为该类型的默认厂商
    """
    try:
        vendor_type_str = request.get("vendor_type")
        if not vendor_type_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少 vendor_type 参数"
            )
        
        vendor_type = VendorType(vendor_type_str)
        
        success, message = await vendor_config_service.set_default_vendor(
            vendor_id=vendor_id,
            vendor_type=vendor_type
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="set_default_vendor",
                details={
                    "vendor_id": vendor_id,
                    "vendor_type": vendor_type.value
                },
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 vendor_type: {str(e)}"
        )
    except Exception as e:
        logger.error(f"设置默认厂商失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"设置默认厂商失败: {str(e)}"
        )


# ========== 测试功能 ==========

@router.post("/test", response_model=VendorTestResponse)
async def test_vendor_config(
    request: VendorTestRequest,
    current_user: User = Depends(get_current_user)
):
    """
    测试厂商配置
    
    测试新配置或已保存配置的连接性
    """
    try:
        from app.models.vendor_config import VendorConfig
        
        if request.vendor_id:
            # 测试已保存的配置
            vendor_data = await vendor_config_service.get_vendor_by_name(
                (await vendor_config_service.get_vendor_by_id(request.vendor_id)).name
            )
            if not vendor_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="厂商不存在"
                )
            # 重新获取完整数据（含解密）
            collection = await vendor_config_service._get_collection()
            raw_data = await collection.find_one({"_id": vendor_data.id})
            vendor = vendor_config_service._decrypt_vendor_data(raw_data)
        elif request.config:
            # 测试新配置
            config_data = request.config
            vendor = VendorConfig(
                name=config_data.name,
                display_name=config_data.display_name,
                description=config_data.description,
                vendor_type=config_data.vendor_type,
                base_url=config_data.base_url,
                api_version=config_data.api_version,
                auth_type=config_data.auth_type,
                api_key=config_data.api_key,
                api_secret=config_data.api_secret,
                bearer_token=config_data.bearer_token,
                username=config_data.username,
                password=config_data.password,
                oauth2_client_id=config_data.oauth2_client_id,
                oauth2_client_secret=config_data.oauth2_client_secret,
                oauth2_token_url=config_data.oauth2_token_url,
                oauth2_scope=config_data.oauth2_scope,
                timeout=config_data.timeout,
                retry_times=config_data.retry_times,
                retry_delay=config_data.retry_delay,
                rate_limit_per_minute=config_data.rate_limit_per_minute,
                extra_config=config_data.extra_config,
                website=config_data.website,
                api_doc_url=config_data.api_doc_url,
                logo_url=config_data.logo_url,
                supported_features=config_data.supported_features
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请提供 vendor_id 或 config"
            )
        
        result = await vendor_config_service.test_vendor_connection(
            vendor=vendor,
            test_type=request.test_type
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试厂商配置失败: {str(e)}"
        )


@router.post("/{vendor_id}/test", response_model=VendorTestResponse)
async def test_saved_vendor(
    vendor_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    测试已保存的厂商配置
    
    测试指定厂商的连接性
    """
    try:
        from app.models.vendor_config import VendorConfig
        
        # 获取完整配置（包含敏感信息）
        collection = await vendor_config_service._get_collection()
        raw_data = await collection.find_one({"_id": __import__('bson').ObjectId(vendor_id)})
        
        if not raw_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="厂商不存在"
            )
        
        vendor = vendor_config_service._decrypt_vendor_data(raw_data)
        
        result = await vendor_config_service.test_vendor_connection(vendor)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试厂商配置失败: {str(e)}"
        )


# ========== 导入导出 ==========

@router.post("/import", response_model=VendorBulkImportResponse)
async def import_vendors(
    request: VendorBulkImportRequest,
    current_user: User = Depends(get_current_user)
):
    """
    批量导入厂商配置
    
    批量导入厂商配置数据
    """
    try:
        # 转换请求数据
        vendors_data = []
        for item in request.vendors:
            vendors_data.append({
                "name": item.name,
                "display_name": item.display_name,
                "vendor_type": item.vendor_type.value,
                "base_url": item.base_url,
                "api_key": item.api_key,
                "api_secret": item.api_secret,
                "extra_config": item.extra_config
            })
        
        result = await vendor_config_service.bulk_import(
            vendors_data=vendors_data,
            overwrite_existing=request.overwrite_existing,
            user_id=_get_user_id(current_user)
        )
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.DATA_IMPORT,
                action="import_vendor_configs",
                details={
                    "imported_count": result.imported_count,
                    "skipped_count": result.skipped_count,
                    "failed_count": result.failed_count
                },
                success=result.success
            )
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        logger.error(f"导入厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导入厂商配置失败: {str(e)}"
        )


@router.get("/export/download")
async def export_vendors(
    vendor_type: Optional[VendorType] = Query(None, description="按厂商类型筛选导出"),
    current_user: User = Depends(get_current_user)
):
    """
    导出厂商配置
    
    导出厂商配置数据（脱敏）
    """
    try:
        export_data = await vendor_config_service.export_vendors(vendor_type=vendor_type)
        
        # 审计日志
        try:
            await log_operation(
                user_id=_get_user_id(current_user),
                username=current_user.get("username", "unknown") if isinstance(current_user, dict) else getattr(current_user, "username", "unknown"),
                action_type=ActionType.DATA_EXPORT,
                action="export_vendor_configs",
                details={
                    "vendor_type": vendor_type.value if vendor_type else None,
                    "exported_count": len(export_data)
                },
                success=True
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "message": f"成功导出 {len(export_data)} 个厂商配置",
            "data": export_data
        }
        
    except Exception as e:
        logger.error(f"导出厂商配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导出厂商配置失败: {str(e)}"
        )


# ========== 预设厂商 ==========

class PresetVendorInfo(BaseModel):
    """预设厂商信息"""
    name: str
    display_name: str
    description: str
    vendor_type: VendorType
    base_url: str
    auth_type: ApiAuthType
    website: str
    api_doc_url: str
    supported_features: List[str]
    extra_config: Dict[str, Any]


@router.get("/presets/list", response_model=List[PresetVendorInfo])
async def get_preset_vendors(
    vendor_type: Optional[VendorType] = Query(None, description="按厂商类型筛选"),
    current_user: User = Depends(get_current_user)
):
    """
    获取预设厂商列表
    
    返回系统预定义的常用厂商配置模板
    """
    presets = [
        # 大模型厂商
        PresetVendorInfo(
            name="openai",
            display_name="OpenAI",
            description="OpenAI 是人工智能领域的领先公司，提供 GPT 系列模型",
            vendor_type=VendorType.LLM,
            base_url="https://api.openai.com/v1",
            auth_type=ApiAuthType.API_KEY,
            website="https://openai.com",
            api_doc_url="https://platform.openai.com/docs",
            supported_features=["chat", "completion", "embedding", "image", "vision", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="anthropic",
            display_name="Anthropic",
            description="Anthropic 专注于 AI 安全研究，提供 Claude 系列模型",
            vendor_type=VendorType.LLM,
            base_url="https://api.anthropic.com",
            auth_type=ApiAuthType.API_KEY,
            website="https://anthropic.com",
            api_doc_url="https://docs.anthropic.com",
            supported_features=["chat", "completion", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="deepseek",
            display_name="DeepSeek",
            description="DeepSeek 提供高性能的 AI 推理服务",
            vendor_type=VendorType.LLM,
            base_url="https://api.deepseek.com",
            auth_type=ApiAuthType.API_KEY,
            website="https://www.deepseek.com",
            api_doc_url="https://platform.deepseek.com/api-docs",
            supported_features=["chat", "completion", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="dashscope",
            display_name="阿里云百炼",
            description="阿里云百炼大模型服务平台，提供通义千问等模型",
            vendor_type=VendorType.LLM,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            auth_type=ApiAuthType.API_KEY,
            website="https://bailian.console.aliyun.com",
            api_doc_url="https://help.aliyun.com/zh/dashscope/",
            supported_features=["chat", "completion", "embedding", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="siliconflow",
            display_name="硅基流动",
            description="硅基流动提供高性价比的 AI 推理服务，支持多种开源模型",
            vendor_type=VendorType.LLM,
            base_url="https://api.siliconflow.cn/v1",
            auth_type=ApiAuthType.API_KEY,
            website="https://siliconflow.cn",
            api_doc_url="https://docs.siliconflow.cn",
            supported_features=["chat", "completion", "embedding", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="302ai",
            display_name="302.AI",
            description="302.AI 是企业级 AI 聚合平台，提供多种主流大模型的统一接口",
            vendor_type=VendorType.LLM,
            base_url="https://api.302.ai/v1",
            auth_type=ApiAuthType.API_KEY,
            website="https://302.ai",
            api_doc_url="https://doc.302.ai",
            supported_features=["chat", "completion", "embedding", "image", "vision", "function_calling", "streaming"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="zhipu",
            display_name="智谱AI",
            description="智谱 AI 提供 GLM 系列中文大模型",
            vendor_type=VendorType.LLM,
            base_url="https://open.bigmodel.cn/api/paas/v4",
            auth_type=ApiAuthType.API_KEY,
            website="https://zhipuai.cn",
            api_doc_url="https://open.bigmodel.cn/doc",
            supported_features=["chat", "completion", "embedding", "function_calling", "streaming"],
            extra_config={}
        ),
        
        # 数据源厂商
        PresetVendorInfo(
            name="tushare",
            display_name="Tushare",
            description="Tushare 专业金融数据接口",
            vendor_type=VendorType.DATA_SOURCE,
            base_url="http://api.tushare.pro",
            auth_type=ApiAuthType.API_KEY,
            website="https://tushare.pro",
            api_doc_url="https://tushare.pro/document/2",
            supported_features=["stock_data", "financial_data", "market_data"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="alpha_vantage",
            display_name="Alpha Vantage",
            description="Alpha Vantage 提供股票、外汇、加密货币数据",
            vendor_type=VendorType.DATA_SOURCE,
            base_url="https://www.alphavantage.co/query",
            auth_type=ApiAuthType.API_KEY,
            website="https://www.alphavantage.co",
            api_doc_url="https://www.alphavantage.co/documentation/",
            supported_features=["stock_data", "forex", "crypto", "technical_indicators"],
            extra_config={}
        ),
        PresetVendorInfo(
            name="quandl",
            display_name="Quandl (Nasdaq Data Link)",
            description="Quandl 提供金融和经济数据",
            vendor_type=VendorType.DATA_SOURCE,
            base_url="https://data.nasdaq.com/api/v3",
            auth_type=ApiAuthType.API_KEY,
            website="https://data.nasdaq.com",
            api_doc_url="https://docs.data.nasdaq.com",
            supported_features=["financial_data", "economic_data", "alternative_data"],
            extra_config={}
        ),
        
        # 存储服务
        PresetVendorInfo(
            name="aws_s3",
            display_name="AWS S3",
            description="Amazon S3 对象存储服务",
            vendor_type=VendorType.STORAGE,
            base_url="https://s3.amazonaws.com",
            auth_type=ApiAuthType.API_KEY_SECRET,
            website="https://aws.amazon.com/s3",
            api_doc_url="https://docs.aws.amazon.com/s3",
            supported_features=["object_storage", "cdn"],
            extra_config={"region": "us-east-1"}
        ),
        PresetVendorInfo(
            name="aliyun_oss",
            display_name="阿里云 OSS",
            description="阿里云对象存储服务",
            vendor_type=VendorType.STORAGE,
            base_url="https://oss.aliyuncs.com",
            auth_type=ApiAuthType.API_KEY_SECRET,
            website="https://www.aliyun.com/product/oss",
            api_doc_url="https://help.aliyun.com/product/31815.html",
            supported_features=["object_storage", "cdn"],
            extra_config={"region": "cn-hangzhou"}
        ),
    ]
    
    if vendor_type:
        presets = [p for p in presets if p.vendor_type == vendor_type]
    
    return presets
