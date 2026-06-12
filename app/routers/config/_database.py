"""
配置管理 - 数据库配置 CRUD + 测试
"""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.routers.auth_db import get_current_user
from app.models.config import (
    DatabaseConfig, DatabaseConfigRequest, ConfigTestResponse
)
from app.services.config_service import config_service
from app.services.operation_log_service import log_operation
from app.models.operation_log import ActionType

from app.routers.config._common import logger


router = APIRouter(prefix="/config/database", tags=["配置管理"])


@router.get("", response_model=List[DatabaseConfig])
async def get_database_configs(
    current_user: dict = Depends(get_current_user)
):
    """获取所有数据库配置"""
    try:
        logger.info("🔄 获取数据库配置列表...")
        configs = await config_service.get_database_configs()
        logger.info(f"✅ 获取到 {len(configs)} 个数据库配置")
        return configs
    except Exception as e:
        logger.error(f"❌ 获取数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取数据库配置失败: {str(e)}"
        )


@router.get("/{db_name}", response_model=DatabaseConfig)
async def get_database_config(
    db_name: str,
    current_user: dict = Depends(get_current_user)
):
    """获取指定的数据库配置"""
    try:
        logger.info(f"🔄 获取数据库配置: {db_name}")
        config = await config_service.get_database_config(db_name)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据库配置 '{db_name}' 不存在"
            )

        return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取数据库配置失败: {str(e)}"
        )


@router.post("", response_model=dict)
async def add_database_config(
    request: DatabaseConfigRequest,
    current_user: dict = Depends(get_current_user)
):
    """添加数据库配置"""
    try:
        logger.info(f"➕ 添加数据库配置: {request.name}")

        # 转换为 DatabaseConfig 对象
        db_config = DatabaseConfig(**request.model_dump())

        # 添加配置
        success = await config_service.add_database_config(db_config)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="添加数据库配置失败，可能已存在同名配置"
            )

        # 记录操作日志
        await log_operation(
            user_id=current_user["id"],
            username=current_user.get("username", "unknown"),
            action_type=ActionType.CONFIG_MANAGEMENT,
            action=f"添加数据库配置: {request.name}",
            details={"name": request.name, "type": request.type, "host": request.host, "port": request.port}
        )

        return {"success": True, "message": "数据库配置添加成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 添加数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加数据库配置失败: {str(e)}"
        )


@router.put("/{db_name}", response_model=dict)
async def update_database_config(
    db_name: str,
    request: DatabaseConfigRequest,
    current_user: dict = Depends(get_current_user)
):
    """更新数据库配置"""
    try:
        logger.info(f"🔄 更新数据库配置: {db_name}")

        # 检查名称是否匹配
        if db_name != request.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL中的名称与请求体中的名称不匹配"
            )

        # 转换为 DatabaseConfig 对象
        db_config = DatabaseConfig(**request.model_dump())

        # 更新配置
        success = await config_service.update_database_config(db_config)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据库配置 '{db_name}' 不存在"
            )

        # 记录操作日志
        await log_operation(
            user_id=current_user["id"],
            username=current_user.get("username", "unknown"),
            action_type=ActionType.CONFIG_MANAGEMENT,
            action=f"更新数据库配置: {db_name}",
            details={"name": request.name, "type": request.type, "host": request.host, "port": request.port}
        )

        return {"success": True, "message": "数据库配置更新成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 更新数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新数据库配置失败: {str(e)}"
        )


@router.delete("/{db_name}", response_model=dict)
async def delete_database_config(
    db_name: str,
    current_user: dict = Depends(get_current_user)
):
    """删除数据库配置"""
    try:
        logger.info(f"🗑️ 删除数据库配置: {db_name}")

        # 删除配置
        success = await config_service.delete_database_config(db_name)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据库配置 '{db_name}' 不存在"
            )

        # 记录操作日志
        await log_operation(
            user_id=current_user["id"],
            username=current_user.get("username", "unknown"),
            action_type=ActionType.CONFIG_MANAGEMENT,
            action=f"删除数据库配置: {db_name}",
            details={"name": db_name}
        )

        return {"success": True, "message": "数据库配置删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除数据库配置失败: {str(e)}"
        )


@router.post("/{db_name}/test", response_model=ConfigTestResponse)
async def test_saved_database_config(
    db_name: str,
    current_user: dict = Depends(get_current_user)
):
    """测试已保存的数据库配置（从数据库中获取完整配置包括密码）"""
    try:
        logger.info(f"🧪 测试已保存的数据库配置: {db_name}")

        # 从数据库获取完整的系统配置
        config = await config_service.get_system_config()
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="系统配置不存在"
            )

        # 查找指定的数据库配置
        db_config = None
        for db in config.database_configs:
            if db.name == db_name:
                db_config = db
                break

        if not db_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据库配置 '{db_name}' 不存在"
            )

        logger.info(f"✅ 找到数据库配置: {db_config.name} ({db_config.type})")
        logger.info(f"📍 连接信息: {db_config.host}:{db_config.port}")
        logger.info(f"🔐 用户名: {db_config.username or '(无)'}")
        logger.info(f"🔐 密码: {'***' if db_config.password else '(无)'}")

        # 使用完整配置进行测试
        result = await config_service.test_database_config(db_config)

        return ConfigTestResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 测试数据库配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试数据库配置失败: {str(e)}"
        )
