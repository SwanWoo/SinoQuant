"""
Alpha 量化交易 REST API 路由

策略管理、回测、数据同步、模拟交易
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.routers.auth_db import get_current_user
from app.core.response import ok, fail
from app.models.alpha import (
    GenerateStrategyRequest,
    UpdateStrategyRequest,
    RunBacktestRequest,
    QuickBacktestRequest,
    StartSimulationRequest,
    SyncDataRequest,
)

logger = logging.getLogger("webapi")

router = APIRouter(prefix="/alpha", tags=["alpha"])


# ---------------------------------------------------------------------------
# 策略管理
# ---------------------------------------------------------------------------

@router.post("/strategies/generate")
async def generate_strategy(
    request: GenerateStrategyRequest,
    user=Depends(get_current_user),
):
    """LLM 根据分析报告生成量化交易策略代码"""
    from app.services.alpha import StrategyGeneratorService
    from app.core.database import get_mongo_db

    market_reports = {
        "market_report": request.market_report,
        "sentiment_report": request.sentiment_report,
        "news_report": request.news_report,
        "fundamentals_report": request.fundamentals_report,
        "trade_decision": request.trade_decision,
    }

    # 如果传了 task_id，从数据库自动加载完整分析报告
    if request.analysis_task_id and not any([
        request.market_report, request.sentiment_report,
        request.news_report, request.fundamentals_report
    ]):
        db = get_mongo_db()
        task_doc = await db.analysis_tasks.find_one(
            {"task_id": request.analysis_task_id}
        )
        if task_doc and task_doc.get("result"):
            r = task_doc["result"]
            reports = r.get("reports", {})
            market_reports["market_report"] = reports.get("market_report", "")
            market_reports["sentiment_report"] = reports.get("sentiment_report", "")
            market_reports["news_report"] = reports.get("news_report", "")
            market_reports["fundamentals_report"] = reports.get("fundamentals_report", "")
            market_reports["trade_decision"] = r.get("trade_decision", {})
            logger.info(f"从分析任务 {request.analysis_task_id} 自动加载报告数据")

    service = StrategyGeneratorService()

    doc = await service.generate_strategy(
        symbol=request.symbol,
        market_reports=market_reports,
        user_id=user["id"],
        analysis_task_id=request.analysis_task_id,
        model_name=request.model_name,
    )

    if doc.get("status") == "error":
        return fail(message="策略生成失败", data=doc, code=400)

    return ok(data=doc, message="策略生成成功")


@router.get("/strategies")
async def list_strategies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    """列出用户的所有策略"""
    from app.services.alpha import StrategyGeneratorService

    service = StrategyGeneratorService()
    items = await service.list_strategies(
        user_id=user["id"],
        limit=limit,
        skip=offset,
    )

    for item in items:
        item.pop("code", None)

    return ok(data={"items": items, "total": len(items)})


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: str,
    user=Depends(get_current_user),
):
    """获取策略详情（包含代码）"""
    from app.services.alpha import StrategyGeneratorService

    service = StrategyGeneratorService()
    doc = await service.get_strategy(strategy_id)
    if not doc:
        raise HTTPException(status_code=404, detail="策略不存在")
    return ok(data=doc)


@router.put("/strategies/{strategy_id}")
async def update_strategy(
    strategy_id: str,
    request: UpdateStrategyRequest,
    user=Depends(get_current_user),
):
    """更新策略代码"""
    from app.services.alpha import StrategyGeneratorService

    service = StrategyGeneratorService()
    doc = await service.update_strategy_code(
        strategy_id=strategy_id,
        code=request.code,
        user_id=user["id"],
    )
    if not doc:
        raise HTTPException(status_code=404, detail="策略不存在")
    return ok(data=doc, message="策略更新成功")


@router.delete("/strategies/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    user=Depends(get_current_user),
):
    """删除策略及其关联回测"""
    from app.services.alpha import StrategyGeneratorService

    service = StrategyGeneratorService()
    deleted = await service.delete_strategy(
        strategy_id=strategy_id,
        user_id=user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="策略不存在")
    return ok(message="策略已删除")


@router.post("/strategies/{strategy_id}/validate")
async def validate_strategy(
    strategy_id: str,
    user=Depends(get_current_user),
):
    """验证策略代码"""
    from app.services.alpha import StrategyGeneratorService

    service = StrategyGeneratorService()
    valid, errors = await service.validate_strategy(
        strategy_id=strategy_id,
        user_id=user["id"],
    )
    return ok(data={"valid": valid, "errors": errors})


# ---------------------------------------------------------------------------
# 回测
# ---------------------------------------------------------------------------

@router.post("/backtests")
async def run_backtest(
    request: RunBacktestRequest,
    user=Depends(get_current_user),
):
    """提交回测任务（后台执行）"""
    from app.services.alpha import StrategyGeneratorService, BacktestService
    from app.core.database import get_mongo_db

    gen_service = StrategyGeneratorService()
    strategy_doc = await gen_service.get_strategy(request.strategy_id)

    if not strategy_doc:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy_doc.get("status") != "validated":
        raise HTTPException(status_code=400, detail="策略代码未通过验证，请先修正")

    # 优先返回已有结果
    db = get_mongo_db()
    existing = await db["alpha_backtests"].find_one(
        {
            "strategy_id": request.strategy_id,
            "status": "completed",
            "statistics.total_return": {"$ne": 0},
        },
        sort=[("started_at", -1)],
    )
    if existing:
        existing.pop("_id", None)
        from app.services.alpha.backtest_service import _serialize_doc
        return ok(data=_serialize_doc(existing), message="回测完成（使用已有结果）")

    bt_service = BacktestService()
    doc = await bt_service.run_backtest(
        strategy_id=request.strategy_id,
        strategy_code=strategy_doc["code"],
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        user_id=user["id"],
        capital=request.capital,
        strategy_params=request.strategy_params,
    )

    return ok(data=doc, message="回测任务已提交")


@router.post("/backtests/quick")
async def run_quick_backtest(
    request: QuickBacktestRequest,
    user=Depends(get_current_user),
):
    """快速回测 — 自动取最近N个交易日的日期范围运行回测"""
    from app.services.alpha import StrategyGeneratorService, BacktestService
    from app.core.database import get_mongo_db

    gen_service = StrategyGeneratorService()
    strategy_doc = await gen_service.get_strategy(request.strategy_id)

    if not strategy_doc:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy_doc.get("status") != "validated":
        raise HTTPException(status_code=400, detail="策略代码未通过验证，请先修正")

    db = get_mongo_db()
    symbol = request.symbols[0]

    # 优先返回已有的该策略+股票回测结果（避免重复执行引擎）
    existing = await db["alpha_backtests"].find_one(
        {
            "strategy_id": request.strategy_id,
            "status": "completed",
            "statistics.total_return": {"$ne": 0},
        },
        sort=[("started_at", -1)],
    )
    if existing:
        existing.pop("_id", None)
        from app.services.alpha.backtest_service import _serialize_doc
        return ok(data=_serialize_doc(existing), message="快速回测完成（使用已有结果）")

    # 没有已有结果，走正常回测流程
    cursor = db["stock_daily_quotes"].find(
        {"symbol": symbol},
        {"trade_date": 1, "_id": 0},
    ).sort("trade_date", -1).limit(request.trading_days)
    trading_date_docs = await cursor.to_list(length=request.trading_days)

    if len(trading_date_docs) < 2:
        raise HTTPException(status_code=400, detail=f"股票 {symbol} 历史数据不足，无法回测")

    dates = sorted([d["trade_date"] for d in trading_date_docs])
    def normalize_date(s):
        if isinstance(s, str):
            return s if "-" in s else f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return s.strftime("%Y-%m-%d")
    start_date = normalize_date(dates[0])
    end_date = normalize_date(dates[-1])

    bt_service = BacktestService()
    doc = await bt_service.run_backtest(
        strategy_id=request.strategy_id,
        strategy_code=strategy_doc["code"],
        symbols=request.symbols,
        start_date=start_date,
        end_date=end_date,
        user_id=user["id"],
        capital=request.capital,
        strategy_params=request.strategy_params,
    )

    return ok(data=doc, message=f"快速回测已提交（{start_date} ~ {end_date}）")


@router.get("/backtests/{backtest_id}")
async def get_backtest_result(
    backtest_id: str,
    user=Depends(get_current_user),
):
    """获取回测结果"""
    from app.services.alpha import BacktestService

    bt_service = BacktestService()
    doc = await bt_service.get_backtest_result(backtest_id)
    if not doc:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    return ok(data=doc)


@router.get("/backtests")
async def list_backtests(
    strategy_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    """列出回测记录"""
    from app.services.alpha import BacktestService

    bt_service = BacktestService()
    items = await bt_service.list_backtests(
        user_id=user["id"],
        strategy_id=strategy_id,
        limit=limit,
        skip=offset,
    )
    return ok(data={"items": items, "total": len(items)})


# ---------------------------------------------------------------------------
# 数据同步
# ---------------------------------------------------------------------------

@router.post("/data/sync")
async def sync_alpha_data(
    request: SyncDataRequest,
    user=Depends(get_current_user),
):
    """同步行情数据到 AlphaLab"""
    from app.services.alpha import get_alpha_data_bridge, AlphaDataBridge
    from app.core.database import get_mongo_db

    db = get_mongo_db()
    from vnpy.alpha import AlphaLab

    lab = AlphaLab("data/alpha_lab")
    bridge = AlphaDataBridge(lab)

    start = datetime.strptime(request.start_date, "%Y-%m-%d") if request.start_date else None
    end = datetime.strptime(request.end_date, "%Y-%m-%d") if request.end_date else None

    results = await bridge.sync_stocks_to_lab(request.symbols, db, start=start, end=end)

    total = sum(results.values())
    success = sum(1 for v in results.values() if v > 0)

    return ok(data={"results": results, "total_bars": total, "success_count": success},
              message=f"同步完成: {success}/{len(request.symbols)} 只股票")


@router.get("/data/status")
async def get_data_sync_status(
    symbols: str = Query(..., description="逗号分隔的6位代码列表"),
    user=Depends(get_current_user),
):
    """检查数据同步状态"""
    from app.core.database import get_mongo_db

    db = get_mongo_db()
    code_list = [s.strip().zfill(6) for s in symbols.split(",") if s.strip()]

    status = {}
    for code in code_list:
        count = await db["stock_daily_quotes"].count_documents({"symbol": code})
        status[code] = {
            "has_data": count > 0,
            "total_bars": count,
        }

    return ok(data=status)


# ---------------------------------------------------------------------------
# 模拟交易
# ---------------------------------------------------------------------------

@router.post("/simulations")
async def start_simulation(
    request: StartSimulationRequest,
    user=Depends(get_current_user),
):
    """启动策略模拟交易"""
    from app.services.alpha import StrategyGeneratorService
    from app.services.alpha.simulation_engine import get_simulation_engine

    gen_service = StrategyGeneratorService()
    strategy_doc = await gen_service.get_strategy(request.strategy_id)
    if not strategy_doc:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy_doc.get("status") != "validated":
        raise HTTPException(status_code=400, detail="策略代码未通过验证")

    engine = get_simulation_engine()
    ctx = await engine.start_simulation(
        strategy_id=request.strategy_id,
        strategy_code=strategy_doc["code"],
        user_id=user["id"],
        symbols=request.symbols,
        capital=request.capital,
        strategy_params=request.strategy_params,
    )
    return ok(data=ctx.to_dict(), message="模拟交易已启动")


@router.post("/simulations/{simulation_id}/stop")
async def stop_simulation(
    simulation_id: str,
    user=Depends(get_current_user),
):
    """停止模拟交易"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    stopped = await engine.stop_simulation(
        simulation_id=simulation_id,
        user_id=user["id"],
    )
    if not stopped:
        raise HTTPException(status_code=404, detail="模拟交易不存在")
    return ok(message="模拟交易已停止")


@router.post("/simulations/{simulation_id}/pause")
async def pause_simulation(
    simulation_id: str,
    user=Depends(get_current_user),
):
    """暂停模拟交易"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    paused = await engine.pause_simulation(
        simulation_id=simulation_id,
        user_id=user["id"],
    )
    if not paused:
        raise HTTPException(status_code=404, detail="模拟交易不存在")
    return ok(message="模拟交易已暂停")


@router.get("/simulations")
async def list_simulations(
    user=Depends(get_current_user),
):
    """列出活跃的模拟交易"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    items = await engine.list_simulations(
        user_id=user["id"]
    )
    return ok(data={"items": items})


@router.get("/simulations/{simulation_id}")
async def get_simulation_status(
    simulation_id: str,
    user=Depends(get_current_user),
):
    """获取模拟交易状态"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    data = await engine.get_simulation(
        simulation_id=simulation_id,
        user_id=user["id"],
    )
    if not data:
        raise HTTPException(status_code=404, detail="模拟交易不存在")
    return ok(data=data)


@router.get("/simulations/{simulation_id}/positions")
async def get_simulation_positions(
    simulation_id: str,
    user=Depends(get_current_user),
):
    """获取模拟交易持仓"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    items = await engine.get_positions(
        simulation_id=simulation_id,
        user_id=user["id"],
    )
    return ok(data={"items": items})


@router.get("/simulations/{simulation_id}/orders")
async def get_simulation_orders(
    simulation_id: str,
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """获取模拟交易订单"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    items = await engine.get_orders(
        simulation_id=simulation_id,
        user_id=user["id"],
        limit=limit,
    )
    return ok(data={"items": items})


@router.get("/simulations/{simulation_id}/pnl")
async def get_simulation_pnl(
    simulation_id: str,
    user=Depends(get_current_user),
):
    """获取模拟交易盈亏历史"""
    from app.services.alpha.simulation_engine import get_simulation_engine

    engine = get_simulation_engine()
    items = await engine.get_pnl_history(
        simulation_id=simulation_id,
        user_id=user["id"],
    )
    return ok(data={"items": items})
