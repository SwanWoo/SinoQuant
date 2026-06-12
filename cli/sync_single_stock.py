#!/usr/bin/env python3
"""
单股票/多股票数据下载CLI工具
将指定股票的全部数据从AKShare下载到MongoDB

用法:
    python cli/sync_single_stock.py 002230
    python cli/sync_single_stock.py 002230 688256 600036
    python cli/sync_single_stock.py 002230 --days 3650
    python cli/sync_single_stock.py 002230 --skip-news
"""
import asyncio
import argparse
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))


async def sync_all_stocks(symbols: list[str], days: int, skip_news: bool):
    """下载多只股票全部数据到MongoDB"""
    from motor.motor_asyncio import AsyncIOMotorClient
    from sinoquant.config.config_manager import config_manager

    config_manager.load_settings()

    mongo_host = os.getenv("MONGODB_HOST", "localhost")
    mongo_port = os.getenv("MONGODB_PORT", "27017")
    mongo_db_name = os.getenv("MONGODB_DATABASE", "sinoquant")
    mongo_url = f"mongodb://{mongo_host}:{mongo_port}"

    client = AsyncIOMotorClient(mongo_url)
    db = client[mongo_db_name]

    # 注入到全局变量，让子服务能用 get_mongo_db() / get_database()
    from app.core import database as db_module
    db_module.mongo_client = client
    db_module.mongo_db = db
    db_module.db_manager.mongo_client = client
    db_module.db_manager.mongo_db = db

    print(f"✅ MongoDB 已连接: {mongo_url}/{mongo_db_name}")

    # 现在用完整的 init_database + sync service
    from app.worker.akshare_sync_service import AKShareSyncService
    from app.services.historical_data_service import HistoricalDataService
    from app.services.news_data_service import NewsDataService
    from sinoquant.dataflows.providers.china.akshare import AKShareProvider

    # 手动构建 service，注入 db
    hist_svc = HistoricalDataService()
    hist_svc.db = db
    hist_svc.collection = db.stock_daily_quotes
    await hist_svc._ensure_indexes()

    news_svc = NewsDataService()
    news_svc.db = db
    await news_svc._ensure_indexes()

    service = AKShareSyncService()
    service.db = db
    service.provider = AKShareProvider()
    service.historical_service = hist_svc
    service.news_service = news_svc

    if not await service.provider.test_connection():
        print("❌ AKShare 连接失败!")
        client.close()
        return

    try:
        for symbol in symbols:
            symbol = str(symbol).zfill(6)
            print(f"\n{'='*50}")
            print(f"  开始下载股票 {symbol} 的数据到MongoDB")
            print(f"  历史数据天数: {days}")
            print(f"{'='*50}")

            # 1. 基础信息
            print("\n[1/5] 同步基础信息...")
            try:
                basic_info = await service.provider.get_stock_basic_info(symbol)
                if basic_info:
                    if hasattr(basic_info, 'model_dump'):
                        basic_data = basic_info.model_dump()
                    elif hasattr(basic_info, 'dict'):
                        basic_data = basic_info.dict()
                    else:
                        basic_data = dict(basic_info)
                    basic_data["code"] = symbol
                    basic_data["symbol"] = symbol
                    basic_data["source"] = "akshare"
                    basic_data["updated_at"] = datetime.utcnow().isoformat()
                    await db.stock_basic_info.update_one(
                        {"code": symbol, "source": "akshare"},
                        {"$set": basic_data},
                        upsert=True,
                    )
                    name = basic_data.get("name", symbol)
                    print(f"  ✅ 基础信息: {name}")
                else:
                    print(f"  ⚠️ 未获取到基础信息")
            except Exception as e:
                print(f"  ❌ 基础信息失败: {e}")

            # 2. 历史行情
            print(f"\n[2/5] 同步历史数据 ({days}天)...")
            try:
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                result = await service.sync_historical_data(
                    symbols=[symbol],
                    start_date=start_date,
                    end_date=end_date,
                    incremental=False,
                )
                records = result.get("total_records", 0)
                print(f"  ✅ 历史数据: {records}条记录")
            except Exception as e:
                print(f"  ❌ 历史数据失败: {e}")

            # 3. 实时行情
            print("\n[3/5] 同步实时行情...")
            try:
                result = await service.sync_realtime_quotes(symbols=[symbol], force=True)
                ok = result.get("success_count", 0) > 0
                print(f"  {'✅' if ok else '⚠️'} 实时行情: {'成功' if ok else '未获取到'}")
            except Exception as e:
                print(f"  ❌ 实时行情失败: {e}")

            # 4. 财务数据
            print("\n[4/5] 同步财务数据...")
            try:
                result = await service.sync_financial_data(symbols=[symbol])
                ok = result.get("success_count", 0) > 0
                print(f"  {'✅' if ok else '⚠️'} 财务数据: {'成功' if ok else '未获取到'}")
            except Exception as e:
                print(f"  ❌ 财务数据失败: {e}")

            # 5. 新闻
            if not skip_news:
                print("\n[5/5] 同步新闻数据...")
                try:
                    result = await service.sync_news_data(
                        symbols=[symbol], max_news_per_stock=50
                    )
                    count = result.get("news_count", 0)
                    print(f"  ✅ 新闻数据: {count}条")
                except Exception as e:
                    print(f"  ❌ 新闻数据失败: {e}")
            else:
                print("\n[5/5] 跳过新闻同步")

            print(f"\n{'='*50}")
            print(f"  股票 {symbol} 数据下载完成!")
            print(f"{'='*50}")

    finally:
        client.close()
        print("\nMongoDB 连接已关闭")


async def main():
    parser = argparse.ArgumentParser(description="单股票数据下载工具 (AKShare → MongoDB)")
    parser.add_argument("symbols", nargs="+", help="股票代码 (如 002230 688256)")
    parser.add_argument(
        "--days", type=int, default=3650, help="历史数据天数 (默认3650天, 约10年)"
    )
    parser.add_argument("--skip-news", action="store_true", help="跳过新闻同步")

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    symbols = [s.zfill(6) for s in args.symbols]
    await sync_all_stocks(symbols, args.days, args.skip_news)
    print("\n🎉 全部下载任务完成!")


if __name__ == "__main__":
    asyncio.run(main())
