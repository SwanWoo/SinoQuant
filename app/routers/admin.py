"""
管理员路由 - 用户管理和系统监控
所有端点需要管理员权限 (is_admin=True)
"""

import re as re_module
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from bson import ObjectId

from app.routers.auth_db import get_current_user
from app.services.user_service import user_service
from app.services.favorites_service import favorites_service
from app.services.operation_log_service import log_operation, get_operation_log_service
from app.models.operation_log import ActionType, OperationLogQuery
from app.core.database import get_mongo_db

try:
    from sinoquant.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('admin')


router = APIRouter()


# --- Request/Response Models ---

class AdminUpdateUserRequest(BaseModel):
    """管理员编辑用户请求"""
    email: Optional[str] = None
    daily_quota: Optional[int] = None
    concurrent_limit: Optional[int] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_admin: Optional[bool] = None


class AdminResetPasswordRequest(BaseModel):
    """管理员重置密码请求"""
    new_password: str = Field(..., min_length=6, max_length=100)


class AdminCreateUserRequest(BaseModel):
    """管理员创建用户请求"""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., pattern=r'^[^@]+@[^@]+\.[^@]+$')
    password: str = Field(..., min_length=6, max_length=100)
    is_admin: bool = False
    daily_quota: int = 1000
    concurrent_limit: int = 3


# --- Helper ---

def _admin_only(user: dict):
    """检查管理员权限"""
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="权限不足")


# --- Dashboard (必须在 /users/{user_id} 前面，避免路径冲突) ---

@router.get("/dashboard")
async def admin_dashboard(
    user: dict = Depends(get_current_user),
):
    """管理面板概览数据"""
    _admin_only(user)
    try:
        db = get_mongo_db()
        users_col = user_service.users_collection

        # 用户统计
        total_users = users_col.count_documents({})
        active_users = users_col.count_documents({"is_active": True})
        admin_users = users_col.count_documents({"is_admin": True})
        today_users = users_col.count_documents({
            "created_at": {"$gte": datetime.utcnow() - timedelta(days=1)}
        })

        # 分析统计
        total_analyses = await db.analysis_tasks.count_documents({})
        today_analyses = await db.analysis_tasks.count_documents({
            "created_at": {"$gte": datetime.utcnow() - timedelta(days=1)}
        })
        pending_tasks = await db.analysis_tasks.count_documents({"status": "pending"})
        processing_tasks = await db.analysis_tasks.count_documents({"status": "processing"})

        # 最近7天趋势（使用 $sum 近似统计，避免 $addToSet 16MB BSON 限制）
        pipeline = [
            {"$match": {
                "created_at": {"$gte": datetime.utcnow() - timedelta(days=7)},
                "user_id": {"$ne": None},  # 排除无 user_id 的记录
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "analyses": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        daily_trend = []
        async for doc in db.analysis_tasks.aggregate(pipeline):
            daily_trend.append({
                "date": doc["_id"],
                "analyses": doc["analyses"],
            })

        # 最近注册的用户
        recent_users_cursor = users_col.find(
            {}, {"hashed_password": 0}
        ).sort("created_at", -1).limit(5)
        recent_users = []
        for doc in recent_users_cursor:
            doc["id"] = str(doc.pop("_id"))
            for dt_field in ("created_at", "updated_at", "last_login"):
                if dt_field in doc and doc[dt_field]:
                    doc[dt_field] = doc[dt_field].isoformat()
            recent_users.append(doc)

        return {
            "success": True,
            "data": {
                "user_stats": {
                    "total": total_users,
                    "active": active_users,
                    "admins": admin_users,
                    "today_new": today_users,
                },
                "analysis_stats": {
                    "total": total_analyses,
                    "today": today_analyses,
                    "pending": pending_tasks,
                    "processing": processing_tasks,
                },
                "daily_trend": daily_trend,
                "recent_users": recent_users,
            },
        }
    except Exception as e:
        logger.error(f"获取管理面板数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取管理面板数据失败: {str(e)}")


# --- User CRUD ---

@router.get("/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    keyword: Optional[str] = Query(None, description="搜索用户名或邮箱"),
    active_only: Optional[bool] = Query(None, description="仅显示活跃用户"),
    user: dict = Depends(get_current_user),
):
    """获取用户列表（管理员）"""
    _admin_only(user)
    try:
        filter_query = {}
        if keyword:
            # 转义正则特殊字符，防止 ReDoS 攻击
            escaped = re_module.escape(keyword)
            filter_query["$or"] = [
                {"username": {"$regex": escaped, "$options": "i"}},
                {"email": {"$regex": escaped, "$options": "i"}},
            ]
        if active_only is not None:
            filter_query["is_active"] = active_only

        collection = user_service.users_collection
        total = collection.count_documents(filter_query)
        cursor = collection.find(filter_query, {
            "hashed_password": 0
        }).sort("created_at", -1).skip(skip).limit(limit)

        users = []
        for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            for dt_field in ("created_at", "updated_at", "last_login"):
                if dt_field in doc and doc[dt_field]:
                    doc[dt_field] = doc[dt_field].isoformat()
            users.append(doc)

        return {"success": True, "data": {"users": users, "total": total}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户列表失败: {str(e)}")


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    user: dict = Depends(get_current_user),
):
    """获取单个用户详情（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        doc = user_service.users_collection.find_one(
            {"_id": ObjectId(user_id)},
            {"hashed_password": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="用户不存在")

        doc["id"] = str(doc.pop("_id"))
        for dt_field in ("created_at", "updated_at", "last_login"):
            if dt_field in doc and doc[dt_field]:
                doc[dt_field] = doc[dt_field].isoformat()

        return {"success": True, "data": doc}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户详情失败: {str(e)}")


@router.post("/users")
async def create_user(
    payload: AdminCreateUserRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """创建用户（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        from app.models.user import UserCreate

        user_create = UserCreate(
            username=payload.username,
            email=payload.email,
            password=payload.password,
        )
        new_user = await user_service.create_user(user_create)
        if not new_user:
            raise HTTPException(status_code=400, detail="用户名或邮箱已存在")

        # 设置额外字段
        update_data = {
            "updated_at": datetime.utcnow(),
            "daily_quota": payload.daily_quota,
            "concurrent_limit": payload.concurrent_limit,
        }
        if payload.is_admin:
            update_data["is_admin"] = True

        user_service.users_collection.update_one(
            {"_id": new_user.id},
            {"$set": update_data}
        )

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"创建用户: {payload.username}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {
            "success": True,
            "data": {
                "id": str(new_user.id),
                "username": new_user.username,
                "email": new_user.email,
                "is_admin": payload.is_admin,
            },
            "message": f"用户 {payload.username} 创建成功",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建用户失败: {str(e)}")


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """编辑用户（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        target = user_service.users_collection.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")

        update_data = {"updated_at": datetime.utcnow()}
        if payload.email is not None:
            # 检查邮箱唯一性
            existing = user_service.users_collection.find_one({
                "email": payload.email,
                "_id": {"$ne": ObjectId(user_id)}
            })
            if existing:
                raise HTTPException(status_code=400, detail="邮箱已被使用")
            update_data["email"] = payload.email
        if payload.daily_quota is not None:
            update_data["daily_quota"] = payload.daily_quota
        if payload.concurrent_limit is not None:
            update_data["concurrent_limit"] = payload.concurrent_limit
        if payload.is_active is not None:
            update_data["is_active"] = payload.is_active
        if payload.is_verified is not None:
            update_data["is_verified"] = payload.is_verified
        if payload.is_admin is not None:
            update_data["is_admin"] = payload.is_admin

        result = user_service.users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_data}
        )
        if result.modified_count == 0 and not any(v is not None for v in [
            payload.email, payload.daily_quota, payload.concurrent_limit,
            payload.is_active, payload.is_verified, payload.is_admin,
        ]):
            raise HTTPException(status_code=400, detail="没有需要更新的字段")

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"编辑用户: {target['username']}",
            details={"updated_fields": list(update_data.keys())},
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": "用户更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"编辑用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"编辑用户失败: {str(e)}")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """删除用户（软删除 - 禁用账户）（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        target = user_service.users_collection.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 防止删除自己
        if str(target["_id"]) == user["id"]:
            raise HTTPException(status_code=400, detail="不能删除自己的账户")

        user_service.users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "is_active": False,
                "is_verified": False,
                "updated_at": datetime.utcnow(),
            }}
        )

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"删除用户: {target['username']}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": f"用户 {target['username']} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除用户失败: {str(e)}")


@router.post("/users/{user_id}/activate")
async def activate_user(
    user_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """激活用户（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        target = user_service.users_collection.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")

        success = await user_service.activate_user(target["username"])
        if not success:
            raise HTTPException(status_code=400, detail="激活失败")

        return {"success": True, "message": f"用户 {target['username']} 已激活"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"激活用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"激活用户失败: {str(e)}")


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """停用用户（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        target = user_service.users_collection.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")

        if str(target["_id"]) == user["id"]:
            raise HTTPException(status_code=400, detail="不能停用自己的账户")

        success = await user_service.deactivate_user(target["username"])
        if not success:
            raise HTTPException(status_code=400, detail="停用失败")

        return {"success": True, "message": f"用户 {target['username']} 已停用"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"停用用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"停用用户失败: {str(e)}")


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    payload: AdminResetPasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """重置用户密码（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        target = user_service.users_collection.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")

        success = await user_service.reset_password(target["username"], payload.new_password)
        if not success:
            raise HTTPException(status_code=400, detail="重置密码失败")

        return {"success": True, "message": f"用户 {target['username']} 的密码已重置"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置密码失败: {e}")
        raise HTTPException(status_code=500, detail=f"重置密码失败: {str(e)}")


# --- User Data ---

@router.get("/users/{user_id}/analyses")
async def get_user_analyses(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """查看用户的分析记录（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()
        filter_query = {"user_id": ObjectId(user_id)}
        if status:
            filter_query["status"] = status

        total = await db.analysis_tasks.count_documents(filter_query)
        skip = (page - 1) * page_size
        cursor = db.analysis_tasks.find(filter_query).sort("created_at", -1).skip(skip).limit(page_size)

        analyses = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            doc["user_id"] = str(doc.get("user_id", ""))
            if doc.get("batch_id"):
                doc["batch_id"] = str(doc["batch_id"])
            # 确保前端所需的字段存在
            if not doc.get("symbol"):
                doc["symbol"] = doc.get("stock_code") or doc.get("stock_symbol", "")
            if not doc.get("stock_name"):
                doc["stock_name"] = doc.get("symbol", "")
            for dt_field in ("created_at", "started_at", "completed_at"):
                if dt_field in doc and doc[dt_field]:
                    doc[dt_field] = doc[dt_field].isoformat()
            analyses.append(doc)

        return {
            "success": True,
            "data": {"analyses": analyses, "total": total, "page": page, "page_size": page_size},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户分析记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户分析记录失败: {str(e)}")


@router.get("/users/{user_id}/reports")
async def get_user_reports(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """查看用户的分析报告（管理员）- 通过 analysis_tasks 关联"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()

        skip = (page - 1) * page_size
        # 使用 $lookup + $facet 在服务端完成关联和分页，避免加载所有 task_ids 到内存
        pipeline = [
            {"$match": {"user_id": ObjectId(user_id), "status": "completed"}},
            {"$lookup": {
                "from": "analysis_reports",
                "localField": "task_id",
                "foreignField": "task_id",
                "as": "report",
            }},
            {"$unwind": {"path": "$report", "preserveNullAndEmptyArrays": True}},
            {"$match": {"report": {"$ne": None}}},
            {"$sort": {"report.created_at": -1}},
            {"$facet": {
                "total": [{"$count": "count"}],
                "reports": [{"$skip": skip}, {"$limit": page_size}],
            }},
        ]
        result = await db.analysis_tasks.aggregate(pipeline).to_list(1)
        facet = result[0] if result else {"total": [], "reports": []}
        total = facet["total"][0]["count"] if facet["total"] else 0

        reports = []
        for doc in facet["reports"]:
            report = doc.get("report", {})
            report["id"] = str(report.pop("_id", ""))

            # 确保关键字段存在
            if not report.get("stock_symbol") and report.get("stock_code"):
                report["stock_symbol"] = report["stock_code"]
            if not report.get("stock_symbol"):
                report["stock_symbol"] = doc.get("stock_code", "")
            if not report.get("stock_name") and report.get("stock_symbol"):
                try:
                    from app.routers.reports import get_stock_name
                    report["stock_name"] = get_stock_name(report["stock_symbol"])
                except Exception:
                    pass

            for dt_field in ("created_at", "updated_at"):
                if dt_field in report and report[dt_field]:
                    report[dt_field] = report[dt_field].isoformat()
            reports.append(report)

        return {
            "success": True,
            "data": {"reports": reports, "total": total, "page": page, "page_size": page_size},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户报告失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户报告失败: {str(e)}")


@router.get("/users/{user_id}/favorites")
async def get_user_favorites(
    user_id: str,
    user: dict = Depends(get_current_user),
):
    """查看用户自选股（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        # 优先从 user_favorites 集合查
        db = get_mongo_db()
        fav_doc = await db.user_favorites.find_one({"user_id": str(user_id)})
        if fav_doc:
            favorites_raw = fav_doc.get("favorites", [])
            favorites = []
            for f in favorites_raw:
                fav = dict(f) if isinstance(f, dict) else {"stock_code": f}
                if isinstance(fav.get("added_at"), datetime):
                    fav["added_at"] = fav["added_at"].isoformat()
                favorites.append(fav)
        else:
            # 回退到 users 集合的 embedded 字段
            user_doc = user_service.users_collection.find_one(
                {"_id": ObjectId(user_id)},
                {"favorite_stocks": 1}
            )
            favorites_raw = (user_doc or {}).get("favorite_stocks", [])
            favorites = []
            for f in favorites_raw:
                fav = dict(f)
                if isinstance(fav.get("added_at"), datetime):
                    fav["added_at"] = fav["added_at"].isoformat()
                favorites.append(fav)

        return {"success": True, "data": {"favorites": favorites, "total": len(favorites)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户自选股失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户自选股失败: {str(e)}")


@router.get("/users/{user_id}/logs")
async def get_user_logs(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action_type: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """查看用户操作日志（管理员）"""
    _admin_only(user)
    try:
        query = OperationLogQuery(
            user_id=user_id,
            page=page,
            page_size=page_size,
            action_type=action_type,
        )
        service = get_operation_log_service()
        logs, total = await service.get_logs(query)

        return {
            "success": True,
            "data": {
                "logs": [log.model_dump() for log in logs],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户操作日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户操作日志失败: {str(e)}")


@router.get("/users/{user_id}/stats")
async def get_user_stats(
    user_id: str,
    user: dict = Depends(get_current_user),
):
    """查看用户统计数据（管理员）"""
    _admin_only(user)
    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()
        oid = ObjectId(user_id)

        # 从 users 获取基础统计
        user_doc = user_service.users_collection.find_one(
            {"_id": oid},
            {"total_analyses": 1, "successful_analyses": 1, "failed_analyses": 1,
             "daily_quota": 1, "concurrent_limit": 1, "created_at": 1, "last_login": 1}
        )
        if not user_doc:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 从 analysis_tasks 聚合更详细的统计
        pipeline = [
            {"$match": {"user_id": oid}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "tokens_used": {"$sum": {
                    "$cond": [
                        {"$and": [
                            {"$ifNull": ["$result", False]},
                            {"$eq": [{"$type": "$result"}, "object"]},
                        ]},
                        {"$ifNull": ["$result.tokens_used", 0]},
                        0,
                    ]
                }},
            }}
        ]
        status_stats = {}
        total_tokens = 0
        async for doc in db.analysis_tasks.aggregate(pipeline):
            status_stats[doc["_id"]] = doc["count"]
            total_tokens += doc.get("tokens_used", 0)

        # 最近7天分析数
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_count = await db.analysis_tasks.count_documents({
            "user_id": oid,
            "created_at": {"$gte": seven_days_ago},
        })

        return {
            "success": True,
            "data": {
                "total_analyses": user_doc.get("total_analyses", 0),
                "successful_analyses": user_doc.get("successful_analyses", 0),
                "failed_analyses": user_doc.get("failed_analyses", 0),
                "daily_quota": user_doc.get("daily_quota", 1000),
                "concurrent_limit": user_doc.get("concurrent_limit", 3),
                "total_tokens_used": total_tokens,
                "recent_7days_analyses": recent_count,
                "status_breakdown": status_stats,
                "created_at": user_doc.get("created_at"),
                "last_login": user_doc.get("last_login"),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户统计失败: {str(e)}")


@router.delete("/users/{user_id}/analyses/{task_id}")
async def delete_user_analysis(
    user_id: str,
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """删除用户的分析记录及其关联报告（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()

        # 查找分析任务（支持 _id 或 task_id）
        find_query: Dict[str, Any] = {}
        try:
            find_query = {"_id": ObjectId(task_id)}
        except Exception:
            find_query = {"task_id": task_id}
        task = await db.analysis_tasks.find_one(find_query)
        if not task:
            raise HTTPException(status_code=404, detail="分析记录不存在")

        actual_task_id = task["task_id"]

        # 删除关联的报告
        delete_report_result = await db.analysis_reports.delete_one({"task_id": actual_task_id})

        # 删除分析任务
        delete_task_result = await db.analysis_tasks.delete_one({"_id": task["_id"]})

        if delete_task_result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="分析记录不存在")

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"删除分析记录: {actual_task_id}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": f"分析记录 {actual_task_id} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除分析记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除分析记录失败: {str(e)}")


@router.post("/users/{user_id}/analyses/{task_id}/cancel")
async def admin_cancel_analysis(
    user_id: str,
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """取消用户的分析任务（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()

        # 验证任务存在（支持 _id 或 task_id）
        find_query: Dict[str, Any] = {}
        try:
            find_query = {"_id": ObjectId(task_id)}
        except Exception:
            find_query = {"task_id": task_id}
        task = await db.analysis_tasks.find_one(find_query)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")

        # 检查任务状态是否可取消
        if task.get("status") not in ("pending", "processing", "running"):
            raise HTTPException(status_code=400, detail=f"任务状态为 {task.get('status')}，无法取消")

        # 更新任务状态
        result = await db.analysis_tasks.update_one(
            {"_id": task["_id"]},
            {"$set": {
                "status": "cancelled",
                "last_error": "管理员取消",
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }}
        )

        # 尝试从内存中移除
        try:
            from app.services.analysis_service import get_simple_analysis_service
            svc = get_simple_analysis_service()
            await svc.memory_manager.remove_task(task["task_id"])
        except Exception:
            pass

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"取消分析任务: {task['task_id']}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": "任务已取消"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消分析任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消分析任务失败: {str(e)}")


@router.post("/users/{user_id}/analyses/{task_id}/mark-failed")
async def admin_mark_analysis_failed(
    user_id: str,
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """将用户分析任务标记为失败（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()

        # 验证任务存在（支持 _id 或 task_id）
        find_query: Dict[str, Any] = {}
        try:
            find_query = {"_id": ObjectId(task_id)}
        except Exception:
            find_query = {"task_id": task_id}
        task = await db.analysis_tasks.find_one(find_query)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")

        if task.get("status") in ("completed", "failed", "cancelled"):
            raise HTTPException(status_code=400, detail=f"任务状态为 {task.get('status')}，无需标记")

        result = await db.analysis_tasks.update_one(
            {"_id": task["_id"]},
            {"$set": {
                "status": "failed",
                "last_error": "管理员标记为失败",
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }}
        )

        # 尝试更新内存状态
        try:
            from app.services.analysis_service import get_simple_analysis_service
            from app.services.memory_state_manager import TaskStatus
            svc = get_simple_analysis_service()
            await svc.memory_manager.update_task_status(
                task_id=task["task_id"],
                status=TaskStatus.FAILED,
                message="管理员标记为失败",
                error_message="管理员标记为失败"
            )
        except Exception:
            pass

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"标记分析任务失败: {task['task_id']}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": "任务已标记为失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"标记分析任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"标记分析任务失败: {str(e)}")


@router.delete("/users/{user_id}/reports/{report_id}")
async def delete_user_report(
    user_id: str,
    report_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """删除用户的分析报告（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        db = get_mongo_db()

        # 构建查询：支持 _id / analysis_id / task_id
        or_conditions = [
            {"analysis_id": report_id},
            {"task_id": report_id},
        ]
        try:
            or_conditions.append({"_id": ObjectId(report_id)})
        except Exception:
            pass

        result = await db.analysis_reports.delete_one({"$or": or_conditions})

        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="报告不存在")

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"删除报告: {report_id}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": "报告已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除报告失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除报告失败: {str(e)}")


@router.delete("/users/{user_id}/favorites/{stock_code}")
async def delete_user_favorite(
    user_id: str,
    stock_code: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """删除用户的自选股（管理员）"""
    _admin_only(user)
    start_time = time.time()
    ip_address = request.client.host if request.client else "unknown"

    try:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="无效的用户ID")

        success = await favorites_service.remove_favorite(user_id, stock_code)
        if not success:
            raise HTTPException(status_code=404, detail="自选股不存在")

        await log_operation(
            user_id=user["id"], username=user["username"],
            action_type=ActionType.USER_MANAGEMENT,
            action=f"删除用户自选股: {stock_code}",
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
        )

        return {"success": True, "message": f"自选股 {stock_code} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户自选股失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除用户自选股失败: {str(e)}")


# --- Global Logs ---

@router.get("/logs")
async def get_global_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action_type: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    keyword: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """全局操作日志（管理员）"""
    _admin_only(user)
    try:
        query = OperationLogQuery(
            page=page,
            page_size=page_size,
            action_type=action_type,
            success=success,
            keyword=keyword,
            user_id=user_id,
        )
        service = get_operation_log_service()
        logs, total = await service.get_logs(query)

        return {
            "success": True,
            "data": {
                "logs": [log.model_dump() for log in logs],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }
    except Exception as e:
        logger.error(f"获取全局日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取全局日志失败: {str(e)}")
