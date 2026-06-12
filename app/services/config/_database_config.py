"""
数据库配置管理 Mixin
"""

import logging
from typing import List, Optional
from app.models.config import DatabaseConfig

logger = logging.getLogger(__name__)


class DatabaseConfigMixin:
    """数据库配置 CRUD"""

    # ========== 数据库配置管理 ==========

    async def add_database_config(self, db_config: DatabaseConfig) -> bool:
        """添加数据库配置"""
        try:
            logger.info(f"➕ 添加数据库配置: {db_config.name}")

            config = await self.get_system_config()
            if not config:
                logger.error("❌ 系统配置为空")
                return False

            # 检查是否已存在同名配置
            for existing_db in config.database_configs:
                if existing_db.name == db_config.name:
                    logger.error(f"❌ 数据库配置 '{db_config.name}' 已存在")
                    return False

            # 添加新配置
            config.database_configs.append(db_config)

            # 保存配置
            result = await self.save_system_config(config)
            if result:
                logger.info(f"✅ 数据库配置 '{db_config.name}' 添加成功")
            else:
                logger.error(f"❌ 数据库配置 '{db_config.name}' 添加失败")

            return result

        except Exception as e:
            logger.error(f"❌ 添加数据库配置失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def update_database_config(self, db_config: DatabaseConfig) -> bool:
        """更新数据库配置"""
        try:
            logger.info(f"🔄 更新数据库配置: {db_config.name}")

            config = await self.get_system_config()
            if not config:
                logger.error("❌ 系统配置为空")
                return False

            # 查找并更新配置
            found = False
            for i, existing_db in enumerate(config.database_configs):
                if existing_db.name == db_config.name:
                    config.database_configs[i] = db_config
                    found = True
                    break

            if not found:
                logger.error(f"❌ 数据库配置 '{db_config.name}' 不存在")
                return False

            # 保存配置
            result = await self.save_system_config(config)
            if result:
                logger.info(f"✅ 数据库配置 '{db_config.name}' 更新成功")
            else:
                logger.error(f"❌ 数据库配置 '{db_config.name}' 更新失败")

            return result

        except Exception as e:
            logger.error(f"❌ 更新数据库配置失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def delete_database_config(self, db_name: str) -> bool:
        """删除数据库配置"""
        try:
            logger.info(f"🗑️ 删除数据库配置: {db_name}")

            config = await self.get_system_config()
            if not config:
                logger.error("❌ 系统配置为空")
                return False

            # 记录原始数量
            original_count = len(config.database_configs)

            # 删除指定配置
            config.database_configs = [
                db for db in config.database_configs
                if db.name != db_name
            ]

            new_count = len(config.database_configs)

            if new_count == original_count:
                logger.error(f"❌ 数据库配置 '{db_name}' 不存在")
                return False

            # 保存配置
            result = await self.save_system_config(config)
            if result:
                logger.info(f"✅ 数据库配置 '{db_name}' 删除成功")
            else:
                logger.error(f"❌ 数据库配置 '{db_name}' 删除失败")

            return result

        except Exception as e:
            logger.error(f"❌ 删除数据库配置失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def get_database_config(self, db_name: str) -> Optional[DatabaseConfig]:
        """获取指定的数据库配置"""
        try:
            config = await self.get_system_config()
            if not config:
                return None

            for db in config.database_configs:
                if db.name == db_name:
                    return db

            return None

        except Exception as e:
            logger.error(f"❌ 获取数据库配置失败: {e}")
            return None

    async def get_database_configs(self) -> List[DatabaseConfig]:
        """获取所有数据库配置"""
        try:
            config = await self.get_system_config()
            if not config:
                return []

            return config.database_configs

        except Exception as e:
            logger.error(f"❌ 获取数据库配置列表失败: {e}")
            return []
