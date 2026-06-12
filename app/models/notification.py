"""
通知数据模型（MongoDB + Pydantic）

对应 MongoDB 集合: notifications
"""
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field, field_serializer
from bson import ObjectId
from app.utils.timezone import now_tz


NotificationType = Literal['analysis', 'alert', 'system']  # 通知类型: 分析完成/告警/系统通知
NotificationStatus = Literal['unread', 'read']  # 通知状态: 未读/已读


class NotificationCreate(BaseModel):
    """创建通知请求 — 生成一条新的用户通知"""
    user_id: str
    type: NotificationType
    title: str
    content: Optional[str] = None
    link: Optional[str] = None
    source: Optional[str] = None
    severity: Optional[Literal['info','success','warning','error']] = None
    metadata: Optional[Dict[str, Any]] = None


class NotificationDB(BaseModel):
    """通知数据库模型 — 对应 notifications 集合的完整文档结构"""
    id: Optional[str] = Field(default=None)
    user_id: str
    type: NotificationType
    title: str
    content: Optional[str] = None
    link: Optional[str] = None
    source: Optional[str] = None
    severity: Optional[Literal['info','success','warning','error']] = 'info'
    status: NotificationStatus = 'unread'
    created_at: datetime = Field(default_factory=now_tz)
    metadata: Optional[Dict[str, Any]] = None


class NotificationOut(BaseModel):
    """通知输出模型 — 返回给前端的通知数据(含序列化时间)"""
    id: str
    type: NotificationType
    title: str
    content: Optional[str] = None
    link: Optional[str] = None
    source: Optional[str] = None
    status: NotificationStatus
    created_at: datetime

    @field_serializer('created_at')
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """序列化 datetime 为 ISO 8601 格式，保留时区信息"""
        if dt:
            return dt.isoformat()
        return None


class NotificationList(BaseModel):
    """通知列表模型 — 分页返回通知数据"""
    items: List[NotificationOut]
    total: int = 0
    page: int = 1
    page_size: int = 20


