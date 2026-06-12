"""
简化的股票分析服务 — facade（门面）

实际实现已拆分到 app.services.analysis 子包：
  - _analysis_config.py   : create_analysis_config, get_provider_by_model_name 等模块级函数
  - _text_utils.py        : 纯文本工具函数
  - _analysis_runner.py   : AnalysisRunnerMixin（核心分析方法）
  - _task_manager.py      : TaskManagerMixin（任务创建/查询/清理）
  - _result_persistence.py: ResultPersistenceMixin（结果保存/状态更新）

本文件保留 SimpleAnalysisService 类和单例，继承所有 mixin，
并重新导出 create_analysis_config / get_provider_by_model_name 以兼容现有 import。
"""

import concurrent.futures
import logging
from typing import Any, Dict, Optional

from app.services.memory_state_manager import get_memory_state_manager, TaskStatus
from app.core.database import get_mongo_db

from app.services.analysis._analysis_config import (
    create_analysis_config,
    get_provider_by_model_name,
)
from app.services.analysis._analysis_runner import AnalysisRunnerMixin
from app.services.analysis._task_manager import TaskManagerMixin
from app.services.analysis._result_persistence import ResultPersistenceMixin

logger = logging.getLogger("app.services.simple_analysis_service")


class SimpleAnalysisService(TaskManagerMixin, AnalysisRunnerMixin, ResultPersistenceMixin):
    """简化的股票分析服务类（facade）"""

    def __init__(self):
        self._trading_graph_cache = {}
        self.memory_manager = get_memory_state_manager()

        # 进度跟踪器缓存
        self._progress_trackers: Dict[str, Any] = {}

        # 🔧 创建共享的线程池，支持并发执行多个分析任务
        # 默认最多同时执行3个分析任务（可根据服务器资源调整）
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)

        logger.info(f"🔧 [服务初始化] SimpleAnalysisService 实例ID: {id(self)}")
        logger.info(f"🔧 [服务初始化] 内存管理器实例ID: {id(self.memory_manager)}")
        logger.info(f"🔧 [服务初始化] 线程池最大并发数: 3")

        # 简单的股票名称缓存，减少重复查询
        self._stock_name_cache: Dict[str, str] = {}

        # 设置 WebSocket 管理器
        try:
            from app.services.websocket_manager import get_websocket_manager
            self.memory_manager.set_websocket_manager(get_websocket_manager())
        except ImportError:
            logger.warning("⚠️ WebSocket 管理器不可用")

    async def _update_progress_async(self, task_id: str, progress: int, message: str):
        """异步更新进度（内存和MongoDB）"""
        try:
            # 更新内存
            await self.memory_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                progress=progress,
                message=message,
                current_step=message
            )

            # 更新 MongoDB
            from datetime import datetime
            db = get_mongo_db()
            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {
                    "$set": {
                        "progress": progress,
                        "current_step": message,
                        "message": message,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            logger.debug(f"✅ [异步更新] 已更新内存和MongoDB: {progress}%")
        except Exception as e:
            logger.warning(f"⚠️ [异步更新] 失败: {e}")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_analysis_service = None


def get_simple_analysis_service() -> SimpleAnalysisService:
    """获取分析服务实例"""
    global _analysis_service
    if _analysis_service is None:
        logger.info("🔧 [单例] 创建新的 SimpleAnalysisService 实例")
        _analysis_service = SimpleAnalysisService()
    else:
        logger.info(f"🔧 [单例] 返回现有的 SimpleAnalysisService 实例: {id(_analysis_service)}")
    return _analysis_service
