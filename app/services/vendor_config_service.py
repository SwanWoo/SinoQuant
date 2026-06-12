"""
厂商配置管理服务

提供第三方厂商配置的增删改查、测试、导入导出等功能
"""

import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId

from app.core.database import get_mongo_db
from app.core.config import settings
from app.models.vendor_config import (
    VendorConfig, VendorConfigRequest, VendorConfigUpdateRequest, VendorConfigResponse, VendorConfigListItem,
    VendorTestResponse, VendorBulkImportResponse, VendorType, ApiAuthType, VendorStatus,
    VendorTypeInfo, VendorAuthTypeInfo, get_vendor_type_display, get_auth_type_display,
    get_auth_type_fields, VENDOR_TYPE_DISPLAY_NAMES, AUTH_TYPE_DISPLAY_NAMES
)
from app.utils.timezone import now_tz
from app.utils.api_key_utils import is_valid_api_key, truncate_api_key
from app.utils.encryption import encrypt_sensitive_fields, decrypt_sensitive_fields

logger = logging.getLogger(__name__)


class VendorConfigService:
    """厂商配置管理服务类"""

    def __init__(self):
        self.db = None
        self.collection_name = "vendor_configs"

    async def _get_db(self):
        """获取数据库连接"""
        if self.db is None:
            self.db = get_mongo_db()
        return self.db

    async def _get_collection(self):
        """获取集合"""
        db = await self._get_db()
        return db[self.collection_name]

    @staticmethod
    def _encryption_key() -> Optional[str]:
        """获取加密密钥，未配置时返回 None（跳过加解密）"""
        return settings.ENCRYPTION_KEY or None

    def _decrypt_vendor_data(self, data: dict) -> VendorConfig:
        """解密数据库文档中的敏感字段，返回 VendorConfig 实例"""
        enc_key = self._encryption_key()
        if enc_key:
            decrypt_sensitive_fields(data, enc_key)
        return VendorConfig(**data)

    def _sanitize_vendor_config(self, vendor: VendorConfig) -> VendorConfigResponse:
        """对厂商配置进行脱敏处理（先解密，再脱敏）"""
        # 解密敏感字段后再脱敏
        enc_key = self._encryption_key()
        if enc_key:
            vendor_dict = vendor.model_dump()
            decrypt_sensitive_fields(vendor_dict, enc_key)
            api_key_plain = vendor_dict.get("api_key")
            api_secret_plain = vendor_dict.get("api_secret")
            bearer_token_plain = vendor_dict.get("bearer_token")
            username_plain = vendor_dict.get("username")
            password_plain = vendor_dict.get("password")
            oauth2_access_token_plain = vendor_dict.get("oauth2_access_token")
            oauth2_client_id_plain = vendor_dict.get("oauth2_client_id")
        else:
            api_key_plain = vendor.api_key
            api_secret_plain = vendor.api_secret
            bearer_token_plain = vendor.bearer_token
            username_plain = vendor.username
            password_plain = vendor.password
            oauth2_access_token_plain = vendor.oauth2_access_token
            oauth2_client_id_plain = vendor.oauth2_client_id

        # 判断是否有认证信息（使用解密后的明文）
        has_credentials = False
        if vendor.auth_type == ApiAuthType.API_KEY and api_key_plain:
            has_credentials = is_valid_api_key(api_key_plain)
        elif vendor.auth_type == ApiAuthType.API_KEY_SECRET and api_key_plain:
            has_credentials = is_valid_api_key(api_key_plain)
        elif vendor.auth_type == ApiAuthType.BEARER_TOKEN and bearer_token_plain:
            has_credentials = len(bearer_token_plain) > 10
        elif vendor.auth_type == ApiAuthType.BASIC_AUTH and username_plain and password_plain:
            has_credentials = True
        elif vendor.auth_type == ApiAuthType.OAUTH2 and oauth2_access_token_plain:
            has_credentials = True

        # 脱敏处理（基于解密后的明文）
        api_key_display = truncate_api_key(api_key_plain) if api_key_plain else None
        api_secret_display = truncate_api_key(api_secret_plain) if api_secret_plain else None
        bearer_token_display = truncate_api_key(bearer_token_plain) if bearer_token_plain else None
        username_display = username_plain[:3] + "***" if username_plain and len(username_plain) > 3 else username_plain

        return VendorConfigResponse(
            id=str(vendor.id),
            name=vendor.name,
            display_name=vendor.display_name,
            description=vendor.description,
            vendor_type=vendor.vendor_type,
            vendor_type_display=get_vendor_type_display(vendor.vendor_type),
            status=vendor.status,
            is_active=vendor.is_active,
            is_default=vendor.is_default,
            sort_order=vendor.sort_order,
            base_url=vendor.base_url,
            api_version=vendor.api_version,
            auth_type=vendor.auth_type,
            auth_type_display=get_auth_type_display(vendor.auth_type),
            api_key=api_key_display,
            api_secret=api_secret_display,
            bearer_token=bearer_token_display,
            username=username_display,
            has_oauth2=bool(oauth2_client_id_plain or oauth2_access_token_plain),
            timeout=vendor.timeout,
            retry_times=vendor.retry_times,
            retry_delay=vendor.retry_delay,
            rate_limit_per_minute=vendor.rate_limit_per_minute,
            extra_config=vendor.extra_config,
            website=vendor.website,
            api_doc_url=vendor.api_doc_url,
            logo_url=vendor.logo_url,
            supported_features=vendor.supported_features,
            last_tested_at=vendor.last_tested_at,
            last_test_result=vendor.last_test_result,
            last_test_message=vendor.last_test_message,
            created_at=vendor.created_at,
            updated_at=vendor.updated_at,
            has_credentials=has_credentials,
            is_user_config=vendor.user_id is not None
        )

    async def get_vendors(
        self,
        vendor_type: Optional[VendorType] = None,
        is_active: Optional[bool] = None,
        status: Optional[VendorStatus] = None
    ) -> List[VendorConfigResponse]:
        """
        获取厂商配置列表
        
        Args:
            vendor_type: 按厂商类型筛选
            is_active: 按启用状态筛选
            status: 按状态筛选
            
        Returns:
            厂商配置列表（脱敏）
        """
        try:
            collection = await self._get_collection()
            
            # 构建查询条件
            query = {}
            if vendor_type:
                query["vendor_type"] = vendor_type.value
            if is_active is not None:
                query["is_active"] = is_active
            if status:
                query["status"] = status.value
                
            # 查询并排序
            cursor = collection.find(query).sort("sort_order", 1)
            vendors_data = await cursor.to_list(length=None)
            
            vendors = []
            for data in vendors_data:
                try:
                    vendor = self._decrypt_vendor_data(data)
                    vendors.append(self._sanitize_vendor_config(vendor))
                except Exception as e:
                    logger.warning(f"解析厂商配置失败: {e}, data: {data}")
                    continue
                    
            return vendors
        except Exception as e:
            logger.error(f"获取厂商配置列表失败: {e}")
            return []

    async def get_vendor_list_items(
        self,
        vendor_type: Optional[VendorType] = None
    ) -> List[VendorConfigListItem]:
        """
        获取简化的厂商配置列表
        
        Args:
            vendor_type: 按厂商类型筛选
            
        Returns:
            简化的厂商配置列表
        """
        try:
            collection = await self._get_collection()
            
            query = {}
            if vendor_type:
                query["vendor_type"] = vendor_type.value
                
            cursor = collection.find(query).sort("sort_order", 1)
            vendors_data = await cursor.to_list(length=None)
            
            items = []
            for data in vendors_data:
                try:
                    has_credentials = bool(
                        data.get("api_key") or 
                        data.get("bearer_token") or
                        (data.get("username") and data.get("password"))
                    )
                    
                    items.append(VendorConfigListItem(
                        id=str(data["_id"]),
                        name=data["name"],
                        display_name=data["display_name"],
                        vendor_type=VendorType(data["vendor_type"]),
                        vendor_type_display=get_vendor_type_display(VendorType(data["vendor_type"])),
                        status=VendorStatus(data.get("status", "active")),
                        is_active=data.get("is_active", True),
                        is_default=data.get("is_default", False),
                        base_url=data.get("base_url"),
                        has_credentials=has_credentials,
                        sort_order=data.get("sort_order", 0),
                        updated_at=data.get("updated_at", now_tz())
                    ))
                except Exception as e:
                    logger.warning(f"解析厂商列表项失败: {e}")
                    continue
                    
            return items
        except Exception as e:
            logger.error(f"获取厂商列表失败: {e}")
            return []

    async def get_user_vendors(
        self,
        user_id: str,
        vendor_type: Optional[VendorType] = None,
        is_active: Optional[bool] = None,
    ) -> List[VendorConfigResponse]:
        """
        获取用户可见的厂商配置列表（用户自有 + 全局配置）

        Args:
            user_id: 用户ID
            vendor_type: 按厂商类型筛选
            is_active: 按启用状态筛选

        Returns:
            厂商配置列表（脱敏）
        """
        try:
            collection = await self._get_collection()

            query = {"$or": [
                {"user_id": user_id},
                {"user_id": None},
                {"user_id": {"$exists": False}},
            ]}
            if vendor_type:
                query["vendor_type"] = vendor_type.value
            if is_active is not None:
                query["is_active"] = is_active

            cursor = collection.find(query).sort([("sort_order", 1), ("user_id", 1)])
            vendors_data = await cursor.to_list(length=None)

            vendors = []
            for data in vendors_data:
                try:
                    vendor = self._decrypt_vendor_data(data)
                    vendors.append(self._sanitize_vendor_config(vendor))
                except Exception as e:
                    logger.warning(f"解析厂商配置失败: {e}")
                    continue

            return vendors
        except Exception as e:
            logger.error(f"获取用户厂商配置列表失败: {e}")
            return []

    async def get_user_vendor_by_name(self, name: str, user_id: str) -> Optional[VendorConfig]:
        """获取用户专属的厂商配置（带全局回退），返回原始数据"""
        return await self.get_vendor_by_name(name, user_id=user_id)

    async def get_vendor_by_id(self, vendor_id: str) -> Optional[VendorConfigResponse]:
        """
        根据ID获取厂商配置
        
        Args:
            vendor_id: 厂商ID
            
        Returns:
            厂商配置（脱敏）
        """
        try:
            collection = await self._get_collection()
            data = await collection.find_one({"_id": ObjectId(vendor_id)})
            
            if not data:
                return None

            vendor = self._decrypt_vendor_data(data)
            return self._sanitize_vendor_config(vendor)
        except Exception as e:
            logger.error(f"获取厂商配置失败: {e}")
            return None

    async def get_vendor_by_name(self, name: str, user_id: Optional[str] = None) -> Optional[VendorConfig]:
        """
        根据名称获取厂商配置（原始数据，用于内部使用）
        优先返回用户专属配置，其次返回全局配置

        Args:
            name: 厂商标识名称
            user_id: 用户ID（可选，传入时优先查用户配置）

        Returns:
            厂商配置（原始数据）
        """
        try:
            collection = await self._get_collection()

            # 优先查用户专属配置
            if user_id:
                data = await collection.find_one({"name": name, "user_id": user_id})
                if data:
                    return self._decrypt_vendor_data(data)

            # 查全局配置（user_id 为 None 或不存在）
            data = await collection.find_one({"name": name, "user_id": None})
            if data:
                return self._decrypt_vendor_data(data)

            # 兼容旧数据：没有 user_id 字段的文档
            data = await collection.find_one({"name": name, "user_id": {"$exists": False}})
            if data:
                return self._decrypt_vendor_data(data)

            return None
        except Exception as e:
            logger.error(f"获取厂商配置失败: {e}")
            return None

    async def create_vendor(
        self, 
        request: VendorConfigRequest, 
        user_id: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        创建厂商配置
        
        Args:
            request: 厂商配置请求
            user_id: 创建者ID
            
        Returns:
            (成功, 消息, 厂商ID)
        """
        try:
            collection = await self._get_collection()

            # 检查名称在相同作用域是否已存在（全局/用户级）
            scope_query = {"name": request.name, "user_id": user_id}
            existing = await collection.find_one(scope_query)
            if existing:
                return False, f"厂商 '{request.name}' 已存在（{'用户配置' if user_id else '全局配置'}）", None
            
            # 如果设置为默认，取消其他同类型厂商的默认状态
            if request.is_default:
                await collection.update_many(
                    {"vendor_type": request.vendor_type.value},
                    {"$set": {"is_default": False}}
                )
            
            # 创建厂商配置
            vendor = VendorConfig(
                user_id=user_id,
                name=request.name,
                display_name=request.display_name,
                description=request.description,
                vendor_type=request.vendor_type,
                status=request.status,
                is_active=request.is_active,
                is_default=request.is_default,
                sort_order=request.sort_order,
                base_url=request.base_url,
                api_version=request.api_version,
                auth_type=request.auth_type,
                api_key=request.api_key,
                api_secret=request.api_secret,
                bearer_token=request.bearer_token,
                username=request.username,
                password=request.password,
                oauth2_client_id=request.oauth2_client_id,
                oauth2_client_secret=request.oauth2_client_secret,
                oauth2_token_url=request.oauth2_token_url,
                oauth2_scope=request.oauth2_scope,
                timeout=request.timeout,
                retry_times=request.retry_times,
                retry_delay=request.retry_delay,
                rate_limit_per_minute=request.rate_limit_per_minute,
                extra_config=request.extra_config,
                website=request.website,
                api_doc_url=request.api_doc_url,
                logo_url=request.logo_url,
                supported_features=request.supported_features,
                created_by=user_id,
                updated_by=user_id
            )
            vendor_dict = vendor.model_dump(by_alias=True)

            # 加密敏感字段后写入数据库
            enc_key = self._encryption_key()
            if enc_key:
                encrypt_sensitive_fields(vendor_dict, enc_key)

            result = await collection.insert_one(vendor_dict)
            
            logger.info(f"创建厂商配置成功: {request.name}, ID: {result.inserted_id}")
            return True, "创建成功", str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"创建厂商配置失败: {e}")
            return False, f"创建失败: {str(e)}", None

    async def update_vendor(
        self, 
        vendor_id: str, 
        request: VendorConfigUpdateRequest,
        user_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        更新厂商配置
        
        Args:
            vendor_id: 厂商ID
            request: 厂商配置请求
            user_id: 更新者ID
            
        Returns:
            (成功, 消息)
        """
        try:
            collection = await self._get_collection()
            
            # 检查厂商是否存在
            existing = await collection.find_one({"_id": ObjectId(vendor_id)})
            if not existing:
                return False, "厂商不存在"
            
            # 如果修改了名称，检查新名称是否在同一作用域内冲突
            if request.name is not None and request.name != existing["name"]:
                scope_user_id = existing.get("user_id")
                name_conflict = await collection.find_one({"name": request.name, "user_id": scope_user_id})
                if name_conflict:
                    return False, f"厂商名称 '{request.name}' 已被使用"
            
            # 如果设置为默认，取消其他同类型厂商的默认状态
            if request.is_default and not existing.get("is_default"):
                vendor_type = request.vendor_type.value if request.vendor_type else existing.get("vendor_type")
                await collection.update_many(
                    {
                        "vendor_type": vendor_type,
                        "_id": {"$ne": ObjectId(vendor_id)}
                    },
                    {"$set": {"is_default": False}}
                )
            
            # 构建更新数据（只包含有值的字段）
            update_data = {
                "updated_at": now_tz(),
                "updated_by": user_id
            }
            
            # 基础字段 - 只在提供时更新
            if request.name is not None:
                update_data["name"] = request.name
            if request.display_name is not None:
                update_data["display_name"] = request.display_name
            if request.description is not None:
                update_data["description"] = request.description
            if request.vendor_type is not None:
                update_data["vendor_type"] = request.vendor_type.value
            if request.status is not None:
                update_data["status"] = request.status.value
            if request.is_active is not None:
                update_data["is_active"] = request.is_active
            if request.is_default is not None:
                update_data["is_default"] = request.is_default
            if request.sort_order is not None:
                update_data["sort_order"] = request.sort_order
            if request.base_url is not None:
                update_data["base_url"] = request.base_url
            if request.api_version is not None:
                update_data["api_version"] = request.api_version
            if request.auth_type is not None:
                update_data["auth_type"] = request.auth_type.value
            if request.timeout is not None:
                update_data["timeout"] = request.timeout
            if request.retry_times is not None:
                update_data["retry_times"] = request.retry_times
            if request.retry_delay is not None:
                update_data["retry_delay"] = request.retry_delay
            if request.rate_limit_per_minute is not None:
                update_data["rate_limit_per_minute"] = request.rate_limit_per_minute
            if request.extra_config is not None:
                update_data["extra_config"] = request.extra_config
            if request.website is not None:
                update_data["website"] = request.website
            if request.api_doc_url is not None:
                update_data["api_doc_url"] = request.api_doc_url
            if request.logo_url is not None:
                update_data["logo_url"] = request.logo_url
            if request.supported_features is not None:
                update_data["supported_features"] = request.supported_features
            
            # 处理认证信息更新（None 表示不更新，空字符串表示清除）
            # 使用 model_dump 检查字段是否被显式设置
            request_dict = request.model_dump()
            
            if 'api_key' in request_dict:
                update_data["api_key"] = request.api_key
            if 'api_secret' in request_dict:
                update_data["api_secret"] = request.api_secret
            if 'bearer_token' in request_dict:
                update_data["bearer_token"] = request.bearer_token
            if 'username' in request_dict:
                update_data["username"] = request.username
            if 'password' in request_dict:
                update_data["password"] = request.password
            if 'oauth2_client_id' in request_dict:
                update_data["oauth2_client_id"] = request.oauth2_client_id
            if 'oauth2_client_secret' in request_dict:
                update_data["oauth2_client_secret"] = request.oauth2_client_secret
            if 'oauth2_token_url' in request_dict:
                update_data["oauth2_token_url"] = request.oauth2_token_url
            if 'oauth2_scope' in request_dict:
                update_data["oauth2_scope"] = request.oauth2_scope

            # 加密敏感字段后写入数据库
            enc_key = self._encryption_key()
            if enc_key:
                encrypt_sensitive_fields(update_data, enc_key)

            await collection.update_one(
                {"_id": ObjectId(vendor_id)},
                {"$set": update_data}
            )
            
            logger.info(f"更新厂商配置成功: {vendor_id}")
            return True, "更新成功"
            
        except Exception as e:
            logger.error(f"更新厂商配置失败: {e}")
            return False, f"更新失败: {str(e)}"

    async def delete_vendor(self, vendor_id: str) -> Tuple[bool, str]:
        """
        删除厂商配置
        
        Args:
            vendor_id: 厂商ID
            
        Returns:
            (成功, 消息)
        """
        try:
            collection = await self._get_collection()
            
            result = await collection.delete_one({"_id": ObjectId(vendor_id)})
            
            if result.deleted_count == 0:
                return False, "厂商不存在"
            
            logger.info(f"删除厂商配置成功: {vendor_id}")
            return True, "删除成功"
            
        except Exception as e:
            logger.error(f"删除厂商配置失败: {e}")
            return False, f"删除失败: {str(e)}"

    async def toggle_vendor_status(
        self, 
        vendor_id: str, 
        is_active: bool
    ) -> Tuple[bool, str]:
        """
        切换厂商启用状态
        
        Args:
            vendor_id: 厂商ID
            is_active: 是否启用
            
        Returns:
            (成功, 消息)
        """
        try:
            collection = await self._get_collection()
            
            result = await collection.update_one(
                {"_id": ObjectId(vendor_id)},
                {
                    "$set": {
                        "is_active": is_active,
                        "status": VendorStatus.ACTIVE.value if is_active else VendorStatus.INACTIVE.value,
                        "updated_at": now_tz()
                    }
                }
            )
            
            if result.modified_count == 0:
                return False, "厂商不存在或无变化"
            
            status_text = "启用" if is_active else "禁用"
            logger.info(f"厂商配置已{status_text}: {vendor_id}")
            return True, f"已{status_text}"
            
        except Exception as e:
            logger.error(f"切换厂商状态失败: {e}")
            return False, f"操作失败: {str(e)}"

    async def set_default_vendor(
        self, 
        vendor_id: str, 
        vendor_type: VendorType
    ) -> Tuple[bool, str]:
        """
        设置默认厂商
        
        Args:
            vendor_id: 厂商ID
            vendor_type: 厂商类型
            
        Returns:
            (成功, 消息)
        """
        try:
            collection = await self._get_collection()
            
            # 取消同类型其他厂商的默认状态
            await collection.update_many(
                {"vendor_type": vendor_type.value},
                {"$set": {"is_default": False}}
            )
            
            # 设置指定厂商为默认
            result = await collection.update_one(
                {"_id": ObjectId(vendor_id)},
                {"$set": {"is_default": True, "updated_at": now_tz()}}
            )
            
            if result.modified_count == 0:
                return False, "厂商不存在"
            
            logger.info(f"设置默认厂商成功: {vendor_id}")
            return True, "设置成功"
            
        except Exception as e:
            logger.error(f"设置默认厂商失败: {e}")
            return False, f"设置失败: {str(e)}"

    async def test_vendor_connection(
        self, 
        vendor: VendorConfig,
        test_type: str = "connection"
    ) -> VendorTestResponse:
        """
        测试厂商连接
        
        Args:
            vendor: 厂商配置
            test_type: 测试类型
            
        Returns:
            测试结果
        """
        start_time = time.time()
        
        try:
            # 根据厂商类型选择测试方法
            if vendor.vendor_type == VendorType.LLM:
                result = await self._test_llm_vendor(vendor)
            elif vendor.vendor_type == VendorType.DATA_SOURCE:
                result = await self._test_data_source_vendor(vendor)
            else:
                # 通用测试：检查基础配置
                result = await self._test_generic_vendor(vendor)
            
            response_time_ms = (time.time() - start_time) * 1000
            
            # 更新测试记录
            await self._update_test_result(
                str(vendor.id),
                result["success"],
                result["message"]
            )
            
            return VendorTestResponse(
                success=result["success"],
                message=result["message"],
                response_time_ms=response_time_ms,
                details=result.get("details")
            )
            
        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            logger.error(f"测试厂商连接失败: {e}")
            
            await self._update_test_result(
                str(vendor.id),
                False,
                str(e)
            )
            
            return VendorTestResponse(
                success=False,
                message=f"测试失败: {str(e)}",
                response_time_ms=response_time_ms
            )

    async def _test_llm_vendor(self, vendor: VendorConfig) -> Dict[str, Any]:
        """测试大模型厂商连接"""
        try:
            import requests
            
            # 检查必要配置
            if not vendor.base_url:
                return {"success": False, "message": "未配置 Base URL"}
            
            # 构建测试请求
            headers = {"Content-Type": "application/json"}
            
            if vendor.auth_type == ApiAuthType.API_KEY:
                if not vendor.api_key:
                    return {"success": False, "message": "未配置 API Key"}
                headers["Authorization"] = f"Bearer {vendor.api_key}"
            elif vendor.auth_type == ApiAuthType.BEARER_TOKEN:
                if not vendor.bearer_token:
                    return {"success": False, "message": "未配置 Bearer Token"}
                headers["Authorization"] = f"Bearer {vendor.bearer_token}"
            
            # 构建 API URL
            base_url = vendor.base_url.rstrip("/")
            if not base_url.endswith("/v1") and "/v" not in base_url:
                base_url += "/v1"
            
            url = f"{base_url}/models"
            
            # 发送测试请求
            response = requests.get(
                url,
                headers=headers,
                timeout=vendor.timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                return {
                    "success": True,
                    "message": f"连接成功，可用模型数量: {len(models)}",
                    "details": {"models_count": len(models)}
                }
            else:
                return {
                    "success": False,
                    "message": f"连接失败: HTTP {response.status_code}",
                    "details": {"status_code": response.status_code, "response": response.text}
                }
                
        except requests.exceptions.Timeout:
            return {"success": False, "message": "连接超时"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "连接错误，请检查 Base URL"}
        except Exception as e:
            return {"success": False, "message": f"测试异常: {str(e)}"}

    async def _test_data_source_vendor(self, vendor: VendorConfig) -> Dict[str, Any]:
        """测试数据源厂商连接"""
        # 这里可以根据具体数据源类型实现测试逻辑
        # 目前返回基本信息检查
        checks = []
        
        if vendor.base_url:
            checks.append("✓ 已配置 Base URL")
        else:
            checks.append("✗ 未配置 Base URL")
        
        if vendor.auth_type == ApiAuthType.API_KEY and vendor.api_key:
            checks.append("✓ 已配置 API Key")
        elif vendor.auth_type == ApiAuthType.API_KEY_SECRET and vendor.api_key:
            checks.append("✓ 已配置 API Key")
            if vendor.api_secret:
                checks.append("✓ 已配置 API Secret")
            else:
                checks.append("○ 未配置 API Secret（可选）")
        elif vendor.auth_type == ApiAuthType.NONE:
            checks.append("✓ 无需认证")
        else:
            checks.append("✗ 认证信息不完整")
        
        # 简单测试 HTTP 连接
        if vendor.base_url:
            try:
                import requests
                response = requests.head(
                    vendor.base_url, 
                    timeout=5,
                    allow_redirects=True
                )
                checks.append(f"✓ HTTP 连接正常 (Status: {response.status_code})")
            except Exception as e:
                checks.append(f"⚠ HTTP 连接测试失败: {str(e)}")
        
        return {
            "success": True,
            "message": "配置检查完成",
            "details": {"checks": checks}
        }

    async def _test_generic_vendor(self, vendor: VendorConfig) -> Dict[str, Any]:
        """通用厂商测试"""
        checks = []
        
        if vendor.base_url:
            checks.append("✓ 已配置 Base URL")
            # 尝试连接
            try:
                import requests
                response = requests.head(
                    vendor.base_url,
                    timeout=5,
                    allow_redirects=True
                )
                checks.append(f"✓ HTTP 连接正常 (Status: {response.status_code})")
            except Exception as e:
                checks.append(f"⚠ HTTP 连接测试失败: {str(e)}")
        else:
            checks.append("○ 未配置 Base URL")
        
        if vendor.auth_type == ApiAuthType.NONE:
            checks.append("✓ 无需认证")
        elif vendor.auth_type == ApiAuthType.API_KEY and vendor.api_key:
            checks.append("✓ 已配置 API Key")
        elif vendor.auth_type == ApiAuthType.API_KEY_SECRET and vendor.api_key:
            checks.append("✓ 已配置 API Key")
        elif vendor.auth_type == ApiAuthType.BEARER_TOKEN and vendor.bearer_token:
            checks.append("✓ 已配置 Bearer Token")
        elif vendor.auth_type == ApiAuthType.BASIC_AUTH and vendor.username and vendor.password:
            checks.append("✓ 已配置 Basic Auth")
        else:
            checks.append("✗ 认证信息不完整")
        
        return {
            "success": True,
            "message": "配置检查完成",
            "details": {"checks": checks}
        }

    async def _update_test_result(
        self, 
        vendor_id: str, 
        success: bool, 
        message: str
    ):
        """更新测试结果到数据库"""
        try:
            collection = await self._get_collection()
            await collection.update_one(
                {"_id": ObjectId(vendor_id)},
                {
                    "$set": {
                        "last_tested_at": now_tz(),
                        "last_test_result": success,
                        "last_test_message": message
                    }
                }
            )
        except Exception as e:
            logger.warning(f"更新测试结果失败: {e}")

    async def bulk_import(
        self,
        vendors_data: List[Dict[str, Any]],
        overwrite_existing: bool = False,
        user_id: Optional[str] = None
    ) -> VendorBulkImportResponse:
        """
        批量导入厂商配置
        
        Args:
            vendors_data: 厂商配置数据列表
            overwrite_existing: 是否覆盖已存在的配置
            user_id: 用户ID
            
        Returns:
            导入结果
        """
        imported = 0
        skipped = 0
        failed = 0
        errors = []
        
        try:
            collection = await self._get_collection()
            
            for data in vendors_data:
                try:
                    name = data.get("name")
                    if not name:
                        failed += 1
                        errors.append({"data": str(data), "error": "缺少 name 字段"})
                        continue
                    
                    # 检查是否已存在
                    existing = await collection.find_one({"name": name})
                    if existing and not overwrite_existing:
                        skipped += 1
                        continue
                    
                    # 构建厂商配置
                    vendor_data = {
                        "name": name,
                        "display_name": data.get("display_name", name),
                        "description": data.get("description"),
                        "vendor_type": data.get("vendor_type", VendorType.CUSTOM.value),
                        "base_url": data.get("base_url"),
                        "api_key": data.get("api_key"),
                        "api_secret": data.get("api_secret"),
                        "auth_type": data.get("auth_type", ApiAuthType.API_KEY.value),
                        "extra_config": data.get("extra_config", {}),
                        "is_active": True,
                        "status": VendorStatus.ACTIVE.value,
                        "created_at": now_tz(),
                        "updated_at": now_tz(),
                        "created_by": user_id,
                        "updated_by": user_id
                    }

                    # 加密敏感字段
                    enc_key = self._encryption_key()
                    if enc_key:
                        encrypt_sensitive_fields(vendor_data, enc_key)
                    
                    if existing and overwrite_existing:
                        # 更新
                        await collection.update_one(
                            {"_id": existing["_id"]},
                            {"$set": {k: v for k, v in vendor_data.items() if k not in ["created_at", "created_by"]}}
                        )
                        imported += 1
                    else:
                        # 插入
                        vendor_data["_id"] = ObjectId()
                        await collection.insert_one(vendor_data)
                        imported += 1
                        
                except Exception as e:
                    failed += 1
                    errors.append({"data": str(data), "error": str(e)})
            
            return VendorBulkImportResponse(
                success=True,
                message=f"导入完成: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个",
                imported_count=imported,
                skipped_count=skipped,
                failed_count=failed,
                errors=errors
            )
            
        except Exception as e:
            logger.error(f"批量导入失败: {e}")
            return VendorBulkImportResponse(
                success=False,
                message=f"批量导入失败: {str(e)}",
                imported_count=imported,
                skipped_count=skipped,
                failed_count=failed,
                errors=errors
            )

    async def export_vendors(
        self,
        vendor_type: Optional[VendorType] = None
    ) -> List[Dict[str, Any]]:
        """
        导出厂商配置（脱敏）
        
        Args:
            vendor_type: 厂商类型筛选
            
        Returns:
            导出的配置列表
        """
        try:
            collection = await self._get_collection()
            
            query = {}
            if vendor_type:
                query["vendor_type"] = vendor_type.value
            
            cursor = collection.find(query)
            vendors_data = await cursor.to_list(length=None)
            
            export_data = []
            for data in vendors_data:
                # 脱敏处理
                export_item = {
                    "name": data.get("name"),
                    "display_name": data.get("display_name"),
                    "description": data.get("description"),
                    "vendor_type": data.get("vendor_type"),
                    "base_url": data.get("base_url"),
                    "api_version": data.get("api_version"),
                    "auth_type": data.get("auth_type"),
                    "timeout": data.get("timeout"),
                    "retry_times": data.get("retry_times"),
                    "rate_limit_per_minute": data.get("rate_limit_per_minute"),
                    "extra_config": data.get("extra_config", {}),
                    "website": data.get("website"),
                    "api_doc_url": data.get("api_doc_url"),
                    "supported_features": data.get("supported_features", []),
                    "is_active": data.get("is_active"),
                    "sort_order": data.get("sort_order")
                    # 注意：敏感字段如 api_key 等不导出
                }
                export_data.append(export_item)
            
            return export_data
            
        except Exception as e:
            logger.error(f"导出厂商配置失败: {e}")
            return []

    def get_vendor_types(self) -> List[VendorTypeInfo]:
        """获取支持的厂商类型列表"""
        return [
            VendorTypeInfo(
                type=VendorType.LLM,
                display_name="大模型厂商",
                description="提供大语言模型服务的厂商，如 OpenAI、Anthropic 等",
                icon="🤖",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.API_KEY_SECRET,
                    ApiAuthType.BEARER_TOKEN,
                    ApiAuthType.NONE
                ]
            ),
            VendorTypeInfo(
                type=VendorType.DATA_SOURCE,
                display_name="数据源厂商",
                description="提供金融数据、股票数据等服务的厂商",
                icon="📊",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.API_KEY_SECRET,
                    ApiAuthType.BEARER_TOKEN,
                    ApiAuthType.BASIC_AUTH,
                    ApiAuthType.NONE
                ]
            ),
            VendorTypeInfo(
                type=VendorType.STORAGE,
                display_name="存储服务",
                description="对象存储、文件存储等服务",
                icon="💾",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.API_KEY_SECRET,
                    ApiAuthType.OAUTH2
                ]
            ),
            VendorTypeInfo(
                type=VendorType.MESSAGE_QUEUE,
                display_name="消息队列",
                description="消息队列服务",
                icon="📨",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.BASIC_AUTH
                ]
            ),
            VendorTypeInfo(
                type=VendorType.CDN,
                display_name="CDN 服务",
                description="内容分发网络服务",
                icon="🌐",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.API_KEY_SECRET
                ]
            ),
            VendorTypeInfo(
                type=VendorType.ANALYTICS,
                display_name="分析服务",
                description="数据分析、日志分析等服务",
                icon="📈",
                supported_auth_types=[
                    ApiAuthType.API_KEY,
                    ApiAuthType.BEARER_TOKEN
                ]
            ),
            VendorTypeInfo(
                type=VendorType.PAYMENT,
                display_name="支付服务",
                description="支付网关、支付处理服务",
                icon="💳",
                supported_auth_types=[
                    ApiAuthType.API_KEY_SECRET,
                    ApiAuthType.OAUTH2
                ]
            ),
            VendorTypeInfo(
                type=VendorType.CUSTOM,
                display_name="自定义",
                description="自定义第三方服务",
                icon="⚙️",
                supported_auth_types=list(ApiAuthType)
            )
        ]

    def get_auth_types(self) -> List[VendorAuthTypeInfo]:
        """获取支持的认证类型列表"""
        return [
            VendorAuthTypeInfo(
                type=ApiAuthType.API_KEY,
                display_name="API Key",
                description="使用单一的 API Key 进行认证",
                required_fields=["api_key"],
                optional_fields=[]
            ),
            VendorAuthTypeInfo(
                type=ApiAuthType.API_KEY_SECRET,
                display_name="API Key + Secret",
                description="使用 API Key 和 Secret 进行认证",
                required_fields=["api_key"],
                optional_fields=["api_secret"]
            ),
            VendorAuthTypeInfo(
                type=ApiAuthType.BEARER_TOKEN,
                display_name="Bearer Token",
                description="使用 Bearer Token 进行认证",
                required_fields=["bearer_token"],
                optional_fields=[]
            ),
            VendorAuthTypeInfo(
                type=ApiAuthType.BASIC_AUTH,
                display_name="Basic Auth",
                description="使用用户名和密码进行认证",
                required_fields=["username", "password"],
                optional_fields=[]
            ),
            VendorAuthTypeInfo(
                type=ApiAuthType.OAUTH2,
                display_name="OAuth 2.0",
                description="使用 OAuth 2.0 进行认证",
                required_fields=["oauth2_client_id", "oauth2_client_secret", "oauth2_token_url"],
                optional_fields=["oauth2_scope"]
            ),
            VendorAuthTypeInfo(
                type=ApiAuthType.NONE,
                display_name="无需认证",
                description="该服务不需要认证",
                required_fields=[],
                optional_fields=[]
            )
        ]


# 全局服务实例
vendor_config_service = VendorConfigService()
