"""
股票详情相关API
- 统一响应包: {success, data, message, timestamp}
- 所有端点均需鉴权 (Bearer Token)
- 路径前缀在 main.py 中挂载为 /api，当前路由自身前缀为 /stocks
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging

from app.routers.auth_db import get_current_user
from app.core.database import get_mongo_db
from app.core.response import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _zfill_code(code: str) -> str:
    """标准化代码：指数代码(sh000001)保留原样，个股代码补零到6位"""
    try:
        s = str(code).strip().lower()
        # 已有 sh/sz 前缀的指数代码（sh000001/sz399001），保留原样
        if len(s) in (8, 9) and s[:2] in ("sh", "sz") and s[2:].isdigit():
            return s
        if len(s) == 6 and s.isdigit():
            return s
        return s.zfill(6)
    except Exception:
        return str(code)


@router.get("/{code}/quote", response_model=dict)
async def get_quote(code: str, current_user: dict = Depends(get_current_user)):
    """获取股票近实时快照（从入库的 market_quotes 集合 + 基础信息集合拼装）
    返回字段（data内，蛇形命名，保持与现有风格一致）:
      - code, name, market
      - price(close), change_percent(pct_chg), amount, prev_close(估算)
      - turnover_rate, amplitude（振幅，替代量比）
      - trade_date, updated_at
    若未命中行情，部分字段为 None
    """
    db = get_mongo_db()
    code6 = _zfill_code(code)

    # 行情 - 先查 MongoDB
    q = await db["market_quotes"].find_one({"code": code6}, {"_id": 0})

    # 按需获取：MongoDB 无数据时从外部 API 获取
    if not q:
        import asyncio
        from app.utils.code_utils import classify_code

        is_index, ak_code, index_name = classify_code(code6)

        try:
            if is_index:
                from app.services.data_sources.akshare_adapter import AKShareAdapter
                adapter = AKShareAdapter()
                idx_quotes = await asyncio.wait_for(
                    asyncio.to_thread(adapter.get_index_spot_quotes),
                    timeout=15.0
                )
                # 兼容查找：先按完整代码（sh000001），再按6位代码（000001）
                if idx_quotes:
                    code6_digit = code6[2:] if len(code6) >= 2 and code6[:2] in ("sh", "sz") else code6
                    q = idx_quotes.get(ak_code) or idx_quotes.get(code6) or idx_quotes.get(code6_digit)
                    if q:
                        logger.info(f"按需获取指数行情: {ak_code}")
            else:
                from app.services.data_sources.manager import DataSourceManager
                mgr = DataSourceManager()
                for adapter in mgr.adapters:
                    try:
                        quotes_map = await asyncio.wait_for(
                            asyncio.to_thread(adapter.get_realtime_quotes),
                            timeout=10.0
                        )
                        if quotes_map and code6 in quotes_map:
                            q = quotes_map[code6]
                            logger.info(f"按需获取个股行情: {code6}, source={adapter.name}")
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"按需获取行情失败: {e}")

    # 🔥 基础信息 - 按数据源优先级查询
    from app.core.unified_config import UnifiedConfigManager
    config = UnifiedConfigManager()
    data_source_configs = await config.get_data_source_configs_async()

    # 提取启用的数据源，按优先级排序
    enabled_sources = [
        ds.type.lower() for ds in data_source_configs
        if ds.enabled and ds.type.lower() in ['tushare', 'akshare', 'baostock']
    ]

    if not enabled_sources:
        enabled_sources = ['tushare', 'akshare', 'baostock']

    # 按优先级查询基础信息
    b = None
    for src in enabled_sources:
        b = await db["stock_basic_info"].find_one({"code": code6, "source": src}, {"_id": 0})
        if b:
            break

    # 如果所有数据源都没有，尝试不带 source 条件查询（兼容旧数据）
    if not b:
        b = await db["stock_basic_info"].find_one({"code": code6}, {"_id": 0})

    # 指数没有 stock_basic_info 是正常的，只要有行情数据就返回
    if not q and not b:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该股票的任何信息")

    close = (q or {}).get("close")
    pct = (q or {}).get("pct_chg")
    pre_close_saved = (q or {}).get("pre_close")
    prev_close = pre_close_saved
    if prev_close is None:
        try:
            if close is not None and pct is not None:
                prev_close = round(float(close) / (1.0 + float(pct) / 100.0), 4)
        except Exception:
            prev_close = None

    # 🔥 优先从 market_quotes 获取 turnover_rate（实时数据）
    # 如果 market_quotes 中没有，再从 stock_basic_info 获取（日度数据）
    turnover_rate = (q or {}).get("turnover_rate")
    turnover_rate_date = None
    if turnover_rate is None:
        turnover_rate = (b or {}).get("turnover_rate")
        turnover_rate_date = (b or {}).get("trade_date")  # 来自日度数据
    else:
        turnover_rate_date = (q or {}).get("trade_date")  # 来自实时数据

    # 🔥 计算振幅（amplitude）替代量比（volume_ratio）
    # 振幅 = (最高价 - 最低价) / 昨收价 × 100%
    amplitude = None
    amplitude_date = None
    try:
        high = (q or {}).get("high")
        low = (q or {}).get("low")
        logger.info(f"🔍 计算振幅: high={high}, low={low}, prev_close={prev_close}")
        if high is not None and low is not None and prev_close is not None and prev_close > 0:
            amplitude = round((float(high) - float(low)) / float(prev_close) * 100, 2)
            amplitude_date = (q or {}).get("trade_date")  # 来自实时数据
            logger.info(f"  ✅ 振幅计算成功: {amplitude}%")
        else:
            logger.warning(f"  ⚠️ 数据不完整，无法计算振幅")
    except Exception as e:
        logger.warning(f"  ❌ 计算振幅失败: {e}")
        amplitude = None

    data = {
        "code": code6,
        "name": (b or {}).get("name"),
        "market": (b or {}).get("market"),
        "price": close,
        "change_percent": pct,
        "amount": (q or {}).get("amount"),
        "volume": (q or {}).get("volume"),
        "open": (q or {}).get("open"),
        "high": (q or {}).get("high"),
        "low": (q or {}).get("low"),
        "prev_close": prev_close,
        # 🔥 优先使用实时数据，降级到日度数据
        "turnover_rate": turnover_rate,
        "amplitude": amplitude,  # 🔥 新增：振幅（替代量比）
        "turnover_rate_date": turnover_rate_date,  # 🔥 新增：换手率数据日期
        "amplitude_date": amplitude_date,  # 🔥 新增：振幅数据日期
        "trade_date": (q or {}).get("trade_date"),
        "updated_at": (q or {}).get("updated_at"),
    }

    return ok(data)


@router.get("/{code}/fundamentals", response_model=dict)
async def get_fundamentals(
    code: str,
    source: Optional[str] = Query(None, description="数据源 (tushare/akshare/baostock/multi_source)"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取基础面快照（优先从 MongoDB 获取）

    数据来源优先级：
    1. stock_basic_info 集合（基础信息、估值指标）
    2. stock_financial_data 集合（财务指标：ROE、负债率等）

    参数：
    - code: 股票代码
    - source: 数据源（可选），默认按优先级：tushare > multi_source > akshare > baostock
    """
    db = get_mongo_db()
    code6 = _zfill_code(code)

    # 1. 获取基础信息（支持数据源筛选）
    query = {"code": code6}

    if source:
        # 指定数据源
        query["source"] = source
        b = await db["stock_basic_info"].find_one(query, {"_id": 0})
        if not b:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"未找到该股票在数据源 {source} 中的基础信息"
            )
    else:
        # 🔥 未指定数据源，按优先级查询
        source_priority = ["tushare", "multi_source", "akshare", "baostock"]
        b = None

        for src in source_priority:
            query_with_source = {"code": code6, "source": src}
            b = await db["stock_basic_info"].find_one(query_with_source, {"_id": 0})
            if b:
                logger.info(f"✅ 使用数据源: {src} 查询股票 {code6}")
                break

        # 如果所有数据源都没有，尝试不带 source 条件查询（兼容旧数据）
        if not b:
            b = await db["stock_basic_info"].find_one({"code": code6}, {"_id": 0})
            if b:
                logger.warning(f"⚠️ 使用旧数据（无 source 字段）: {code6}")

        if not b:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该股票的基础信息")

    # 2. 尝试从 stock_financial_data 获取最新财务指标
    # 🔥 按数据源优先级查询，而不是按时间戳，避免混用不同数据源的数据
    financial_data = None
    try:
        # 获取数据源优先级配置
        from app.core.unified_config import UnifiedConfigManager
        config = UnifiedConfigManager()
        data_source_configs = await config.get_data_source_configs_async()

        # 提取启用的数据源，按优先级排序
        enabled_sources = [
            ds.type.lower() for ds in data_source_configs
            if ds.enabled and ds.type.lower() in ['tushare', 'akshare', 'baostock']
        ]

        if not enabled_sources:
            enabled_sources = ['tushare', 'akshare', 'baostock']

        # 按数据源优先级查询财务数据
        for data_source in enabled_sources:
            financial_data = await db["stock_financial_data"].find_one(
                {"$or": [{"symbol": code6}, {"code": code6}], "data_source": data_source},
                {"_id": 0},
                sort=[("report_period", -1)]  # 按报告期降序，获取该数据源的最新数据
            )
            if financial_data:
                logger.info(f"✅ 使用数据源 {data_source} 的财务数据 (报告期: {financial_data.get('report_period')})")
                break

        if not financial_data:
            logger.warning(f"⚠️ 未找到 {code6} 的财务数据")
    except Exception as e:
        logger.error(f"获取财务数据失败: {e}")

    # 3. 获取实时PE/PB（优先使用实时计算）
    from sinoquant.dataflows.realtime_metrics import get_pe_pb_with_fallback
    import asyncio

    # 在线程池中执行同步的实时计算
    realtime_metrics = await asyncio.to_thread(
        get_pe_pb_with_fallback,
        code6,
        db.client
    )

    # 4. 构建返回数据
    # 🔥 优先使用实时市值，降级到 stock_basic_info 的静态市值
    realtime_market_cap = realtime_metrics.get("market_cap")  # 实时市值（亿元）
    total_mv = realtime_market_cap if realtime_market_cap else b.get("total_mv")

    data = {
        "code": code6,
        "name": b.get("name"),
        "industry": b.get("industry"),  # 行业（如：银行、软件服务）
        "market": b.get("market"),      # 交易所（如：主板、创业板）

        # 板块信息：使用 market 字段（主板/创业板/科创板/北交所等）
        "sector": b.get("market"),

        # 估值指标（优先使用实时计算，降级到 stock_basic_info）
        "pe": realtime_metrics.get("pe") or b.get("pe"),
        "pb": realtime_metrics.get("pb") or b.get("pb"),
        "pe_ttm": realtime_metrics.get("pe_ttm") or b.get("pe_ttm"),
        "pb_mrq": realtime_metrics.get("pb_mrq") or b.get("pb_mrq"),

        # 🔥 市销率（PS）- 动态计算（使用实时市值）
        "ps": None,
        "ps_ttm": None,

        # PE/PB 数据来源标识
        "pe_source": realtime_metrics.get("source", "unknown"),
        "pe_is_realtime": realtime_metrics.get("is_realtime", False),
        "pe_updated_at": realtime_metrics.get("updated_at"),

        # ROE（优先从 stock_financial_data 获取，其次从 stock_basic_info）
        "roe": None,

        # 负债率（从 stock_financial_data 获取）
        "debt_ratio": None,

        # 市值：优先使用实时市值，降级到静态市值
        "total_mv": total_mv,
        "circ_mv": b.get("circ_mv"),

        # 🔥 市值来源标识
        "mv_is_realtime": bool(realtime_market_cap),

        # 交易指标（可能为空）
        "turnover_rate": b.get("turnover_rate"),
        "volume_ratio": b.get("volume_ratio"),

        "updated_at": b.get("updated_at"),
    }

    # 5. 从财务数据中提取 ROE、负债率和计算 PS
    if financial_data:
        # ROE（净资产收益率）
        if financial_data.get("financial_indicators"):
            indicators = financial_data["financial_indicators"]
            data["roe"] = indicators.get("roe")
            data["debt_ratio"] = indicators.get("debt_to_assets")

        # 如果 financial_indicators 中没有，尝试从顶层字段获取
        if data["roe"] is None:
            data["roe"] = financial_data.get("roe")
        if data["debt_ratio"] is None:
            data["debt_ratio"] = financial_data.get("debt_to_assets")

        # 🔥 动态计算 PS（市销率）- 使用实时市值
        # 优先使用 TTM 营业收入，如果没有则使用单期营业收入
        revenue_ttm = financial_data.get("revenue_ttm")
        revenue = financial_data.get("revenue")
        revenue_for_ps = revenue_ttm if revenue_ttm and revenue_ttm > 0 else revenue

        if revenue_for_ps and revenue_for_ps > 0:
            # 🔥 使用实时市值（如果有），否则使用静态市值
            if total_mv and total_mv > 0:
                # 营业收入单位：元，需要转换为亿元
                revenue_yi = revenue_for_ps / 100000000
                ps_calculated = total_mv / revenue_yi
                data["ps"] = round(ps_calculated, 2)
                data["ps_ttm"] = round(ps_calculated, 2) if revenue_ttm else None

    # 6. 如果财务数据中没有 ROE，使用 stock_basic_info 中的
    if data["roe"] is None:
        data["roe"] = b.get("roe")

    return ok(data)


@router.get("/{code}/kline", response_model=dict)
async def get_kline(code: str, period: str = "day", limit: int = 120, adj: str = "none", current_user: dict = Depends(get_current_user)):
    """获取K线数据（MongoDB缓存优先，Tushare/AkShare兜底）
    period: day/week/month/5m/15m/30m/60m
    adj: none/qfq/hfq

    🔥 新增功能：当天实时K线数据
    - 交易时间内（09:30-15:00）：从 market_quotes 获取实时数据
    - 收盘后：检查历史数据是否有当天数据，没有则从 market_quotes 获取
    """
    import logging
    from datetime import datetime, timedelta, time as dtime
    from zoneinfo import ZoneInfo
    logger = logging.getLogger(__name__)

    valid_periods = {"day","week","month","5m","15m","30m","60m"}
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"不支持的period: {period}")

    code_padded = _zfill_code(code)
    adj_norm = None if adj in (None, "none", "", "null") else adj
    items = None
    source = None

    # Redis 缓存：日线/周线/月线缓存1小时，分钟线缓存5分钟
    import json
    cache_ttl = 3600 if period in ("day", "week", "month") else 300
    cache_key = f"kline:{code_padded}:{period}:{adj or 'none'}:{limit}"
    try:
        from app.core.database import get_redis_client
        redis = get_redis_client()
        if redis:
            cached = await redis.get(cache_key)
            if cached:
                logger.info(f"K线缓存命中: {cache_key}")
                return json.loads(cached)
    except Exception:
        pass  # Redis 不可用时降级为无缓存

    # 检测是否为指数代码
    from app.utils.code_utils import classify_code
    is_index, ak_code, index_name = classify_code(code_padded)

    if is_index:
        # 指数：跳过 MongoDB 缓存，直接走外部 API
        import asyncio as _asyncio
        from app.services.data_sources.manager import DataSourceManager
        mgr = DataSourceManager()
        try:
            items, source = await _asyncio.wait_for(
                _asyncio.to_thread(mgr.get_kline_with_fallback, code_padded, period, limit, adj_norm),
                timeout=60.0
            )
        except _asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="获取指数K线数据超时")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取指数K线数据失败: {str(e)}")

    if not is_index:
        # 个股：MongoDB 缓存优先，外部 API 兜底
        # 周期映射：前端 -> MongoDB
        period_map = {
            "day": "daily",
            "week": "weekly",
            "month": "monthly",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "60m": "60min"
        }
        mongodb_period = period_map.get(period, "daily")

        # 获取当前时间（北京时间）
        from app.core.config import settings
        tz = ZoneInfo(settings.TIMEZONE)
        now = datetime.now(tz)
        today_str_yyyymmdd = now.strftime("%Y%m%d")
        today_str_formatted = now.strftime("%Y-%m-%d")

        # 1. 优先从 MongoDB 缓存获取
        try:
            from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()

            end_date = now.strftime("%Y-%m-%d")
            start_date = (now - timedelta(days=limit * 2)).strftime("%Y-%m-%d")

            logger.info(f"尝试从 MongoDB 获取 K 线数据: {code_padded}, period={period}, limit={limit}")
            df = adapter.get_historical_data(code_padded, start_date, end_date, period=mongodb_period)

            if df is not None and not df.empty:
                items = []
                for _, row in df.tail(limit).iterrows():
                    items.append({
                        "time": row.get("trade_date", row.get("date", "")),
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": float(row.get("volume", row.get("vol", 0))),
                        "amount": float(row.get("amount", 0)) if "amount" in row else None,
                    })
                source = "mongodb"
                logger.info(f"从 MongoDB 获取到 {len(items)} 条 K 线数据")
        except Exception as e:
            logger.warning(f"MongoDB 获取 K 线失败: {e}")

        # 2. 如果 MongoDB 没有数据，降级到外部 API
        if not items:
            logger.info(f"MongoDB 无数据，降级到外部 API")
            try:
                import asyncio
                from app.services.data_sources.manager import DataSourceManager

                mgr = DataSourceManager()
                items, source = await asyncio.wait_for(
                    asyncio.to_thread(mgr.get_kline_with_fallback, code_padded, period, limit, adj_norm),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="获取K线数据超时，请稍后重试")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"获取K线数据失败: {str(e)}")

        # 3. 检查是否需要添加当天实时数据（仅针对日线）
        if period == "day" and items:
            try:
                has_today_data = any(
                    item.get("time") in [today_str_yyyymmdd, today_str_formatted]
                    for item in items
                )

                current_time = now.time()
                is_weekday = now.weekday() < 5
                is_trading_time = (
                    is_weekday and (
                        (dtime(9, 30) <= current_time <= dtime(11, 30)) or
                        (dtime(13, 0) <= current_time <= dtime(15, 30))
                    )
                )

                if is_trading_time:
                    from app.core.database import get_mongo_db
                    db = get_mongo_db()
                    market_quotes_coll = db["market_quotes"]
                    realtime_quote = await market_quotes_coll.find_one({"code": code_padded})

                    if realtime_quote:
                        today_kline = {
                            "time": today_str_formatted,
                            "open": float(realtime_quote.get("open", 0)),
                            "high": float(realtime_quote.get("high", 0)),
                            "low": float(realtime_quote.get("low", 0)),
                            "close": float(realtime_quote.get("close", 0)),
                            "volume": float(realtime_quote.get("volume", 0)),
                            "amount": float(realtime_quote.get("amount", 0)),
                        }

                        if has_today_data:
                            items[-1] = today_kline
                        else:
                            items.append(today_kline)
                        source = f"{source}+market_quotes"
            except Exception as e:
                logger.warning(f"获取当天实时数据失败（忽略）: {e}")

    data = {
        "code": code_padded,
        "period": period,
        "limit": limit,
        "adj": adj if adj else "none",
        "source": source,
        "items": items or []
    }
    result = ok(data)

    # 写入 Redis 缓存
    try:
        from app.core.database import get_redis_client
        redis = get_redis_client()
        if redis:
            await redis.setex(cache_key, cache_ttl, json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        pass

    return result


@router.get("/{code}/news", response_model=dict)
async def get_news(code: str, days: int = 2, limit: int = 50, include_announcements: bool = True, current_user: dict = Depends(get_current_user)):
    """获取新闻与公告（Tushare 主，AkShare 兜底）"""
    from app.services.data_sources.manager import DataSourceManager
    mgr = DataSourceManager()
    items, source = mgr.get_news_with_fallback(code=_zfill_code(code), days=days, limit=limit, include_announcements=include_announcements)
    data = {
        "code": _zfill_code(code),
        "days": days,
        "limit": limit,
        "include_announcements": include_announcements,
        "source": source,
        "items": items or []
    }
    return ok(data)

