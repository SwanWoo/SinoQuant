"""
基本面数据获取 Mixin

提供多数据源的基本面数据查询与格式化，支持自动降级。
"""

import time
from typing import Dict, List

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()


class FundamentalsMixin:
    """基本面数据获取 mixin"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_fundamentals_data(self, symbol: str) -> str:
        """
        获取基本面数据，支持多数据源和自动降级
        优先级：MongoDB → Tushare → AKShare → 生成分析

        Args:
            symbol: 股票代码

        Returns:
            str: 基本面分析报告
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(
            f"📊 [数据来源: {self.current_source.value}] 开始获取基本面数据: {symbol}",
            extra={
                "symbol": symbol,
                "data_source": self.current_source.value,
                "event_type": "fundamentals_fetch_start",
            },
        )

        start_time = time.time()

        try:
            if self.current_source == ChinaDataSource.MONGODB:
                result = self._get_mongodb_fundamentals(symbol)
            elif self.current_source == ChinaDataSource.TUSHARE:
                result = self._get_tushare_fundamentals(symbol)
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_fundamentals(symbol)
            else:
                result = self._generate_fundamentals_analysis(symbol)

            duration = time.time() - start_time
            result_length = len(result) if result else 0

            if result and "❌" not in result:
                logger.info(
                    f"✅ [数据来源: {self.current_source.value}] 成功获取基本面数据: "
                    f"{symbol} ({result_length}字符, 耗时{duration:.2f}秒)",
                    extra={
                        "symbol": symbol,
                        "data_source": self.current_source.value,
                        "duration": duration,
                        "result_length": result_length,
                        "event_type": "fundamentals_fetch_success",
                    },
                )
                return result
            else:
                logger.warning(
                    f"⚠️ [数据来源: {self.current_source.value}失败] "
                    f"基本面数据质量异常，尝试降级: {symbol}",
                    extra={
                        "symbol": symbol,
                        "data_source": self.current_source.value,
                        "event_type": "fundamentals_fetch_fallback",
                    },
                )
                return self._try_fallback_fundamentals(symbol)

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"❌ [数据来源: {self.current_source.value}异常] "
                f"获取基本面数据失败: {symbol} - {e}",
                extra={
                    "symbol": symbol,
                    "data_source": self.current_source.value,
                    "duration": duration,
                    "error": str(e),
                    "event_type": "fundamentals_fetch_exception",
                },
                exc_info=True,
            )
            return self._try_fallback_fundamentals(symbol)

    def get_china_stock_fundamentals_tushare(self, symbol: str) -> str:
        """
        使用Tushare获取中国股票基本面数据（兼容旧接口）

        Args:
            symbol: 股票代码

        Returns:
            str: 基本面分析报告
        """
        return self._get_tushare_fundamentals(symbol)

    # ------------------------------------------------------------------
    # 各数据源基本面获取
    # ------------------------------------------------------------------

    def _get_mongodb_fundamentals(self, symbol: str) -> str:
        """从 MongoDB 获取财务数据"""
        logger.debug(f"📊 [MongoDB] 调用参数: symbol={symbol}")

        try:
            from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            import pandas as pd

            adapter = get_mongodb_cache_adapter()
            financial_data = adapter.get_financial_data(symbol)

            if financial_data is not None:
                if isinstance(financial_data, pd.DataFrame):
                    if not financial_data.empty:
                        logger.info(
                            f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} "
                            f"({len(financial_data)}条记录)"
                        )
                        financial_dict_list = financial_data.to_dict("records")
                        return self._format_financial_data(symbol, financial_dict_list)
                    else:
                        logger.warning(
                            f"⚠️ [数据来源: MongoDB] 财务数据为空: {symbol}，降级到其他数据源"
                        )
                        return self._try_fallback_fundamentals(symbol)
                elif isinstance(financial_data, list) and len(financial_data) > 0:
                    logger.info(
                        f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} "
                        f"({len(financial_data)}条记录)"
                    )
                    return self._format_financial_data(symbol, financial_data)
                elif isinstance(financial_data, dict):
                    logger.info(
                        f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} (单条记录)"
                    )
                    financial_dict_list = [financial_data]
                    return self._format_financial_data(symbol, financial_dict_list)
                else:
                    logger.warning(
                        f"⚠️ [数据来源: MongoDB] 未找到财务数据: {symbol}，降级到其他数据源"
                    )
                    return self._try_fallback_fundamentals(symbol)
            else:
                logger.warning(
                    f"⚠️ [数据来源: MongoDB] 未找到财务数据: {symbol}，降级到其他数据源"
                )
                return self._try_fallback_fundamentals(symbol)

        except Exception as e:
            logger.error(
                f"❌ [数据来源: MongoDB异常] 获取财务数据失败: {e}", exc_info=True
            )
            return self._try_fallback_fundamentals(symbol)

    def _get_tushare_fundamentals(self, symbol: str) -> str:
        """从 Tushare 获取基本面数据 - 暂时不可用，需要实现"""
        logger.warning(f"⚠️ Tushare基本面数据功能暂时不可用")
        return f"⚠️ Tushare基本面数据功能暂时不可用，请使用其他数据源"

    def _get_akshare_fundamentals(self, symbol: str) -> str:
        """从 AKShare 生成基本面分析"""
        logger.debug(f"📊 [AKShare] 调用参数: symbol={symbol}")

        try:
            logger.info(
                f"📊 [数据来源: AKShare-生成分析] 生成基本面分析: {symbol}"
            )
            return self._generate_fundamentals_analysis(symbol)
        except Exception as e:
            logger.error(f"❌ [数据来源: AKShare异常] 生成基本面分析失败: {e}")
            return f"❌ 生成{symbol}基本面分析失败: {e}"

    # ------------------------------------------------------------------
    # 估值指标
    # ------------------------------------------------------------------

    def _get_valuation_indicators(self, symbol: str) -> Dict:
        """从stock_basic_info集合获取估值指标"""
        try:
            from sinoquant.config.database_manager import get_database_manager

            db_manager = get_database_manager()
            if not db_manager.is_mongodb_available():
                return {}

            client = db_manager.get_mongodb_client()
            db = client[db_manager.config.mongodb_config.database_name]

            collection = db["stock_basic_info"]
            result = collection.find_one({"ts_code": symbol})

            if result:
                return {
                    "pe": result.get("pe"),
                    "pb": result.get("pb"),
                    "pe_ttm": result.get("pe_ttm"),
                    "total_mv": result.get("total_mv"),
                    "circ_mv": result.get("circ_mv"),
                }
            return {}

        except Exception as e:
            logger.error(f"获取{symbol}估值指标失败: {e}")
            return {}

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    def _format_financial_data(self, symbol: str, financial_data: List[Dict]) -> str:
        """格式化财务数据为报告"""
        try:
            if not financial_data or len(financial_data) == 0:
                return f"❌ 未找到{symbol}的财务数据"

            latest = financial_data[0]

            report = f"📊 {symbol} 基本面数据（来自MongoDB）\n\n"
            report += f"📅 报告期: {latest.get('report_period', latest.get('end_date', '未知'))}\n"
            report += f"📈 数据来源: MongoDB财务数据库\n\n"

            report += "💰 财务指标:\n"
            revenue = latest.get("revenue") or latest.get("total_revenue")
            if revenue is not None:
                report += f"   营业总收入: {revenue:,.2f}\n"

            net_profit = latest.get("net_profit") or latest.get("net_income")
            if net_profit is not None:
                report += f"   净利润: {net_profit:,.2f}\n"

            total_assets = latest.get("total_assets")
            if total_assets is not None:
                report += f"   总资产: {total_assets:,.2f}\n"

            total_liab = latest.get("total_liab")
            if total_liab is not None:
                report += f"   总负债: {total_liab:,.2f}\n"

            total_equity = latest.get("total_equity")
            if total_equity is not None:
                report += f"   股东权益: {total_equity:,.2f}\n"

            # 估值指标
            report += "\n📊 估值指标:\n"
            valuation_data = self._get_valuation_indicators(symbol)
            if valuation_data:
                pe = valuation_data.get("pe")
                if pe is not None:
                    report += f"   市盈率(PE): {pe:.2f}\n"

                pb = valuation_data.get("pb")
                if pb is not None:
                    report += f"   市净率(PB): {pb:.2f}\n"

                pe_ttm = valuation_data.get("pe_ttm")
                if pe_ttm is not None:
                    report += f"   市盈率TTM(PE_TTM): {pe_ttm:.2f}\n"

                total_mv = valuation_data.get("total_mv")
                if total_mv is not None:
                    report += f"   总市值: {total_mv:.2f}亿元\n"

                circ_mv = valuation_data.get("circ_mv")
                if circ_mv is not None:
                    report += f"   流通市值: {circ_mv:.2f}亿元\n"
            else:
                pe = latest.get("pe")
                if pe is not None:
                    report += f"   市盈率(PE): {pe:.2f}\n"

                pb = latest.get("pb")
                if pb is not None:
                    report += f"   市净率(PB): {pb:.2f}\n"

                ps = latest.get("ps")
                if ps is not None:
                    report += f"   市销率(PS): {ps:.2f}\n"

            # 盈利能力
            report += "\n💹 盈利能力:\n"
            roe = latest.get("roe")
            if roe is not None:
                report += f"   净资产收益率(ROE): {roe:.2f}%\n"

            roa = latest.get("roa")
            if roa is not None:
                report += f"   总资产收益率(ROA): {roa:.2f}%\n"

            gross_margin = latest.get("gross_margin")
            if gross_margin is not None:
                report += f"   毛利率: {gross_margin:.2f}%\n"

            netprofit_margin = latest.get("netprofit_margin") or latest.get("net_margin")
            if netprofit_margin is not None:
                report += f"   净利率: {netprofit_margin:.2f}%\n"

            # 现金流
            n_cashflow_act = latest.get("n_cashflow_act")
            if n_cashflow_act is not None:
                report += "\n💰 现金流:\n"
                report += f"   经营活动现金流: {n_cashflow_act:,.2f}\n"

                n_cashflow_inv_act = latest.get("n_cashflow_inv_act")
                if n_cashflow_inv_act is not None:
                    report += f"   投资活动现金流: {n_cashflow_inv_act:,.2f}\n"

                c_cash_equ_end_period = latest.get("c_cash_equ_end_period")
                if c_cash_equ_end_period is not None:
                    report += f"   期末现金及等价物: {c_cash_equ_end_period:,.2f}\n"

            report += f"\n📝 共有 {len(financial_data)} 期财务数据\n"

            return report

        except Exception as e:
            logger.error(f"❌ 格式化财务数据失败: {e}")
            return f"❌ 格式化{symbol}财务数据失败: {e}"

    # ------------------------------------------------------------------
    # 生成分析
    # ------------------------------------------------------------------

    def _generate_fundamentals_analysis(self, symbol: str) -> str:
        """生成基本的基本面分析"""
        try:
            stock_info = self.get_stock_info(symbol)

            report = f"📊 {symbol} 基本面分析（生成）\n\n"
            report += f"📈 股票名称: {stock_info.get('name', '未知')}\n"
            report += f"🏢 所属行业: {stock_info.get('industry', '未知')}\n"
            report += f"📍 所属地区: {stock_info.get('area', '未知')}\n"
            report += f"📅 上市日期: {stock_info.get('list_date', '未知')}\n"
            report += f"🏛️ 交易所: {stock_info.get('exchange', '未知')}\n\n"

            report += "⚠️ 注意: 详细财务数据需要从数据源获取\n"
            report += "💡 建议: 启用MongoDB缓存以获取完整的财务数据\n"

            return report

        except Exception as e:
            logger.error(f"❌ 生成基本面分析失败: {e}")
            return f"❌ 生成{symbol}基本面分析失败: {e}"

    # ------------------------------------------------------------------
    # 降级
    # ------------------------------------------------------------------

    def _try_fallback_fundamentals(self, symbol: str) -> str:
        """基本面数据降级处理"""
        try:
            from sinoquant.config.runtime_settings import local_data_only_enabled
            if local_data_only_enabled():
                logger.warning(
                    f"🔒 [本地数据模式] 禁止外部API降级，{symbol}的基本面数据在MongoDB中不可用"
                )
                return (
                    f"❌ [本地数据模式] MongoDB中没有{symbol}的基本面数据。"
                    f"请先使用数据同步功能下载财务数据，或关闭TA_LOCAL_DATA_ONLY模式。"
                )
        except Exception:
            pass

        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.error(
            f"🔄 {self.current_source.value}失败，尝试备用数据源获取基本面..."
        )

        fallback_order = self._get_data_source_priority_order(symbol)

        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 尝试备用数据源获取基本面: {source.value}")

                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_fundamentals(symbol)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_fundamentals(symbol)
                    else:
                        continue

                    if result and "❌" not in result:
                        logger.info(
                            f"✅ [数据来源: 备用数据源] 降级成功获取基本面: {source.value}"
                        )
                        return result
                    else:
                        logger.warning(f"⚠️ 备用数据源{source.value}返回错误结果")

                except Exception as e:
                    logger.error(f"❌ 备用数据源{source.value}异常: {e}")
                    continue

        logger.warning(
            f"⚠️ [数据来源: 生成分析] 所有数据源失败，生成基本分析: {symbol}"
        )
        return self._generate_fundamentals_analysis(symbol)
