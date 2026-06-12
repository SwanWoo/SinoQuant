"""
股票行情数据获取 Mixin

提供多数据源的行情数据获取、DataFrame 接口、技术指标格式化等功能。
"""

import asyncio
import time

import numpy as np
import pandas as pd

from sinoquant.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()


class StockDataMixin:
    """股票行情数据获取 mixin"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_stock_data(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
    ) -> str:
        """
        获取股票数据的统一接口，支持多周期数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期（daily/weekly/monthly），默认为daily

        Returns:
            str: 格式化的股票数据
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(
            f"📊 [数据来源: {self.current_source.value}] 开始获取{period}数据: {symbol}",
            extra={
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "data_source": self.current_source.value,
                "event_type": "data_fetch_start",
            },
        )

        logger.info(f"🔍 [股票代码追踪] DataSourceManager.get_stock_data 接收到的股票代码: '{symbol}' (类型: {type(symbol)})")
        logger.info(f"🔍 [股票代码追踪] 股票代码长度: {len(str(symbol))}")
        logger.info(f"🔍 [股票代码追踪] 股票代码字符: {list(str(symbol))}")
        logger.info(f"🔍 [股票代码追踪] 当前数据源: {self.current_source.value}")

        start_time = time.time()

        try:
            actual_source = None

            if self.current_source == ChinaDataSource.MONGODB:
                result, actual_source = self._get_mongodb_data(symbol, start_date, end_date, period)
            elif self.current_source == ChinaDataSource.TUSHARE:
                logger.info(f"🔍 [股票代码追踪] 调用 Tushare 数据源，传入参数: symbol='{symbol}', period='{period}'")
                result = self._get_tushare_data(symbol, start_date, end_date, period)
                actual_source = "tushare"
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_data(symbol, start_date, end_date, period)
                actual_source = "akshare"
            elif self.current_source == ChinaDataSource.BAOSTOCK:
                result = self._get_baostock_data(symbol, start_date, end_date, period)
                actual_source = "baostock"
            else:
                result = f"❌ 不支持的数据源: {self.current_source.value}"
                actual_source = None

            # 兜底：防止下游返回 (result, source) 元组导致字符串操作报错
            if isinstance(result, tuple):
                fallback_source = result[1] if len(result) > 1 else None
                result = result[0] if len(result) > 0 else ""
                if not actual_source and fallback_source:
                    actual_source = fallback_source

            duration = time.time() - start_time
            result_length = len(result) if result else 0
            is_success = result and "❌" not in result and "错误" not in result
            display_source = actual_source or self.current_source.value

            if is_success:
                logger.info(
                    f"✅ [数据来源: {display_source}] 成功获取股票数据: {symbol} "
                    f"({result_length}字符, 耗时{duration:.2f}秒)",
                    extra={
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                        "data_source": display_source,
                        "actual_source": actual_source,
                        "requested_source": self.current_source.value,
                        "duration": duration,
                        "result_length": result_length,
                        "result_preview": result[:200] + "..." if result_length > 200 else result,
                        "event_type": "data_fetch_success",
                    },
                )
                return result
            else:
                logger.warning(
                    f"⚠️ [数据来源: {self.current_source.value}失败] "
                    f"数据质量异常，尝试降级到其他数据源: {symbol}",
                    extra={
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                        "data_source": self.current_source.value,
                        "duration": duration,
                        "result_length": result_length,
                        "result_preview": result[:200] + "..." if result_length > 200 else result,
                        "event_type": "data_fetch_warning",
                    },
                )
                fallback_result, fallback_source = self._try_fallback_sources(symbol, start_date, end_date, period)
                if fallback_result and "❌" not in fallback_result and "错误" not in fallback_result:
                    logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取数据: {symbol}")
                    if fallback_source:
                        logger.info(f"✅ [数据来源: 备用数据源] 实际使用数据源: {fallback_source}")
                    return fallback_result
                else:
                    logger.error(f"❌ [数据来源: 所有数据源失败] 所有数据源都无法获取有效数据: {symbol}")
                    return result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"❌ [数据获取] 异常失败: {e}",
                extra={
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                    "data_source": self.current_source.value,
                    "duration": duration,
                    "error": str(e),
                    "event_type": "data_fetch_exception",
                },
                exc_info=True,
            )
            fallback_result, fallback_source = self._try_fallback_sources(symbol, start_date, end_date, period)
            if fallback_result and "❌" not in fallback_result and "错误" not in fallback_result:
                if fallback_source:
                    logger.info(f"✅ [异常恢复] 使用备用数据源恢复成功: {fallback_source}")
                return fallback_result
            return fallback_result

    def get_stock_dataframe(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
    ) -> pd.DataFrame:
        """
        获取股票数据的 DataFrame 接口，支持多数据源和自动降级

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期（daily/weekly/monthly），默认为daily

        Returns:
            pd.DataFrame: 股票数据 DataFrame，列标准：open, high, low, close, vol, amount, date
        """
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(f"📊 [DataFrame接口] 获取股票数据: {symbol} ({start_date} 到 {end_date})")

        try:
            df = None
            if self.current_source == ChinaDataSource.MONGODB:
                from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                adapter = get_mongodb_cache_adapter()
                df = adapter.get_historical_data(symbol, start_date, end_date, period=period)
            elif self.current_source == ChinaDataSource.TUSHARE:
                from sinoquant.dataflows.providers.china.tushare import get_tushare_provider
                provider = get_tushare_provider()
                df = provider.get_daily_data(symbol, start_date, end_date)
            elif self.current_source == ChinaDataSource.AKSHARE:
                from sinoquant.dataflows.providers.china.akshare import get_akshare_provider
                provider = get_akshare_provider()
                df = provider.get_stock_data(symbol, start_date, end_date)
            elif self.current_source == ChinaDataSource.BAOSTOCK:
                from sinoquant.dataflows.providers.china.baostock import get_baostock_provider
                provider = get_baostock_provider()
                df = provider.get_stock_data(symbol, start_date, end_date)

            if df is not None and not df.empty:
                logger.info(f"✅ [DataFrame接口] 从 {self.current_source.value} 获取成功: {len(df)}条")
                return self._standardize_dataframe(df)

            # 降级到其他数据源
            logger.warning(f"⚠️ [DataFrame接口] {self.current_source.value} 失败，尝试降级")
            try:
                from sinoquant.config.runtime_settings import local_data_only_enabled
                if local_data_only_enabled():
                    logger.warning(f"🔒 [本地数据模式] DataFrame接口禁止外部API降级: {symbol}")
                    return pd.DataFrame()
            except Exception:
                pass
            for source in self.available_sources:
                if source == self.current_source:
                    continue
                try:
                    if source == ChinaDataSource.MONGODB:
                        from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                        adapter = get_mongodb_cache_adapter()
                        df = adapter.get_historical_data(symbol, start_date, end_date, period=period)
                    elif source == ChinaDataSource.TUSHARE:
                        from sinoquant.dataflows.providers.china.tushare import get_tushare_provider
                        provider = get_tushare_provider()
                        df = provider.get_daily_data(symbol, start_date, end_date)
                    elif source == ChinaDataSource.AKSHARE:
                        from sinoquant.dataflows.providers.china.akshare import get_akshare_provider
                        provider = get_akshare_provider()
                        df = provider.get_stock_data(symbol, start_date, end_date)
                    elif source == ChinaDataSource.BAOSTOCK:
                        from sinoquant.dataflows.providers.china.baostock import get_baostock_provider
                        provider = get_baostock_provider()
                        df = provider.get_stock_data(symbol, start_date, end_date)

                    if df is not None and not df.empty:
                        logger.info(f"✅ [DataFrame接口] 降级到 {source.value} 成功: {len(df)}条")
                        return self._standardize_dataframe(df)
                except Exception as e:
                    logger.warning(f"⚠️ [DataFrame接口] {source.value} 失败: {e}")
                    continue

            logger.error(f"❌ [DataFrame接口] 所有数据源都失败: {symbol}")
            return pd.DataFrame()

        except Exception as e:
            logger.error(f"❌ [DataFrame接口] 获取失败: {e}", exc_info=True)
            return pd.DataFrame()

    def get_china_stock_data_tushare(
        self, symbol: str, start_date: str, end_date: str
    ) -> str:
        """使用Tushare获取中国A股历史数据"""
        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        original_source = self.current_source
        self.current_source = ChinaDataSource.TUSHARE

        try:
            result = self._get_tushare_data(symbol, start_date, end_date)
            return result
        finally:
            self.current_source = original_source

    # ------------------------------------------------------------------
    # DataFrame 标准化
    # ------------------------------------------------------------------

    def _standardize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 DataFrame 列名和格式"""
        if df is None or df.empty:
            return pd.DataFrame()

        out = df.copy()

        colmap = {
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
            "Volume": "vol", "Amount": "amount", "symbol": "code", "Symbol": "code",
            "open": "open", "high": "high", "low": "low", "close": "close",
            "vol": "vol", "volume": "vol", "amount": "amount", "code": "code",
            "date": "date", "trade_date": "date",
            "日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
            "成交量": "vol", "成交额": "amount", "涨跌幅": "pct_change", "涨跌额": "change",
        }
        out = out.rename(columns={c: colmap.get(c, c) for c in out.columns})

        if "date" in out.columns:
            try:
                out["date"] = pd.to_datetime(out["date"])
                out = out.sort_values("date")
            except Exception:
                pass

        if "pct_change" not in out.columns and "close" in out.columns:
            out["pct_change"] = out["close"].pct_change() * 100.0

        return out

    # ------------------------------------------------------------------
    # 格式化（技术指标计算）
    # ------------------------------------------------------------------

    def _format_stock_data_response(
        self, data: pd.DataFrame, symbol: str, stock_name: str,
        start_date: str, end_date: str,
    ) -> str:
        """格式化股票数据响应（包含技术指标）"""
        try:
            original_data_count = len(data)
            logger.info(f"📊 [技术指标] 开始计算技术指标，原始数据: {original_data_count}条")

            if "date" in data.columns:
                data = data.sort_values("date")

            # 计算移动平均线
            data["ma5"] = data["close"].rolling(window=5, min_periods=1).mean()
            data["ma10"] = data["close"].rolling(window=10, min_periods=1).mean()
            data["ma20"] = data["close"].rolling(window=20, min_periods=1).mean()
            data["ma60"] = data["close"].rolling(window=60, min_periods=1).mean()

            # RSI（同花顺风格：使用中国式SMA）
            delta = data["close"].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            avg_gain6 = gain.ewm(com=5, adjust=True).mean()
            avg_loss6 = loss.ewm(com=5, adjust=True).mean()
            rs6 = avg_gain6 / avg_loss6.replace(0, np.nan)
            data["rsi6"] = 100 - (100 / (1 + rs6))

            avg_gain12 = gain.ewm(com=11, adjust=True).mean()
            avg_loss12 = loss.ewm(com=11, adjust=True).mean()
            rs12 = avg_gain12 / avg_loss12.replace(0, np.nan)
            data["rsi12"] = 100 - (100 / (1 + rs12))

            avg_gain24 = gain.ewm(com=23, adjust=True).mean()
            avg_loss24 = loss.ewm(com=23, adjust=True).mean()
            rs24 = avg_gain24 / avg_loss24.replace(0, np.nan)
            data["rsi24"] = 100 - (100 / (1 + rs24))

            # RSI14（国际标准，简单移动平均）
            gain14 = gain.rolling(window=14, min_periods=1).mean()
            loss14 = loss.rolling(window=14, min_periods=1).mean()
            rs14 = gain14 / loss14.replace(0, np.nan)
            data["rsi14"] = 100 - (100 / (1 + rs14))

            # MACD
            ema12 = data["close"].ewm(span=12, adjust=False).mean()
            ema26 = data["close"].ewm(span=26, adjust=False).mean()
            data["macd_dif"] = ema12 - ema26
            data["macd_dea"] = data["macd_dif"].ewm(span=9, adjust=False).mean()
            data["macd"] = (data["macd_dif"] - data["macd_dea"]) * 2

            # 布林带
            data["boll_mid"] = data["close"].rolling(window=20, min_periods=1).mean()
            std = data["close"].rolling(window=20, min_periods=1).std()
            data["boll_upper"] = data["boll_mid"] + 2 * std
            data["boll_lower"] = data["boll_mid"] - 2 * std

            logger.info(f"✅ [技术指标] 技术指标计算完成")

            display_rows = min(5, len(data))
            display_data = data.tail(display_rows)
            latest_data = data.iloc[-1]

            # 调试日志
            logger.info(f"🔍 [技术指标详情] ===== 最近{display_rows}个交易日数据 =====")
            for i, (idx, row) in enumerate(display_data.iterrows(), 1):
                logger.info(f"🔍 [技术指标详情] 第{i}天 ({row.get('date', 'N/A')}):")
                logger.info(f"   价格: 开={row.get('open', 0):.2f}, 高={row.get('high', 0):.2f}, 低={row.get('low', 0):.2f}, 收={row.get('close', 0):.2f}")
                logger.info(f"   MA: MA5={row.get('ma5', 0):.2f}, MA10={row.get('ma10', 0):.2f}, MA20={row.get('ma20', 0):.2f}, MA60={row.get('ma60', 0):.2f}")
                logger.info(f"   MACD: DIF={row.get('macd_dif', 0):.4f}, DEA={row.get('macd_dea', 0):.4f}, MACD={row.get('macd', 0):.4f}")
                logger.info(f"   RSI: RSI6={row.get('rsi6', 0):.2f}, RSI12={row.get('rsi12', 0):.2f}, RSI24={row.get('rsi24', 0):.2f} (同花顺风格)")
                logger.info(f"   RSI14: {row.get('rsi14', 0):.2f} (国际标准)")
                logger.info(f"   BOLL: 上={row.get('boll_upper', 0):.2f}, 中={row.get('boll_mid', 0):.2f}, 下={row.get('boll_lower', 0):.2f}")
            logger.info(f"🔍 [技术指标详情] ===== 数据详情结束 =====")

            latest_price = latest_data.get("close", 0)
            prev_close = data.iloc[-2].get("close", latest_price) if len(data) > 1 else latest_price
            change = latest_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close != 0 else 0

            result = f"📊 {stock_name}({symbol}) - 技术分析数据\n"
            result += f"数据期间: {start_date} 至 {end_date}\n"
            result += f"数据条数: {original_data_count}条 (展示最近{display_rows}个交易日)\n\n"
            result += f"💰 最新价格: ¥{latest_price:.2f}\n"
            result += f"📈 涨跌额: {change:+.2f} ({change_pct:+.2f}%)\n\n"

            # 移动平均线
            result += f"📊 移动平均线 (MA):\n"
            result += f"   MA5:  ¥{latest_data['ma5']:.2f}"
            result += " (价格在MA5上方 ↑)\n" if latest_price > latest_data["ma5"] else " (价格在MA5下方 ↓)\n"

            result += f"   MA10: ¥{latest_data['ma10']:.2f}"
            result += " (价格在MA10上方 ↑)\n" if latest_price > latest_data["ma10"] else " (价格在MA10下方 ↓)\n"

            result += f"   MA20: ¥{latest_data['ma20']:.2f}"
            result += " (价格在MA20上方 ↑)\n" if latest_price > latest_data["ma20"] else " (价格在MA20下方 ↓)\n"

            result += f"   MA60: ¥{latest_data['ma60']:.2f}"
            result += " (价格在MA60上方 ↑)\n\n" if latest_price > latest_data["ma60"] else " (价格在MA60下方 ↓)\n\n"

            # MACD
            result += f"📈 MACD指标:\n"
            result += f"   DIF:  {latest_data['macd_dif']:.3f}\n"
            result += f"   DEA:  {latest_data['macd_dea']:.3f}\n"
            result += f"   MACD: {latest_data['macd']:.3f}"
            result += " (多头 ↑)\n" if latest_data["macd"] > 0 else " (空头 ↓)\n"

            if len(data) > 1:
                prev_dif = data.iloc[-2]["macd_dif"]
                prev_dea = data.iloc[-2]["macd_dea"]
                curr_dif = latest_data["macd_dif"]
                curr_dea = latest_data["macd_dea"]
                if prev_dif <= prev_dea and curr_dif > curr_dea:
                    result += "   ⚠️ MACD金叉信号（DIF上穿DEA）\n\n"
                elif prev_dif >= prev_dea and curr_dif < curr_dea:
                    result += "   ⚠️ MACD死叉信号（DIF下穿DEA）\n\n"
                else:
                    result += "\n"
            else:
                result += "\n"

            # RSI
            rsi6 = latest_data["rsi6"]
            rsi12 = latest_data["rsi12"]
            rsi24 = latest_data["rsi24"]
            result += f"📉 RSI指标 (同花顺风格):\n"
            result += f"   RSI6:  {rsi6:.2f}"
            result += " (超买 ⚠️)\n" if rsi6 >= 80 else " (超卖 ⚠️)\n" if rsi6 <= 20 else "\n"
            result += f"   RSI12: {rsi12:.2f}"
            result += " (超买 ⚠️)\n" if rsi12 >= 80 else " (超卖 ⚠️)\n" if rsi12 <= 20 else "\n"
            result += f"   RSI24: {rsi24:.2f}"
            result += " (超买 ⚠️)\n" if rsi24 >= 80 else " (超卖 ⚠️)\n" if rsi24 <= 20 else "\n"

            if rsi6 > rsi12 > rsi24:
                result += "   趋势: 多头排列 ↑\n\n"
            elif rsi6 < rsi12 < rsi24:
                result += "   趋势: 空头排列 ↓\n\n"
            else:
                result += "   趋势: 震荡整理 ↔\n\n"

            # 布林带
            result += f"📊 布林带 (BOLL):\n"
            result += f"   上轨: ¥{latest_data['boll_upper']:.2f}\n"
            result += f"   中轨: ¥{latest_data['boll_mid']:.2f}\n"
            result += f"   下轨: ¥{latest_data['boll_lower']:.2f}\n"

            boll_position = (
                (latest_price - latest_data["boll_lower"])
                / (latest_data["boll_upper"] - latest_data["boll_lower"])
                * 100
            )
            result += f"   价格位置: {boll_position:.1f}%"
            if boll_position >= 80:
                result += " (接近上轨，可能超买 ⚠️)\n\n"
            elif boll_position <= 20:
                result += " (接近下轨，可能超卖 ⚠️)\n\n"
            else:
                result += " (中性区域)\n\n"

            # 价格统计
            result += f"📊 价格统计 (最近{display_rows}个交易日):\n"
            result += f"   最高价: ¥{display_data['high'].max():.2f}\n"
            result += f"   最低价: ¥{display_data['low'].min():.2f}\n"
            result += f"   平均价: ¥{display_data['close'].mean():.2f}\n"
            volume_value = self._get_volume_safely(display_data)
            result += f"   平均成交量: {volume_value:,.0f}股\n"

            return result

        except Exception as e:
            logger.error(f"❌ 格式化数据响应失败: {e}", exc_info=True)
            return f"❌ 格式化{symbol}数据失败: {e}"

    # ------------------------------------------------------------------
    # 各数据源获取方法
    # ------------------------------------------------------------------

    def _get_mongodb_data(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> tuple:
        """从MongoDB获取多周期数据 - 包含技术指标计算"""
        logger.debug(f"📊 [MongoDB] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")

        try:
            from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()

            df = adapter.get_historical_data(symbol, start_date, end_date, period=period)

            if df is not None and not df.empty:
                logger.info(f"✅ [数据来源: MongoDB缓存] 成功获取{period}数据: {symbol} ({len(df)}条记录)")

                stock_name = f"股票{symbol}"
                if "name" in df.columns and not df["name"].empty:
                    stock_name = df["name"].iloc[0]

                result = self._format_stock_data_response(df, symbol, stock_name, start_date, end_date)
                logger.info(f"✅ [MongoDB] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
                return result, "mongodb"
            else:
                logger.info(f"🔄 [MongoDB] 未找到{period}数据: {symbol}，开始尝试备用数据源")
                return self._try_fallback_sources(symbol, start_date, end_date, period)

        except Exception as e:
            logger.error(f"❌ [数据来源: MongoDB异常] 获取{period}数据失败: {symbol}, 错误: {e}")
            return self._try_fallback_sources(symbol, start_date, end_date, period)

    def _get_tushare_data(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> str:
        """使用Tushare获取多周期数据 - 使用provider + 统一缓存"""
        logger.debug(f"📊 [Tushare] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")

        logger.info(f"🔍 [股票代码追踪] _get_tushare_data 接收到的股票代码: '{symbol}' (类型: {type(symbol)})")
        logger.info(f"🔍 [股票代码追踪] 股票代码长度: {len(str(symbol))}")
        logger.info(f"🔍 [股票代码追踪] 股票代码字符: {list(str(symbol))}")
        logger.info(f"🔍 [DataSourceManager详细日志] _get_tushare_data 开始执行")
        logger.info(f"🔍 [DataSourceManager详细日志] 当前数据源: {self.current_source.value}")

        start_time = time.time()
        try:
            # 1. 先尝试从缓存获取
            cached_data = self._get_cached_data(symbol, start_date, end_date, max_age_hours=24)
            if cached_data is not None and not cached_data.empty:
                logger.info(f"✅ [缓存命中] 从缓存获取{symbol}数据")
                provider = self._get_tushare_adapter()
                if provider:
                    stock_info = self._run_async(provider.get_stock_basic_info(symbol))
                    stock_name = stock_info.get("name", f"股票{symbol}") if stock_info else f"股票{symbol}"
                else:
                    stock_name = f"股票{symbol}"
                return self._format_stock_data_response(cached_data, symbol, stock_name, start_date, end_date)

            # 2. 缓存未命中，从provider获取
            logger.info(f"🔍 [股票代码追踪] 调用 tushare_provider，传入参数: symbol='{symbol}'")
            logger.info(f"🔍 [DataSourceManager详细日志] 开始调用tushare_provider...")

            provider = self._get_tushare_adapter()
            if not provider:
                return f"❌ Tushare提供器不可用"

            data = self._run_async(provider.get_historical_data(symbol, start_date, end_date))

            if data is not None and not data.empty:
                self._save_to_cache(symbol, data, start_date, end_date)
                stock_info = self._run_async(provider.get_stock_basic_info(symbol))
                stock_name = stock_info.get("name", f"股票{symbol}") if stock_info else f"股票{symbol}"

                result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)

                duration = time.time() - start_time
                logger.info(f"🔍 [DataSourceManager详细日志] 调用完成，耗时: {duration:.3f}秒")
                logger.info(f"🔍 [股票代码追踪] 返回结果前200字符: {result[:200] if result else 'None'}")
                logger.debug(f"📊 [Tushare] 调用完成: 耗时={duration:.2f}s, 结果长度={len(result) if result else 0}")

                return result
            else:
                result = f"❌ 未获取到{symbol}的有效数据"
                duration = time.time() - start_time
                logger.warning(f"⚠️ [Tushare] 未获取到数据，耗时={duration:.2f}s")
                return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [Tushare] 调用失败: {e}, 耗时={duration:.2f}s", exc_info=True)
            logger.error(f"❌ [DataSourceManager详细日志] 异常类型: {type(e).__name__}")
            logger.error(f"❌ [DataSourceManager详细日志] 异常信息: {str(e)}")
            import traceback
            logger.error(f"❌ [DataSourceManager详细日志] 异常堆栈: {traceback.format_exc()}")
            raise

    def _get_akshare_data(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> str:
        """使用AKShare获取多周期数据 - 包含技术指标计算"""
        logger.debug(f"📊 [AKShare] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")

        start_time = time.time()
        try:
            from sinoquant.dataflows.providers.china.akshare import get_akshare_provider
            provider = get_akshare_provider()

            data = self._run_async(provider.get_historical_data(symbol, start_date, end_date, period))

            duration = time.time() - start_time

            if data is not None and not data.empty:
                stock_info = self._run_async(provider.get_stock_basic_info(symbol))
                stock_name = stock_info.get("name", f"股票{symbol}") if stock_info else f"股票{symbol}"

                result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)

                logger.debug(f"📊 [AKShare] 调用成功: 耗时={duration:.2f}s, 数据条数={len(data)}, 结果长度={len(result)}")
                logger.info(f"✅ [AKShare] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
                return result
            else:
                result = f"❌ 未能获取{symbol}的股票数据"
                logger.warning(f"⚠️ [AKShare] 数据为空: 耗时={duration:.2f}s")
                return result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [AKShare] 调用失败: {e}, 耗时={duration:.2f}s", exc_info=True)
            return f"❌ AKShare获取{symbol}数据失败: {e}"

    def _get_baostock_data(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> str:
        """使用BaoStock获取多周期数据 - 包含技术指标计算"""
        from sinoquant.dataflows.providers.china.baostock import get_baostock_provider
        provider = get_baostock_provider()

        data = self._run_async(provider.get_historical_data(symbol, start_date, end_date, period))

        if data is not None and not data.empty:
            stock_info = self._run_async(provider.get_stock_basic_info(symbol))
            stock_name = stock_info.get("name", f"股票{symbol}") if stock_info else f"股票{symbol}"

            result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)
            logger.info(f"✅ [BaoStock] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
            return result
        else:
            return f"❌ 未能获取{symbol}的股票数据"

    # ------------------------------------------------------------------
    # 降级
    # ------------------------------------------------------------------

    def _try_fallback_sources(
        self, symbol: str, start_date: str, end_date: str, period: str = "daily"
    ) -> tuple:
        """
        尝试备用数据源 - 避免递归调用

        Returns:
            tuple[str, str | None]: (结果字符串, 实际使用的数据源名称)
        """
        try:
            from sinoquant.config.runtime_settings import local_data_only_enabled
            if local_data_only_enabled():
                logger.warning(
                    f"🔒 [本地数据模式] 禁止外部API降级，{symbol}的{period}数据在MongoDB中不可用"
                )
                return (
                    f"❌ [本地数据模式] MongoDB中没有{symbol}的{period}数据。"
                    f"请先使用数据同步功能下载数据，或关闭TA_LOCAL_DATA_ONLY模式。",
                    None
                )
        except Exception:
            pass

        ChinaDataSource = self.ChinaDataSource  # type: ignore[attr-defined]

        logger.info(f"🔄 [{self.current_source.value}] 失败，尝试备用数据源获取{period}数据: {symbol}")

        fallback_order = self._get_data_source_priority_order(symbol)

        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 [备用数据源] 尝试 {source.value} 获取{period}数据: {symbol}")

                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_data(symbol, start_date, end_date, period)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_data(symbol, start_date, end_date, period)
                    elif source == ChinaDataSource.BAOSTOCK:
                        result = self._get_baostock_data(symbol, start_date, end_date, period)
                    else:
                        logger.warning(f"⚠️ 未知数据源: {source.value}")
                        continue

                    if "❌" not in result:
                        logger.info(f"✅ [备用数据源-{source.value}] 成功获取{period}数据: {symbol}")
                        return result, source.value
                    else:
                        logger.warning(f"⚠️ [备用数据源-{source.value}] 返回错误结果: {symbol}")

                except Exception as e:
                    logger.error(f"❌ [备用数据源-{source.value}] 获取失败: {symbol}, 错误: {e}")
                    continue

        logger.error(f"❌ [所有数据源失败] 无法获取{period}数据: {symbol}")
        return f"❌ 所有数据源都无法获取{symbol}的{period}数据", None

    # ------------------------------------------------------------------
    # 异步辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro):
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
