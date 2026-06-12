"""
AKShare data source adapter
"""
from typing import Optional, Dict
import logging
from datetime import datetime, timedelta
import pandas as pd

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)


class AKShareAdapter(DataSourceAdapter):
    """AKShare数据源适配器"""

    def __init__(self):
        super().__init__()  # 调用父类初始化

    @property
    def name(self) -> str:
        return "akshare"

    def _get_default_priority(self) -> int:
        return 2  # 数字越大优先级越高

    def is_available(self) -> bool:
        """检查AKShare是否可用"""
        try:
            import akshare as ak  # noqa: F401
            return True
        except ImportError:
            return False

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """获取股票列表（使用 AKShare 的 stock_info_a_code_name 接口获取真实股票名称）"""
        if not self.is_available():
            return None
        try:
            import akshare as ak
            logger.info("AKShare: Fetching stock list with real names from stock_info_a_code_name()...")

            # 使用 AKShare 的 stock_info_a_code_name 接口获取股票代码和名称
            df = ak.stock_info_a_code_name()

            if df is None or df.empty:
                logger.warning("AKShare: stock_info_a_code_name() returned empty data")
                return None

            # 标准化列名（AKShare 返回的列名可能是中文）
            # 通常返回的列：code（代码）、name（名称）
            df = df.rename(columns={
                'code': 'symbol',
                '代码': 'symbol',
                'name': 'name',
                '名称': 'name'
            })

            # 确保有必需的列
            if 'symbol' not in df.columns or 'name' not in df.columns:
                logger.error(f"AKShare: Unexpected column names: {df.columns.tolist()}")
                return None

            # 生成 ts_code 和其他字段
            def generate_ts_code(code: str) -> str:
                """根据股票代码生成 ts_code"""
                if not code:
                    return ""
                code = str(code).zfill(6)
                if code.startswith(('60', '68', '90')):
                    return f"{code}.SH"
                elif code.startswith(('00', '30', '20')):
                    return f"{code}.SZ"
                elif code.startswith(('8', '4')):
                    return f"{code}.BJ"
                else:
                    return f"{code}.SZ"  # 默认深圳

            def get_market(code: str) -> str:
                """根据股票代码判断市场"""
                if not code:
                    return ""
                code = str(code).zfill(6)
                if code.startswith('000'):
                    return '主板'
                elif code.startswith('002'):
                    return '中小板'
                elif code.startswith('300'):
                    return '创业板'
                elif code.startswith('60'):
                    return '主板'
                elif code.startswith('688'):
                    return '科创板'
                elif code.startswith('8'):
                    return '北交所'
                elif code.startswith('4'):
                    return '新三板'
                else:
                    return '未知'

            # 添加 ts_code 和 market 字段
            df['ts_code'] = df['symbol'].apply(generate_ts_code)
            df['market'] = df['symbol'].apply(get_market)
            df['area'] = ''
            df['industry'] = ''
            df['list_date'] = ''

            logger.info(f"AKShare: Successfully fetched {len(df)} stocks with real names")
            return df

        except Exception as e:
            logger.error(f"AKShare: Failed to fetch stock list: {e}")
            return None

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取每日基础财务数据（快速版）"""
        if not self.is_available():
            return None
        try:
            import akshare as ak  # noqa: F401
            logger.info(f"AKShare: Attempting to get basic financial data for {trade_date}")

            stock_df = self.get_stock_list()
            if stock_df is None or stock_df.empty:
                logger.warning("AKShare: No stock list available")
                return None

            max_stocks = 10
            stock_list = stock_df.head(max_stocks)

            basic_data = []
            processed_count = 0
            import time
            start_time = time.time()
            timeout_seconds = 30

            for _, stock in stock_list.iterrows():
                if time.time() - start_time > timeout_seconds:
                    logger.warning(f"AKShare: Timeout reached, processed {processed_count} stocks")
                    break
                try:
                    symbol = stock.get('symbol', '')
                    name = stock.get('name', '')
                    ts_code = stock.get('ts_code', '')
                    if not symbol:
                        continue
                    info_data = ak.stock_individual_info_em(symbol=symbol)
                    if info_data is not None and not info_data.empty:
                        info_dict = {}
                        for _, row in info_data.iterrows():
                            item = row.get('item', '')
                            value = row.get('value', '')
                            info_dict[item] = value
                        latest_price = self._safe_float(info_dict.get('最新', 0))
                        # 🔥 AKShare 的"总市值"单位是万元，需要转换为亿元（与 Tushare 一致）
                        total_mv_wan = self._safe_float(info_dict.get('总市值', 0))  # 万元
                        total_mv_yi = total_mv_wan / 10000 if total_mv_wan else None  # 转换为亿元
                        basic_data.append({
                            'ts_code': ts_code,
                            'trade_date': trade_date,
                            'name': name,
                            'close': latest_price,
                            'total_mv': total_mv_yi,  # 亿元（与 Tushare 一致）
                            'turnover_rate': None,
                            'pe': None,
                            'pb': None,
                        })
                        processed_count += 1
                        if processed_count % 5 == 0:
                            logger.debug(f"AKShare: Processed {processed_count} stocks in {time.time() - start_time:.1f}s")
                except Exception as e:
                    logger.debug(f"AKShare: Failed to get data for {symbol}: {e}")
                    continue

            if basic_data:
                df = pd.DataFrame(basic_data)
                logger.info(f"AKShare: Successfully fetched basic data for {trade_date}, {len(df)} records")
                return df
            else:
                logger.warning("AKShare: No basic data collected")
                return None
        except Exception as e:
            logger.error(f"AKShare: Failed to fetch basic data for {trade_date}: {e}")
            return None

    def _safe_float(self, value) -> Optional[float]:
        try:
            if value is None or value == '' or value == 'None':
                return None
            return float(value)
        except (ValueError, TypeError):
            return None


    def _parse_realtime_quotes(self, df, source: str) -> Dict[str, Dict[str, Optional[float]]]:
        """统一解析实时行情 DataFrame"""
        if df is None or getattr(df, "empty", True):
            return {}

        # 列名兼容（两个接口的列名可能不同）
        code_col = next((c for c in ["代码", "code", "symbol", "股票代码"] if c in df.columns), None)
        price_col = next((c for c in ["最新价", "现价", "最新价(元)", "price", "最新", "trade"] if c in df.columns), None)
        pct_col = next((c for c in ["涨跌幅", "涨跌幅(%)", "涨幅", "pct_chg", "changepercent"] if c in df.columns), None)
        amount_col = next((c for c in ["成交额", "成交额(元)", "amount", "成交额(万元)", "amount(万元)"] if c in df.columns), None)
        open_col = next((c for c in ["今开", "开盘", "open", "今开(元)"] if c in df.columns), None)
        high_col = next((c for c in ["最高", "high"] if c in df.columns), None)
        low_col = next((c for c in ["最低", "low"] if c in df.columns), None)
        pre_close_col = next((c for c in ["昨收", "昨收(元)", "pre_close", "昨收价", "settlement"] if c in df.columns), None)
        volume_col = next((c for c in ["成交量", "成交量(手)", "volume", "成交量(股)", "vol"] if c in df.columns), None)

        if not code_col or not price_col:
            logger.error(f"AKShare {source} 缺少必要列: code={code_col}, price={price_col}, columns={list(df.columns)}")
            return {}

        result: Dict[str, Dict[str, Optional[float]]] = {}
        for _, row in df.iterrows():  # type: ignore
            code_raw = row.get(code_col)
            if not code_raw:
                continue
            code_str = str(code_raw).strip()

            if len(code_str) > 6:
                code_str = ''.join(filter(str.isdigit, code_str))

            if code_str.isdigit():
                code_clean = code_str.lstrip('0') or '0'
                code = code_clean.zfill(6)
            else:
                code_digits = ''.join(filter(str.isdigit, code_str))
                if code_digits:
                    code = code_digits.zfill(6)
                else:
                    continue

            close = self._safe_float(row.get(price_col))
            pct = self._safe_float(row.get(pct_col)) if pct_col else None
            amt = self._safe_float(row.get(amount_col)) if amount_col else None
            op = self._safe_float(row.get(open_col)) if open_col else None
            hi = self._safe_float(row.get(high_col)) if high_col else None
            lo = self._safe_float(row.get(low_col)) if low_col else None
            pre = self._safe_float(row.get(pre_close_col)) if pre_close_col else None
            vol = self._safe_float(row.get(volume_col)) if volume_col else None

            result[code] = {
                "close": close,
                "pct_chg": pct,
                "amount": amt,
                "volume": vol,
                "open": op,
                "high": hi,
                "low": lo,
                "pre_close": pre
            }

        logger.info(f"AKShare {source}: 获取到 {len(result)} 只股票的实时行情")
        return result

    def get_realtime_quotes(self, source: str = "eastmoney"):
        """
        获取全市场实时快照，返回以6位代码为键的字典
        东方财富失败时自动降级到新浪

        Args:
            source: 数据源选择，"eastmoney"（东方财富）或 "sina"（新浪财经）

        Returns:
            Dict[str, Dict]: {code: {close, pct_chg, amount, ...}}
        """
        if not self.is_available():
            return None

        import akshare as ak  # type: ignore

        # 依次尝试：优先请求的源 → 另一个源
        sources = ["eastmoney", "sina"] if source == "eastmoney" else [source, "eastmoney", "sina"]
        for src in sources:
            try:
                if src == "sina":
                    df = ak.stock_zh_a_spot()
                    logger.info("使用 AKShare 新浪财经接口获取实时行情")
                else:
                    df = ak.stock_zh_a_spot_em()
                    logger.info("使用 AKShare 东方财富接口获取实时行情")

                result = self._parse_realtime_quotes(df, src)
                if result:
                    return result
                else:
                    logger.warning(f"AKShare {src} 返回空数据，尝试下一个数据源")
            except Exception as e:
                logger.warning(f"获取AKShare {src} 实时快照失败: {e}，尝试下一个数据源")
                continue

        return None

    def _get_tx_symbol(self, code6: str) -> str:
        """将6位代码转为腾讯格式（如 000001 -> sz000001）"""
        if code6.startswith(('60', '68', '90')):
            return f"sh{code6}"
        else:
            return f"sz{code6}"

    def _parse_kline_items(self, df, limit: int) -> list:
        """统一解析 K 线 DataFrame 为 list[dict]"""
        if df is None or getattr(df, 'empty', True):
            return []
        df = df.tail(limit)
        items = []
        for _, row in df.iterrows():
            items.append({
                "time": str(row.get('日期') or row.get('date') or ''),
                "open": self._safe_float(row.get('开盘') or row.get('open')),
                "high": self._safe_float(row.get('最高') or row.get('high')),
                "low": self._safe_float(row.get('最低') or row.get('low')),
                "close": self._safe_float(row.get('收盘') or row.get('close')),
                "volume": self._safe_float(row.get('成交量') or row.get('volume')),
                "amount": self._safe_float(row.get('成交额') or row.get('amount')),
            })
        return items

    def get_kline(self, code: str, period: str = "day", limit: int = 120, adj: Optional[str] = None):
        """AKShare K-line. 自动判断指数/个股，走不同接口。东方财富失败时降级到腾讯。"""
        from app.utils.code_utils import classify_code

        is_index, ak_code, _ = classify_code(code)
        if is_index:
            return self._get_index_kline(ak_code, limit)

        if not self.is_available():
            return None

        import akshare as ak
        code6 = str(code).zfill(6)

        # 日/周/月 K 线：先尝试东方财富，失败降级到腾讯
        if period in ("day", "week", "month"):
            # 1. 尝试东方财富
            try:
                period_map = {"day": "daily", "week": "weekly", "month": "monthly"}
                adjust_map = {None: "", "qfq": "qfq", "hfq": "hfq"}
                df = ak.stock_zh_a_hist(symbol=code6, period=period_map[period], adjust=adjust_map.get(adj, ""))
                items = self._parse_kline_items(df, limit)
                if items:
                    return items
            except Exception as e:
                logger.warning(f"AKShare stock_zh_a_hist failed ({code6}), falling back to TX: {e}")

            # 2. 降级到腾讯（仅支持日线）
            if period == "day":
                try:
                    tx_code = self._get_tx_symbol(code6)
                    df = ak.stock_zh_a_hist_tx(symbol=tx_code, adjust=adj or "")
                    items = self._parse_kline_items(df, limit)
                    if items:
                        return items
                except Exception as e:
                    logger.warning(f"AKShare stock_zh_a_hist_tx failed ({code6}): {e}")

            return None

        else:
            # 分钟 K 线：暂仅东方财富
            per_map = {"5m": "5", "15m": "15", "30m": "30", "60m": "60"}
            if period not in per_map:
                return None
            try:
                df = ak.stock_zh_a_minute(symbol=code6, period=per_map[period], adjust=adj if adj in ("qfq", "hfq") else "")
                items = self._parse_kline_items(df, limit)
                return items if items else None
            except Exception as e:
                logger.error(f"AKShare get_kline minute failed ({code6}): {e}")
                return None

    def _get_index_kline(self, ak_code: str, limit: int = 120):
        """获取指数日K线数据，东方财富失败降级到腾讯"""
        if not self.is_available():
            return None
        import akshare as ak

        # 1. 尝试东方财富
        try:
            df = ak.stock_zh_index_daily_em(symbol=ak_code)
            items = self._parse_kline_items(df, limit)
            if items:
                return items
        except Exception as e:
            logger.warning(f"AKShare stock_zh_index_daily_em failed ({ak_code}), falling back to TX: {e}")

        # 2. 降级到腾讯
        try:
            df = ak.stock_zh_a_hist_tx(symbol=ak_code)
            items = self._parse_kline_items(df, limit)
            if items:
                return items
        except Exception as e:
            logger.warning(f"AKShare stock_zh_a_hist_tx failed ({ak_code}): {e}")

        return None

    def get_index_spot_quotes(self, source: str = "eastmoney") -> Optional[Dict[str, Dict]]:
        """获取全市场指数实时行情，支持 eastmoney / sina spot / sina hq 降级"""
        if not self.is_available():
            return None

        import akshare as ak

        # 依次尝试 eastmoney → sina spot → sina hq
        for src in ["eastmoney", "sina_spot", "sina_hq"]:
            try:
                if src == "eastmoney":
                    df = ak.stock_zh_index_spot_em()
                elif src == "sina_spot":
                    df = ak.stock_zh_index_spot_sina()
                else:
                    # sina_hq: 直接用 hq.sinajs.cn 获取主要指数，不会被封
                    return self._get_index_quotes_sina_hq()

                if df is None or getattr(df, "empty", True):
                    continue

                code_col = next((c for c in ["代码", "code"] if c in df.columns), None)
                name_col = next((c for c in ["名称", "指数名称"] if c in df.columns), None)
                price_col = next((c for c in ["最新价", "现价", "收盘价", "price"] if c in df.columns), None)
                pct_col = next((c for c in ["涨跌幅", "涨跌幅(%)"] if c in df.columns), None)
                amount_col = next((c for c in ["成交额", "成交额(元)", "amount"] if c in df.columns), None)
                open_col = next((c for c in ["今开", "开盘", "open"] if c in df.columns), None)
                high_col = next((c for c in ["最高", "high"] if c in df.columns), None)
                low_col = next((c for c in ["最低", "low"] if c in df.columns), None)
                pre_close_col = next((c for c in ["昨收", "昨收(元)", "pre_close"] if c in df.columns), None)
                volume_col = next((c for c in ["成交量", "volume"] if c in df.columns), None)

                if not code_col or not price_col:
                    logger.error(f"AKShare index spot ({src}): 缺少必要列 code={code_col}, price={price_col}")
                    continue

                result: Dict[str, Dict] = {}
                for _, row in df.iterrows():
                    code_raw = str(row.get(code_col, "")).strip().lower()
                    if not code_raw or len(code_raw) < 3 or not code_raw[2:].isdigit():
                        continue
                    result[code_raw] = {
                        "code": code_raw,
                        "name": str(row.get(name_col, "")) if name_col else "",
                        "close": self._safe_float(row.get(price_col)),
                        "pct_chg": self._safe_float(row.get(pct_col)),
                        "amount": self._safe_float(row.get(amount_col)),
                        "open": self._safe_float(row.get(open_col)),
                        "high": self._safe_float(row.get(high_col)),
                        "low": self._safe_float(row.get(low_col)),
                        "pre_close": self._safe_float(row.get(pre_close_col)),
                        "volume": self._safe_float(row.get(volume_col)),
                    }
                if result:
                    logger.info(f"AKShare: 通过 {src} 获取到 {len(result)} 个指数实时行情")
                    return result
            except Exception as e:
                logger.warning(f"AKShare get_index_spot_quotes ({src}) failed: {e}")
                continue

        return None

    def _get_index_quotes_sina_hq(self) -> Optional[Dict[str, Dict]]:
        """通过新浪 hq.sinajs.cn 获取主要指数行情（不会被封）"""
        try:
            import requests as req
            # 主要指数列表（去重）
            indices = [
                "sh000001",  # 上证指数
                "sh000002",  # 上证A股
                "sh000010",  # 上证180
                "sh000012",  # 上证50
                "sh000015",  # 上证红利
                "sh000016",  # 上证50
                "sh000300",  # 沪深300
                "sh000688",  # 科创50
                "sh000852",  # 中证1000
                "sh000903",  # 中证100
                "sh000905",  # 中证500
                "sh000906",  # 中证800
                "sz399001",  # 深证成指
                "sz399004",  # 深证100R
                "sz399005",  # 中小100
                "sz399006",  # 创业板指
                "sz399303",  # 国证2000
                "sz399324",  # 深证主板50
                "sz399432",  # 中证传媒
                "sz399673",  # 创业板50
            ]
            url = "https://hq.sinajs.cn/list=" + ",".join(indices)
            r = req.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
            if r.status_code != 200:
                return None

            result: Dict[str, Dict] = {}
            for line in r.text.strip().split("\n"):
                if not line.startswith("var hq_str_"):
                    continue
                code = line.split("var hq_str_")[1].split("=")[0]
                data_str = line.split('"')[1]
                if not data_str or data_str == "":
                    continue
                parts = data_str.split(",")
                if len(parts) < 10:
                    continue
                result[code] = {
                    "code": code,
                    "name": parts[0],
                    "open": self._safe_float(parts[1]),
                    "pre_close": self._safe_float(parts[2]),
                    "close": self._safe_float(parts[3]),
                    "high": self._safe_float(parts[4]),
                    "low": self._safe_float(parts[5]),
                    "volume": self._safe_float(parts[8]),
                    "amount": self._safe_float(parts[9]),
                    "pct_chg": None,  # hq.sinajs.cn 不直接提供涨跌幅
                }
                # 计算涨跌幅
                pre = result[code].get("pre_close")
                close = result[code].get("close")
                if pre and close and pre > 0:
                    result[code]["pct_chg"] = round((close - pre) / pre * 100, 3)

            if result:
                logger.info(f"AKShare: 通过 sina_hq 获取到 {len(result)} 个指数行情")
            return result if result else None
        except Exception as e:
            logger.warning(f"AKShare _get_index_quotes_sina_hq failed: {e}")
            return None

    def get_news(self, code: str, days: int = 2, limit: int = 50, include_announcements: bool = True):
        """AKShare-based news/announcements fallback"""
        if not self.is_available():
            return None
        try:
            import akshare as ak
            code6 = str(code).zfill(6)
            items = []
            # news
            try:
                dfn = ak.stock_news_em(symbol=code6)
                if dfn is not None and not dfn.empty:
                    for _, row in dfn.head(limit).iterrows():
                        items.append({
                            # AkShare 将字段标准化为中文列名：新闻标题 / 文章来源 / 发布时间 / 新闻链接
                            "title": str(row.get('新闻标题') or row.get('标题') or row.get('title') or ''),
                            "source": str(row.get('文章来源') or row.get('来源') or row.get('source') or 'akshare'),
                            "time": str(row.get('发布时间') or row.get('time') or ''),
                            "url": str(row.get('新闻链接') or row.get('url') or ''),
                            "type": "news",
                        })
            except Exception:
                pass
            # announcements
            try:
                if include_announcements:
                    dfa = ak.stock_announcement_em(symbol=code6)
                    if dfa is not None and not dfa.empty:
                        for _, row in dfa.head(max(0, limit - len(items))).iterrows():
                            items.append({
                                "title": str(row.get('公告标题') or row.get('title') or ''),
                                "source": "akshare",
                                "time": str(row.get('公告时间') or row.get('time') or ''),
                                "url": str(row.get('公告链接') or row.get('url') or ''),
                                "type": "announcement",
                            })
            except Exception:
                pass
            return items if items else None
        except Exception as e:
            logger.error(f"AKShare get_news failed: {e}")
            return None

    def find_latest_trade_date(self) -> Optional[str]:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        logger.info(f"AKShare: Using yesterday as trade date: {yesterday}")
        return yesterday

