"""
第三方厂商配置数据模型

对应 MongoDB 集合: llm_providers (部分), system_configs
用于统一管理各类第三方服务的 API Key、URL 和配置参数
支持大模型厂商、数据源厂商、以及其他第三方 API 服务
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from app.models.user import PyObjectId
from app.utils.timezone import now_tz


class VendorType(str, Enum):
    """厂商类型枚举 — LLM/数据源/存储/消息队列/CDN/分析/支付/自定义"""
    LLM = "llm"                    # 大模型厂商
    DATA_SOURCE = "data_source"    # 数据源厂商
    STORAGE = "storage"            # 存储服务
    MESSAGE_QUEUE = "message_queue"  # 消息队列
    CDN = "cdn"                    # CDN 服务
    ANALYTICS = "analytics"        # 分析服务
    PAYMENT = "payment"            # 支付服务
    CUSTOM = "custom"              # 自定义类型


class VendorStatus(str, Enum):
    """厂商状态枚举 — 启用/禁用/连接错误/测试中"""
    ACTIVE = "active"              # 启用
    INACTIVE = "inactive"          # 禁用
    ERROR = "error"                # 连接错误
    TESTING = "testing"            # 测试中


class ApiAuthType(str, Enum):
    """API 认证类型 — API Key/Key+Secret/Bearer/Basic Auth/OAuth2/无认证"""
    API_KEY = "api_key"            # 单 API Key
    API_KEY_SECRET = "api_key_secret"  # API Key + Secret
    BEARER_TOKEN = "bearer_token"  # Bearer Token
    BASIC_AUTH = "basic_auth"      # Basic Auth (用户名/密码)
    OAUTH2 = "oauth2"              # OAuth2
    NONE = "none"                  # 无需认证


class VendorConfig(BaseModel):
    """第三方厂商配置模型 — 统一管理厂商 API 地址/密钥/认证/超时等配置"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")

    # 用户隔离（None = 全局配置，有值 = 用户专属配置）
    user_id: Optional[str] = Field(None, description="所属用户ID，None表示全局配置（管理员管理）")

    # 基础信息
    name: str = Field(..., description="厂商唯一标识（英文，如 openai, tushare）")
    display_name: str = Field(..., description="显示名称（如 OpenAI, Tushare）")
    description: Optional[str] = Field(None, description="厂商描述")
    vendor_type: VendorType = Field(..., description="厂商类型")
    
    # 状态
    status: VendorStatus = Field(default=VendorStatus.ACTIVE, description="厂商状态")
    is_active: bool = Field(default=True, description="是否启用")
    is_default: bool = Field(default=False, description="是否为默认厂商")
    sort_order: int = Field(default=0, description="排序顺序")
    
    # API 配置
    base_url: Optional[str] = Field(None, description="基础 API URL")
    api_version: Optional[str] = Field(None, description="API 版本（如 v1, v2）")
    auth_type: ApiAuthType = Field(default=ApiAuthType.API_KEY, description="认证类型")
    
    # 认证信息（加密存储）
    api_key: Optional[str] = Field(None, description="API Key")
    api_secret: Optional[str] = Field(None, description="API Secret")
    bearer_token: Optional[str] = Field(None, description="Bearer Token")
    username: Optional[str] = Field(None, description="用户名（Basic Auth）")
    password: Optional[str] = Field(None, description="密码（Basic Auth）")
    
    # OAuth2 配置
    oauth2_client_id: Optional[str] = Field(None, description="OAuth2 Client ID")
    oauth2_client_secret: Optional[str] = Field(None, description="OAuth2 Client Secret")
    oauth2_token_url: Optional[str] = Field(None, description="OAuth2 Token URL")
    oauth2_scope: Optional[str] = Field(None, description="OAuth2 Scope")
    oauth2_access_token: Optional[str] = Field(None, description="OAuth2 Access Token")
    oauth2_refresh_token: Optional[str] = Field(None, description="OAuth2 Refresh Token")
    oauth2_expires_at: Optional[datetime] = Field(None, description="OAuth2 Token 过期时间")
    
    # 请求配置
    timeout: int = Field(default=30, description="请求超时时间（秒）")
    retry_times: int = Field(default=3, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟（秒）")
    rate_limit_per_minute: Optional[int] = Field(None, description="每分钟请求限制")
    
    # 额外配置参数
    extra_config: Dict[str, Any] = Field(default_factory=dict, description="额外配置参数")
    
    # 元数据
    website: Optional[str] = Field(None, description="官网地址")
    api_doc_url: Optional[str] = Field(None, description="API 文档地址")
    logo_url: Optional[str] = Field(None, description="Logo 地址")
    supported_features: List[str] = Field(default_factory=list, description="支持的功能列表")
    
    # 测试信息
    last_tested_at: Optional[datetime] = Field(None, description="上次测试时间")
    last_test_result: Optional[bool] = Field(None, description="上次测试结果")
    last_test_message: Optional[str] = Field(None, description="上次测试消息")
    
    # 时间戳
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)
    created_by: Optional[str] = Field(None, description="创建者ID")
    updated_by: Optional[str] = Field(None, description="更新者ID")
    
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class VendorConfigRequest(BaseModel):
    """厂商配置创建请求"""
    name: str = Field(..., description="厂商唯一标识")
    display_name: str = Field(..., description="显示名称")
    description: Optional[str] = Field(None, description="厂商描述")
    vendor_type: VendorType = Field(..., description="厂商类型")
    
    status: VendorStatus = Field(default=VendorStatus.ACTIVE)
    is_active: bool = Field(default=True)
    is_default: bool = Field(default=False)
    sort_order: int = Field(default=0)
    
    base_url: Optional[str] = Field(None, description="基础 API URL")
    api_version: Optional[str] = Field(None, description="API 版本")
    auth_type: ApiAuthType = Field(default=ApiAuthType.API_KEY)
    
    # 认证信息（创建/更新时传入）
    api_key: Optional[str] = Field(None, description="API Key")
    api_secret: Optional[str] = Field(None, description="API Secret")
    bearer_token: Optional[str] = Field(None, description="Bearer Token")
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码")
    oauth2_client_id: Optional[str] = Field(None, description="OAuth2 Client ID")
    oauth2_client_secret: Optional[str] = Field(None, description="OAuth2 Client Secret")
    oauth2_token_url: Optional[str] = Field(None, description="OAuth2 Token URL")
    oauth2_scope: Optional[str] = Field(None, description="OAuth2 Scope")
    
    timeout: int = Field(default=30)
    retry_times: int = Field(default=3)
    retry_delay: float = Field(default=1.0)
    rate_limit_per_minute: Optional[int] = Field(None)
    
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    website: Optional[str] = Field(None)
    api_doc_url: Optional[str] = Field(None)
    logo_url: Optional[str] = Field(None)
    supported_features: List[str] = Field(default_factory=list)


class VendorConfigUpdateRequest(BaseModel):
    """厂商配置部分更新请求 — 所有字段可选，仅更新传入的字段"""
    name: Optional[str] = Field(None, description="厂商唯一标识")
    display_name: Optional[str] = Field(None, description="显示名称")
    description: Optional[str] = Field(None, description="厂商描述")
    vendor_type: Optional[VendorType] = Field(None, description="厂商类型")
    
    status: Optional[VendorStatus] = Field(None)
    is_active: Optional[bool] = Field(None)
    is_default: Optional[bool] = Field(None)
    sort_order: Optional[int] = Field(None)
    
    base_url: Optional[str] = Field(None, description="基础 API URL")
    api_version: Optional[str] = Field(None, description="API 版本")
    auth_type: Optional[ApiAuthType] = Field(None, description="认证类型")
    
    # 认证信息（更新时传入，None 表示不更新）
    api_key: Optional[str] = Field(None, description="API Key")
    api_secret: Optional[str] = Field(None, description="API Secret")
    bearer_token: Optional[str] = Field(None, description="Bearer Token")
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码")
    oauth2_client_id: Optional[str] = Field(None, description="OAuth2 Client ID")
    oauth2_client_secret: Optional[str] = Field(None, description="OAuth2 Client Secret")
    oauth2_token_url: Optional[str] = Field(None, description="OAuth2 Token URL")
    oauth2_scope: Optional[str] = Field(None, description="OAuth2 Scope")
    
    timeout: Optional[int] = Field(None)
    retry_times: Optional[int] = Field(None)
    retry_delay: Optional[float] = Field(None)
    rate_limit_per_minute: Optional[int] = Field(None)
    
    extra_config: Optional[Dict[str, Any]] = Field(None)
    website: Optional[str] = Field(None)
    api_doc_url: Optional[str] = Field(None)
    logo_url: Optional[str] = Field(None)
    supported_features: Optional[List[str]] = Field(None)


class VendorConfigResponse(BaseModel):
    """厂商配置响应 — 返回给前端的配置(含脱敏密钥和认证类型显示名)"""
    id: str = Field(..., description="厂商ID")
    name: str = Field(..., description="厂商唯一标识")
    display_name: str = Field(..., description="显示名称")
    description: Optional[str] = Field(None)
    vendor_type: VendorType = Field(...)
    vendor_type_display: str = Field(..., description="厂商类型显示名称")
    
    status: VendorStatus = Field(...)
    is_active: bool = Field(...)
    is_default: bool = Field(...)
    sort_order: int = Field(...)
    
    base_url: Optional[str] = Field(None)
    api_version: Optional[str] = Field(None)
    auth_type: ApiAuthType = Field(...)
    auth_type_display: str = Field(..., description="认证类型显示名称")
    
    # 脱敏后的认证信息
    api_key: Optional[str] = Field(None, description="脱敏后的 API Key")
    api_secret: Optional[str] = Field(None, description="脱敏后的 API Secret")
    bearer_token: Optional[str] = Field(None, description="脱敏后的 Bearer Token")
    username: Optional[str] = Field(None, description="脱敏后的用户名")
    has_oauth2: bool = Field(default=False, description="是否配置了 OAuth2")
    
    timeout: int = Field(...)
    retry_times: int = Field(...)
    retry_delay: float = Field(...)
    rate_limit_per_minute: Optional[int] = Field(None)
    
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    website: Optional[str] = Field(None)
    api_doc_url: Optional[str] = Field(None)
    logo_url: Optional[str] = Field(None)
    supported_features: List[str] = Field(default_factory=list)
    
    last_tested_at: Optional[datetime] = Field(None)
    last_test_result: Optional[bool] = Field(None)
    last_test_message: Optional[str] = Field(None)
    
    created_at: datetime = Field(...)
    updated_at: datetime = Field(...)
    
    # 便捷属性
    has_credentials: bool = Field(..., description="是否已配置认证信息")
    is_user_config: bool = Field(default=False, description="是否为用户专属配置（非全局）")


class VendorConfigListItem(BaseModel):
    """厂商配置列表项 — 列表页用的简化模型"""
    id: str = Field(...)
    name: str = Field(...)
    display_name: str = Field(...)
    vendor_type: VendorType = Field(...)
    vendor_type_display: str = Field(...)
    status: VendorStatus = Field(...)
    is_active: bool = Field(...)
    is_default: bool = Field(...)
    base_url: Optional[str] = Field(None)
    has_credentials: bool = Field(...)
    sort_order: int = Field(...)
    updated_at: datetime = Field(...)
    is_user_config: bool = Field(default=False, description="是否为用户专属配置")


class VendorTestRequest(BaseModel):
    """厂商配置测试请求 — 测试已有配置或新配置的连通性"""
    vendor_id: Optional[str] = Field(None, description="厂商ID（如已保存）")
    config: Optional[VendorConfigRequest] = Field(None, description="厂商配置（如新配置）")
    test_type: str = Field(default="connection", description="测试类型: connection, auth, full")


class VendorTestResponse(BaseModel):
    """厂商配置测试结果 — 连通性/认证/响应时间"""
    success: bool = Field(...)
    message: str = Field(...)
    response_time_ms: Optional[float] = Field(None, description="响应时间（毫秒）")
    details: Optional[Dict[str, Any]] = Field(None, description="详细结果")
    timestamp: datetime = Field(default_factory=now_tz)


class VendorBulkImportItem(BaseModel):
    """批量导入项 — 单个厂商的导入数据"""
    name: str = Field(...)
    display_name: str = Field(...)
    vendor_type: VendorType = Field(...)
    base_url: Optional[str] = Field(None)
    api_key: Optional[str] = Field(None)
    api_secret: Optional[str] = Field(None)
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class VendorBulkImportRequest(BaseModel):
    """批量导入请求 — 一次导入多个厂商配置"""
    vendors: List[VendorBulkImportItem] = Field(...)
    overwrite_existing: bool = Field(default=False, description="是否覆盖已存在的配置")


class VendorBulkImportResponse(BaseModel):
    """批量导入响应 — 导入/跳过/失败计数"""
    success: bool = Field(...)
    message: str = Field(...)
    imported_count: int = Field(...)
    skipped_count: int = Field(...)
    failed_count: int = Field(...)
    errors: List[Dict[str, str]] = Field(default_factory=list)


class VendorTypeInfo(BaseModel):
    """厂商类型信息 — 类型的显示名/描述/图标/支持的认证方式"""
    type: VendorType = Field(...)
    display_name: str = Field(...)
    description: str = Field(...)
    icon: str = Field(...)
    supported_auth_types: List[ApiAuthType] = Field(default_factory=list)


class VendorAuthTypeInfo(BaseModel):
    """认证类型信息 — 认证方式的显示名/必需字段/可选字段"""
    type: ApiAuthType = Field(...)
    display_name: str = Field(...)
    description: str = Field(...)
    required_fields: List[str] = Field(..., description="必需字段列表")
    optional_fields: List[str] = Field(default_factory=list, description="可选字段列表")


# 厂商类型显示名称映射
VENDOR_TYPE_DISPLAY_NAMES = {
    VendorType.LLM: "大模型厂商",
    VendorType.DATA_SOURCE: "数据源厂商",
    VendorType.STORAGE: "存储服务",
    VendorType.MESSAGE_QUEUE: "消息队列",
    VendorType.CDN: "CDN 服务",
    VendorType.ANALYTICS: "分析服务",
    VendorType.PAYMENT: "支付服务",
    VendorType.CUSTOM: "自定义",
}

# 认证类型显示名称映射
AUTH_TYPE_DISPLAY_NAMES = {
    ApiAuthType.API_KEY: "API Key",
    ApiAuthType.API_KEY_SECRET: "API Key + Secret",
    ApiAuthType.BEARER_TOKEN: "Bearer Token",
    ApiAuthType.BASIC_AUTH: "Basic Auth",
    ApiAuthType.OAUTH2: "OAuth 2.0",
    ApiAuthType.NONE: "无需认证",
}

# 认证类型字段映射
AUTH_TYPE_FIELDS = {
    ApiAuthType.API_KEY: {
        "required": ["api_key"],
        "optional": []
    },
    ApiAuthType.API_KEY_SECRET: {
        "required": ["api_key"],
        "optional": ["api_secret"]
    },
    ApiAuthType.BEARER_TOKEN: {
        "required": ["bearer_token"],
        "optional": []
    },
    ApiAuthType.BASIC_AUTH: {
        "required": ["username", "password"],
        "optional": []
    },
    ApiAuthType.OAUTH2: {
        "required": ["oauth2_client_id", "oauth2_client_secret", "oauth2_token_url"],
        "optional": ["oauth2_scope"]
    },
    ApiAuthType.NONE: {
        "required": [],
        "optional": []
    },
}


def get_vendor_type_display(vendor_type: VendorType) -> str:
    """获取厂商类型显示名称"""
    return VENDOR_TYPE_DISPLAY_NAMES.get(vendor_type, "未知")


def get_auth_type_display(auth_type: ApiAuthType) -> str:
    """获取认证类型显示名称"""
    return AUTH_TYPE_DISPLAY_NAMES.get(auth_type, "未知")


def get_auth_type_fields(auth_type: ApiAuthType) -> Dict[str, List[str]]:
    """获取认证类型所需字段"""
    return AUTH_TYPE_FIELDS.get(auth_type, {"required": [], "optional": []})
