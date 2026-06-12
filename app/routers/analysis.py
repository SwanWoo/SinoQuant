"""
股票分析API路由
增强版本，支持优先级、进度跟踪、任务管理等功能

本模块定义了股票分析相关的所有HTTP接口，包括：
- 单股分析任务提交与结果查询
- 批量分析任务提交与并发执行
- 任务状态查询与进度追踪
- 用户分析历史记录查询
- WebSocket实时进度推送
- 僵尸任务检测与清理（管理员功能）
- 任务取消与删除

数据流向：前端请求 → 路由层（本文件） → 服务层（analysis_service/simple_analysis_service） → 数据层（MongoDB + 内存状态管理）
"""

# ==================== 第三方库导入 ====================
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import time
import uuid
import asyncio

# ==================== 项目内部模块导入 ====================
# 用户认证依赖：从请求中提取并验证当前登录用户信息
from app.routers.auth_db import get_current_user
# 队列服务：用于兼容旧版排队式分析任务的管理
from app.services.queue_service import get_queue_service, QueueService
# 分析服务（新版）：支持异步后台执行的分析任务管理服务
from app.services.analysis_service import get_analysis_service
# 简化分析服务：封装了任务创建、执行、状态查询等核心业务逻辑
from app.services.simple_analysis_service import get_simple_analysis_service
# WebSocket管理器：管理WebSocket连接的建立、消息广播与断开
from app.services.websocket_manager import get_websocket_manager
# 文本清洗工具：用于处理LLM返回内容中的特殊标记（如思考链标签）
from sinoquant.utils.text_utils import (
    clean_llm_response,
    remove_thinking_content,
    sanitize_report_modules,
)
# 分析相关的Pydantic数据模型定义
from app.models.analysis import (
    SingleAnalysisRequest, BatchAnalysisRequest, AnalysisParameters,
    AnalysisTaskResponse, AnalysisBatchResponse, AnalysisHistoryQuery
)

# ==================== 路由器与日志初始化 ====================
# 创建API路由器实例，所有端点将注册到此路由器
router = APIRouter()
# 获取webapi命名空间的日志记录器，用于记录请求处理过程中的调试和错误信息
logger = logging.getLogger("webapi")


# ==================== 兼容性请求模型定义 ====================
# 以下模型用于兼容旧版API端点的请求参数格式，新版API使用 app.models.analysis 中的模型

class SingleAnalyzeRequest(BaseModel):
    """单股分析请求模型（兼容旧版）

    旧版接口使用的请求体结构，仅包含股票代码和可选参数字典。
    新版接口已迁移至 SingleAnalysisRequest 模型。
    """
    symbol: str  # 股票代码，如 "000001.SZ"
    parameters: dict = Field(default_factory=dict)  # 可选的分析参数，默认为空字典


class BatchAnalyzeRequest(BaseModel):
    """批量分析请求模型（兼容旧版）

    旧版接口使用的请求体结构，包含多个股票代码和批次元信息。
    新版接口已迁移至 BatchAnalysisRequest 模型。
    """
    symbols: List[str]  # 股票代码列表，如 ["000001.SZ", "600000.SH"]
    parameters: dict = Field(default_factory=dict)  # 可选的分析参数，默认为空字典
    title: str = Field(default="批量分析", description="批次标题")  # 批次标题，用于标识本次批量分析
    description: Optional[str] = Field(None, description="批次描述")  # 可选的批次描述信息


# ==================== 新版API端点 ====================

@router.post("/single", response_model=Dict[str, Any])
async def submit_single_analysis(
    request: SingleAnalysisRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user)
):
    """提交单股分析任务 - 使用 BackgroundTasks 异步执行

    该接口接收单股分析请求后，立即创建任务记录并返回任务ID，
    实际的分析工作在后台异步执行，避免阻塞HTTP响应。

    处理流程：
    1. 接收请求，验证用户身份
    2. 调用分析服务创建任务记录（分配task_id，初始化状态为pending）
    3. 将实际分析逻辑注册为后台任务
    4. 立即返回任务ID和成功响应

    参数：
        request: 单股分析请求，包含股票代码和分析参数
        background_tasks: FastAPI后台任务管理器，用于注册异步执行的任务
        user: 当前登录用户信息（通过JWT令牌自动注入）

    返回：
        success: 是否成功提交
        data: 包含task_id的任务创建结果
        message: 提示信息
    """
    try:
        # 记录请求信息，便于调试追踪
        logger.info(f"🎯 收到单股分析请求")
        logger.info(f"👤 用户信息: {user}")
        logger.info(f"📊 请求数据: {request}")

        # 调用简化分析服务创建任务记录，返回包含task_id的结果字典
        # 此步骤仅创建任务，不执行分析
        analysis_service = get_simple_analysis_service()
        result = await analysis_service.create_analysis_task(user["id"], request)

        # 提取task_id和user_id为局部变量，避免在闭包中直接引用可变上下文
        # 这是Python闭包的常见陷阱：循环变量和延迟绑定可能导致意外行为
        task_id = result["task_id"]
        user_id = user["id"]

        # 定义后台任务的包装函数
        # 必须使用包装函数而非直接传入协程，因为BackgroundTasks需要可调用对象
        async def run_analysis_task():
            """包装函数：在后台运行分析任务

            在FastAPI的后台任务框架中执行分析逻辑。
            每次执行时会重新获取服务实例，确保在正确的异步上下文中操作。
            """
            try:
                logger.info(f"🚀 [BackgroundTask] 开始执行分析任务: {task_id}")
                logger.info(f"📝 [BackgroundTask] task_id={task_id}, user_id={user_id}")
                logger.info(f"📝 [BackgroundTask] request={request}")

                # 重新获取服务实例，确保在后台任务的异步上下文中使用有效的连接
                # 避免使用主请求上下文中已关闭的数据库连接
                logger.info(f"🔧 [BackgroundTask] 正在获取服务实例...")
                service = get_simple_analysis_service()
                logger.info(f"✅ [BackgroundTask] 服务实例获取成功: {id(service)}")

                # 调用服务层的后台执行方法，执行完整的分析流程
                # 包括：数据获取 → 各分析师分析 → 研究辩论 → 交易决策 → 风险评估
                logger.info(f"🚀 [BackgroundTask] 准备调用 execute_analysis_background...")
                await service.execute_analysis_background(
                    task_id,
                    user_id,
                    request
                )
                logger.info(f"✅ [BackgroundTask] 分析任务完成: {task_id}")
            except Exception as e:
                # 捕获后台任务中的异常，防止未处理异常导致任务静默失败
                logger.error(f"❌ [BackgroundTask] 分析任务失败: {task_id}, 错误: {e}", exc_info=True)

        # 将异步包装函数注册为FastAPI后台任务
        # 后台任务会在HTTP响应发送后执行，不会阻塞客户端
        background_tasks.add_task(run_analysis_task)

        logger.info(f"✅ 分析任务已在后台启动: {result}")

        # 立即返回任务创建结果，客户端可通过轮询状态接口获取进度
        return {
            "success": True,
            "data": result,
            "message": "分析任务已在后台启动"
        }
    except Exception as e:
        # 处理任务创建阶段的异常（如参数验证失败、数据库连接错误等）
        logger.error(f"❌ 提交单股分析任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 测试路由 ====================

@router.get("/test-route")
async def test_route():
    """测试路由是否工作

    用于验证分析路由模块是否被正确注册到FastAPI应用中。
    当前端无法访问分析相关接口时，可先调用此接口排查路由注册问题。
    """
    logger.info("🧪 测试路由被调用了！")
    return {"message": "测试路由工作正常", "timestamp": time.time()}


# ==================== 任务状态查询端点 ====================

@router.get("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def get_task_status_new(
    task_id: str,
    user: dict = Depends(get_current_user)
):
    """获取分析任务状态（新版异步实现）

    按以下优先级查询任务状态：
    1. 内存状态管理器（实时状态，最快）
    2. MongoDB analysis_tasks 集合（进行中的任务记录）
    3. MongoDB analysis_reports 集合（已完成的分析报告）

    参数：
        task_id: 任务唯一标识符
        user: 当前登录用户信息

    返回：
        success: 是否查询成功
        data: 任务状态信息，包含status、progress、elapsed_time等
        message: 提示信息
    """
    try:
        logger.info(f"🔍 [NEW ROUTE] 进入新版状态查询路由: {task_id}")
        logger.info(f"👤 [NEW ROUTE] 用户: {user}")

        # 首先尝试从内存状态管理器中获取任务状态（速度最快，数据最新）
        analysis_service = get_simple_analysis_service()
        logger.info(f"🔧 [NEW ROUTE] 获取分析服务实例: {id(analysis_service)}")

        result = await analysis_service.get_task_status(task_id)
        logger.info(f"📊 [NEW ROUTE] 查询结果: {result is not None}")

        if result:
            # 内存中找到任务状态，直接返回
            return {
                "success": True,
                "data": result,
                "message": "任务状态获取成功"
            }
        else:
            # 内存中未找到，可能任务已持久化到MongoDB或服务重启导致内存丢失
            # 依次从MongoDB的analysis_tasks和analysis_reports集合中查找
            logger.info(f"📊 [STATUS] 内存中未找到，尝试从MongoDB查找: {task_id}")

            from app.core.database import get_mongo_db
            db = get_mongo_db()

            # 第一步：从analysis_tasks集合中查找（正在进行的任务）
            # 该集合记录了所有已创建但可能未完成的分析任务
            task_result = await db.analysis_tasks.find_one({"task_id": task_id})

            if task_result:
                logger.info(f"✅ [STATUS] 从analysis_tasks找到任务: {task_id}")

                # 构造状态响应对象（适用于正在进行的任务）
                status = task_result.get("status", "pending")  # 任务当前状态：pending/processing/completed/failed
                progress = task_result.get("progress", 0)  # 任务进度百分比，0-100

                # 计算任务已运行时长（秒）
                start_time = task_result.get("started_at") or task_result.get("created_at")
                current_time = datetime.utcnow()
                elapsed_time = 0
                if start_time:
                    elapsed_time = (current_time - start_time).total_seconds()

                # 构造统一的状态响应结构
                status_data = {
                    "task_id": task_id,
                    "status": status,
                    "progress": progress,
                    "message": f"任务{status}中...",
                    "current_step": status,  # 当前执行步骤（与状态相同）
                    "start_time": start_time,
                    "end_time": task_result.get("completed_at"),
                    "elapsed_time": elapsed_time,
                    "remaining_time": 0,  # 无法准确估算剩余时间
                    "estimated_total_time": 0,
                    "symbol": task_result.get("symbol") or task_result.get("stock_code"),
                    "stock_code": task_result.get("symbol") or task_result.get("stock_code"),  # 兼容字段
                    "stock_symbol": task_result.get("symbol") or task_result.get("stock_code"),
                    "source": "mongodb_tasks"  # 标记数据来源，便于前端区分数据新鲜度
                }

                return {
                    "success": True,
                    "data": status_data,
                    "message": "任务状态获取成功（从任务记录恢复）"
                }

            # 第二步：从analysis_reports集合中查找（已完成的任务）
            # 完成的分析任务会将其结果保存到报告集合中
            mongo_result = await db.analysis_reports.find_one({"task_id": task_id})

            if mongo_result:
                logger.info(f"✅ [STATUS] 从analysis_reports找到任务: {task_id}")

                # 构造已完成任务的状态响应
                # 计算已完成任务的总执行时长
                start_time = mongo_result.get("created_at")
                end_time = mongo_result.get("updated_at")
                elapsed_time = 0
                if start_time and end_time:
                    elapsed_time = (end_time - start_time).total_seconds()

                status_data = {
                    "task_id": task_id,
                    "status": "completed",  # 已完成状态
                    "progress": 100,  # 完成进度为100%
                    "message": "分析完成（从历史记录恢复）",
                    "current_step": "completed",
                    "start_time": start_time,
                    "end_time": end_time,
                    "elapsed_time": elapsed_time,
                    "remaining_time": 0,
                    "estimated_total_time": elapsed_time,  # 已完成任务的总时长就是已用时间
                    "stock_code": mongo_result.get("stock_symbol"),
                    "stock_symbol": mongo_result.get("stock_symbol"),
                    "analysts": mongo_result.get("analysts", []),  # 参与分析的分析师列表
                    "research_depth": mongo_result.get("research_depth", "快速"),  # 研究深度
                    "source": "mongodb_reports"  # 标记数据来源为历史报告
                }

                return {
                    "success": True,
                    "data": status_data,
                    "message": "任务状态获取成功（从历史记录恢复）"
                }
            else:
                # 所有数据源中均未找到该任务，返回404
                logger.warning(f"❌ [STATUS] MongoDB中也未找到: {task_id} trace={task_id}")
                raise HTTPException(status_code=404, detail="任务不存在")

    except HTTPException:
        # 直接抛出HTTP异常（如404），不再包装
        raise
    except Exception as e:
        # 处理其他未预期的异常（如数据库连接错误）
        logger.error(f"❌ 获取任务状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 任务结果查询端点 ====================

@router.get("/tasks/{task_id}/result", response_model=Dict[str, Any])
async def get_task_result(
    task_id: str,
    user: dict = Depends(get_current_user)
):
    """获取分析任务结果

    按以下优先级查询任务结果数据：
    1. 内存状态管理器中的result_data（实时数据，仅completed状态）
    2. MongoDB analysis_reports 集合（已完成的分析报告，优先按task_id匹配）
    3. MongoDB analysis_tasks 集合的 result 字段（兜底数据源）

    查询到结果后，还会进行以下处理：
    - 从文件系统加载报告模块（如果reports字段缺失）
    - 从state字段中提取各分析师报告（如果reports仍缺失）
    - 清洗LLM返回内容中的思考链标签
    - 补全关键字段（summary、recommendation、key_points）
    - 格式验证和类型安全转换

    参数：
        task_id: 任务唯一标识符
        user: 当前登录用户信息

    返回：
        success: 是否查询成功
        data: 完整的分析结果数据
        message: 提示信息
    """
    try:
        logger.info(f"🔍 [RESULT] 获取任务结果: {task_id}")
        logger.info(f"👤 [RESULT] 用户: {user}")

        # 首先尝试从内存中获取任务状态和结果
        analysis_service = get_simple_analysis_service()
        task_status = await analysis_service.get_task_status(task_id)

        # 初始化结果数据变量
        result_data = None

        if task_status and task_status.get('status') == 'completed':
            # 任务已完成，从内存中提取结果数据
            result_data = task_status.get('result_data')
            logger.info(f"📊 [RESULT] 从内存中获取到结果数据")

            # 调试日志：检查内存中的数据结构完整性
            if result_data:
                logger.info(f"📊 [RESULT] 内存数据键: {list(result_data.keys())}")
                logger.info(f"📊 [RESULT] 内存中有decision字段: {bool(result_data.get('decision'))}")
                logger.info(f"📊 [RESULT] 内存中summary长度: {len(result_data.get('summary', ''))}")
                logger.info(f"📊 [RESULT] 内存中recommendation长度: {len(result_data.get('recommendation', ''))}")
                if result_data.get('decision'):
                    decision = result_data['decision']
                    logger.info(f"📊 [RESULT] 内存decision内容: action={decision.get('action')}, target_price={decision.get('target_price')}")
            else:
                logger.warning(f"⚠️ [RESULT] 内存中result_data为空")

        if not result_data:
            # 内存中未找到结果，尝试从MongoDB中查找
            logger.info(f"📊 [RESULT] 内存中未找到，尝试从MongoDB查找: {task_id}")

            from app.core.database import get_mongo_db
            db = get_mongo_db()

            # 优先使用task_id在analysis_reports集合中查找
            # 这是最直接的匹配方式，能找到最新的分析报告
            mongo_result = await db.analysis_reports.find_one({"task_id": task_id})

            if not mongo_result:
                # 兼容旧数据：旧版记录可能没有task_id字段
                # 尝试先从analysis_tasks中获取analysis_id，再用analysis_id查找报告
                tasks_doc_for_id = await db.analysis_tasks.find_one({"task_id": task_id}, {"result.analysis_id": 1})
                analysis_id = tasks_doc_for_id.get("result", {}).get("analysis_id") if tasks_doc_for_id else None
                if analysis_id:
                    logger.info(f"🔎 [RESULT] 按analysis_id兜底查询 analysis_reports: {analysis_id}")
                    mongo_result = await db.analysis_reports.find_one({"analysis_id": analysis_id})

            if mongo_result:
                logger.info(f"✅ [RESULT] 从MongoDB找到结果: {task_id}")

                # 从MongoDB文档中提取并构造标准化的结果数据结构
                # 保持与web目录中的数据格式一致
                result_data = {
                    "analysis_id": mongo_result.get("analysis_id"),  # 分析唯一标识
                    "stock_symbol": mongo_result.get("stock_symbol"),  # 股票代码
                    "stock_code": mongo_result.get("stock_symbol"),  # 兼容性字段
                    "analysis_date": mongo_result.get("analysis_date"),  # 分析日期
                    "summary": mongo_result.get("summary", ""),  # 分析摘要
                    "recommendation": mongo_result.get("recommendation", ""),  # 投资建议
                    "confidence_score": mongo_result.get("confidence_score", 0.0),  # 置信度评分（0-1）
                    "risk_level": mongo_result.get("risk_level", "中等"),  # 风险等级
                    "key_points": mongo_result.get("key_points", []),  # 关键要点列表
                    "execution_time": mongo_result.get("execution_time", 0),  # 执行耗时（秒）
                    "tokens_used": mongo_result.get("tokens_used", 0),  # LLM消耗的token数量
                    "analysts": mongo_result.get("analysts", []),  # 参与分析的分析师列表
                    "research_depth": mongo_result.get("research_depth", "快速"),  # 研究深度
                    "reports": mongo_result.get("reports", {}),  # 各分析师的详细报告
                    "created_at": mongo_result.get("created_at"),  # 创建时间
                    "updated_at": mongo_result.get("updated_at"),  # 更新时间
                    "status": mongo_result.get("status", "completed"),  # 任务状态
                    "decision": mongo_result.get("decision", {}),  # 最终交易决策
                    "source": "mongodb"  # 标记数据来源
                }

                # 记录MongoDB数据的结构信息，便于调试
                logger.info(f"📊 [RESULT] MongoDB数据结构: {list(result_data.keys())}")
                logger.info(f"📊 [RESULT] MongoDB summary长度: {len(result_data['summary'])}")
                logger.info(f"📊 [RESULT] MongoDB recommendation长度: {len(result_data['recommendation'])}")
                logger.info(f"📊 [RESULT] MongoDB decision字段: {bool(result_data.get('decision'))}")
                if result_data.get('decision'):
                    decision = result_data['decision']
                    logger.info(f"📊 [RESULT] MongoDB decision内容: action={decision.get('action')}, target_price={decision.get('target_price')}, confidence={decision.get('confidence')}")
            else:
                # 最后的兜底：从analysis_tasks集合的result字段中获取
                # 某些旧版任务的结果直接存储在任务记录的result子文档中
                tasks_doc = await db.analysis_tasks.find_one(
                    {"task_id": task_id},
                    {"result": 1, "symbol": 1, "stock_code": 1, "created_at": 1, "completed_at": 1}
                )
                if tasks_doc and tasks_doc.get("result"):
                    r = tasks_doc["result"] or {}
                    logger.info("✅ [RESULT] 从analysis_tasks.result 找到结果")
                    # 获取股票代码（优先使用symbol字段，兼容旧版stock_code字段）
                    symbol = (tasks_doc.get("symbol") or tasks_doc.get("stock_code") or
                             r.get("stock_symbol") or r.get("stock_code"))
                    result_data = {
                        "analysis_id": r.get("analysis_id"),
                        "stock_symbol": symbol,
                        "stock_code": symbol,  # 兼容字段
                        "analysis_date": r.get("analysis_date"),
                        "summary": r.get("summary", ""),
                        "recommendation": r.get("recommendation", ""),
                        "confidence_score": r.get("confidence_score", 0.0),
                        "risk_level": r.get("risk_level", "中等"),
                        "key_points": r.get("key_points", []),
                        "execution_time": r.get("execution_time", 0),
                        "tokens_used": r.get("tokens_used", 0),
                        "analysts": r.get("analysts", []),
                        "research_depth": r.get("research_depth", "快速"),
                        "reports": r.get("reports", {}),
                        "state": r.get("state", {}),  # 完整的工作流状态数据
                        "detailed_analysis": r.get("detailed_analysis", {}),  # 详细分析数据
                        "created_at": tasks_doc.get("created_at"),
                        "updated_at": tasks_doc.get("completed_at"),
                        "status": r.get("status", "completed"),
                        "decision": r.get("decision", {}),
                        "source": "analysis_tasks"  # 数据来源标记
                    }

        # 所有数据源均未找到结果，返回404
        if not result_data:
            logger.warning(f"❌ [RESULT] 所有数据源都未找到结果: {task_id}")
            raise HTTPException(status_code=404, detail="分析结果不存在")

        # 二次检查（防御性编程），确保result_data不为空
        if not result_data:
            raise HTTPException(status_code=404, detail="分析结果不存在")

        # ==================== 报告模块补全逻辑 ====================
        # 如果reports字段缺失或为空，尝试从文件系统或state字段中提取各分析师报告
        if 'reports' not in result_data or not result_data['reports']:
            import os
            from pathlib import Path

            stock_symbol = result_data.get('stock_symbol') or result_data.get('stock_code')
            # analysis_date可能是日期字符串或时间戳，取前10个字符作为日期部分
            analysis_date_raw = result_data.get('analysis_date')
            analysis_date = str(analysis_date_raw)[:10] if analysis_date_raw else None

            loaded_reports = {}
            try:
                # 方案1：从环境变量 TRADINGAGENTS_RESULTS_DIR 指定的位置读取报告文件
                base_env = os.getenv('TRADINGAGENTS_RESULTS_DIR')
                project_root = Path.cwd()
                if base_env:
                    base_path = Path(base_env)
                    if not base_path.is_absolute():
                        base_path = project_root / base_env  # 相对路径转为绝对路径
                else:
                    base_path = project_root / 'results'  # 默认存储位置

                # 构造候选的报告目录路径列表
                candidate_dirs = []
                if stock_symbol and analysis_date:
                    candidate_dirs.append(base_path / stock_symbol / analysis_date / 'reports')
                # 方案2：兼容其他可能的保存路径
                if stock_symbol and analysis_date:
                    candidate_dirs.append(project_root / 'data' / 'analysis_results' / stock_symbol / analysis_date / 'reports')
                    candidate_dirs.append(project_root / 'data' / 'analysis_results' / 'detailed' / stock_symbol / analysis_date / 'reports')

                # 遍历候选目录，读取所有.md格式的报告文件
                for d in candidate_dirs:
                    if d.exists() and d.is_dir():
                        for f in d.glob('*.md'):
                            try:
                                content = f.read_text(encoding='utf-8')
                                if content and content.strip():
                                    # 以文件名（不含扩展名）作为报告的key
                                    loaded_reports[f.stem] = content.strip()
                            except Exception:
                                pass  # 单个文件读取失败不影响其他文件

                if loaded_reports:
                    result_data['reports'] = loaded_reports
                    # 如果summary或recommendation缺失，尝试从同名报告中补全
                    if not result_data.get('summary') and loaded_reports.get('summary'):
                        result_data['summary'] = loaded_reports.get('summary')
                    if not result_data.get('recommendation') and loaded_reports.get('recommendation'):
                        result_data['recommendation'] = loaded_reports.get('recommendation')
                    logger.info(f"📁 [RESULT] 从文件系统加载到 {len(loaded_reports)} 个报告: {list(loaded_reports.keys())}")
            except Exception as fs_err:
                logger.warning(f"⚠️ [RESULT] 从文件系统加载报告失败: {fs_err}")

            # 方案3：如果文件系统也没有报告，尝试从state字段中提取
            # state字段保存了LangGraph工作流的完整状态，包含各分析师的输出
            if 'reports' not in result_data or not result_data['reports']:
                logger.info(f"📊 [RESULT] reports字段缺失，尝试从state中提取")

                reports = {}
                state = result_data.get('state', {})

                if isinstance(state, dict):
                    # 定义所有可能的报告字段名称
                    # 这些字段名对应LangGraph工作流中各节点的输出key
                    report_fields = [
                        'market_report',       # 市场分析师报告
                        'sentiment_report',    # 情绪分析师报告
                        'news_report',         # 新闻分析师报告
                        'fundamentals_report', # 基本面分析师报告
                        'investment_plan',     # 投资计划（研究经理输出）
                        'trader_investment_plan',  # 交易员投资计划
                        'final_trade_decision'     # 最终交易决策
                    ]

                    # 从state中逐个提取报告内容，过滤掉过短的无效内容
                    for field in report_fields:
                        value = state.get(field, "")
                        if isinstance(value, str) and len(value.strip()) > 10:
                            reports[field] = value.strip()

                    # 处理研究团队辩论状态报告
                    # investment_debate_state 包含多空研究员的辩论历史和研究经理的决策
                    investment_debate_state = state.get('investment_debate_state', {})
                    if isinstance(investment_debate_state, dict):
                        # 提取多头研究员的辩论历史
                        bull_content = investment_debate_state.get('bull_history', "")
                        if isinstance(bull_content, str) and len(bull_content.strip()) > 10:
                            reports['bull_researcher'] = bull_content.strip()

                        # 提取空头研究员的辩论历史
                        bear_content = investment_debate_state.get('bear_history', "")
                        if isinstance(bear_content, str) and len(bear_content.strip()) > 10:
                            reports['bear_researcher'] = bear_content.strip()

                        # 提取研究经理的最终决策
                        judge_decision = investment_debate_state.get('judge_decision', "")
                        if isinstance(judge_decision, str) and len(judge_decision.strip()) > 10:
                            reports['research_team_decision'] = judge_decision.strip()

                    # 处理风险管理团队辩论状态报告
                    # risk_debate_state 包含三种风格分析师的辩论历史和投资组合经理的决策
                    risk_debate_state = state.get('risk_debate_state', {})
                    if isinstance(risk_debate_state, dict):
                        # 提取激进分析师的辩论历史
                        risky_content = risk_debate_state.get('risky_history', "")
                        if isinstance(risky_content, str) and len(risky_content.strip()) > 10:
                            reports['risky_analyst'] = risky_content.strip()

                        # 提取保守分析师的辩论历史
                        safe_content = risk_debate_state.get('safe_history', "")
                        if isinstance(safe_content, str) and len(safe_content.strip()) > 10:
                            reports['safe_analyst'] = safe_content.strip()

                        # 提取中性分析师的辩论历史
                        neutral_content = risk_debate_state.get('neutral_history', "")
                        if isinstance(neutral_content, str) and len(neutral_content.strip()) > 10:
                            reports['neutral_analyst'] = neutral_content.strip()

                        # 提取投资组合经理（风险经理）的最终决策
                        risk_decision = risk_debate_state.get('judge_decision', "")
                        if isinstance(risk_decision, str) and len(risk_decision.strip()) > 10:
                            reports['risk_management_decision'] = risk_decision.strip()

                    logger.info(f"📊 [RESULT] 从state中提取到 {len(reports)} 个报告: {list(reports.keys())}")
                    result_data['reports'] = reports
                else:
                    logger.warning(f"⚠️ [RESULT] state字段不是字典类型: {type(state)}")

        # ==================== 报告内容清洗 ====================
        # 确保reports字段中的所有内容都是字符串类型，过滤空值和None
        if 'reports' in result_data and result_data['reports']:
            reports = result_data['reports']
            if isinstance(reports, dict):
                cleaned_reports = {}
                for key, value in reports.items():
                    if isinstance(value, str) and value.strip():
                        # 有效的非空字符串报告，去除首尾空白
                        cleaned_reports[key] = value.strip()
                    elif value is not None:
                        # 非字符串类型的报告内容，转换为字符串
                        str_value = str(value).strip()
                        if str_value:  # 只保存转换后非空的内容
                            cleaned_reports[key] = str_value
                        # None或空字符串的报告直接跳过

                result_data['reports'] = cleaned_reports
                logger.info(f"📊 [RESULT] 清理reports字段，包含 {len(cleaned_reports)} 个有效报告")

                # 清理后无有效报告时，设置为空字典
                if not cleaned_reports:
                    logger.warning(f"⚠️ [RESULT] 清理后没有有效报告")
                    result_data['reports'] = {}
            else:
                # reports字段类型异常（应为字典），重置为空字典
                logger.warning(f"⚠️ [RESULT] reports字段不是字典类型: {type(reports)}")
                result_data['reports'] = {}

        # ==================== 关键字段补全 ====================
        # 当summary、recommendation、key_points等关键字段缺失时，从已有数据中推断补全
        try:
            reports = result_data.get('reports', {}) or {}
            decision = result_data.get('decision', {}) or {}

            # 补全recommendation（投资建议）
            # 优先使用decision中的结构化信息，其次从报告中兜底
            if not result_data.get('recommendation'):
                rec_candidates = []
                # 从decision字段构造推荐文本
                if isinstance(decision, dict) and decision.get('action'):
                    parts = [
                        f"操作: {decision.get('action')}",  # 操作方向：买入/卖出/持有
                        f"目标价: {decision.get('target_price')}" if decision.get('target_price') else None,
                        f"置信度: {decision.get('confidence')}" if decision.get('confidence') is not None else None
                    ]
                    rec_candidates.append("；".join([p for p in parts if p]))
                # 从最终交易决策或投资计划报告中兜底
                for k in ['final_trade_decision', 'investment_plan']:
                    v = reports.get(k)
                    if isinstance(v, str) and len(v.strip()) > 10:
                        rec_candidates.append(v.strip())
                if rec_candidates:
                    # 取最有信息量的一条（最长内容）
                    result_data['recommendation'] = max(rec_candidates, key=len)[:2000]

            # 补全summary（分析摘要）
            # 从各分析师报告中拼接生成综合摘要
            if not result_data.get('summary'):
                sum_candidates = []
                for k in ['market_report', 'fundamentals_report', 'sentiment_report', 'news_report']:
                    v = reports.get(k)
                    if isinstance(v, str) and len(v.strip()) > 50:
                        sum_candidates.append(v.strip())
                if sum_candidates:
                    # 将各报告内容用双换行拼接，限制总长度为3000字符
                    result_data['summary'] = ("\n\n".join(sum_candidates))[:3000]

            # 补全key_points（关键要点）
            if not result_data.get('key_points'):
                kp = []
                if isinstance(decision, dict):
                    if decision.get('action'):
                        kp.append(f"操作建议: {decision.get('action')}")
                    if decision.get('target_price'):
                        kp.append(f"目标价: {decision.get('target_price')}")
                    if decision.get('confidence') is not None:
                        kp.append(f"置信度: {decision.get('confidence')}")
                # 从投资计划或最终决策中截取前几句作为要点
                for k in ['investment_plan', 'final_trade_decision']:
                    v = reports.get(k)
                    if isinstance(v, str) and len(v.strip()) > 10:
                        kp.append(v.strip()[:120])  # 每条要点限制120字符
                if kp:
                    result_data['key_points'] = kp[:5]  # 最多保留5条要点
        except Exception as fill_err:
            # 补全逻辑的异常不应阻断结果返回，仅记录警告
            logger.warning(f"⚠️ [RESULT] 补全关键字段时出错: {fill_err}")


        # ==================== 详细分析数据兜底 ====================
        # 当summary/recommendation/reports仍然缺失时，从detailed_analysis字段推断
        try:
            if not result_data.get('summary') or not result_data.get('recommendation') or not result_data.get('reports'):
                da = result_data.get('detailed_analysis')

                # 如果reports仍为空，将detailed_analysis内容放入reports
                if (not result_data.get('reports')) and isinstance(da, str) and len(da.strip()) > 20:
                    result_data['reports'] = {'detailed_analysis': da.strip()}
                elif (not result_data.get('reports')) and isinstance(da, dict) and da:
                    # 将字典中的长文本项提取为报告
                    extracted = {}
                    for k, v in da.items():
                        if isinstance(v, str) and len(v.strip()) > 20:
                            extracted[k] = v.strip()
                    if extracted:
                        result_data['reports'] = extracted

                # 补全summary（从detailed_analysis提取）
                if not result_data.get('summary'):
                    if isinstance(da, str) and da.strip():
                        result_data['summary'] = da.strip()[:3000]
                    elif isinstance(da, dict) and da:
                        # 取最长的文本作为摘要
                        texts = [v.strip() for v in da.values() if isinstance(v, str) and v.strip()]
                        if texts:
                            result_data['summary'] = max(texts, key=len)[:3000]

                # 补全recommendation（从detailed_analysis中提取含"建议"关键字的段落）
                if not result_data.get('recommendation'):
                    rec = None
                    if isinstance(da, str):
                        import re
                        # 用正则匹配包含"投资建议"、"建议"或"结论"的段落
                        m = re.search(r'(投资建议|建议|结论)[:：]?\s*(.+)', da)
                        if m:
                            rec = m.group(0)
                    elif isinstance(da, dict):
                        # 按优先级依次检查常见的关键字段
                        for key in ['final_trade_decision', 'investment_plan', '结论', '建议']:
                            v = da.get(key)
                            if isinstance(v, str) and len(v.strip()) > 10:
                                rec = v.strip()
                                break
                    if rec:
                        result_data['recommendation'] = rec[:2000]
        except Exception as da_err:
            logger.warning(f"⚠️ [RESULT] 从detailed_analysis补全失败: {da_err}")

        # ==================== 类型安全转换工具函数 ====================
        # 这些函数确保最终返回的数据字段类型正确，避免前端因类型不匹配而出错

        def safe_string(value, default=""):
            """安全地转换为字符串类型

            将任意类型的值转换为字符串，处理None和异常情况。
            用于确保analysis_id、stock_symbol等字符串字段不会出现类型错误。
            """
            if value is None:
                return default
            if isinstance(value, str):
                return value
            return str(value)

        def safe_number(value, default=0):
            """安全地转换为数字类型

            将任意类型的值转换为数字（int或float），处理None、字符串数字等异常情况。
            用于确保confidence_score、execution_time等数值字段的类型安全。
            """
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return value
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        def safe_list(value, default=None):
            """安全地转换为列表类型

            确保值为列表类型，用于analysts、key_points等列表字段。
            非列表类型的值将被替换为默认空列表。
            """
            if default is None:
                default = []
            if value is None:
                return default
            if isinstance(value, list):
                return value
            return default

        def safe_dict(value, default=None):
            """安全地转换为字典类型

            确保值为字典类型，用于decision、reports、state等字典字段。
            非字典类型的值将被替换为默认空字典。
            """
            if default is None:
                default = {}
            if value is None:
                return default
            if isinstance(value, dict):
                return value
            return default

        # 调试日志：检查最终构建前的result_data完整性
        logger.info(f"🔍 [FINAL] 构建最终结果前，result_data键: {list(result_data.keys())}")
        logger.info(f"🔍 [FINAL] result_data中有decision: {bool(result_data.get('decision'))}")
        if result_data.get('decision'):
            logger.info(f"🔍 [FINAL] decision内容: {result_data['decision']}")

        # ==================== 构建严格验证的最终结果 ====================
        # 使用安全转换函数确保每个字段的类型正确
        # 对summary和recommendation应用remove_thinking_content清洗LLM思考链标签
        # 对detailed_analysis、state、decision应用clean_llm_response清洗LLM特殊标记
        final_result_data = {
            "analysis_id": safe_string(result_data.get("analysis_id"), "unknown"),  # 分析ID，兜底为"unknown"
            "stock_symbol": safe_string(result_data.get("stock_symbol"), "UNKNOWN"),  # 股票代码
            "stock_code": safe_string(result_data.get("stock_code"), "UNKNOWN"),  # 兼容字段
            "analysis_date": safe_string(result_data.get("analysis_date"), "2025-08-20"),  # 分析日期
            "summary": remove_thinking_content(safe_string(result_data.get("summary"), "分析摘要暂无")),  # 清洗后的分析摘要
            "recommendation": remove_thinking_content(safe_string(result_data.get("recommendation"), "投资建议暂无")),  # 清洗后的投资建议
            "confidence_score": safe_number(result_data.get("confidence_score"), 0.0),  # 置信度评分
            "risk_level": safe_string(result_data.get("risk_level"), "中等"),  # 风险等级
            "key_points": safe_list(result_data.get("key_points")),  # 关键要点列表
            "execution_time": safe_number(result_data.get("execution_time"), 0),  # 执行耗时
            "tokens_used": safe_number(result_data.get("tokens_used"), 0),  # Token消耗量
            "analysts": safe_list(result_data.get("analysts")),  # 参与分析的分析师列表
            "research_depth": safe_string(result_data.get("research_depth"), "快速"),  # 研究深度
            "detailed_analysis": clean_llm_response(safe_dict(result_data.get("detailed_analysis"))),  # 清洗后的详细分析
            "state": clean_llm_response(safe_dict(result_data.get("state"))),  # 清洗后的工作流状态
            "decision": clean_llm_response(safe_dict(result_data.get("decision")))  # 清洗后的交易决策
        }

        # ==================== 报告模块特殊处理 ====================
        # 对reports字段进行额外的清洗和验证
        # sanitize_report_modules会处理旧数据中可能存在的dict/history字符串污染问题
        reports_data = safe_dict(result_data.get("reports"))
        validated_reports = sanitize_report_modules(reports_data)

        final_result_data["reports"] = validated_reports

        logger.info(f"✅ [RESULT] 成功获取任务结果: {task_id}")
        logger.info(f"📊 [RESULT] 最终返回 {len(final_result_data.get('reports', {}))} 个报告")

        # 调试日志：检查最终返回数据的完整性
        logger.info(f"🔍 [FINAL] 最终返回数据键: {list(final_result_data.keys())}")
        logger.info(f"🔍 [FINAL] 最终返回中有decision: {bool(final_result_data.get('decision'))}")
        if final_result_data.get('decision'):
            logger.info(f"🔍 [FINAL] 最终decision内容: {final_result_data['decision']}")

        return {
            "success": True,
            "data": final_result_data,
            "message": "分析结果获取成功"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [RESULT] 获取任务结果失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 任务列表查询端点 ====================

@router.get("/tasks/all", response_model=Dict[str, Any])
async def list_all_tasks(
    user: dict = Depends(get_current_user),
    status: Optional[str] = Query(None, description="任务状态过滤"),
    limit: int = Query(20, ge=1, le=100, description="返回数量限制"),
    offset: int = Query(0, ge=0, description="偏移量")
):
    """获取所有任务列表（不限用户）

    管理员级别的接口，返回系统中所有用户的任务列表。
    支持按任务状态过滤和分页查询。

    参数：
        user: 当前登录用户信息
        status: 可选的状态过滤条件（pending/processing/completed/failed）
        limit: 返回数量上限，默认20，最大100
        offset: 分页偏移量，默认0

    返回：
        success: 是否查询成功
        data: 包含任务列表、总数、分页参数的字典
        message: 提示信息
    """
    try:
        logger.info(f"📋 查询所有任务列表")

        tasks = await get_simple_analysis_service().list_all_tasks(
            status=status,
            limit=limit,
            offset=offset
        )

        return {
            "success": True,
            "data": {
                "tasks": tasks,
                "total": len(tasks),
                "limit": limit,
                "offset": offset
            },
            "message": "任务列表获取成功"
        }

    except Exception as e:
        logger.error(f"❌ 获取任务列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks", response_model=Dict[str, Any])
async def list_user_tasks(
    user: dict = Depends(get_current_user),
    status: Optional[str] = Query(None, description="任务状态过滤"),
    limit: int = Query(20, ge=1, le=100, description="返回数量限制"),
    offset: int = Query(0, ge=0, description="偏移量")
):
    """获取当前用户的任务列表

    返回当前登录用户创建的所有分析任务，支持状态过滤和分页。
    与list_all_tasks不同，此接口仅返回当前用户的任务。

    参数：
        user: 当前登录用户信息（用于筛选该用户的任务）
        status: 可选的状态过滤条件
        limit: 返回数量上限
        offset: 分页偏移量

    返回：
        success: 是否查询成功
        data: 包含任务列表、总数、分页参数的字典
        message: 提示信息
    """
    try:
        logger.info(f"📋 查询用户任务列表: {user['id']}")

        tasks = await get_simple_analysis_service().list_user_tasks(
            user_id=user["id"],
            status=status,
            limit=limit,
            offset=offset
        )

        return {
            "success": True,
            "data": {
                "tasks": tasks,
                "total": len(tasks),
                "limit": limit,
                "offset": offset
            },
            "message": "任务列表获取成功"
        }

    except Exception as e:
        logger.error(f"❌ 获取任务列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 批量分析端点 ====================

@router.post("/batch", response_model=Dict[str, Any])
async def submit_batch_analysis(
    request: BatchAnalysisRequest,
    user: dict = Depends(get_current_user)
):
    """提交批量分析任务（真正的并发执行）

    接收多只股票的分析请求，为每只股票创建独立的分析任务，
    然后使用 asyncio.create_task 实现真正的并发执行。

    重要：不使用 FastAPI 的 BackgroundTasks，因为 BackgroundTasks 是串行执行的，
    无法实现批量任务的并发处理。

    处理流程：
    1. 验证请求参数（股票代码数量不超过10个）
    2. 为每只股票创建独立的分析任务记录
    3. 使用 asyncio.create_task 并发启动所有分析任务
    4. 立即返回批次ID和所有任务ID

    参数：
        request: 批量分析请求，包含股票代码列表和分析参数
        user: 当前登录用户信息

    返回：
        success: 是否成功提交
        data: 包含batch_id、task_ids、mapping的批次信息
        message: 提示信息
    """
    try:
        logger.info(f"🎯 [批量分析] 收到批量分析请求: title={request.title}")

        simple_service = get_simple_analysis_service()
        # 生成唯一的批次标识符
        batch_id = str(uuid.uuid4())
        # 存储所有创建的任务ID
        task_ids: List[str] = []
        # 存储股票代码与任务ID的映射关系
        mapping: List[Dict[str, str]] = []

        # 获取股票代码列表（兼容旧字段名）
        stock_symbols = request.get_symbols()
        logger.info(f"📊 [批量分析] 股票代码列表: {stock_symbols}")

        # 验证股票代码列表不为空
        if not stock_symbols:
            raise ValueError("股票代码列表不能为空")

        # 限制批量分析的股票数量，防止资源过度消耗
        MAX_BATCH_SIZE = 10
        if len(stock_symbols) > MAX_BATCH_SIZE:
            raise ValueError(f"批量分析最多支持 {MAX_BATCH_SIZE} 个股票，当前提交了 {len(stock_symbols)} 个")

        # 为每只股票创建单股分析任务记录
        for i, symbol in enumerate(stock_symbols):
            logger.info(f"📝 [批量分析] 正在创建第 {i+1}/{len(stock_symbols)} 个任务: {symbol}")

            # 构造单股分析请求对象
            single_req = SingleAnalysisRequest(
                symbol=symbol,
                stock_code=symbol,  # 兼容字段
                parameters=request.parameters
            )

            try:
                # 调用服务层创建任务记录
                create_res = await simple_service.create_analysis_task(user["id"], single_req)
                task_id = create_res.get("task_id")
                if not task_id:
                    raise RuntimeError(f"创建任务失败：未返回task_id (symbol={symbol})")
                task_ids.append(task_id)
                mapping.append({"symbol": symbol, "stock_code": symbol, "task_id": task_id})
                logger.info(f"✅ [批量分析] 已创建任务: {task_id} - {symbol}")
            except Exception as create_error:
                # 单个任务创建失败时，中断整个批量操作
                logger.error(f"❌ [批量分析] 创建任务失败: {symbol}, 错误: {create_error}", exc_info=True)
                raise

        # 使用 asyncio.create_task 实现真正的并发执行
        # 相比 BackgroundTasks 的串行执行，这能显著缩短批量分析的总耗时
        async def run_concurrent_analysis():
            """并发执行所有分析任务

            为每只股票的分析创建独立的 asyncio.Task，
            然后使用 asyncio.gather 并发执行所有任务。
            return_exceptions=True 确保单个任务失败不会影响其他任务。
            """
            tasks = []
            for i, symbol in enumerate(stock_symbols):
                task_id = task_ids[i]
                single_req = SingleAnalysisRequest(
                    symbol=symbol,
                    stock_code=symbol,
                    parameters=request.parameters
                )

                # 定义单个分析任务的执行函数
                async def run_single_analysis(tid: str, req: SingleAnalysisRequest, uid: str):
                    """执行单个分析任务

                    参数通过函数参数传入而非闭包捕获，避免延迟绑定问题。
                    """
                    try:
                        logger.info(f"🚀 [并发任务] 开始执行: {tid} - {req.stock_code}")
                        await simple_service.execute_analysis_background(tid, uid, req)
                        logger.info(f"✅ [并发任务] 执行完成: {tid}")
                    except Exception as e:
                        logger.error(f"❌ [并发任务] 执行失败: {tid}, 错误: {e}", exc_info=True)

                # 创建异步任务并添加到任务列表
                task = asyncio.create_task(run_single_analysis(task_id, single_req, user["id"]))
                tasks.append(task)
                logger.info(f"✅ [批量分析] 已创建并发任务: {task_id} - {symbol}")

            # 等待所有任务完成
            # return_exceptions=True：将异常作为结果返回，而不是抛出
            # 这确保单个任务的失败不会导致其他任务被取消
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"🎉 [批量分析] 所有任务执行完成: batch_id={batch_id}")

        # 在后台启动并发任务（不等待完成，立即返回响应）
        asyncio.create_task(run_concurrent_analysis())
        logger.info(f"🚀 [批量分析] 已启动 {len(task_ids)} 个并发任务")

        return {
            "success": True,
            "data": {
                "batch_id": batch_id,  # 批次唯一标识
                "total_tasks": len(task_ids),  # 总任务数
                "task_ids": task_ids,  # 各任务的ID列表
                "mapping": mapping,  # 股票代码与任务ID的映射
                "status": "submitted"  # 批次状态：已提交
            },
            "message": f"批量分析任务已提交，共{len(task_ids)}个股票，正在并发执行"
        }
    except Exception as e:
        logger.error(f"❌ [批量分析] 提交失败: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 兼容性旧版API端点 ====================
# 以下端点保留用于向后兼容，新代码应使用上方的新版API

@router.post("/analyze")
async def analyze_single(
    req: SingleAnalyzeRequest,
    user: dict = Depends(get_current_user),
    svc: QueueService = Depends(get_queue_service)
):
    """单股分析（兼容性端点）

    旧版单股分析接口，使用队列服务（QueueService）进行任务排队。
    新代码应使用 POST /single 端点。

    参数：
        req: 旧版单股分析请求
        user: 当前登录用户
        svc: 队列服务实例（通过依赖注入获取）

    返回：
        task_id: 任务ID
        status: 任务状态（queued表示已入队）
    """
    try:
        task_id = await svc.enqueue_task(
            user_id=user["id"],
            symbol=req.symbol,
            params=req.parameters
        )
        return {"task_id": task_id, "status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/analyze/batch")
async def analyze_batch(
    req: BatchAnalyzeRequest,
    user: dict = Depends(get_current_user),
    svc: QueueService = Depends(get_queue_service)
):
    """批量分析（兼容性端点）

    旧版批量分析接口，使用队列服务创建批量任务。
    新代码应使用 POST /batch 端点。

    参数：
        req: 旧版批量分析请求
        user: 当前登录用户
        svc: 队列服务实例

    返回：
        batch_id: 批次ID
        submitted: 成功提交的任务数量
    """
    try:
        batch_id, submitted = await svc.create_batch(
            user_id=user["id"],
            symbols=req.symbols,
            params=req.parameters
        )
        return {"batch_id": batch_id, "submitted": submitted}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, user: dict = Depends(get_current_user), svc: QueueService = Depends(get_queue_service)):
    """获取批次信息（兼容性端点）

    根据批次ID查询批量分析任务的信息，验证任务所有权。

    参数：
        batch_id: 批次唯一标识
        user: 当前登录用户
        svc: 队列服务实例

    返回：
        批次信息字典

    异常：
        404: 批次不存在或不属于当前用户
    """
    b = await svc.get_batch(batch_id)
    if not b or b.get("user") != user["id"]:
        raise HTTPException(status_code=404, detail="batch not found")
    return b


# ==================== 已注释的旧版端点 ====================
# 以下路由已被新的异步实现替代，保留注释作为参考

# 注意：这个路由被移到了 /tasks/{task_id}/status 之后，避免路由冲突
# @router.get("/tasks/{task_id}")
# async def get_task(
#     task_id: str,
#     user: dict = Depends(get_current_user),
#     svc: QueueService = Depends(get_queue_service)
# ):
#     """获取任务详情"""
#     t = await svc.get_task(task_id)
#     if not t or t.get("user") != user["id"]:
#         raise HTTPException(status_code=404, detail="任务不存在")
#     return t

# 原有的路由已被新的异步实现替代
# @router.get("/tasks/{task_id}/status")
# async def get_task_status_old(
#     task_id: str,
#     user: dict = Depends(get_current_user)
# ):
#     """获取任务状态和进度（旧版实现）"""
#     try:
#         status = await get_analysis_service().get_task_status(task_id)
#         if not status:
#             raise HTTPException(status_code=404, detail="任务不存在")
#         return {
#             "success": True,
#             "data": status
#         }
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=str(e))


# ==================== 任务操作端点 ====================

@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    svc: QueueService = Depends(get_queue_service)
):
    """取消任务

    取消指定ID的分析任务。仅任务创建者可以取消自己的任务。

    参数：
        task_id: 要取消的任务ID
        user: 当前登录用户
        svc: 队列服务实例

    返回：
        success: 是否取消成功
        message: 提示信息

    异常：
        404: 任务不存在或不属于当前用户
        400: 取消操作失败（如任务已完成无法取消）
    """
    try:
        # 验证任务所有权，确保只有任务创建者可以取消
        task = await svc.get_task(task_id)
        if not task or task.get("user") != user["id"]:
            raise HTTPException(status_code=404, detail="任务不存在")

        success = await svc.cancel_task(task_id)
        if success:
            return {"success": True, "message": "任务已取消"}
        else:
            raise HTTPException(status_code=400, detail="取消任务失败")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/user/queue-status")
async def get_user_queue_status(
    user: dict = Depends(get_current_user),
    svc: QueueService = Depends(get_queue_service)
):
    """获取用户队列状态

    查询当前用户在分析队列中的任务排队情况，
    包括等待中的任务数量、正在执行的任务数量等。

    参数：
        user: 当前登录用户
        svc: 队列服务实例

    返回：
        success: 是否查询成功
        data: 队列状态信息
    """
    try:
        status = await svc.get_user_queue_status(user["id"])
        return {
            "success": True,
            "data": status
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 用户分析历史查询端点 ====================

@router.get("/user/history")
async def get_user_analysis_history(
    user: dict = Depends(get_current_user),
    status: Optional[str] = Query(None, description="任务状态过滤"),
    start_date: Optional[str] = Query(None, description="开始日期，YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期，YYYY-MM-DD"),
    symbol: Optional[str] = Query(None, description="股票代码"),
    stock_code: Optional[str] = Query(None, description="股票代码(已废弃,使用symbol)"),
    market_type: Optional[str] = Query(None, description="市场类型"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小")
):
    """获取用户分析历史（支持基础筛选与分页）

    查询当前用户的历史分析记录，支持按状态、日期范围、股票代码、市场类型筛选，
    并支持分页查询。

    数据来源优先级：内存状态管理器 > MongoDB

    参数：
        user: 当前登录用户
        status: 可选的任务状态过滤条件
        start_date: 可选的开始日期（格式：YYYY-MM-DD）
        end_date: 可选的结束日期（格式：YYYY-MM-DD）
        symbol: 可选的股票代码过滤
        stock_code: 股票代码（已废弃，请使用symbol参数）
        market_type: 可选的市场类型过滤
        page: 页码，默认1
        page_size: 每页大小，默认20，最大100

    返回：
        success: 是否查询成功
        data: 包含任务列表、总数、分页参数的字典
        message: 提示信息
    """
    try:
        # 获取用户任务列表（内存优先，MongoDB兜底）
        raw_tasks = await get_simple_analysis_service().list_user_tasks(
            user_id=user["id"],
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size
        )

        # 定义日期范围过滤辅助函数
        def in_date_range(t: Optional[str]) -> bool:
            """判断时间字符串是否在指定的日期范围内

            参数：
                t: ISO格式的时间字符串，可能包含时区信息

            返回：
                True表示在范围内或无法解析（默认通过），False表示不在范围内
            """
            if not t:
                return True  # 无时间信息时默认通过
            try:
                # 兼容带Z后缀和不带时区的时间字符串
                dt = datetime.fromisoformat(t.replace('Z', '+00:00')) if 'Z' in t else datetime.fromisoformat(t)
            except Exception:
                return True  # 解析失败时默认通过
            ok = True
            if start_date:
                try:
                    ok = ok and (dt.date() >= datetime.fromisoformat(start_date).date())
                except Exception:
                    pass
            if end_date:
                try:
                    ok = ok and (dt.date() <= datetime.fromisoformat(end_date).date())
                except Exception:
                    pass
            return ok

        # 获取查询的股票代码（兼容旧字段名stock_code）
        query_symbol = symbol or stock_code

        # 对原始任务列表进行二次筛选
        filtered = []
        for x in raw_tasks:
            # 按股票代码过滤
            if query_symbol:
                task_symbol = x.get("symbol") or x.get("stock_code") or x.get("stock_symbol")
                if task_symbol not in [query_symbol]:
                    continue
            # 按市场类型过滤（从任务参数中判断）
            if market_type:
                params = x.get("parameters") or {}
                if params.get("market_type") != market_type:
                    continue
            # 按时间范围过滤（优先使用start_time，其次使用created_at）
            t = x.get("start_time") or x.get("created_at")
            if not in_date_range(t):
                continue
            filtered.append(x)

        return {
            "success": True,
            "data": {
                "tasks": filtered,
                "total": len(filtered),
                "page": page,
                "page_size": page_size
            },
            "message": "历史查询成功"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== WebSocket端点 ====================

@router.websocket("/ws/task/{task_id}")
async def websocket_task_progress(websocket: WebSocket, task_id: str):
    """WebSocket 端点：实时获取任务进度

    建立WebSocket长连接，客户端可实时接收分析任务的进度更新。
    服务端通过WebSocket管理器在分析过程中主动推送进度消息。

    连接流程：
    1. 客户端发起WebSocket连接请求
    2. 服务端接受连接并注册到WebSocket管理器
    3. 发送连接确认消息
    4. 保持连接活跃，接收客户端心跳
    5. 分析服务在任务进度更新时通过管理器推送消息
    6. 客户端断开连接时自动清理

    参数：
        websocket: WebSocket连接实例
        task_id: 要监听进度的任务ID
    """
    import json
    websocket_manager = get_websocket_manager()

    try:
        # 接受WebSocket连接并注册到管理器，关联指定的任务ID
        await websocket_manager.connect(websocket, task_id)

        # 发送连接确认消息，通知客户端连接已成功建立
        await websocket.send_text(json.dumps({
            "type": "connection_established",
            "task_id": task_id,
            "message": "WebSocket 连接已建立"
        }))

        # 保持连接活跃，接收客户端消息（主要用于心跳检测）
        while True:
            try:
                # 等待接收客户端消息
                data = await websocket.receive_text()
                # 可以在此处理客户端发送的指令（如暂停、取消等）
                logger.debug(f"📡 收到 WebSocket 消息: {data}")
            except WebSocketDisconnect:
                # 客户端主动断开连接
                break
            except Exception as e:
                # 消息处理异常，断开连接
                logger.warning(f"⚠️ WebSocket 消息处理错误: {e}")
                break

    except WebSocketDisconnect:
        # 客户端断开连接（正常情况）
        logger.info(f"🔌 WebSocket 客户端断开连接: {task_id}")
    except Exception as e:
        # 连接建立或通信过程中的异常
        logger.error(f"❌ WebSocket 连接错误: {e}")
    finally:
        # 无论连接是否异常，都需要从管理器中注销该连接
        await websocket_manager.disconnect(websocket, task_id)


# ==================== 任务详情查询端点 ====================
# 注意：此路由放在最后，避免与 /tasks/{task_id}/status 等动态路径冲突

@router.get("/tasks/{task_id}/details")
async def get_task_details(
    task_id: str,
    user: dict = Depends(get_current_user),
    svc: QueueService = Depends(get_queue_service)
):
    """获取任务详情（使用不同的路径避免冲突）

    查询指定任务的完整详情信息，包括参数、状态、结果等。
    使用 /details 路径后缀与 /status 路径区分。

    参数：
        task_id: 任务ID
        user: 当前登录用户
        svc: 队列服务实例

    返回：
        任务详情字典

    异常：
        404: 任务不存在或不属于当前用户
    """
    t = await svc.get_task(task_id)
    if not t or t.get("user") != user["id"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


# ==================== 僵尸任务管理端点（管理员专用） ====================

@router.get("/admin/zombie-tasks")
async def get_zombie_tasks(
    max_running_hours: int = Query(default=2, ge=1, le=72, description="最大运行时长（小时）"),
    user: dict = Depends(get_current_user)
):
    """获取僵尸任务列表（仅管理员）

    僵尸任务定义：长时间处于 processing/running/pending 状态但未完成的任务。
    这些任务可能因服务重启、异常中断等原因卡在中间状态。

    参数：
        max_running_hours: 判定为僵尸任务的最大运行时长阈值（小时），默认2小时
        user: 当前登录用户（需为管理员）

    返回：
        success: 是否查询成功
        data: 僵尸任务列表
        total: 僵尸任务数量
        max_running_hours: 使用的阈值

    异常：
        403: 非管理员用户
        500: 查询失败
    """
    # 验证管理员权限
    if user.get("username") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问")

    try:
        svc = get_simple_analysis_service()
        zombie_tasks = await svc.get_zombie_tasks(max_running_hours)

        return {
            "success": True,
            "data": zombie_tasks,
            "total": len(zombie_tasks),
            "max_running_hours": max_running_hours
        }
    except Exception as e:
        logger.error(f"❌ 获取僵尸任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取僵尸任务失败: {str(e)}")


@router.post("/admin/cleanup-zombie-tasks")
async def cleanup_zombie_tasks(
    max_running_hours: int = Query(default=2, ge=1, le=72, description="最大运行时长（小时）"),
    user: dict = Depends(get_current_user)
):
    """清理僵尸任务（仅管理员）

    将长时间处于 processing/running/pending 状态的任务标记为失败。
    清理后这些任务不会再阻塞队列或占用资源。

    参数：
        max_running_hours: 判定为僵尸任务的最大运行时长阈值（小时）
        user: 当前登录用户（需为管理员）

    返回：
        success: 是否清理成功
        data: 清理结果，包含清理数量等信息
        message: 提示信息

    异常：
        403: 非管理员用户
        500: 清理操作失败
    """
    # 验证管理员权限
    if user.get("username") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问")

    try:
        svc = get_simple_analysis_service()
        result = await svc.cleanup_zombie_tasks(max_running_hours)

        return {
            "success": True,
            "data": result,
            "message": f"已清理 {result.get('total_cleaned', 0)} 个僵尸任务"
        }
    except Exception as e:
        logger.error(f"❌ 清理僵尸任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理僵尸任务失败: {str(e)}")


# ==================== 任务管理操作端点 ====================

@router.post("/tasks/{task_id}/mark-failed")
async def mark_task_as_failed(
    task_id: str,
    user: dict = Depends(get_current_user)
):
    """将指定任务标记为失败

    用于手动清理卡住的任务。同时更新内存状态管理器和MongoDB中的任务状态。

    操作步骤：
    1. 更新内存状态管理器中的任务状态为FAILED
    2. 更新MongoDB analysis_tasks集合中的状态和相关字段

    参数：
        task_id: 要标记为失败的任务ID
        user: 当前登录用户

    返回：
        success: 是否操作成功
        message: 提示信息

    异常：
        500: 操作失败
    """
    try:
        svc = get_simple_analysis_service()

        # 更新内存中的任务状态为失败
        from app.services.memory_state_manager import TaskStatus
        await svc.memory_manager.update_task_status(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message="手动标记为失败",
            error_message="用户手动标记为失败"
        )

        # 更新MongoDB中的任务状态
        from app.core.database import get_mongo_db
        from datetime import datetime
        db = get_mongo_db()

        result = await db.analysis_tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "status": "failed",  # 更新状态为失败
                    "last_error": "用户手动标记为失败",  # 记录失败原因
                    "completed_at": datetime.utcnow(),  # 记录完成时间
                    "updated_at": datetime.utcnow()  # 记录更新时间
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"✅ 任务 {task_id} 已标记为失败")
            return {
                "success": True,
                "message": "任务已标记为失败"
            }
        else:
            # 任务未找到或已经是失败状态
            logger.warning(f"⚠️ 任务 {task_id} 未找到或已是失败状态")
            return {
                "success": True,
                "message": "任务未找到或已是失败状态"
            }
    except Exception as e:
        logger.error(f"❌ 标记任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"标记任务失败: {str(e)}")


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    user: dict = Depends(get_current_user)
):
    """删除指定任务

    从内存状态管理器和MongoDB中彻底删除任务记录。
    此操作不可逆，删除后任务数据无法恢复。

    操作步骤：
    1. 从内存状态管理器中移除任务
    2. 从MongoDB analysis_tasks集合中删除任务文档

    参数：
        task_id: 要删除的任务ID
        user: 当前登录用户

    返回：
        success: 是否删除成功
        message: 提示信息

    异常：
        500: 删除操作失败
    """
    try:
        svc = get_simple_analysis_service()

        # 从内存中删除任务记录
        await svc.memory_manager.remove_task(task_id)

        # 从MongoDB中删除任务文档
        from app.core.database import get_mongo_db
        db = get_mongo_db()

        result = await db.analysis_tasks.delete_one({"task_id": task_id})

        if result.deleted_count > 0:
            logger.info(f"✅ 任务 {task_id} 已删除")
            return {
                "success": True,
                "message": "任务已删除"
            }
        else:
            # 任务未找到（可能已被清理）
            logger.warning(f"⚠️ 任务 {task_id} 未找到")
            return {
                "success": True,
                "message": "任务未找到"
            }
    except Exception as e:
        logger.error(f"❌ 删除任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除任务失败: {str(e)}")
