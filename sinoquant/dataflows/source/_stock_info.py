"""
股票基本信息获取 Mixin

提供多数据源的股票基本信息查询，支持自动降级。
"""

import asyncio
from typing import Dict, List, Optional, Any

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()


class StockInfoMixin:
    """股票基本信息获取 mixin"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_stock_info(self, symbol: str) -> Dict:
        """
        获取股票基本信息，支持多数据源和自动降级
        优先级：MongoDB → Tushare → AKShare → BaoStock
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(f"📊 [数据来源: {self.current_source.value}] 开始获取股票信息: {symbol}")

        # 优先使用 App Mongo 缓存（当 ta_use_app_cache=True）
        try:
            from sinoquant.config.runtime_settings import use_app_cache_enabled  # type: ignore
            use_cache = use_app_cache_enabled(False)
            logger.info(f"🔧 [配置检查] use_app_cache_enabled() 返回值: {use_cache}")
        except Exception as e:
            logger.error(f"❌ [配置检查] use_app_cache_enabled() 调用失败: {e}", exc_info=True)
            use_cache = False

        logger.info(f"🔧 [配置] ta_use_app_cache={use_cache}, current_source={self.current_source.value}")

        if use_cache:
            try:
                from sinoquant.dataflows.cache.app_adapter import get_basics_from_cache, get_market_quote_dataframe
                doc = get_basics_from_cache(symbol)
                if doc:
                    name = doc.get("name") or doc.get("stock_name") or ""
                    board_labels = {"主板", "中小板", "创业板", "科创板"}
                    raw_industry = (doc.get("industry") or doc.get("industry_name") or "").strip()
                    sec_or_cat = (doc.get("sec") or doc.get("category") or "").strip()
                    market_val = (doc.get("market") or "").strip()
                    industry_val = raw_industry or sec_or_cat or "未知"
                    changed = False
                    if raw_industry in board_labels:
                        if not market_val:
                            market_val = raw_industry
                            changed = True
                        if sec_or_cat:
                            industry_val = sec_or_cat
                            changed = True
                    if changed:
                        try:
                            logger.debug(
                                f"🔧 [字段归一化] industry原值='{raw_industry}' "
                                f"→ 行业='{industry_val}', 市场/板块='{market_val or doc.get('market', '未知')}'"
                            )
                        except Exception:
                            pass

                    result = {
                        "symbol": symbol,
                        "name": name or f"股票{symbol}",
                        "area": doc.get("area", "未知"),
                        "industry": industry_val or "未知",
                        "market": market_val or doc.get("market", "未知"),
                        "list_date": doc.get("list_date", "未知"),
                        "source": "app_cache",
                    }
                    # 追加快照行情
                    try:
                        df = get_market_quote_dataframe(symbol)
                        if df is not None and not df.empty:
                            row = df.iloc[-1]
                            result["current_price"] = row.get("close")
                            result["change_pct"] = row.get("pct_chg")
                            result["volume"] = row.get("volume")
                            result["quote_date"] = row.get("date")
                            result["quote_source"] = "market_quotes"
                            logger.info(
                                f"✅ [股票信息] 附加行情 | "
                                f"price={result['current_price']} pct={result['change_pct']} "
                                f"vol={result['volume']} code={symbol}"
                            )
                    except Exception as _e:
                        logger.debug(f"附加行情失败（忽略）：{_e}")

                    if name:
                        logger.info(f"✅ [数据来源: MongoDB-stock_basic_info] 成功获取: {symbol}")
                        return result
                    else:
                        logger.warning(f"⚠️ [数据来源: MongoDB] 未找到有效名称: {symbol}，降级到其他数据源")
            except Exception as e:
                logger.error(f"❌ [数据来源: MongoDB异常] 获取股票信息失败: {e}", exc_info=True)

        # 首先尝试当前数据源
        try:
            if self.current_source == ChinaDataSource.TUSHARE:
                from sinoquant.dataflows.interface import get_china_stock_info_tushare
                info_str = get_china_stock_info_tushare(symbol)
                result = self._parse_stock_info_string(info_str, symbol)

                if result.get("name") and result["name"] != f"股票{symbol}":
                    logger.info(f"✅ [数据来源: Tushare-股票信息] 成功获取: {symbol}")
                    return result
                else:
                    logger.warning(f"⚠️ [数据来源: Tushare失败] 返回无效信息，尝试降级: {symbol}")
                    return self._try_fallback_stock_info(symbol)
            else:
                adapter = self.get_data_adapter()
                if adapter and hasattr(adapter, "get_stock_info"):
                    result = adapter.get_stock_info(symbol)
                    if result.get("name") and result["name"] != f"股票{symbol}":
                        logger.info(f"✅ [数据来源: {self.current_source.value}-股票信息] 成功获取: {symbol}")
                        return result
                    else:
                        logger.warning(f"⚠️ [数据来源: {self.current_source.value}失败] 返回无效信息，尝试降级: {symbol}")
                        return self._try_fallback_stock_info(symbol)
                else:
                    logger.warning(f"⚠️ [数据来源: {self.current_source.value}] 不支持股票信息获取，尝试降级: {symbol}")
                    return self._try_fallback_stock_info(symbol)

        except Exception as e:
            logger.error(f"❌ [数据来源: {self.current_source.value}异常] 获取股票信息失败: {e}", exc_info=True)
            return self._try_fallback_stock_info(symbol)

    def get_stock_basic_info(self, stock_code: str = None) -> Optional[Dict[str, Any]]:
        """
        获取股票基础信息（兼容 stock_data_service 接口）

        Args:
            stock_code: 股票代码，如果为 None 则返回所有股票列表

        Returns:
            Dict: 股票信息字典，或包含 error 字段的错误字典
        """
        if stock_code is None:
            logger.info("📊 获取所有股票列表")
            try:
                from sinoquant.config.database_manager import get_database_manager
                db_manager = get_database_manager()
                if db_manager and db_manager.is_mongodb_available():
                    collection = db_manager.mongodb_db["stock_basic_info"]
                    stocks = list(collection.find({}, {"_id": 0}))
                    if stocks:
                        logger.info(f"✅ 从MongoDB获取所有股票: {len(stocks)}条")
                        return stocks
            except Exception as e:
                logger.warning(f"⚠️ 从MongoDB获取所有股票失败: {e}")
            return []

        try:
            result = self.get_stock_info(stock_code)
            if result and result.get("name"):
                return result
            else:
                return {"error": f"未找到股票 {stock_code} 的信息"}
        except Exception as e:
            logger.error(f"❌ 获取股票信息失败: {e}")
            return {"error": str(e)}

    def get_stock_data_with_fallback(self, stock_code: str, start_date: str, end_date: str) -> str:
        """
        获取股票数据（兼容 stock_data_service 接口）

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            str: 格式化的股票数据报告
        """
        logger.info(f"📊 获取股票数据: {stock_code} ({start_date} 到 {end_date})")

        try:
            return self.get_stock_data(stock_code, start_date, end_date)
        except Exception as e:
            logger.error(f"❌ 获取股票数据失败: {e}")
            return (
                f"❌ 获取股票数据失败: {str(e)}\n\n"
                "💡 建议：\n"
                "1. 检查网络连接\n"
                "2. 确认股票代码格式正确\n"
                "3. 检查数据源配置"
            )

    # ------------------------------------------------------------------
    # 降级
    # ------------------------------------------------------------------

    def _try_fallback_stock_info(self, symbol: str) -> Dict:
        """尝试使用备用数据源获取股票基本信息"""
        try:
            from sinoquant.config.runtime_settings import local_data_only_enabled
            if local_data_only_enabled():
                logger.warning(
                    f"🔒 [本地数据模式] 禁止外部API降级，{symbol}的股票信息在MongoDB中不可用"
                )
                return {
                    "symbol": symbol,
                    "name": f"股票{symbol}",
                    "source": "local_unavailable",
                    "error": f"[本地数据模式] MongoDB中没有{symbol}的基础信息。请先同步数据。"
                }
        except Exception:
            pass

        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.error(f"🔄 {self.current_source.value}失败，尝试备用数据源获取股票信息...")

        available_sources = self.available_sources.copy()
        if self.current_source.value in available_sources:
            available_sources.remove(self.current_source.value)

        for source_name in available_sources:
            try:
                source = ChinaDataSource(source_name)
                logger.info(f"🔄 尝试备用数据源获取股票信息: {source_name}")

                if source == ChinaDataSource.TUSHARE:
                    result = self._get_tushare_stock_info(symbol)
                elif source == ChinaDataSource.AKSHARE:
                    result = self._get_akshare_stock_info(symbol)
                elif source == ChinaDataSource.BAOSTOCK:
                    result = self._get_baostock_stock_info(symbol)
                else:
                    original_source = self.current_source
                    self.current_source = source
                    adapter = self.get_data_adapter()
                    self.current_source = original_source

                    if adapter and hasattr(adapter, "get_stock_info"):
                        result = adapter.get_stock_info(symbol)
                    else:
                        logger.warning(f"⚠️ [股票信息] {source_name}不支持股票信息获取")
                        continue

                if result.get("name") and result["name"] != f"股票{symbol}":
                    logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取股票信息: {source_name}")
                    return result
                else:
                    logger.warning(f"⚠️ [数据来源: {source_name}] 返回无效信息")

            except Exception as e:
                logger.error(f"❌ 备用数据源{source_name}失败: {e}")
                continue

        logger.error(f"❌ 所有数据源都无法获取{symbol}的股票信息")
        return {"symbol": symbol, "name": f"股票{symbol}", "source": "unknown"}

    # ------------------------------------------------------------------
    # 各数据源股票信息获取
    # ------------------------------------------------------------------

    def _get_tushare_stock_info(self, symbol: str) -> Dict:
        """使用Tushare获取股票基本信息"""
        try:
            from sinoquant.dataflows.providers.china.tushare import get_tushare_provider

            provider = get_tushare_provider()
            if not provider or not provider.is_available():
                logger.warning(f"⚠️ [股票信息] Tushare提供器不可用")
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "tushare"}

            stock_info = self._run_async_info(provider.get_stock_basic_info(symbol))

            if stock_info and isinstance(stock_info, dict):
                info = {
                    "symbol": symbol,
                    "name": stock_info.get("name", f"股票{symbol}"),
                    "area": stock_info.get("area", "未知"),
                    "industry": stock_info.get("industry", "未知"),
                    "market": stock_info.get("market", "未知"),
                    "list_date": stock_info.get("list_date", "未知"),
                    "source": "tushare",
                }
                logger.info(f"✅ [Tushare股票信息] {symbol} -> {info['name']}")
                return info
            else:
                logger.warning(f"⚠️ [股票信息] Tushare返回空数据: {symbol}")
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "tushare"}

        except Exception as e:
            logger.error(f"❌ [股票信息] Tushare获取失败: {symbol}, 错误: {e}")
            return {"symbol": symbol, "name": f"股票{symbol}", "source": "tushare", "error": str(e)}

    def _get_akshare_stock_info(self, symbol: str) -> Dict:
        """
        使用AKShare获取股票基本信息

        注意：stock_individual_info_em 接受纯6位代码（不带 sh/sz 前缀），
        带前缀会导致 API 返回异常格式触发 pandas ValueError。
        """
        try:
            import akshare as ak

            # stock_individual_info_em 使用纯6位代码，不加前缀
            akshare_symbol = symbol
            logger.debug(f"📊 [AKShare股票信息] 原始代码: {symbol}")

            stock_info = ak.stock_individual_info_em(symbol=akshare_symbol)

            if stock_info is not None and not stock_info.empty:
                info = {"symbol": symbol, "source": "akshare"}

                name_row = stock_info[stock_info["item"] == "股票简称"]
                if not name_row.empty:
                    stock_name = name_row["value"].iloc[0]
                    info["name"] = stock_name
                    logger.info(f"✅ [AKShare股票信息] {symbol} -> {stock_name}")
                else:
                    info["name"] = f"股票{symbol}"
                    logger.warning(f"⚠️ [AKShare股票信息] 未找到股票简称: {symbol}")

                info["area"] = "未知"
                info["industry"] = "未知"
                info["market"] = "未知"
                info["list_date"] = "未知"

                return info
            else:
                logger.warning(f"⚠️ [AKShare股票信息] 返回空数据: {symbol}")
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "akshare"}

        except Exception as e:
            logger.error(f"❌ [股票信息] AKShare获取失败: {symbol}, 错误: {e}")
            return {"symbol": symbol, "name": f"股票{symbol}", "source": "akshare", "error": str(e)}

    def _get_baostock_stock_info(self, symbol: str) -> Dict:
        """使用BaoStock获取股票基本信息"""
        try:
            import baostock as bs

            if symbol.startswith("6"):
                bs_code = f"sh.{symbol}"
            else:
                bs_code = f"sz.{symbol}"

            lg = bs.login()
            if lg.error_code != "0":
                logger.error(f"❌ [股票信息] BaoStock登录失败: {lg.error_msg}")
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "baostock"}

            rs = bs.query_stock_basic(code=bs_code)
            if rs.error_code != "0":
                bs.logout()
                logger.error(f"❌ [股票信息] BaoStock查询失败: {rs.error_msg}")
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "baostock"}

            data_list = []
            while (rs.error_code == "0") & rs.next():
                data_list.append(rs.get_row_data())

            bs.logout()

            if data_list:
                info = {"symbol": symbol, "source": "baostock"}
                info["name"] = data_list[0][1]
                info["area"] = "未知"
                info["industry"] = "未知"
                info["market"] = "未知"
                info["list_date"] = data_list[0][2]
                return info
            else:
                return {"symbol": symbol, "name": f"股票{symbol}", "source": "baostock"}

        except Exception as e:
            logger.error(f"❌ [股票信息] BaoStock获取失败: {e}")
            return {"symbol": symbol, "name": f"股票{symbol}", "source": "baostock", "error": str(e)}

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _parse_stock_info_string(self, info_str: str, symbol: str) -> Dict:
        """解析股票信息字符串为字典"""
        try:
            info = {"symbol": symbol, "source": self.current_source.value}
            lines = info_str.split("\n")

            for line in lines:
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()

                    if "股票名称" in key:
                        info["name"] = value
                    elif "所属行业" in key:
                        info["industry"] = value
                    elif "所属地区" in key:
                        info["area"] = value
                    elif "上市市场" in key:
                        info["market"] = value
                    elif "上市日期" in key:
                        info["list_date"] = value

            return info

        except Exception as e:
            logger.error(f"⚠️ 解析股票信息失败: {e}")
            return {"symbol": symbol, "name": f"股票{symbol}", "source": self.current_source.value}

    # ------------------------------------------------------------------
    # 异步辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async_info(coro):
        """在线程池中安全运行协程"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
