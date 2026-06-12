"""
任务管理 mixin — 创建任务、后台执行、状态查询、列表、僵尸清理
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.core.database import get_mongo_db
from app.models.analysis import AnalysisStatus, SingleAnalysisRequest
from app.models.notification import NotificationCreate
from app.services.memory_state_manager import TaskStatus
from app.services.progress_log_handler import register_analysis_tracker, unregister_analysis_tracker
from app.services.redis_progress_tracker import RedisProgressTracker, get_progress_by_id

logger = logging.getLogger("app.services.simple_analysis_service")


class TaskManagerMixin:
    """提供任务创建、后台执行、状态查询和僵尸清理功能的 mixin。"""

    # ------------------------------------------------------------------
    # 创建任务
    # ------------------------------------------------------------------

    async def create_analysis_task(
        self,
        user_id: str,
        request: SingleAnalysisRequest
    ) -> Dict[str, Any]:
        """创建分析任务（立即返回，不执行分析）"""
        try:
            # 生成任务ID
            task_id = str(uuid.uuid4())

            # 🔧 使用 get_symbol() 方法获取股票代码（兼容 symbol 和 stock_code 字段）
            stock_code = request.get_symbol()
            if not stock_code:
                raise ValueError("股票代码不能为空")

            logger.info(f"📝 创建分析任务: {task_id} - {stock_code}")
            logger.info(f"🔍 内存管理器实例ID: {id(self.memory_manager)}")

            # 在内存中创建任务状态
            task_state = await self.memory_manager.create_task(
                task_id=task_id,
                user_id=user_id,
                stock_code=stock_code,
                parameters=request.parameters.model_dump() if request.parameters else {},
                stock_name=(self._resolve_stock_name(stock_code) if hasattr(self, '_resolve_stock_name') else None),
            )

            logger.info(f"✅ 任务状态已创建: {task_state.task_id}")

            # 立即验证任务是否可以查询到
            verify_task = await self.memory_manager.get_task(task_id)
            if verify_task:
                logger.info(f"✅ 任务创建验证成功: {verify_task.task_id}")
            else:
                logger.error(f"❌ 任务创建验证失败: 无法查询到刚创建的任务 {task_id}")

            # 补齐股票名称并写入数据库任务文档的初始记录
            code = stock_code
            name = self._resolve_stock_name(code) if hasattr(self, '_resolve_stock_name') else f"股票{code}"

            try:
                db = get_mongo_db()
                result = await db.analysis_tasks.update_one(
                    {"task_id": task_id},
                    {"$setOnInsert": {
                        "task_id": task_id,
                        "user_id": user_id,
                        "stock_code": code,
                        "stock_symbol": code,
                        "stock_name": name,
                        "status": "pending",
                        "progress": 0,
                        "created_at": datetime.utcnow(),
                    }},
                    upsert=True
                )

                if result.upserted_id or result.matched_count > 0:
                    logger.info(f"✅ 任务已保存到MongoDB: {task_id}")
                else:
                    logger.warning(f"⚠️ MongoDB保存结果异常: matched={result.matched_count}, upserted={result.upserted_id}")

            except Exception as e:
                logger.error(f"❌ 创建任务时写入MongoDB失败: {e}")
                # 这里不应该忽略错误，因为没有MongoDB记录会导致状态查询失败
                # 但为了不影响任务执行，我们记录错误但继续执行
                import traceback
                logger.error(f"❌ MongoDB保存详细错误: {traceback.format_exc()}")

            return {
                "task_id": task_id,
                "status": "pending",
                "message": "任务已创建，等待执行"
            }

        except Exception as e:
            logger.error(f"❌ 创建分析任务失败: {e}")
            raise

    # ------------------------------------------------------------------
    # 后台执行分析
    # ------------------------------------------------------------------

    async def execute_analysis_background(
        self,
        task_id: str,
        user_id: str,
        request: SingleAnalysisRequest
    ):
        """在后台执行分析任务"""
        # 🔧 使用 get_symbol() 方法获取股票代码（兼容 symbol 和 stock_code 字段）
        stock_code = request.get_symbol()

        # 添加最外层的异常捕获，确保所有异常都被记录
        try:
            logger.info(f"🎯🎯🎯 [ENTRY] execute_analysis_background 方法被调用: {task_id}")
            logger.info(f"🎯🎯🎯 [ENTRY] user_id={user_id}, stock_code={stock_code}")
        except Exception as entry_error:
            print(f"❌❌❌ [CRITICAL] 日志记录失败: {entry_error}")
            import traceback
            traceback.print_exc()

        progress_tracker = None
        try:
            logger.info(f"🚀 开始后台执行分析任务: {task_id}")

            # 🔍 验证股票代码是否存在
            logger.info(f"🔍 开始验证股票代码: {stock_code}")
            from sinoquant.utils.stock_validator import prepare_stock_data
            from datetime import datetime

            # 获取市场类型
            market_type = request.parameters.market_type if request.parameters else "A股"

            # 获取分析日期并转换为字符串格式
            analysis_date = request.parameters.analysis_date if request.parameters else None
            if analysis_date:
                # 如果是 datetime 对象，转换为字符串
                if isinstance(analysis_date, datetime):
                    analysis_date = analysis_date.strftime('%Y-%m-%d')
                # 如果是字符串，确保格式正确
                elif isinstance(analysis_date, str):
                    # 尝试解析并重新格式化，确保格式统一
                    try:
                        parsed_date = datetime.strptime(analysis_date, '%Y-%m-%d')
                        analysis_date = parsed_date.strftime('%Y-%m-%d')
                    except ValueError:
                        # 如果格式不对，使用今天
                        analysis_date = datetime.now().strftime('%Y-%m-%d')
                        logger.warning(f"⚠️ 分析日期格式不正确，使用今天: {analysis_date}")

            # 验证股票代码并预获取数据
            validation_result = await asyncio.to_thread(
                prepare_stock_data,
                stock_code=stock_code,
                market_type=market_type,
                period_days=30,
                analysis_date=analysis_date
            )

            if not validation_result.is_valid:
                error_msg = f"❌ 股票代码验证失败: {validation_result.error_message}"
                logger.error(error_msg)
                logger.error(f"💡 建议: {validation_result.suggestion}")

                # 构建用户友好的错误消息
                user_friendly_error = (
                    f"❌ 股票代码无效\n\n"
                    f"{validation_result.error_message}\n\n"
                    f"💡 {validation_result.suggestion}"
                )

                # 更新任务状态为失败
                await self.memory_manager.update_task_status(
                    task_id=task_id,
                    status=AnalysisStatus.FAILED,
                    progress=0,
                    error_message=user_friendly_error
                )

                # 更新MongoDB状态
                await self._update_task_status(
                    task_id,
                    AnalysisStatus.FAILED,
                    0,
                    error_message=user_friendly_error
                )

                return

            logger.info(f"✅ 股票代码验证通过: {stock_code} - {validation_result.stock_name}")
            logger.info(f"📊 市场类型: {validation_result.market_type}")
            logger.info(f"📈 历史数据: {'有' if validation_result.has_historical_data else '无'}")
            logger.info(f"📋 基本信息: {'有' if validation_result.has_basic_info else '无'}")

            # 在线程池中创建Redis进度跟踪器（避免阻塞事件循环）
            def create_progress_tracker():
                """在线程中创建进度跟踪器"""
                logger.info(f"📊 [线程] 创建进度跟踪器: {task_id}")
                tracker = RedisProgressTracker(
                    task_id=task_id,
                    analysts=(request.parameters.selected_analysts if request.parameters else None) or ["market", "fundamentals"],
                    research_depth=(request.parameters.research_depth if request.parameters else None) or "标准",
                    llm_provider="dashscope"
                )
                logger.info(f"✅ [线程] 进度跟踪器创建完成: {task_id}")
                return tracker

            progress_tracker = await asyncio.to_thread(create_progress_tracker)

            # 缓存进度跟踪器
            self._progress_trackers[task_id] = progress_tracker

            # 注册到日志监控
            register_analysis_tracker(task_id, progress_tracker)

            # 初始化进度（在线程中执行）
            await asyncio.to_thread(
                progress_tracker.update_progress,
                {
                    "progress_percentage": 10,
                    "last_message": "🚀 开始股票分析"
                }
            )

            # 更新状态为运行中
            await self.memory_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                progress=10,
                message="分析开始...",
                current_step="initialization"
            )

            # 同步更新MongoDB状态
            await self._update_task_status(task_id, AnalysisStatus.PROCESSING, 10)

            # 数据准备阶段（在线程中执行）
            await asyncio.to_thread(
                progress_tracker.update_progress,
                {
                    "progress_percentage": 20,
                    "last_message": "🔧 检查环境配置"
                }
            )
            await self.memory_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                progress=20,
                message="准备分析数据...",
                current_step="data_preparation"
            )

            # 同步更新MongoDB状态
            await self._update_task_status(task_id, AnalysisStatus.PROCESSING, 20)

            # 执行实际的分析
            result = await self._execute_analysis_sync(task_id, user_id, request, progress_tracker)

            # 标记进度跟踪器完成（在线程中执行）
            await asyncio.to_thread(progress_tracker.mark_completed)

            # 保存分析结果到文件和数据库
            try:
                logger.info(f"💾 开始保存分析结果: {task_id}")
                await self._save_analysis_results_complete(task_id, result)
                logger.info(f"✅ 分析结果保存完成: {task_id}")
            except Exception as save_error:
                logger.error(f"❌ 保存分析结果失败: {task_id} - {save_error}")
                # 保存失败不影响分析完成状态

            # 🔍 调试：检查即将保存到内存的result
            logger.info(f"🔍 [DEBUG] 即将保存到内存的result键: {list(result.keys())}")
            logger.info(f"🔍 [DEBUG] 即将保存到内存的decision: {bool(result.get('decision'))}")
            if result.get('decision'):
                logger.info(f"🔍 [DEBUG] 即将保存的decision内容: {result['decision']}")

            # 更新状态为完成
            await self.memory_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                message="分析完成",
                current_step="completed",
                result_data=result
            )

            # 同步更新MongoDB状态为完成
            await self._update_task_status(task_id, AnalysisStatus.COMPLETED, 100)

            # 创建通知：分析完成（方案B：REST+SSE）
            try:
                from app.services.notifications_service import get_notifications_service
                svc = get_notifications_service()
                summary = str(result.get("summary", ""))[:120]
                await svc.create_and_publish(
                    payload=NotificationCreate(
                        user_id=str(user_id),
                        type='analysis',
                        title=f"{request.stock_code} 分析完成",
                        content=summary,
                        link=f"/stocks/{request.stock_code}",
                        source='analysis'
                    )
                )
            except Exception as notif_err:
                logger.warning(f"⚠️ 创建通知失败(忽略): {notif_err}")

            logger.info(f"✅ 后台分析任务完成: {task_id}")
            return result

        except Exception as e:
            logger.error(f"❌ 后台分析任务失败: {task_id} - {e}")

            # 格式化错误信息为用户友好的提示
            try:
                from ..utils.error_formatter import ErrorFormatter

                # 收集上下文信息
                error_context = {}
                if hasattr(request, 'parameters') and request.parameters:
                    if hasattr(request.parameters, 'quick_model'):
                        error_context['model'] = request.parameters.quick_model
                    if hasattr(request.parameters, 'deep_model'):
                        error_context['model'] = request.parameters.deep_model

                # 格式化错误
                formatted_error = ErrorFormatter.format_error(str(e), error_context)

                # 构建用户友好的错误消息
                user_friendly_error = (
                    f"{formatted_error['title']}\n\n"
                    f"{formatted_error['message']}\n\n"
                    f"💡 {formatted_error['suggestion']}"
                )
            except ImportError:
                user_friendly_error = f"分析执行失败: {type(e).__name__}: {e}"

            # 标记进度跟踪器失败
            if progress_tracker:
                progress_tracker.mark_failed(user_friendly_error)

            # 更新状态为失败
            await self.memory_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.FAILED,
                progress=0,
                message="分析失败",
                current_step="failed",
                error_message=user_friendly_error
            )

            # 同步更新MongoDB状态为失败
            await self._update_task_status(task_id, AnalysisStatus.FAILED, 0, user_friendly_error)
        finally:
            # 清理进度跟踪器缓存
            if task_id in self._progress_trackers:
                del self._progress_trackers[task_id]

            # 从日志监控中注销
            unregister_analysis_tracker(task_id)

    # ------------------------------------------------------------------
    # 任务状态查询
    # ------------------------------------------------------------------

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        logger.info(f"🔍 查询任务状态: {task_id}")
        logger.info(f"🔍 当前服务实例ID: {id(self)}")
        logger.info(f"🔍 内存管理器实例ID: {id(self.memory_manager)}")

        # 强制使用全局内存管理器实例（临时解决方案）
        global_memory_manager = self.memory_manager
        logger.info(f"🔍 全局内存管理器实例ID: {id(global_memory_manager)}")

        # 获取统计信息
        stats = await global_memory_manager.get_statistics()
        logger.info(f"📊 内存中任务统计: {stats}")

        result = await global_memory_manager.get_task_dict(task_id)
        if result:
            logger.info(f"✅ 找到任务: {task_id} - 状态: {result.get('status')}")

            # 🔍 调试：检查从内存获取的result_data
            result_data = result.get('result_data')
            logger.debug(f"🔍 [GET_STATUS] result_data存在: {bool(result_data)}")
            if result_data:
                logger.debug(f"🔍 [GET_STATUS] result_data键: {list(result_data.keys())}")
                logger.debug(f"🔍 [GET_STATUS] result_data中有decision: {bool(result_data.get('decision'))}")
                if result_data.get('decision'):
                    logger.debug(f"🔍 [GET_STATUS] decision内容: {result_data['decision']}")
            else:
                logger.debug(f"🔍 [GET_STATUS] result_data为空或不存在（任务运行中，这是正常的）")

            # 优先从Redis获取详细进度信息
            redis_progress = get_progress_by_id(task_id)
            if redis_progress:
                logger.info(f"📊 [Redis进度] 获取到详细进度: {task_id}")

                # 从 steps 数组中提取当前步骤的名称和描述
                current_step_index = redis_progress.get('current_step', 0)
                steps = redis_progress.get('steps', [])
                current_step_name = redis_progress.get('current_step_name', '')
                current_step_description = redis_progress.get('current_step_description', '')

                # 如果 Redis 中的名称/描述为空，从 steps 数组中提取
                if not current_step_name and steps and 0 <= current_step_index < len(steps):
                    current_step_info = steps[current_step_index]
                    current_step_name = current_step_info.get('name', '')
                    current_step_description = current_step_info.get('description', '')
                    logger.info(f"📋 从steps数组提取当前步骤信息: index={current_step_index}, name={current_step_name}")

                # 合并Redis进度数据
                result.update({
                    'progress': redis_progress.get('progress_percentage', result.get('progress', 0)),
                    'current_step': current_step_index,  # 使用索引而不是名称
                    'current_step_name': current_step_name,  # 步骤名称
                    'current_step_description': current_step_description,  # 步骤描述
                    'message': redis_progress.get('last_message', result.get('message', '')),
                    'elapsed_time': redis_progress.get('elapsed_time', 0),
                    'remaining_time': redis_progress.get('remaining_time', 0),
                    'estimated_total_time': redis_progress.get('estimated_total_time', result.get('estimated_duration', 300)),  # 🔧 修复：使用Redis中的预估总时长
                    'steps': steps,
                    'start_time': result.get('start_time'),  # 保持原有格式
                    'last_update': redis_progress.get('last_update', result.get('start_time'))
                })
            else:
                # 如果Redis中没有，尝试从内存中的进度跟踪器获取
                if task_id in self._progress_trackers:
                    progress_tracker = self._progress_trackers[task_id]
                    progress_data = progress_tracker.to_dict()

                    # 合并进度跟踪器的详细信息
                    result.update({
                        'progress': progress_data['progress'],
                        'current_step': progress_data['current_step'],
                        'message': progress_data['message'],
                        'elapsed_time': progress_data['elapsed_time'],
                        'remaining_time': progress_data['remaining_time'],
                        'estimated_total_time': progress_data.get('estimated_total_time', 0),
                        'steps': progress_data['steps'],
                        'start_time': progress_data['start_time'],
                        'last_update': progress_data['last_update']
                    })
                    logger.info(f"📊 合并内存进度跟踪器数据: {task_id}")
                else:
                    logger.info(f"⚠️ 未找到进度信息: {task_id}")
        else:
            logger.warning(f"❌ 未找到任务: {task_id}")

        return result

    # ------------------------------------------------------------------
    # 任务列表查询
    # ------------------------------------------------------------------

    async def list_all_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取所有任务列表（不限用户）
        - 合并内存和 MongoDB 数据
        - 按开始时间倒序排列
        """
        try:
            task_status = None
            if status:
                try:
                    status_mapping = {
                        "processing": "running",
                        "pending": "pending",
                        "completed": "completed",
                        "failed": "failed",
                        "cancelled": "cancelled"
                    }
                    mapped_status = status_mapping.get(status, status)
                    task_status = TaskStatus(mapped_status)
                except ValueError:
                    logger.warning(f"⚠️ [Tasks] 无效的状态值: {status}")
                    task_status = None

            # 1) 从内存读取所有任务
            logger.info(f"📋 [Tasks] 准备从内存读取所有任务: status={status}, limit={limit}, offset={offset}")
            tasks_in_mem = await self.memory_manager.list_all_tasks(
                status=task_status,
                limit=limit * 2,
                offset=0
            )
            logger.info(f"📋 [Tasks] 内存返回数量: {len(tasks_in_mem)}")

            # 2) 从 MongoDB 读取任务
            db = get_mongo_db()
            collection = db["analysis_tasks"]

            query = {}
            if task_status:
                query["status"] = task_status.value

            count = await collection.count_documents(query)
            logger.info(f"📋 [Tasks] MongoDB 任务总数: {count}")

            cursor = collection.find(query).sort("start_time", -1).limit(limit * 2)
            tasks_from_db = []
            async for doc in cursor:
                doc.pop("_id", None)
                tasks_from_db.append(doc)

            logger.info(f"📋 [Tasks] MongoDB 返回数量: {len(tasks_from_db)}")

            # 3) 合并任务（内存优先）
            task_dict = {}

            # 先添加 MongoDB 中的任务
            for task in tasks_from_db:
                task_id = task.get("task_id")
                if task_id:
                    task_dict[task_id] = task

            # 再添加内存中的任务（覆盖 MongoDB 中的同名任务）
            for task in tasks_in_mem:
                task_id = task.get("task_id")
                if task_id:
                    task_dict[task_id] = task

            # 转换为列表并按时间排序
            merged_tasks = list(task_dict.values())
            merged_tasks.sort(key=lambda x: x.get('start_time', ''), reverse=True)

            # 分页
            results = merged_tasks[offset:offset + limit]

            # 为结果补齐股票名称
            results = self._enrich_stock_names(results)
            logger.info(f"📋 [Tasks] 合并后返回数量: {len(results)} (内存: {len(tasks_in_mem)}, MongoDB: {count})")
            return results
        except Exception as outer_e:
            logger.error(f"❌ list_all_tasks 外层异常: {outer_e}", exc_info=True)
            return []

    async def list_user_tasks(
        self,
        user_id: str,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取用户任务列表
        - 对于 processing 状态：优先从内存读取（实时进度）
        - 对于 completed/failed/all 状态：合并内存和 MongoDB 数据
        """
        try:
            task_status = None
            if status:
                try:
                    # 前端传递的是 "processing"，但 TaskStatus 使用的是 "running"
                    # 需要做映射转换
                    status_mapping = {
                        "processing": "running",  # 前端使用 processing，内存使用 running
                        "pending": "pending",
                        "completed": "completed",
                        "failed": "failed",
                        "cancelled": "cancelled"
                    }
                    mapped_status = status_mapping.get(status, status)
                    task_status = TaskStatus(mapped_status)
                except ValueError:
                    logger.warning(f"⚠️ [Tasks] 无效的状态值: {status}")
                    task_status = None

            # 1) 从内存读取任务
            logger.info(f"📋 [Tasks] 准备从内存读取任务: user_id={user_id}, status={status} (mapped to {task_status}), limit={limit}, offset={offset}")
            tasks_in_mem = await self.memory_manager.list_user_tasks(
                user_id=user_id,
                status=task_status,
                limit=limit * 2,  # 多读一些，后面合并去重
                offset=0  # 内存中的任务不多，全部读取
            )
            logger.info(f"📋 [Tasks] 内存返回数量: {len(tasks_in_mem)}")

            # 2) 🔧 对于 processing/running 状态，需要合并 MongoDB 数据以获取最新进度
            # 因为 graph_progress_callback 可能直接更新了 MongoDB，而内存数据可能是旧的

            # 3) 从 MongoDB 读取历史任务（用于合并或兜底）
            logger.info(f"📋 [Tasks] 从 MongoDB 读取历史任务")
            mongo_tasks: List[Dict[str, Any]] = []
            count = 0
            try:
                db = get_mongo_db()

                # user_id 可能是字符串或 ObjectId，做兼容
                uid_candidates: List[Any] = [user_id]

                # 特殊处理 admin 用户
                if str(user_id) == 'admin':
                    # admin 用户：添加固定的 ObjectId 和字符串形式
                    try:
                        from bson import ObjectId
                        admin_oid_str = '507f1f77bcf86cd799439011'
                        uid_candidates.append(ObjectId(admin_oid_str))
                        uid_candidates.append(admin_oid_str)  # 兼容字符串存储
                        logger.info(f"📋 [Tasks] admin用户查询，候选ID: ['admin', ObjectId('{admin_oid_str}'), '{admin_oid_str}']")
                    except Exception as e:
                        logger.warning(f"⚠️ [Tasks] admin用户ObjectId创建失败: {e}")
                else:
                    # 普通用户：尝试转换为 ObjectId
                    try:
                        from bson import ObjectId
                        uid_candidates.append(ObjectId(user_id))
                        logger.debug(f"📋 [Tasks] 用户ID已转换为ObjectId: {user_id}")
                    except Exception as conv_err:
                        logger.warning(f"⚠️ [Tasks] 用户ID转换ObjectId失败，按字符串匹配: {conv_err}")

                # 兼容 user_id 与 user 两种字段名
                base_condition = {"$in": uid_candidates}
                or_conditions: List[Dict[str, Any]] = [
                    {"user_id": base_condition},
                    {"user": base_condition}
                ]
                query = {"$or": or_conditions}

                if task_status:
                    # RUNNING 在 MongoDB 中可能存为 "running" 或 "processing"，需要兼容
                    status_value = task_status.value
                    if status_value == "running":
                        query["status"] = {"$in": ["running", "processing"]}
                    else:
                        query["status"] = status_value
                    logger.info(f"📋 [Tasks] 添加状态过滤: {status_value}")

                logger.info(f"📋 [Tasks] MongoDB 查询条件: {query}")
                # 读取更多数据用于合并
                cursor = db.analysis_tasks.find(query).sort("created_at", -1).limit(limit * 2)
                async for doc in cursor:
                    count += 1
                    # 兼容 user_id 或 user 字段
                    user_field_val = doc.get("user_id", doc.get("user"))
                    # 🔧 兼容多种股票代码字段名：symbol, stock_code, stock_symbol
                    stock_code_value = doc.get("symbol") or doc.get("stock_code") or doc.get("stock_symbol")
                    item = {
                        "task_id": doc.get("task_id"),
                        "user_id": str(user_field_val) if user_field_val is not None else None,
                        "symbol": stock_code_value,  # 🔧 添加 symbol 字段（前端优先使用）
                        "stock_code": stock_code_value,  # 🔧 兼容字段
                        "stock_symbol": stock_code_value,  # 🔧 兼容字段
                        "stock_name": doc.get("stock_name"),
                        "status": str(doc.get("status", "pending")),
                        "progress": int(doc.get("progress", 0) or 0),
                        "message": doc.get("message", ""),
                        "current_step": doc.get("current_step", ""),
                        "start_time": doc.get("started_at") or doc.get("created_at"),
                        "end_time": doc.get("completed_at"),
                        "parameters": doc.get("parameters", {}),
                        "execution_time": doc.get("execution_time"),
                        "tokens_used": doc.get("tokens_used"),
                        # 为兼容前端，这里沿用 memory_manager 的字段名
                        "result_data": doc.get("result"),
                    }
                    # 时间格式转为 ISO 字符串（添加时区信息）
                    for k in ("start_time", "end_time"):
                        if item.get(k) and hasattr(item[k], "isoformat"):
                            dt = item[k]
                            # 如果是 naive datetime（没有时区信息），假定为 UTC+8
                            if dt.tzinfo is None:
                                china_tz = timezone(timedelta(hours=8))
                                dt = dt.replace(tzinfo=china_tz)
                            item[k] = dt.isoformat()
                    mongo_tasks.append(item)

                logger.info(f"📋 [Tasks] MongoDB 返回数量: {count}")
            except Exception as mongo_e:
                logger.error(f"❌ MongoDB 查询任务列表失败: {mongo_e}", exc_info=True)
                # MongoDB 查询失败，继续使用内存数据

            # 4) 合并内存和 MongoDB 数据，去重
            # 🔧 对于 processing/running 状态，优先使用 MongoDB 中的进度数据
            # 因为 graph_progress_callback 直接更新 MongoDB，而内存数据可能是旧的
            task_dict = {}

            # 先添加内存中的任务
            for task in tasks_in_mem:
                task_id = task.get("task_id")
                if task_id:
                    task_dict[task_id] = task

            # 再添加 MongoDB 中的任务
            # 对于 processing/running 状态，使用 MongoDB 中的进度数据（更新）
            # 对于其他状态，如果内存中已有，则跳过（内存优先）
            for task in mongo_tasks:
                task_id = task.get("task_id")
                if not task_id:
                    continue

                # 如果内存中已有这个任务
                if task_id in task_dict:
                    mem_task = task_dict[task_id]
                    mongo_task = task

                    # 如果是 processing/running 状态，使用 MongoDB 中的进度数据
                    if mongo_task.get("status") in ["processing", "running"]:
                        # 保留内存中的基本信息，但更新进度相关字段
                        mem_task["progress"] = mongo_task.get("progress", mem_task.get("progress", 0))
                        mem_task["message"] = mongo_task.get("message", mem_task.get("message", ""))
                        mem_task["current_step"] = mongo_task.get("current_step", mem_task.get("current_step", ""))
                        logger.debug(f"🔄 [Tasks] 更新任务进度: {task_id}, progress={mem_task['progress']}%")
                else:
                    # 内存中没有，直接添加 MongoDB 中的任务
                    task_dict[task_id] = task

            # 转换为列表并按时间排序
            merged_tasks = list(task_dict.values())
            merged_tasks.sort(key=lambda x: x.get('start_time', ''), reverse=True)

            # 分页
            results = merged_tasks[offset:offset + limit]

            # 🔥 统一处理时区信息（确保所有时间字段都有时区标识）
            china_tz = timezone(timedelta(hours=8))

            for task in results:
                for time_field in ("start_time", "end_time", "created_at", "started_at", "completed_at"):
                    value = task.get(time_field)
                    if value:
                        # 如果是 datetime 对象
                        if hasattr(value, "isoformat"):
                            # 如果是 naive datetime，添加时区信息
                            if value.tzinfo is None:
                                value = value.replace(tzinfo=china_tz)
                            task[time_field] = value.isoformat()
                        # 如果是字符串且没有时区标识，添加时区标识
                        elif isinstance(value, str) and value and not value.endswith(('Z', '+08:00', '+00:00')):
                            # 检查是否是 ISO 格式的时间字符串
                            if 'T' in value or ' ' in value:
                                task[time_field] = value.replace(' ', 'T') + '+08:00'

            # 为结果补齐股票名称
            results = self._enrich_stock_names(results)
            logger.info(f"📋 [Tasks] 合并后返回数量: {len(results)} (内存: {len(tasks_in_mem)}, MongoDB: {count})")
            return results
        except Exception as outer_e:
            logger.error(f"❌ list_user_tasks 外层异常: {outer_e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # 僵尸任务管理
    # ------------------------------------------------------------------

    async def cleanup_zombie_tasks(self, max_running_hours: int = 2) -> Dict[str, Any]:
        """清理僵尸任务（长时间处于 processing/running 状态的任务）

        Args:
            max_running_hours: 最大运行时长（小时），超过此时长的任务将被标记为失败

        Returns:
            清理结果统计
        """
        try:
            # 1) 清理内存中的僵尸任务
            memory_cleaned = await self.memory_manager.cleanup_zombie_tasks(max_running_hours)

            # 2) 清理 MongoDB 中的僵尸任务
            db = get_mongo_db()
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(hours=max_running_hours)

            # 查找长时间处于 processing 状态的任务
            zombie_filter = {
                "status": {"$in": ["processing", "running", "pending"]},
                "$or": [
                    {"started_at": {"$lt": cutoff_time}},
                    {"created_at": {"$lt": cutoff_time, "started_at": None}}
                ]
            }

            # 更新为失败状态
            update_result = await db.analysis_tasks.update_many(
                zombie_filter,
                {
                    "$set": {
                        "status": "failed",
                        "last_error": f"任务超时（运行时间超过 {max_running_hours} 小时）",
                        "completed_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            mongo_cleaned = update_result.modified_count

            logger.info(f"🧹 僵尸任务清理完成: 内存={memory_cleaned}, MongoDB={mongo_cleaned}")

            return {
                "success": True,
                "memory_cleaned": memory_cleaned,
                "mongo_cleaned": mongo_cleaned,
                "total_cleaned": memory_cleaned + mongo_cleaned,
                "max_running_hours": max_running_hours
            }

        except Exception as e:
            logger.error(f"❌ 清理僵尸任务失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "memory_cleaned": 0,
                "mongo_cleaned": 0,
                "total_cleaned": 0
            }

    async def get_zombie_tasks(self, max_running_hours: int = 2) -> List[Dict[str, Any]]:
        """获取僵尸任务列表（不执行清理，仅查询）

        Args:
            max_running_hours: 最大运行时长（小时）

        Returns:
            僵尸任务列表
        """
        try:
            db = get_mongo_db()
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(hours=max_running_hours)

            # 查找长时间处于 processing 状态的任务
            zombie_filter = {
                "status": {"$in": ["processing", "running", "pending"]},
                "$or": [
                    {"started_at": {"$lt": cutoff_time}},
                    {"created_at": {"$lt": cutoff_time, "started_at": None}}
                ]
            }

            cursor = db.analysis_tasks.find(zombie_filter).sort("created_at", -1)
            zombie_tasks = []

            async for doc in cursor:
                task = {
                    "task_id": doc.get("task_id"),
                    "user_id": str(doc.get("user_id", doc.get("user"))),
                    "stock_code": doc.get("stock_code"),
                    "stock_name": doc.get("stock_name"),
                    "status": doc.get("status"),
                    "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
                    "started_at": doc.get("started_at").isoformat() if doc.get("started_at") else None,
                    "running_hours": None
                }

                # 计算运行时长
                start_time = doc.get("started_at") or doc.get("created_at")
                if start_time:
                    running_seconds = (datetime.utcnow() - start_time).total_seconds()
                    task["running_hours"] = round(running_seconds / 3600, 2)

                zombie_tasks.append(task)

            logger.info(f"📋 查询到 {len(zombie_tasks)} 个僵尸任务")
            return zombie_tasks

        except Exception as e:
            logger.error(f"❌ 查询僵尸任务失败: {e}", exc_info=True)
            return []
