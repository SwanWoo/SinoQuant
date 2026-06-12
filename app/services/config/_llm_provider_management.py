"""
LLM 厂家管理 Mixin
"""

import os
import logging
from typing import List, Optional, Dict, Any
from bson import ObjectId
from app.utils.timezone import now_tz
from app.models.config import LLMProvider

logger = logging.getLogger(__name__)


class LLMProviderMixin:
    """LLM 厂家 CRUD 及环境变量迁移"""

    # ========== 大模型厂家管理 ==========

    async def get_llm_providers(self) -> List[LLMProvider]:
        """获取所有大模型厂家（合并环境变量配置）"""
        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            providers_data = await providers_collection.find().to_list(length=None)
            providers = []

            logger.info(f"🔍 [get_llm_providers] 从数据库获取到 {len(providers_data)} 个供应商")

            for provider_data in providers_data:
                provider = LLMProvider(**provider_data)

                # 判断数据库中的 API Key 是否有效
                db_key_valid = self._is_valid_api_key(provider.api_key)
                logger.info(f"🔍 [get_llm_providers] 供应商 {provider.display_name} ({provider.name}): 数据库密钥有效={db_key_valid}")

                # 初始化 extra_config
                provider.extra_config = provider.extra_config or {}

                if not db_key_valid:
                    # 数据库中的 Key 无效，尝试从环境变量获取
                    logger.info(f"🔍 [get_llm_providers] 尝试从环境变量获取 {provider.name} 的 API 密钥...")
                    env_key = self._get_env_api_key(provider.name)
                    if env_key:
                        provider.api_key = env_key
                        provider.extra_config["source"] = "environment"
                        provider.extra_config["has_api_key"] = True
                        logger.info(f"✅ [get_llm_providers] 从环境变量为厂家 {provider.display_name} 获取API密钥")
                    else:
                        provider.extra_config["has_api_key"] = False
                        logger.warning(f"⚠️ [get_llm_providers] 厂家 {provider.display_name} 的数据库配置和环境变量都未配置有效的API密钥")
                else:
                    # 数据库中的 Key 有效，使用数据库配置
                    provider.extra_config["source"] = "database"
                    provider.extra_config["has_api_key"] = True
                    logger.info(f"✅ [get_llm_providers] 使用数据库配置的 {provider.display_name} API密钥")

                providers.append(provider)

            logger.info(f"🔍 [get_llm_providers] 返回 {len(providers)} 个供应商")
            return providers
        except Exception as e:
            logger.error(f"❌ [get_llm_providers] 获取厂家列表失败: {e}", exc_info=True)
            return []

    def _is_valid_api_key(self, api_key: Optional[str]) -> bool:
        """
        判断 API Key 是否有效

        有效条件：
        1. Key 不为空
        2. Key 不是占位符（不以 'your_' 或 'your-' 开头，不以 '_here' 结尾）
        3. Key 不是截断的密钥（不包含 '...'）
        4. Key 长度 >= 3（允许短密钥用于本地/测试环境）

        Args:
            api_key: 待验证的 API Key

        Returns:
            bool: True 表示有效，False 表示无效
        """
        if not api_key:
            return False

        # 去除首尾空格
        api_key = api_key.strip()

        # 检查是否为空
        if not api_key:
            return False

        # 检查是否为占位符（前缀）
        if api_key.startswith('your_') or api_key.startswith('your-'):
            return False

        # 检查是否为占位符（后缀）
        if api_key.endswith('_here') or api_key.endswith('-here'):
            return False

        # 检查是否为截断的密钥（包含 '...'）
        if '...' in api_key:
            return False

        # 检查长度（允许短密钥用于本地/测试环境，如 Ollama）
        if len(api_key) < 3:
            return False

        return True

    def _get_env_api_key(self, provider_name: str) -> Optional[str]:
        """从环境变量获取API密钥"""
        # 环境变量映射表
        env_key_mapping = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
            "azure": "AZURE_OPENAI_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            # 聚合渠道
            "302ai": "AI302_API_KEY",
            "oneapi": "ONEAPI_API_KEY",
            "newapi": "NEWAPI_API_KEY",
            "custom_aggregator": "CUSTOM_AGGREGATOR_API_KEY"
        }

        # 对于自定义厂家，回退到通用命名规则：{PROVIDER_NAME}_API_KEY
        env_var = env_key_mapping.get(provider_name) or f"{provider_name.upper()}_API_KEY"
        if env_var:
            api_key = os.getenv(env_var)
            # 使用统一的验证方法
            if self._is_valid_api_key(api_key):
                return api_key

        return None

    async def add_llm_provider(self, provider: LLMProvider) -> str:
        """添加大模型厂家"""
        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            # 检查厂家名称是否已存在
            existing = await providers_collection.find_one({"name": provider.name})
            if existing:
                raise ValueError(f"厂家 {provider.name} 已存在")

            provider.created_at = now_tz()
            provider.updated_at = now_tz()

            # 修复：删除 _id 字段，让 MongoDB 自动生成 ObjectId
            provider_data = provider.model_dump(by_alias=True, exclude_unset=True)
            if "_id" in provider_data:
                del provider_data["_id"]

            result = await providers_collection.insert_one(provider_data)
            return str(result.inserted_id)
        except Exception as e:
            print(f"添加厂家失败: {e}")
            raise

    async def update_llm_provider(self, provider_id: str, update_data: Dict[str, Any]) -> bool:
        """更新大模型厂家"""
        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            update_data["updated_at"] = now_tz()

            # 兼容处理：尝试 ObjectId 和字符串两种类型
            # 原因：历史数据可能混用了 ObjectId 和字符串作为 _id
            try:
                # 先尝试作为 ObjectId 查询
                result = await providers_collection.update_one(
                    {"_id": ObjectId(provider_id)},
                    {"$set": update_data}
                )

                # 如果没有匹配到，再尝试作为字符串查询
                if result.matched_count == 0:
                    result = await providers_collection.update_one(
                        {"_id": provider_id},
                        {"$set": update_data}
                    )
            except Exception:
                # 如果 ObjectId 转换失败，直接用字符串查询
                result = await providers_collection.update_one(
                    {"_id": provider_id},
                    {"$set": update_data}
                )

            # 修复：matched_count > 0 表示找到了记录（即使没有修改）
            # modified_count > 0 只有在实际修改了字段时才为真
            # 如果记录存在但值相同，modified_count 为 0，但这不应该返回 404
            return result.matched_count > 0
        except Exception as e:
            print(f"更新厂家失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def delete_llm_provider(self, provider_id: str) -> bool:
        """删除大模型厂家"""
        try:
            print(f"🗑️ 删除厂家 - provider_id: {provider_id}")
            print(f"🔍 ObjectId类型: {type(ObjectId(provider_id))}")

            db = await self._get_db()
            providers_collection = db.llm_providers
            print(f"📊 数据库: {db.name}, 集合: {providers_collection.name}")

            # 先列出所有厂家的ID，看看格式
            all_providers = await providers_collection.find({}, {"_id": 1, "display_name": 1}).to_list(length=None)
            print(f"📋 数据库中所有厂家ID:")
            for p in all_providers:
                print(f"   - {p['_id']} ({type(p['_id'])}) - {p.get('display_name')}")
                if str(p['_id']) == provider_id:
                    print(f"   ✅ 找到匹配的ID!")

            # 尝试不同的查找方式
            print(f"🔍 尝试用ObjectId查找...")
            existing1 = await providers_collection.find_one({"_id": ObjectId(provider_id)})

            print(f"🔍 尝试用字符串查找...")
            existing2 = await providers_collection.find_one({"_id": provider_id})

            print(f"🔍 ObjectId查找结果: {existing1 is not None}")
            print(f"🔍 字符串查找结果: {existing2 is not None}")

            existing = existing1 or existing2
            if not existing:
                print(f"❌ 两种方式都找不到厂家: {provider_id}")
                return False

            print(f"✅ 找到厂家: {existing.get('display_name')}")

            # 使用找到的方式进行删除
            if existing1:
                result = await providers_collection.delete_one({"_id": ObjectId(provider_id)})
            else:
                result = await providers_collection.delete_one({"_id": provider_id})

            success = result.deleted_count > 0

            print(f"🗑️ 删除结果: {success}, deleted_count: {result.deleted_count}")
            return success

        except Exception as e:
            print(f"❌ 删除厂家失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def toggle_llm_provider(self, provider_id: str, is_active: bool) -> bool:
        """切换大模型厂家状态"""
        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            # 兼容处理：尝试 ObjectId 和字符串两种类型
            try:
                # 先尝试作为 ObjectId 查询
                result = await providers_collection.update_one(
                    {"_id": ObjectId(provider_id)},
                    {"$set": {"is_active": is_active, "updated_at": now_tz()}}
                )

                # 如果没有匹配到，再尝试作为字符串查询
                if result.matched_count == 0:
                    result = await providers_collection.update_one(
                        {"_id": provider_id},
                        {"$set": {"is_active": is_active, "updated_at": now_tz()}}
                    )
            except Exception:
                # 如果 ObjectId 转换失败，直接用字符串查询
                result = await providers_collection.update_one(
                    {"_id": provider_id},
                    {"$set": {"is_active": is_active, "updated_at": now_tz()}}
                )

            return result.matched_count > 0
        except Exception as e:
            print(f"切换厂家状态失败: {e}")
            return False

    async def init_aggregator_providers(self) -> Dict[str, Any]:
        """
        初始化聚合渠道厂家配置

        Returns:
            初始化结果统计
        """
        from app.constants.model_capabilities import AGGREGATOR_PROVIDERS

        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            added_count = 0
            skipped_count = 0
            updated_count = 0

            for provider_name, config in AGGREGATOR_PROVIDERS.items():
                # 从环境变量获取 API Key
                api_key = self._get_env_api_key(provider_name)

                # 检查是否已存在
                existing = await providers_collection.find_one({"name": provider_name})

                if existing:
                    # 如果已存在但没有 API Key，且环境变量中有，则更新
                    if not existing.get("api_key") and api_key:
                        update_data = {
                            "api_key": api_key,
                            "is_active": True,  # 有 API Key 则自动启用
                            "updated_at": now_tz()
                        }
                        await providers_collection.update_one(
                            {"name": provider_name},
                            {"$set": update_data}
                        )
                        updated_count += 1
                        print(f"✅ 更新聚合渠道 {config['display_name']} 的 API Key")
                    else:
                        skipped_count += 1
                        print(f"⏭️ 聚合渠道 {config['display_name']} 已存在，跳过")
                    continue

                # 创建聚合渠道厂家配置
                provider_data = {
                    "name": provider_name,
                    "display_name": config["display_name"],
                    "description": config["description"],
                    "website": config.get("website"),
                    "api_doc_url": config.get("api_doc_url"),
                    "default_base_url": config["default_base_url"],
                    "is_active": bool(api_key),  # 有 API Key 则自动启用
                    "supported_features": ["chat", "completion", "function_calling", "streaming"],
                    "api_key": api_key or "",
                    "extra_config": {
                        "supported_providers": config.get("supported_providers", []),
                        "source": "environment" if api_key else "manual"
                    },
                    # 聚合渠道标识
                    "is_aggregator": True,
                    "aggregator_type": "openai_compatible",
                    "model_name_format": config.get("model_name_format", "{provider}/{model}"),
                    "created_at": now_tz(),
                    "updated_at": now_tz()
                }

                provider = LLMProvider(**provider_data)
                # 修复：删除 _id 字段，让 MongoDB 自动生成 ObjectId
                insert_data = provider.model_dump(by_alias=True, exclude_unset=True)
                if "_id" in insert_data:
                    del insert_data["_id"]
                await providers_collection.insert_one(insert_data)
                added_count += 1

                if api_key:
                    print(f"✅ 添加聚合渠道: {config['display_name']} (已从环境变量获取 API Key)")
                else:
                    print(f"✅ 添加聚合渠道: {config['display_name']} (需手动配置 API Key)")

            message_parts = []
            if added_count > 0:
                message_parts.append(f"成功添加 {added_count} 个聚合渠道")
            if updated_count > 0:
                message_parts.append(f"更新 {updated_count} 个")
            if skipped_count > 0:
                message_parts.append(f"跳过 {skipped_count} 个已存在的")

            return {
                "success": True,
                "added": added_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "message": "，".join(message_parts) if message_parts else "无变更"
            }

        except Exception as e:
            print(f"❌ 初始化聚合渠道失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "message": "初始化聚合渠道失败"
            }

    async def migrate_env_to_providers(self) -> Dict[str, Any]:
        """将环境变量配置迁移到厂家管理"""
        try:
            db = await self._get_db()
            providers_collection = db.llm_providers

            # 预设厂家配置
            default_providers = [
                {
                    "name": "openai",
                    "display_name": "OpenAI",
                    "description": "OpenAI是人工智能领域的领先公司，提供GPT系列模型",
                    "website": "https://openai.com",
                    "api_doc_url": "https://platform.openai.com/docs",
                    "default_base_url": "https://api.openai.com/v1",
                    "supported_features": ["chat", "completion", "embedding", "image", "vision", "function_calling", "streaming"]
                },
                {
                    "name": "anthropic",
                    "display_name": "Anthropic",
                    "description": "Anthropic专注于AI安全研究，提供Claude系列模型",
                    "website": "https://anthropic.com",
                    "api_doc_url": "https://docs.anthropic.com",
                    "default_base_url": "https://api.anthropic.com",
                    "supported_features": ["chat", "completion", "function_calling", "streaming"]
                },
                {
                    "name": "dashscope",
                    "display_name": "阿里云百炼",
                    "description": "阿里云百炼大模型服务平台，提供通义千问等模型",
                    "website": "https://bailian.console.aliyun.com",
                    "api_doc_url": "https://help.aliyun.com/zh/dashscope/",
                    "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "supported_features": ["chat", "completion", "embedding", "function_calling", "streaming"]
                },
                {
                    "name": "deepseek",
                    "display_name": "DeepSeek",
                    "description": "DeepSeek提供高性能的AI推理服务",
                    "website": "https://www.deepseek.com",
                    "api_doc_url": "https://platform.deepseek.com/api-docs",
                    "default_base_url": "https://api.deepseek.com",
                    "supported_features": ["chat", "completion", "function_calling", "streaming"]
                }
            ]

            migrated_count = 0
            updated_count = 0
            skipped_count = 0

            for provider_config in default_providers:
                # 从环境变量获取API密钥
                api_key = self._get_env_api_key(provider_config["name"])

                # 检查是否已存在
                existing = await providers_collection.find_one({"name": provider_config["name"]})

                if existing:
                    # 如果已存在但没有API密钥，且环境变量中有密钥，则更新
                    if not existing.get("api_key") and api_key:
                        update_data = {
                            "api_key": api_key,
                            "is_active": True,
                            "extra_config": {"migrated_from": "environment"},
                            "updated_at": now_tz()
                        }
                        await providers_collection.update_one(
                            {"name": provider_config["name"]},
                            {"$set": update_data}
                        )
                        updated_count += 1
                        print(f"✅ 更新厂家 {provider_config['display_name']} 的API密钥")
                    else:
                        skipped_count += 1
                        print(f"⏭️ 跳过厂家 {provider_config['display_name']} (已有配置)")
                    continue

                # 创建新厂家配置
                provider_data = {
                    **provider_config,
                    "api_key": api_key,
                    "is_active": bool(api_key),  # 有密钥的自动启用
                    "extra_config": {"migrated_from": "environment"} if api_key else {},
                    "created_at": now_tz(),
                    "updated_at": now_tz()
                }

                await providers_collection.insert_one(provider_data)
                migrated_count += 1
                print(f"✅ 创建厂家 {provider_config['display_name']}")

            total_changes = migrated_count + updated_count
            message_parts = []
            if migrated_count > 0:
                message_parts.append(f"新建 {migrated_count} 个厂家")
            if updated_count > 0:
                message_parts.append(f"更新 {updated_count} 个厂家的API密钥")
            if skipped_count > 0:
                message_parts.append(f"跳过 {skipped_count} 个已配置的厂家")

            if total_changes > 0:
                message = "迁移完成：" + "，".join(message_parts)
            else:
                message = "所有厂家都已配置，无需迁移"

            return {
                "success": True,
                "migrated_count": migrated_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "message": message
            }

        except Exception as e:
            print(f"环境变量迁移失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "环境变量迁移失败"
            }
