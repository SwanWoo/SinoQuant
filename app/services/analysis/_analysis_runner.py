"""
分析执行 mixin — FastAPI 后端调用 LangGraph 交易引擎的核心桥接层

职责：将前端的股票分析请求转化为对 SinaQuantGraph（LangGraph 多智能体图）的调用，
     并将图的执行结果格式化为前端可用的分析报告。

调用链路：
  前端 POST /analysis/single
    → analysis.py 路由
    → SimpleAnalysisService.execute_analysis_background()  [_task_manager.py]
    → _execute_analysis_sync()  →  run_in_executor(线程池)
    → _run_analysis_sync()  [本文件的核心方法]
    → SinaQuantGraph.propagate()  [sinoquant/graph/trading_graph.py]

_run_analysis_sync 的完整流程：
  1. 模型选择：根据研究深度推荐/验证快速模型和深度模型
  2. 配置构建：调用 create_analysis_config() 组装 SinaQuantGraph 需要的配置
  3. 引擎初始化：创建 SinaQuantGraph 实例（每次新建，保证线程安全）
  4. 执行分析：调用 propagate() 驱动多智能体辩论流程
  5. 结果提取：从 state 中提取各分析师报告、辩论记录、交易决策
  6. 结果格式化：翻译决策为中文、生成摘要和投资建议
  7. 返回结构化结果（交由 _result_persistence.py 持久化）

线程安全说明：
  _run_analysis_sync 在 ThreadPoolExecutor 中运行，不能直接使用 motor 异步客户端。
  更新 MongoDB 时使用 pymongo 同步客户端，或创建新的 asyncio 事件循环。
"""

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.models.analysis import SingleAnalysisRequest
from app.models.user import PyObjectId
from app.services.memory_state_manager import TaskStatus
from app.services.redis_progress_tracker import RedisProgressTracker
from sinoquant.graph.trading_graph import SinaQuantGraph
from sinoquant.utils.text_utils import (
    normalize_report_content,
    remove_thinking_content,
    sanitize_report_modules,
)

from ._analysis_config import (
    create_analysis_config,
    get_provider_and_url_by_model_sync,
    _apply_report_visibility_policy,
)
from ._text_utils import (
    _get_stock_info_safe,
    _pick_best_summary,
    _build_summary_from_text,
)

logger = logging.getLogger("app.services.simple_analysis_service")


class AnalysisRunnerMixin:
    """提供核心分析执行功能的 mixin

    被 SimpleAnalysisService 继承，提供以下方法：
    - _resolve_stock_name(): 解析股票名称（带缓存）
    - _enrich_stock_names(): 批量补齐股票名称
    - _get_trading_graph(): 创建 SinaQuantGraph 实例
    - _execute_analysis_sync(): 异步包装，将同步分析提交到线程池
    - _run_analysis_sync(): 核心方法，完整执行分析并返回结构化结果
    """

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _resolve_stock_name(self, code: Optional[str]) -> str:
        """解析股票名称（带缓存）"""
        if not code:
            return ""
        # 命中缓存
        if code in self._stock_name_cache:
            return self._stock_name_cache[code]
        name = None
        try:
            if _get_stock_info_safe:
                info = _get_stock_info_safe(code)
                if isinstance(info, dict):
                    name = info.get("name")
        except Exception as e:
            logger.warning(f"⚠️ 获取股票名称失败: {code} - {e}")
        if not name:
            name = f"股票{code}"
        # 写缓存
        self._stock_name_cache[code] = name
        return name

    def _enrich_stock_names(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """为任务列表补齐股票名称(就地更新)"""
        try:
            for t in tasks:
                code = t.get("stock_code") or t.get("stock_symbol")
                name = t.get("stock_name")
                if not name and code:
                    t["stock_name"] = self._resolve_stock_name(code)
        except Exception as e:
            logger.warning(f"⚠️ 补齐股票名称时出现异常: {e}")
        return tasks

    def _convert_user_id(self, user_id: str) -> PyObjectId:
        """将字符串用户ID转换为PyObjectId"""
        try:
            logger.info(f"🔄 开始转换用户ID: {user_id} (类型: {type(user_id)})")

            # 如果是admin用户，使用固定的ObjectId
            if user_id == "admin":
                admin_object_id = ObjectId("507f1f77bcf86cd799439011")
                logger.info(f"🔄 转换admin用户ID: {user_id} -> {admin_object_id}")
                return PyObjectId(admin_object_id)
            else:
                # 尝试将字符串转换为ObjectId
                object_id = ObjectId(user_id)
                logger.info(f"🔄 转换用户ID: {user_id} -> {object_id}")
                return PyObjectId(object_id)
        except Exception as e:
            logger.error(f"❌ 用户ID转换失败: {user_id} -> {e}")
            # 如果转换失败，生成一个新的ObjectId
            new_object_id = ObjectId()
            logger.warning(f"⚠️ 生成新的用户ID: {new_object_id}")
            return PyObjectId(new_object_id)

    def _get_trading_graph(self, config: Dict[str, Any]) -> SinaQuantGraph:
        """创建 SinaQuantGraph 实例（每次新建，保证线程安全）

        ⚠️ 为什么每次都创建新实例？
        SinaQuantGraph 内部持有可变状态（self.ticker, self.curr_state 等），
        如果多个分析任务共享同一个实例，会导致数据混淆（A 任务的股票代码
        被 B 任务覆盖）。虽然每次创建有初始化开销（LLM 实例化 + 图编译），
        但这是保证并发安全的必要代价。

        Args:
            config: 分析配置字典，由 create_analysis_config() 生成。
                包含 LLM 参数、供应商信息、分析师选择等。

        Returns:
            初始化完成的 SinaQuantGraph 实例，可直接调用 propagate()
        """
        # 🔧 [并发安全] 每次都创建新实例，避免多线程共享状态
        # 不再使用缓存，因为 SinaQuantGraph 有可变的实例变量
        logger.info(f"🔧 创建新的SinaQuant实例（并发安全模式）...")

        trading_graph = SinaQuantGraph(
            selected_analysts=config.get("selected_analysts", ["market", "fundamentals"]),
            debug=config.get("debug", False),
            config=config
        )

        logger.info(f"✅ SinaQuant实例创建成功（实例ID: {id(trading_graph)}）")

        return trading_graph

    # ------------------------------------------------------------------
    # 异步包装
    # ------------------------------------------------------------------

    async def _execute_analysis_sync(
        self,
        task_id: str,
        user_id: str,
        request: SingleAnalysisRequest,
        progress_tracker: Optional[RedisProgressTracker] = None
    ) -> Dict[str, Any]:
        """将同步分析提交到共享线程池执行

        为什么需要线程池？
        SinaQuantGraph.propagate() 是同步方法（内部调用 LangGraph 的同步 stream()），
        而 FastAPI 的请求处理是异步的。通过 run_in_executor() 将同步分析
        提交到线程池，避免阻塞事件循环。

        线程池配置：
        SimpleAnalysisService 使用 ThreadPoolExecutor(max_workers=3)，
        最多允许 3 个分析任务并发执行。
        """
        # 🔧 使用共享线程池，支持多个任务并发执行
        # 不再每次创建新的线程池，避免串行执行
        loop = asyncio.get_event_loop()
        logger.info(f"🚀 [线程池] 提交分析任务到共享线程池: {task_id} - {request.stock_code}")
        result = await loop.run_in_executor(
            self._thread_pool,  # 使用共享线程池
            self._run_analysis_sync,
            task_id,
            user_id,
            request,
            progress_tracker
        )
        logger.info(f"✅ [线程池] 分析任务执行完成: {task_id}")
        return result

    # ------------------------------------------------------------------
    # 核心同步分析方法
    # ------------------------------------------------------------------

    def _run_analysis_sync(
        self,
        task_id: str,
        user_id: str,
        request: SingleAnalysisRequest,
        progress_tracker: Optional[RedisProgressTracker] = None
    ) -> Dict[str, Any]:
        """同步执行分析的核心方法（在 ThreadPoolExecutor 中运行）

        完整流程（7个阶段）：
          1. 模型选择：前端指定或自动推荐快速/深度模型
          2. 配置构建：创建 SinaQuantGraph 所需的配置字典
          3. 引擎初始化：创建 SinaQuantGraph 实例（每次新建）
          4. LangGraph 执行：调用 propagate() 驱动多智能体辩论
          5. 报告提取：从 state 中提取各分析师和辩论报告
          6. 结果格式化：翻译决策、生成摘要和建议
          7. 构建返回结果：组装包含 reports、decision、summary 的结构化字典

        线程安全注意事项：
          - 不能直接使用 motor 异步客户端（事件循环冲突）
          - 更新 MongoDB 使用 pymongo 同步客户端
          - 更新内存状态需要创建新的 asyncio 事件循环
          - 进度回调（graph_progress_callback）在 propagate() 内部同步调用
        """
        try:
            # ========== 阶段0：在线程中初始化日志系统 ==========
            # ThreadPoolExecutor 的工作线程不会继承主线程的日志配置，
            # 需要手动初始化，否则日志输出可能丢失
            from sinoquant.utils.logging_init import init_logging, get_logger
            init_logging()
            thread_logger = get_logger('analysis_thread')

            thread_logger.info(f"🔄 [线程池] 开始执行分析: {task_id} - {request.stock_code}")
            logger.info(f"🔄 [线程池] 开始执行分析: {task_id} - {request.stock_code}")

            # 🔧 进度更新辅助函数：同时更新 Redis 进度跟踪器和 MongoDB
            # 在线程池中不能直接 await motor 操作，因此使用同步方式更新
            # 基础准备阶段 (10%): 0.03 + 0.02 + 0.01 + 0.02 + 0.02 = 0.10
            # 步骤索引 0-4 对应 0-10%

            # 异步更新进度（在线程池中调用）
            def update_progress_sync(progress: int, message: str, step: str):
                """在线程池中同步更新进度"""
                try:
                    # 同时更新 Redis 进度跟踪器
                    if progress_tracker:
                        progress_tracker.update_progress({
                            "progress_percentage": progress,
                            "last_message": message
                        })

                    # 🔥 使用同步方式更新内存和 MongoDB，避免事件循环冲突
                    # 1. 更新内存中的任务状态（使用新事件循环）
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            self.memory_manager.update_task_status(
                                task_id=task_id,
                                status=TaskStatus.RUNNING,
                                progress=progress,
                                message=message,
                                current_step=step
                            )
                        )
                    finally:
                        loop.close()

                    # 2. 更新 MongoDB（使用同步客户端，避免事件循环冲突）
                    from pymongo import MongoClient
                    from app.core.config import settings
                    from datetime import datetime

                    sync_client = MongoClient(settings.MONGO_URI)
                    sync_db = sync_client[settings.MONGO_DB]

                    sync_db.analysis_tasks.update_one(
                        {"task_id": task_id},
                        {
                            "$set": {
                                "progress": progress,
                                "current_step": step,
                                "message": message,
                                "updated_at": datetime.utcnow()
                            }
                        }
                    )
                    sync_client.close()

                except Exception as e:
                    logger.warning(f"⚠️ 进度更新失败: {e}")

            # ========== 阶段1：模型选择 ==========
            # 智能模型选择逻辑：前端指定 → 自动推荐
            # 快速模型：用于分析师工具调用、格式化等轻量任务
            # 深度模型：用于研究经理裁决、风险经理评估等复杂推理

            # 配置阶段进度
            update_progress_sync(7, "⚙️ 配置分析参数", "configuration")

            # 智能模型选择：优先使用前端指定的模型，否则自动推荐
            from app.services.model_capability_service import get_model_capability_service
            capability_service = get_model_capability_service()

            research_depth = request.parameters.research_depth if request.parameters else "标准"

            # 1. 检查前端是否指定了模型
            if (request.parameters and
                hasattr(request.parameters, 'quick_analysis_model') and
                hasattr(request.parameters, 'deep_analysis_model') and
                request.parameters.quick_analysis_model and
                request.parameters.deep_analysis_model):

                # 使用前端指定的模型
                quick_model = request.parameters.quick_analysis_model
                deep_model = request.parameters.deep_analysis_model

                logger.info(f"📝 [分析服务] 用户指定模型: quick={quick_model}, deep={deep_model}")

                # 验证模型是否合适
                validation = capability_service.validate_model_pair(
                    quick_model, deep_model, research_depth
                )

                if not validation["valid"]:
                    # 用户明确选择了模型，仅记录警告，不自动切换
                    # 自动切换会导致用户困惑（选了A模型却用B模型报错）
                    for warning in validation["warnings"]:
                        logger.warning(warning)
                    logger.warning(f"⚠️ 用户选择的模型未通过验证，但仍尊重用户选择: quick={quick_model}, deep={deep_model}")
                else:
                    for warning in validation["warnings"]:
                        logger.info(warning)
                    logger.info(f"✅ 用户选择的模型验证通过: quick={quick_model}, deep={deep_model}")

            else:
                # 2. 自动推荐模型
                quick_model, deep_model = capability_service.recommend_models_for_depth(
                    research_depth
                )
                logger.info(f"🤖 自动推荐模型: quick={quick_model}, deep={deep_model}")

            # ========== 阶段2：查找模型对应的供应商和 API 地址 ==========
            # 每个模型关联一个供应商（如 deepseek-chat → DeepSeek），
            # 供应商决定了 API Key 和 base_url
            quick_provider_info = get_provider_and_url_by_model_sync(quick_model, user_id=user_id)
            deep_provider_info = get_provider_and_url_by_model_sync(deep_model, user_id=user_id)

            quick_provider = quick_provider_info["provider"]
            deep_provider = deep_provider_info["provider"]
            quick_backend_url = quick_provider_info["backend_url"]
            deep_backend_url = deep_provider_info["backend_url"]

            logger.info(f"🔍 [供应商查找] 快速模型 {quick_model} 对应的供应商: {quick_provider}")
            logger.info(f"🔍 [API地址] 快速模型使用 backend_url: {quick_backend_url}")
            logger.info(f"🔍 [供应商查找] 深度模型 {deep_model} 对应的供应商: {deep_provider}")
            logger.info(f"🔍 [API地址] 深度模型使用 backend_url: {deep_backend_url}")

            # 检查两个模型是否来自同一个厂家
            if quick_provider == deep_provider:
                logger.info(f"✅ [供应商验证] 两个模型来自同一厂家: {quick_provider}")
            else:
                logger.info(f"✅ [混合模式] 快速模型({quick_provider}) 和 深度模型({deep_provider}) 来自不同厂家")

            # 获取市场类型
            market_type = request.parameters.market_type if request.parameters else "A股"
            logger.info(f"📊 [市场类型] 使用市场类型: {market_type}")

            # ========== 阶段2.5：创建分析配置 ==========
            # create_analysis_config() 将模型选择、研究深度、分析师选择等
            # 组装为 SinaQuantGraph 构造函数需要的配置字典
            config = create_analysis_config(
                research_depth=research_depth,
                selected_analysts=request.parameters.selected_analysts if request.parameters else ["market", "fundamentals"],
                quick_model=quick_model,
                deep_model=deep_model,
                llm_provider=quick_provider,  # 主要使用快速模型的供应商
                market_type=market_type,  # 使用前端传递的市场类型
                user_id=user_id  # 传入用户ID实现用户级配置
            )

            # 🔧 添加混合模式配置
            config["quick_provider"] = quick_provider
            config["deep_provider"] = deep_provider
            config["quick_backend_url"] = quick_backend_url
            config["deep_backend_url"] = deep_backend_url
            config["backend_url"] = quick_backend_url  # 保持向后兼容

            # 🔍 验证配置中的模型
            logger.info(f"🔍 [模型验证] 配置中的快速模型: {config.get('quick_think_llm')}")
            logger.info(f"🔍 [模型验证] 配置中的深度模型: {config.get('deep_think_llm')}")
            logger.info(f"🔍 [模型验证] 配置中的LLM供应商: {config.get('llm_provider')}")

            # ========== 阶段3：初始化 LangGraph 交易引擎 ==========
            # 创建新的 SinaQuantGraph 实例，内部会编译整个多智能体图
            # 这是整个分析的核心引擎：分析师→辩论→交易→风险评估

            update_progress_sync(9, "🚀 初始化AI分析引擎", "engine_initialization")
            trading_graph = self._get_trading_graph(config)

            # 🔍 验证TradingGraph实例中的配置
            logger.info(f"🔍 [引擎验证] TradingGraph配置中的快速模型: {trading_graph.config.get('quick_think_llm')}")
            logger.info(f"🔍 [引擎验证] TradingGraph配置中的深度模型: {trading_graph.config.get('deep_think_llm')}")

            # ========== 阶段4：准备分析参数并调用 propagate() ==========
            start_time = datetime.now()

            # 🔧 使用前端传递的分析日期，如果没有则使用当前日期
            if request.parameters and hasattr(request.parameters, 'analysis_date') and request.parameters.analysis_date:
                # 前端传递的是 datetime 对象或字符串
                if isinstance(request.parameters.analysis_date, datetime):
                    analysis_date = request.parameters.analysis_date.strftime("%Y-%m-%d")
                elif isinstance(request.parameters.analysis_date, str):
                    analysis_date = request.parameters.analysis_date
                else:
                    analysis_date = datetime.now().strftime("%Y-%m-%d")
                logger.info(f"📅 使用前端指定的分析日期: {analysis_date}")
            else:
                analysis_date = datetime.now().strftime("%Y-%m-%d")
                logger.info(f"📅 使用当前日期作为分析日期: {analysis_date}")

            # 🔧 智能日期范围处理：获取最近10天的数据，自动处理周末/节假日
            # 这样可以确保即使是周末或节假日，也能获取到最后一个交易日的数据
            from sinoquant.utils.dataflow_utils import get_trading_date_range
            data_start_date, data_end_date = get_trading_date_range(analysis_date, lookback_days=10)

            logger.info(f"📅 分析目标日期: {analysis_date}")
            logger.info(f"📅 数据查询范围: {data_start_date} 至 {data_end_date} (最近10天)")
            logger.info(f"💡 说明: 获取10天数据可自动处理周末、节假日和数据延迟问题")

            # 开始分析 - 进度10%，即将进入分析师阶段
            # 注意：不要手动设置过高的进度，让 graph_progress_callback 来更新实际的分析进度
            update_progress_sync(10, "🤖 开始多智能体协作分析", "agent_analysis")

            # 启动一个异步任务来模拟进度更新
            def simulate_progress():
                """模拟SinaQuant内部进度"""
                try:
                    if not progress_tracker:
                        return

                    # 分析师阶段 - 根据选择的分析师数量动态调整
                    analysts = request.parameters.selected_analysts if request.parameters else ["market", "fundamentals"]

                    # 模拟分析师执行
                    for i, analyst in enumerate(analysts):
                        time.sleep(15)  # 每个分析师大约15秒
                        if analyst == "market":
                            progress_tracker.update_progress("📊 市场分析师正在分析")
                        elif analyst == "fundamentals":
                            progress_tracker.update_progress("💼 基本面分析师正在分析")
                        elif analyst == "news":
                            progress_tracker.update_progress("📰 新闻分析师正在分析")
                        elif analyst == "social":
                            progress_tracker.update_progress("💬 社交媒体分析师正在分析")

                    # 研究团队阶段
                    time.sleep(10)
                    progress_tracker.update_progress("🐂 看涨研究员构建论据")

                    time.sleep(8)
                    progress_tracker.update_progress("🐻 看跌研究员识别风险")

                    # 辩论阶段 - 根据5个级别确定辩论轮次
                    research_depth_val = request.parameters.research_depth if request.parameters else "标准"
                    if research_depth_val == "快速":
                        debate_rounds = 1
                    elif research_depth_val == "基础":
                        debate_rounds = 1
                    elif research_depth_val == "标准":
                        debate_rounds = 1
                    elif research_depth_val == "深度":
                        debate_rounds = 2
                    elif research_depth_val == "全面":
                        debate_rounds = 3
                    else:
                        debate_rounds = 1  # 默认

                    for round_num in range(debate_rounds):
                        time.sleep(12)
                        progress_tracker.update_progress(f"🎯 研究辩论 第{round_num+1}轮")

                    time.sleep(8)
                    progress_tracker.update_progress("👔 研究经理形成共识")

                    # 交易员阶段
                    time.sleep(10)
                    progress_tracker.update_progress("💼 交易员制定策略")

                    # 风险管理阶段
                    time.sleep(8)
                    progress_tracker.update_progress("🔥 激进风险评估")

                    time.sleep(6)
                    progress_tracker.update_progress("🛡️ 保守风险评估")

                    time.sleep(6)
                    progress_tracker.update_progress("⚖️ 中性风险评估")

                    time.sleep(8)
                    progress_tracker.update_progress("🎯 风险经理制定策略")

                    # 最终阶段
                    time.sleep(5)
                    progress_tracker.update_progress("📡 信号处理")

                except Exception as e:
                    logger.warning(f"⚠️ 进度模拟失败: {e}")

            # 启动进度模拟线程
            progress_thread = threading.Thread(target=simulate_progress, daemon=True)
            progress_thread.start()

            # 进度回调函数：接收 LangGraph 各节点的执行进度
            # trading_graph.propagate() 在内部执行每个节点时调用此函数，
            # 函数将节点名映射到百分比进度，同时更新 Redis 和 MongoDB
            # 节点进度映射表（与 RedisProgressTracker 的步骤权重对应）
            node_progress_map = {
                # 分析师阶段 (10% → 45%)
                "📊 市场分析师": 27.5,      # 10% + 17.5% (假设2个分析师)
                "💼 基本面分析师": 45,       # 10% + 35%
                "📰 新闻分析师": 27.5,       # 如果有3个分析师
                "💬 社交媒体分析师": 27.5,   # 如果有4个分析师
                # 研究辩论阶段 (45% → 70%)
                "🐂 看涨研究员": 51.25,      # 45% + 6.25%
                "🐻 看跌研究员": 57.5,       # 45% + 12.5%
                "👔 研究经理": 70,           # 45% + 25%
                # 交易员阶段 (70% → 78%)
                "💼 交易员决策": 78,         # 70% + 8%
                # 风险评估阶段 (78% → 93%)
                "🔥 激进风险评估": 81.75,    # 78% + 3.75%
                "🛡️ 保守风险评估": 85.5,    # 78% + 7.5%
                "⚖️ 中性风险评估": 89.25,   # 78% + 11.25%
                "🎯 风险经理": 93,           # 78% + 15%
                # 最终阶段 (93% → 100%)
                "📊 生成报告": 97,           # 93% + 4%
            }

            def graph_progress_callback(message: str):
                """接收 LangGraph 的进度更新

                根据节点名称直接映射到进度百分比，确保与 RedisProgressTracker 的步骤权重一致
                注意：只在进度增加时更新，避免覆盖 RedisProgressTracker 的虚拟步骤进度
                """
                try:
                    logger.info(f"🎯🎯🎯 [Graph进度回调被调用] message={message}")
                    if not progress_tracker:
                        logger.warning(f"⚠️ progress_tracker 为 None，无法更新进度")
                        return

                    # 查找节点对应的进度百分比
                    progress_pct = node_progress_map.get(message)

                    if progress_pct is not None:
                        # 获取当前进度（使用 progress_data 属性）
                        current_progress = progress_tracker.progress_data.get('progress_percentage', 0)

                        # 只在进度增加时更新，避免覆盖虚拟步骤的进度
                        if int(progress_pct) > current_progress:
                            # 更新 Redis 进度跟踪器
                            progress_tracker.update_progress({
                                'progress_percentage': int(progress_pct),
                                'last_message': message
                            })
                            logger.info(f"📊 [Graph进度] 进度已更新: {current_progress}% → {int(progress_pct)}% - {message}")

                            # 🔥 同时更新内存和 MongoDB
                            try:
                                import asyncio
                                from datetime import datetime

                                # 尝试获取当前运行的事件循环
                                try:
                                    loop = asyncio.get_running_loop()
                                    # 如果在事件循环中，使用 create_task
                                    asyncio.create_task(
                                        self._update_progress_async(task_id, int(progress_pct), message)
                                    )
                                    logger.debug(f"✅ [Graph进度] 已提交异步更新任务: {int(progress_pct)}%")
                                except RuntimeError:
                                    # 没有运行的事件循环，使用同步方式更新 MongoDB
                                    from pymongo import MongoClient
                                    from app.core.config import settings

                                    # 创建同步 MongoDB 客户端
                                    sync_client = MongoClient(settings.MONGO_URI)
                                    sync_db = sync_client[settings.MONGO_DB]

                                    # 同步更新 MongoDB
                                    sync_db.analysis_tasks.update_one(
                                        {"task_id": task_id},
                                        {
                                            "$set": {
                                                "progress": int(progress_pct),
                                                "current_step": message,
                                                "message": message,
                                                "updated_at": datetime.utcnow()
                                            }
                                        }
                                    )
                                    sync_client.close()

                                    # 异步更新内存（创建新的事件循环）
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                    try:
                                        loop.run_until_complete(
                                            self.memory_manager.update_task_status(
                                                task_id=task_id,
                                                status=TaskStatus.RUNNING,
                                                progress=int(progress_pct),
                                                message=message,
                                                current_step=message
                                            )
                                        )
                                    finally:
                                        loop.close()

                                    logger.debug(f"✅ [Graph进度] 已同步更新内存和MongoDB: {int(progress_pct)}%")
                            except Exception as sync_err:
                                logger.warning(f"⚠️ [Graph进度] 同步更新失败: {sync_err}")
                        else:
                            # 进度没有增加，只更新消息
                            progress_tracker.update_progress({
                                'last_message': message
                            })
                            logger.info(f"📊 [Graph进度] 进度未变化({current_progress}% >= {int(progress_pct)}%)，仅更新消息: {message}")
                    else:
                        # 未知节点，只更新消息
                        logger.warning(f"⚠️ [Graph进度] 未知节点: {message}，仅更新消息")
                        progress_tracker.update_progress({
                            'last_message': message
                        })

                except Exception as e:
                    logger.error(f"❌ Graph进度回调失败: {e}", exc_info=True)

            logger.info(f"🚀 准备调用 trading_graph.propagate，progress_callback={graph_progress_callback}")

            # ★ 核心调用：执行多智能体辩论分析 ★
            # propagate() 是 SinaQuantGraph 的主入口方法：
            #   1. 创建初始状态（股票代码、日期）
            #   2. 流式执行 LangGraph 图（分析师→辩论→交易→风险评估）
            #   3. 通过 progress_callback 实时反馈进度
            #   4. 返回 (state, decision) 二元组
            # state 包含所有分析师报告和辩论记录
            # decision 包含结构化的交易决策（action/confidence/risk_score）
            state, decision = trading_graph.propagate(
                request.get_symbol(),
                analysis_date,
                progress_callback=graph_progress_callback,
                task_id=task_id
            )

            logger.info(f"✅ trading_graph.propagate 执行完成")

            # 🔍 调试：检查decision的结构
            logger.info(f"🔍 [DEBUG] Decision类型: {type(decision)}")
            logger.info(f"🔍 [DEBUG] Decision内容: {decision}")
            if isinstance(decision, dict):
                logger.info(f"🔍 [DEBUG] Decision键: {list(decision.keys())}")
            elif hasattr(decision, '__dict__'):
                logger.info(f"🔍 [DEBUG] Decision属性: {list(vars(decision).keys())}")

            # ========== 阶段5：从 state 中提取各分析师报告 ==========
            # state 是 propagate() 返回的完整状态字典，包含：
            #   - market_report / sentiment_report / news_report / fundamentals_report
            #   - investment_debate_state（看涨/看跌辩论记录）
            #   - risk_debate_state（激进/保守/中性风险辩论记录）
            #   - final_trade_decision（最终交易决策）
            if progress_tracker:
                progress_tracker.update_progress("📊 处理分析结果")
            update_progress_sync(90, "处理分析结果...", "result_processing")

            execution_time = (datetime.now() - start_time).total_seconds()

            # 从state中提取reports字段
            reports = {}
            try:
                # 定义所有可能的报告字段
                report_fields = [
                    'market_report',
                    'sentiment_report',
                    'news_report',
                    'fundamentals_report',
                    'investment_plan',
                    'trader_investment_plan',
                    'final_trade_decision'
                ]

                # 从state中提取报告内容
                for field in report_fields:
                    if hasattr(state, field):
                        value = getattr(state, field, "")
                    elif isinstance(state, dict) and field in state:
                        value = state[field]
                    else:
                        value = ""

                    normalized_value = normalize_report_content(value, field)
                    if normalized_value:
                        reports[field] = normalized_value
                        logger.info(f"📊 [REPORTS] 提取报告: {field} - 长度: {len(normalized_value)}")
                    else:
                        logger.debug(f"⚠️ [REPORTS] 跳过报告: {field} - 内容为空或无效")

                # 处理研究团队辩论状态报告
                if hasattr(state, 'investment_debate_state') or (isinstance(state, dict) and 'investment_debate_state' in state):
                    debate_state = getattr(state, 'investment_debate_state', None) if hasattr(state, 'investment_debate_state') else state.get('investment_debate_state')
                    if debate_state:
                        # 提取多头研究员历史
                        if hasattr(debate_state, 'bull_history'):
                            bull_content = getattr(debate_state, 'bull_history', "")
                        elif isinstance(debate_state, dict) and 'bull_history' in debate_state:
                            bull_content = debate_state['bull_history']
                        else:
                            bull_content = ""

                        cleaned_content = normalize_report_content(bull_content, 'bull_researcher')
                        if cleaned_content:
                            reports['bull_researcher'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: bull_researcher - 长度: {len(cleaned_content)}")

                        # 提取空头研究员历史
                        if hasattr(debate_state, 'bear_history'):
                            bear_content = getattr(debate_state, 'bear_history', "")
                        elif isinstance(debate_state, dict) and 'bear_history' in debate_state:
                            bear_content = debate_state['bear_history']
                        else:
                            bear_content = ""

                        cleaned_content = normalize_report_content(bear_content, 'bear_researcher')
                        if cleaned_content:
                            reports['bear_researcher'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: bear_researcher - 长度: {len(cleaned_content)}")

                        # 提取研究经理决策
                        if hasattr(debate_state, 'judge_decision'):
                            decision_content = getattr(debate_state, 'judge_decision', "")
                        elif isinstance(debate_state, dict) and 'judge_decision' in debate_state:
                            decision_content = debate_state['judge_decision']
                        else:
                            decision_content = str(debate_state)

                        cleaned_content = normalize_report_content(decision_content, 'research_team_decision')
                        if cleaned_content:
                            reports['research_team_decision'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: research_team_decision - 长度: {len(cleaned_content)}")

                # 处理风险管理团队辩论状态报告
                if hasattr(state, 'risk_debate_state') or (isinstance(state, dict) and 'risk_debate_state' in state):
                    risk_state = getattr(state, 'risk_debate_state', None) if hasattr(state, 'risk_debate_state') else state.get('risk_debate_state')
                    if risk_state:
                        # 提取激进分析师历史
                        if hasattr(risk_state, 'risky_history'):
                            risky_content = getattr(risk_state, 'risky_history', "")
                        elif isinstance(risk_state, dict) and 'risky_history' in risk_state:
                            risky_content = risk_state['risky_history']
                        else:
                            risky_content = ""

                        cleaned_content = normalize_report_content(risky_content, 'risky_analyst')
                        if cleaned_content:
                            reports['risky_analyst'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: risky_analyst - 长度: {len(cleaned_content)}")

                        # 提取保守分析师历史
                        if hasattr(risk_state, 'safe_history'):
                            safe_content = getattr(risk_state, 'safe_history', "")
                        elif isinstance(risk_state, dict) and 'safe_history' in risk_state:
                            safe_content = risk_state['safe_history']
                        else:
                            safe_content = ""

                        cleaned_content = normalize_report_content(safe_content, 'safe_analyst')
                        if cleaned_content:
                            reports['safe_analyst'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: safe_analyst - 长度: {len(cleaned_content)}")

                        # 提取中性分析师历史
                        if hasattr(risk_state, 'neutral_history'):
                            neutral_content = getattr(risk_state, 'neutral_history', "")
                        elif isinstance(risk_state, dict) and 'neutral_history' in risk_state:
                            neutral_content = risk_state['neutral_history']
                        else:
                            neutral_content = ""

                        cleaned_content = normalize_report_content(neutral_content, 'neutral_analyst')
                        if cleaned_content:
                            reports['neutral_analyst'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: neutral_analyst - 长度: {len(cleaned_content)}")

                        # 提取投资组合经理决策
                        if hasattr(risk_state, 'judge_decision'):
                            risk_decision = getattr(risk_state, 'judge_decision', "")
                        elif isinstance(risk_state, dict) and 'judge_decision' in risk_state:
                            risk_decision = risk_state['judge_decision']
                        else:
                            risk_decision = str(risk_state)

                        cleaned_content = normalize_report_content(risk_decision, 'risk_management_decision')
                        if cleaned_content:
                            reports['risk_management_decision'] = cleaned_content
                            logger.info(f"📊 [REPORTS] 提取报告: risk_management_decision - 长度: {len(cleaned_content)}")

                logger.info(f"📊 [REPORTS] 从state中提取到 {len(reports)} 个报告: {list(reports.keys())}")

                # 分级精简输出：按研究深度保留对应报告模块
                original_count = len(reports)
                selected_analysts_for_policy = request.parameters.selected_analysts if request.parameters else []
                research_depth_for_policy = request.parameters.research_depth if request.parameters else "标准"
                reports = _apply_report_visibility_policy(
                    reports=reports,
                    research_depth=research_depth_for_policy,
                    selected_analysts=selected_analysts_for_policy,
                )
                if len(reports) != original_count:
                    logger.info(
                        f"📉 [REPORTS] 分级精简策略生效: {original_count} -> {len(reports)} "
                        f"(depth={research_depth_for_policy}, analysts={selected_analysts_for_policy})"
                    )
                reports = sanitize_report_modules(reports)

            except Exception as e:
                logger.warning(f"⚠️ 提取reports时出错: {e}")
                # 降级到从detailed_analysis提取
                try:
                    if isinstance(decision, dict):
                        for key, value in decision.items():
                            if isinstance(value, str) and len(value) > 50:
                                reports[key] = normalize_report_content(value, str(key))
                        logger.info(f"📊 降级：从decision中提取到 {len(reports)} 个报告")
                    reports = sanitize_report_modules(reports)
                except Exception as fallback_error:
                    logger.warning(f"⚠️ 降级提取也失败: {fallback_error}")

            # ========== 阶段6：格式化交易决策 ==========
            # 将 LLM 输出的英文决策翻译为中文，提取目标价格
            formatted_decision = {}
            try:
                if isinstance(decision, dict):
                    # 处理目标价格
                    target_price = decision.get('target_price')
                    if target_price is not None and target_price != 'N/A':
                        try:
                            if isinstance(target_price, str):
                                # 移除货币符号和空格
                                clean_price = target_price.replace('$', '').replace('¥', '').replace('￥', '').strip()
                                target_price = float(clean_price) if clean_price and clean_price != 'None' else None
                            elif isinstance(target_price, (int, float)):
                                target_price = float(target_price)
                            else:
                                target_price = None
                        except (ValueError, TypeError):
                            target_price = None
                    else:
                        target_price = None

                    # 将英文投资建议转换为中文
                    action_translation = {
                        'BUY': '买入',
                        'SELL': '卖出',
                        'HOLD': '持有',
                        'buy': '买入',
                        'sell': '卖出',
                        'hold': '持有'
                    }
                    action = decision.get('action', '持有')
                    chinese_action = action_translation.get(action, action)

                    formatted_decision = {
                        'action': chinese_action,
                        'confidence': decision.get('confidence', 0.5),
                        'risk_score': decision.get('risk_score', 0.3),
                        'target_price': target_price,
                        'reasoning': remove_thinking_content(decision.get('reasoning', '暂无分析推理'))
                    }

                    logger.info(f"🎯 [DEBUG] 格式化后的decision: {formatted_decision}")
                else:
                    # 处理其他类型
                    formatted_decision = {
                        'action': '持有',
                        'confidence': 0.5,
                        'risk_score': 0.3,
                        'target_price': None,
                        'reasoning': '暂无分析推理'
                    }
                    logger.warning(f"⚠️ Decision不是字典类型: {type(decision)}")
            except Exception as e:
                logger.error(f"❌ 格式化decision失败: {e}")
                formatted_decision = {
                    'action': '持有',
                    'confidence': 0.5,
                    'risk_score': 0.3,
                    'target_price': None,
                    'reasoning': '暂无分析推理'
                }

            # ========== 阶段6.5：生成摘要和投资建议 ==========
            # summary: 从各报告中提取的最有价值的摘要（优先使用交易决策和研究员裁决）
            # recommendation: 基于 decision.action 和 target_price 的投资建议
            summary = ""
            recommendation = ""

            summary_candidates: List[Any] = []
            if isinstance(reports, dict):
                for key in [
                    "final_trade_decision",
                    "research_team_decision",
                    "trader_investment_plan",
                    "risk_management_decision",
                    "market_report",
                    "fundamentals_report",
                    "news_report",
                    "sentiment_report",
                ]:
                    value = reports.get(key)
                    if value:
                        summary_candidates.append(value)

            if isinstance(state, dict):
                state_final_decision = state.get("final_trade_decision")
                if state_final_decision:
                    summary_candidates.append(state_final_decision)

            summary = _pick_best_summary(summary_candidates, max_chars=220)
            if summary:
                logger.info(f"📝 [SUMMARY] 从候选报告提取摘要: {len(summary)}字符")

            # 3. 生成recommendation（从decision的reasoning）
            if isinstance(formatted_decision, dict):
                action = formatted_decision.get('action', '持有')
                target_price = formatted_decision.get('target_price')
                reasoning = formatted_decision.get('reasoning', '')

                # 生成投资建议
                recommendation = f"投资建议：{action}。"
                if target_price:
                    recommendation += f"目标价格：{target_price}元。"
                if reasoning:
                    recommendation += f"决策依据：{reasoning}"
                logger.info(f"💡 [RECOMMENDATION] 生成投资建议: {len(recommendation)}字符")

            # 4. 如果报告摘要为空，尝试从决策推理提炼
            if not summary and isinstance(formatted_decision, dict):
                reasoning_summary = _build_summary_from_text(
                    formatted_decision.get("reasoning", ""),
                    max_chars=200,
                )
                if reasoning_summary:
                    summary = reasoning_summary
                    logger.info(f"📝 [SUMMARY] 从决策推理提取摘要: {len(summary)}字符")

            # 5. 最后的备用方案
            if not summary:
                summary = f"对{request.stock_code}的分析已完成，请查看详细报告。"
                logger.warning(f"⚠️ [SUMMARY] 使用备用摘要")

            if not recommendation:
                recommendation = f"请参考详细分析报告做出投资决策。"
                logger.warning(f"⚠️ [RECOMMENDATION] 使用备用建议")

            # 过滤思维链内容（最终清理）
            summary = remove_thinking_content(summary)
            recommendation = remove_thinking_content(recommendation)
            if not summary:
                summary = f"对{request.stock_code}的分析已完成，请查看详细报告。"
                logger.warning("⚠️ [SUMMARY] 清洗后为空，回退到默认摘要")

            # 从决策中提取模型信息
            model_info = decision.get('model_info', 'Unknown') if isinstance(decision, dict) else 'Unknown'

            # ========== 阶段7：构建返回结果 ==========
            # 组装包含所有分析信息的结构化字典，交由 _result_persistence.py 持久化
            result = {
                "analysis_id": str(uuid.uuid4()),
                "stock_code": request.get_symbol(),
                "stock_symbol": request.get_symbol(),
                "analysis_date": analysis_date,
                "summary": summary,
                "recommendation": recommendation,
                "confidence_score": formatted_decision.get("confidence", 0.0) if isinstance(formatted_decision, dict) else 0.0,
                "risk_level": "中等",  # 可以根据risk_score计算
                "key_points": [],  # 可以从reasoning中提取关键点
                "detailed_analysis": decision,
                "execution_time": execution_time,
                "tokens_used": decision.get("tokens_used", 0) if isinstance(decision, dict) else 0,
                "state": state,
                # 添加分析师信息
                "analysts": request.parameters.selected_analysts if request.parameters else [],
                "research_depth": request.parameters.research_depth if request.parameters else "快速",
                # 添加提取的报告内容
                "reports": reports,
                # 🔥 关键修复：添加格式化后的decision字段！
                "decision": formatted_decision,
                # 🔥 添加模型信息字段
                "model_info": model_info,
                # 🆕 性能指标数据
                "performance_metrics": state.get("performance_metrics", {}) if isinstance(state, dict) else {}
            }

            logger.info(f"✅ [线程池] 分析完成: {task_id} - 耗时{execution_time:.2f}秒")

            # 🔍 调试：检查返回的result结构
            logger.info(f"🔍 [DEBUG] 返回result的键: {list(result.keys())}")
            logger.info(f"🔍 [DEBUG] 返回result中有decision: {bool(result.get('decision'))}")
            if result.get('decision'):
                decision_val = result['decision']
                logger.info(f"🔍 [DEBUG] 返回decision内容: {decision_val}")

            return result

        except Exception as e:
            import traceback
            logger.error(f"❌ [线程池] 分析执行失败: {task_id} - {type(e).__name__}: {e}")
            logger.error(f"❌ [线程池] Traceback: {traceback.format_exc()}")

            # 格式化错误信息为用户友好的提示
            try:
                from ..utils.error_formatter import ErrorFormatter

                # 收集上下文信息
                error_context = {}
                if request and hasattr(request, 'parameters') and request.parameters:
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

            # 抛出包含友好错误信息的异常
            raise Exception(user_friendly_error) from e
