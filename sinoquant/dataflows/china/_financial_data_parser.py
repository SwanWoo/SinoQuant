"""
财务数据解析 mixin

包含从 MongoDB、AKShare、Tushare 解析财务数据并转换为标准化指标的方法。
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from sinoquant.config.runtime_settings import get_timezone_name, use_app_cache_enabled
from sinoquant.utils.logging_manager import get_logger

logger = get_logger('agents')


class FinancialDataParserMixin:
    """财务数据解析 mixin — 提供从多种数据源解析财务指标的能力"""

    # ------------------------------------------------------------------
    # 公开入口：将 MongoDB 财务数据转换为基本面分析格式字符串
    # ------------------------------------------------------------------

    def _format_financial_data_to_fundamentals(self, financial_data: dict, symbol: str) -> str:
        """将MongoDB财务数据转换为基本面分析格式"""
        try:
            # 提取关键财务指标
            revenue = financial_data.get('total_revenue', 'N/A')
            net_profit = financial_data.get('net_profit', 'N/A')
            total_assets = financial_data.get('total_assets', 'N/A')
            total_equity = financial_data.get('total_equity', 'N/A')
            report_period = financial_data.get('report_period', 'N/A')

            # 格式化数值（如果是数字则添加千分位，否则显示原值）
            def format_number(value):
                if isinstance(value, (int, float)):
                    return f"{value:,.2f}"
                return str(value)

            revenue_str = format_number(revenue)
            net_profit_str = format_number(net_profit)
            total_assets_str = format_number(total_assets)
            total_equity_str = format_number(total_equity)

            # 计算财务比率
            roe = 'N/A'
            if isinstance(net_profit, (int, float)) and isinstance(total_equity, (int, float)) and total_equity != 0:
                roe = f"{(net_profit / total_equity * 100):.2f}%"

            roa = 'N/A'
            if isinstance(net_profit, (int, float)) and isinstance(total_assets, (int, float)) and total_assets != 0:
                roa = f"{(net_profit / total_assets * 100):.2f}%"

            # 格式化输出
            fundamentals_report = f"""
# {symbol} 基本面数据分析

## 📊 财务概况
- **报告期**: {report_period}
- **营业收入**: {revenue_str} 元
- **净利润**: {net_profit_str} 元
- **总资产**: {total_assets_str} 元
- **股东权益**: {total_equity_str} 元

## 📈 财务比率
- **净资产收益率(ROE)**: {roe}
- **总资产收益率(ROA)**: {roa}

## 📝 数据说明
- 数据来源: MongoDB财务数据库
- 更新时间: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
- 数据类型: 同步财务数据
"""
            return fundamentals_report.strip()

        except Exception as e:
            logger.warning(f"⚠️ 格式化财务数据失败: {e}")
            return f"# {symbol} 基本面数据\n\n❌ 数据格式化失败: {str(e)}"

    # ------------------------------------------------------------------
    # 获取真实财务指标（调度器）
    # ------------------------------------------------------------------

    def _estimate_financial_metrics(self, symbol: str, current_price: str) -> dict:
        """获取真实财务指标（从 MongoDB、AKShare、Tushare 获取，失败则抛出异常）"""

        # 提取价格数值
        try:
            price_value = float(current_price.replace('¥', '').replace(',', ''))
        except:
            price_value = 10.0  # 默认值

        # 尝试获取真实财务数据
        real_metrics = self._get_real_financial_metrics(symbol, price_value)
        if real_metrics:
            logger.info(f"✅ 使用真实财务数据: {symbol}")
            return real_metrics

        # 如果无法获取真实数据，抛出异常
        error_msg = f"无法获取股票 {symbol} 的财务数据。已尝试所有数据源（MongoDB、AKShare、Tushare）均失败。"
        logger.error(f"❌ {error_msg}")
        raise ValueError(error_msg)

    def _get_real_financial_metrics(self, symbol: str, price_value: float) -> dict:
        """获取真实财务指标 - 优先使用数据库缓存，再使用API"""
        try:
            # 🔥 优先从 market_quotes 获取实时股价，替换传入的 price_value
            from sinoquant.config.database_manager import get_database_manager
            db_manager = get_database_manager()
            db_client = None

            if db_manager.is_mongodb_available():
                try:
                    db_client = db_manager.get_mongodb_client()
                    db = db_client['sinoquant']

                    # 标准化股票代码为6位
                    code6 = symbol.replace('.SH', '').replace('.SZ', '').zfill(6)

                    # 从 market_quotes 获取实时股价
                    quote = db.market_quotes.find_one({"code": code6})
                    if quote and quote.get("close"):
                        realtime_price = float(quote.get("close"))
                        logger.info(f"✅ 从 market_quotes 获取实时股价: {code6} = {realtime_price}元 (原价格: {price_value}元)")
                        price_value = realtime_price
                    else:
                        logger.info(f"⚠️ market_quotes 中未找到{code6}的实时股价，使用传入价格: {price_value}元")
                except Exception as e:
                    logger.warning(f"⚠️ 从 market_quotes 获取实时股价失败: {e}，使用传入价格: {price_value}元")
            else:
                logger.info(f"⚠️ MongoDB 不可用，使用传入价格: {price_value}元")

            # 第一优先级：从 MongoDB stock_financial_data 集合获取标准化财务数据
            if use_app_cache_enabled(False):
                logger.info(f"🔍 优先从 MongoDB stock_financial_data 集合获取{symbol}财务数据")

                # 直接从 MongoDB 获取标准化的财务数据
                from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                adapter = get_mongodb_cache_adapter()
                financial_data = adapter.get_financial_data(symbol)

                if financial_data:
                    logger.info(f"✅ [财务数据] 从 stock_financial_data 集合获取{symbol}财务数据")
                    # 解析 MongoDB 标准化的财务数据
                    metrics = self._parse_mongodb_financial_data(financial_data, price_value)
                    if metrics:
                        logger.info(f"✅ MongoDB 财务数据解析成功，返回指标")
                        return metrics
                    else:
                        logger.warning(f"⚠️ MongoDB 财务数据解析失败")
                else:
                    logger.info(f"🔄 MongoDB 未找到{symbol}财务数据，尝试从 AKShare API 获取")
            else:
                logger.info(f"🔄 数据库缓存未启用，直接从AKShare API获取{symbol}财务数据")

            # 第二优先级：从AKShare API获取
            from ..providers.china.akshare import get_akshare_provider

            akshare_provider = get_akshare_provider()

            if akshare_provider.connected:
                # AKShare的get_financial_data是异步方法，需要使用asyncio运行
                loop = asyncio.get_event_loop()
                financial_data = loop.run_until_complete(akshare_provider.get_financial_data(symbol))

                if financial_data and any(not v.empty if hasattr(v, 'empty') else bool(v) for v in financial_data.values()):
                    logger.info(f"✅ AKShare财务数据获取成功: {symbol}")
                    # 获取股票基本信息（也是异步方法）
                    stock_info = loop.run_until_complete(akshare_provider.get_stock_basic_info(symbol))

                    # 解析AKShare财务数据
                    logger.debug(f"🔧 调用AKShare解析函数，股价: {price_value}")
                    metrics = self._parse_akshare_financial_data(financial_data, stock_info, price_value)
                    logger.debug(f"🔧 AKShare解析结果: {metrics}")
                    if metrics:
                        logger.info(f"✅ AKShare解析成功，返回指标")
                        # 缓存原始财务数据到数据库（而不是解析后的指标）
                        self._cache_raw_financial_data(symbol, financial_data, stock_info)
                        return metrics
                    else:
                        logger.warning(f"⚠️ AKShare解析失败，返回None")
                else:
                    logger.warning(f"⚠️ AKShare未获取到{symbol}财务数据，尝试Tushare")
            else:
                logger.warning(f"⚠️ AKShare未连接，尝试Tushare")

            # 第三优先级：使用Tushare数据源
            logger.info(f"🔄 使用Tushare备用数据源获取{symbol}财务数据")
            from ..providers.china.tushare import get_tushare_provider

            provider = get_tushare_provider()
            if not provider.connected:
                logger.debug(f"Tushare未连接，无法获取{symbol}真实财务数据")
                return None

            # 获取财务数据（异步方法）
            loop = asyncio.get_event_loop()
            financial_data = loop.run_until_complete(provider.get_financial_data(symbol))
            if not financial_data:
                logger.debug(f"未获取到{symbol}的财务数据")
                return None

            # 获取股票基本信息（异步方法）
            stock_info = loop.run_until_complete(provider.get_stock_basic_info(symbol))

            # 解析Tushare财务数据
            metrics = self._parse_financial_data(financial_data, stock_info, price_value)
            if metrics:
                # 缓存原始财务数据到数据库
                self._cache_raw_financial_data(symbol, financial_data, stock_info)
                return metrics

        except Exception as e:
            logger.debug(f"获取{symbol}真实财务数据失败: {e}")

        return None

    # ------------------------------------------------------------------
    # MongoDB 财务数据解析
    # ------------------------------------------------------------------

    def _parse_mongodb_financial_data(self, financial_data: dict, price_value: float) -> dict:
        """解析 MongoDB 标准化的财务数据为指标"""
        try:
            logger.debug(f"📊 [财务数据] 开始解析 MongoDB 财务数据，包含字段: {list(financial_data.keys())}")

            metrics = {}

            # MongoDB 的 financial_data 是扁平化的结构，直接包含所有财务指标
            # 不再是嵌套的 {balance_sheet, income_statement, ...} 结构

            # 直接从 financial_data 中提取指标
            latest_indicators = financial_data

            # ROE - 净资产收益率 (添加范围验证)
            roe = latest_indicators.get('roe') or latest_indicators.get('roe_waa')
            if roe is not None and str(roe) != 'nan' and roe != '--':
                try:
                    roe_val = float(roe)
                    # ROE 通常在 -100% 到 100% 之间，极端情况可能超出
                    if -200 <= roe_val <= 200:
                        metrics["roe"] = f"{roe_val:.1f}%"
                    else:
                        logger.warning(f"⚠️ ROE 数据异常: {roe_val}，超出合理范围 [-200%, 200%]，设为 N/A")
                        metrics["roe"] = "N/A"
                except (ValueError, TypeError):
                    metrics["roe"] = "N/A"
            else:
                metrics["roe"] = "N/A"

            # ROA - 总资产收益率 (添加范围验证)
            roa = latest_indicators.get('roa') or latest_indicators.get('roa2')
            if roa is not None and str(roa) != 'nan' and roa != '--':
                try:
                    roa_val = float(roa)
                    # ROA 通常在 -50% 到 50% 之间
                    if -100 <= roa_val <= 100:
                        metrics["roa"] = f"{roa_val:.1f}%"
                    else:
                        logger.warning(f"⚠️ ROA 数据异常: {roa_val}，超出合理范围 [-100%, 100%]，设为 N/A")
                        metrics["roa"] = "N/A"
                except (ValueError, TypeError):
                    metrics["roa"] = "N/A"
            else:
                metrics["roa"] = "N/A"

            # 毛利率 - 添加范围验证
            gross_margin = latest_indicators.get('gross_margin')
            if gross_margin is not None and str(gross_margin) != 'nan' and gross_margin != '--':
                try:
                    gross_margin_val = float(gross_margin)
                    # 验证范围：毛利率应该在 -100% 到 100% 之间
                    # 如果超出范围，可能是数据错误（如存储的是绝对金额而不是百分比）
                    if -100 <= gross_margin_val <= 100:
                        metrics["gross_margin"] = f"{gross_margin_val:.1f}%"
                    else:
                        logger.warning(f"⚠️ 毛利率数据异常: {gross_margin_val}，超出合理范围 [-100%, 100%]，设为 N/A")
                        metrics["gross_margin"] = "N/A"
                except (ValueError, TypeError):
                    metrics["gross_margin"] = "N/A"
            else:
                metrics["gross_margin"] = "N/A"

            # 净利率 - 添加范围验证
            net_margin = latest_indicators.get('netprofit_margin')
            if net_margin is not None and str(net_margin) != 'nan' and net_margin != '--':
                try:
                    net_margin_val = float(net_margin)
                    # 验证范围：净利率应该在 -100% 到 100% 之间
                    if -100 <= net_margin_val <= 100:
                        metrics["net_margin"] = f"{net_margin_val:.1f}%"
                    else:
                        logger.warning(f"⚠️ 净利率数据异常: {net_margin_val}，超出合理范围 [-100%, 100%]，设为 N/A")
                        metrics["net_margin"] = "N/A"
                except (ValueError, TypeError):
                    metrics["net_margin"] = "N/A"
            else:
                metrics["net_margin"] = "N/A"

            # 计算 PE/PB - 优先使用实时计算，降级到静态数据
            # 同时获取 PE 和 PE_TTM 两个指标
            pe_value = None
            pe_ttm_value = None
            pb_value = None
            is_loss_stock = False  # 🔥 标记是否为亏损股

            try:
                # 优先使用实时计算
                from sinoquant.dataflows.realtime_metrics import get_pe_pb_with_fallback
                from sinoquant.config.database_manager import get_database_manager

                db_manager = get_database_manager()
                if db_manager.is_mongodb_available():
                    client = db_manager.get_mongodb_client()
                    # 从symbol中提取股票代码
                    stock_code = latest_indicators.get('code') or latest_indicators.get('symbol', '').replace('.SZ', '').replace('.SH', '')

                    logger.info(f"📊 [PE计算] 开始计算股票 {stock_code} 的PE/PB")

                    if stock_code:
                        logger.info(f"📊 [PE计算-第1层] 尝试实时计算 PE/PB (股票代码: {stock_code})")

                        # 获取实时PE/PB
                        realtime_metrics = get_pe_pb_with_fallback(stock_code, client)

                        if realtime_metrics:
                            # 获取市值数据（优先保存）
                            market_cap = realtime_metrics.get('market_cap')
                            if market_cap is not None and market_cap > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["total_mv"] = f"{market_cap:.2f}亿元{realtime_tag}"
                                logger.info(f"✅ [总市值获取成功] 总市值={market_cap:.2f}亿元 | 实时={is_realtime}")

                            # 使用实时PE（动态市盈率）
                            pe_value = realtime_metrics.get('pe')
                            if pe_value is not None and pe_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pe"] = f"{pe_value:.1f}倍{realtime_tag}"

                                # 详细日志
                                price = realtime_metrics.get('price', 'N/A')
                                market_cap_log = realtime_metrics.get('market_cap', 'N/A')
                                source = realtime_metrics.get('source', 'unknown')
                                updated_at = realtime_metrics.get('updated_at', 'N/A')

                                logger.info(f"✅ [PE计算-第1层成功] PE={pe_value:.2f}倍 | 来源={source} | 实时={is_realtime}")
                                logger.info(f"   └─ 计算数据: 股价={price}元, 市值={market_cap_log}亿元, 更新时间={updated_at}")
                            elif pe_value is None:
                                # 🔥 PE 为 None，检查是否是亏损股
                                pe_ttm_check = latest_indicators.get('pe_ttm')
                                # pe_ttm 为 None、<= 0、'nan'、'--' 都认为是亏损股
                                if pe_ttm_check is None or pe_ttm_check <= 0 or str(pe_ttm_check) == 'nan' or pe_ttm_check == '--':
                                    is_loss_stock = True
                                    logger.info(f"⚠️ [PE计算-第1层] PE为None且pe_ttm={pe_ttm_check}，确认为亏损股")

                            # 使用实时PE_TTM（TTM市盈率）
                            pe_ttm_value = realtime_metrics.get('pe_ttm')
                            if pe_ttm_value is not None and pe_ttm_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pe_ttm"] = f"{pe_ttm_value:.1f}倍{realtime_tag}"
                                logger.info(f"✅ [PE_TTM计算-第1层成功] PE_TTM={pe_ttm_value:.2f}倍 | 来源={source} | 实时={is_realtime}")
                            elif pe_ttm_value is None and not is_loss_stock:
                                # 🔥 PE_TTM 为 None，再次检查是否是亏损股
                                pe_ttm_check = latest_indicators.get('pe_ttm')
                                # pe_ttm 为 None、<= 0、'nan'、'--' 都认为是亏损股
                                if pe_ttm_check is None or pe_ttm_check <= 0 or str(pe_ttm_check) == 'nan' or pe_ttm_check == '--':
                                    is_loss_stock = True
                                    logger.info(f"⚠️ [PE_TTM计算-第1层] PE_TTM为None且pe_ttm={pe_ttm_check}，确认为亏损股")

                            # 使用实时PB
                            pb_value = realtime_metrics.get('pb')
                            if pb_value is not None and pb_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pb"] = f"{pb_value:.2f}倍{realtime_tag}"
                                logger.info(f"✅ [PB计算-第1层成功] PB={pb_value:.2f}倍 | 来源={realtime_metrics.get('source')} | 实时={is_realtime}")
                        else:
                            # 🔥 检查是否因为亏损导致返回 None
                            # 从 stock_basic_info 获取 pe_ttm 判断是否亏损
                            pe_ttm_static = latest_indicators.get('pe_ttm')
                            # pe_ttm 为 None、<= 0、'nan'、'--' 都认为是亏损股
                            if pe_ttm_static is None or pe_ttm_static <= 0 or str(pe_ttm_static) == 'nan' or pe_ttm_static == '--':
                                is_loss_stock = True
                                logger.info(f"⚠️ [PE计算-第1层失败] 检测到亏损股（pe_ttm={pe_ttm_static}），跳过降级计算")
                            else:
                                logger.warning(f"⚠️ [PE计算-第1层失败] 实时计算返回空结果，将尝试降级计算")

            except Exception as e:
                logger.warning(f"⚠️ [PE计算-第1层异常] 实时计算失败: {e}，将尝试降级计算")

            # 如果实时计算失败，尝试从 latest_indicators 获取总市值
            if "total_mv" not in metrics:
                logger.info(f"📊 [总市值-第2层] 尝试从 stock_basic_info 获取")
                total_mv_static = latest_indicators.get('total_mv')
                if total_mv_static is not None and total_mv_static > 0:
                    metrics["total_mv"] = f"{total_mv_static:.2f}亿元"
                    logger.info(f"✅ [总市值-第2层成功] 总市值={total_mv_static:.2f}亿元 (来源: stock_basic_info)")
                else:
                    # 尝试从 money_cap 计算（万元转亿元）
                    money_cap = latest_indicators.get('money_cap')
                    if money_cap is not None and money_cap > 0:
                        total_mv_yi = money_cap / 10000
                        metrics["total_mv"] = f"{total_mv_yi:.2f}亿元"
                        logger.info(f"✅ [总市值-第3层成功] 总市值={total_mv_yi:.2f}亿元 (从money_cap转换)")
                    else:
                        metrics["total_mv"] = "N/A"
                        logger.warning(f"⚠️ [总市值-全部失败] 无可用总市值数据")

            # 如果实时计算失败，尝试传统计算方式
            if pe_value is None:
                # 🔥 如果已经确认是亏损股，直接设置 PE 为 N/A，不再尝试降级计算
                if is_loss_stock:
                    metrics["pe"] = "N/A"
                    logger.info(f"⚠️ [PE计算-亏损股] 已确认为亏损股，PE设置为N/A，跳过第2层计算")
                else:
                    logger.info(f"📊 [PE计算-第2层] 尝试使用市值/净利润计算")

                    net_profit = latest_indicators.get('net_profit')

                    # 🔥 关键修复：检查净利润是否为正数（亏损股不计算PE）
                    if net_profit and net_profit > 0:
                        try:
                            # 使用市值/净利润计算PE
                            money_cap = latest_indicators.get('money_cap')
                            if money_cap and money_cap > 0:
                                pe_calculated = money_cap / net_profit
                                metrics["pe"] = f"{pe_calculated:.1f}倍"
                                logger.info(f"✅ [PE计算-第2层成功] PE={pe_calculated:.2f}倍")
                                logger.info(f"   └─ 计算公式: 市值({money_cap}万元) / 净利润({net_profit}万元)")
                            else:
                                logger.warning(f"⚠️ [PE计算-第2层失败] 市值无效: {money_cap}，尝试第3层")

                                # 第三层降级：直接使用 latest_indicators 中的 pe 字段（仅当为正数时）
                                pe_static = latest_indicators.get('pe')
                                if pe_static is not None and str(pe_static) != 'nan' and pe_static != '--':
                                    try:
                                        pe_float = float(pe_static)
                                        # 🔥 只接受正数的 PE
                                        if pe_float > 0:
                                            metrics["pe"] = f"{pe_float:.1f}倍"
                                            logger.info(f"✅ [PE计算-第3层成功] 使用静态PE: {metrics['pe']}")
                                            logger.info(f"   └─ 数据来源: stock_basic_info.pe")
                                        else:
                                            metrics["pe"] = "N/A"
                                            logger.info(f"⚠️ [PE计算-第3层跳过] 静态PE为负数或零（亏损股）: {pe_float}")
                                    except (ValueError, TypeError):
                                        metrics["pe"] = "N/A"
                                        logger.error(f"❌ [PE计算-第3层失败] 静态PE格式错误: {pe_static}")
                                else:
                                    metrics["pe"] = "N/A"
                                    logger.error(f"❌ [PE计算-全部失败] 无可用PE数据")
                        except (ValueError, TypeError, ZeroDivisionError) as e:
                            metrics["pe"] = "N/A"
                            logger.error(f"❌ [PE计算-第2层异常] 计算失败: {e}")
                    elif net_profit and net_profit < 0:
                        # 🔥 亏损股：PE 设置为 N/A
                        metrics["pe"] = "N/A"
                        logger.info(f"⚠️ [PE计算-亏损股] 净利润为负数（{net_profit}万元），PE设置为N/A")
                    else:
                        logger.warning(f"⚠️ [PE计算-第2层跳过] 净利润无效: {net_profit}，尝试第3层")

                        # 第三层降级：直接使用 latest_indicators 中的 pe 字段（仅当为正数时）
                        pe_static = latest_indicators.get('pe')
                        if pe_static is not None and str(pe_static) != 'nan' and pe_static != '--':
                            try:
                                pe_float = float(pe_static)
                                # 🔥 只接受正数的 PE
                                if pe_float > 0:
                                    metrics["pe"] = f"{pe_float:.1f}倍"
                                    logger.info(f"✅ [PE计算-第3层成功] 使用静态PE: {metrics['pe']}")
                                    logger.info(f"   └─ 数据来源: stock_basic_info.pe")
                                else:
                                    metrics["pe"] = "N/A"
                                    logger.info(f"⚠️ [PE计算-第3层跳过] 静态PE为负数或零（亏损股）: {pe_float}")
                            except (ValueError, TypeError):
                                metrics["pe"] = "N/A"
                                logger.error(f"❌ [PE计算-第3层失败] 静态PE格式错误: {pe_static}")
                        else:
                            metrics["pe"] = "N/A"
                            logger.error(f"❌ [PE计算-全部失败] 无可用PE数据")

            # 如果 PE_TTM 未获取到，尝试从静态数据获取
            if pe_ttm_value is None:
                # 🔥 如果已经确认是亏损股，直接设置 PE_TTM 为 N/A
                if is_loss_stock:
                    metrics["pe_ttm"] = "N/A"
                    logger.info(f"⚠️ [PE_TTM计算-亏损股] 已确认为亏损股，PE_TTM设置为N/A")
                else:
                    logger.info(f"📊 [PE_TTM计算-第2层] 尝试从静态数据获取")
                    pe_ttm_static = latest_indicators.get('pe_ttm')
                    if pe_ttm_static is not None and str(pe_ttm_static) != 'nan' and pe_ttm_static != '--':
                        try:
                            pe_ttm_float = float(pe_ttm_static)
                            # 🔥 只接受正数的 PE_TTM（亏损股不显示PE_TTM）
                            if pe_ttm_float > 0:
                                metrics["pe_ttm"] = f"{pe_ttm_float:.1f}倍"
                                logger.info(f"✅ [PE_TTM计算-第2层成功] 使用静态PE_TTM: {metrics['pe_ttm']}")
                                logger.info(f"   └─ 数据来源: stock_basic_info.pe_ttm")
                            else:
                                metrics["pe_ttm"] = "N/A"
                                logger.info(f"⚠️ [PE_TTM计算-第2层跳过] 静态PE_TTM为负数或零（亏损股）: {pe_ttm_float}")
                        except (ValueError, TypeError):
                            metrics["pe_ttm"] = "N/A"
                            logger.error(f"❌ [PE_TTM计算-第2层失败] 静态PE_TTM格式错误: {pe_ttm_static}")
                    else:
                        metrics["pe_ttm"] = "N/A"
                        logger.warning(f"⚠️ [PE_TTM计算-全部失败] 无可用PE_TTM数据")

            if pb_value is None:
                total_equity = latest_indicators.get('total_hldr_eqy_exc_min_int')
                if total_equity and total_equity > 0:
                    try:
                        # 使用市值/净资产计算PB
                        money_cap = latest_indicators.get('money_cap')
                        if money_cap and money_cap > 0:
                            # 注意单位转换：money_cap 是万元，total_equity 是元
                            # PB = 市值(万元) * 10000 / 净资产(元)
                            pb_calculated = (money_cap * 10000) / total_equity
                            metrics["pb"] = f"{pb_calculated:.2f}倍"
                            logger.info(f"✅ [PB计算-第2层成功] PB={pb_calculated:.2f}倍")
                            logger.info(f"   └─ 计算公式: 市值{money_cap}万元 * 10000 / 净资产{total_equity}元 = {metrics['pb']}")
                        else:
                            # 第三层降级：直接使用 latest_indicators 中的 pb 字段
                            pb_static = latest_indicators.get('pb') or latest_indicators.get('pb_mrq')
                            if pb_static is not None and str(pb_static) != 'nan' and pb_static != '--':
                                try:
                                    metrics["pb"] = f"{float(pb_static):.2f}倍"
                                    logger.info(f"✅ [PB计算-第3层成功] 使用静态PB: {metrics['pb']}")
                                    logger.info(f"   └─ 数据来源: stock_basic_info.pb")
                                except (ValueError, TypeError):
                                    metrics["pb"] = "N/A"
                            else:
                                metrics["pb"] = "N/A"
                    except (ValueError, TypeError, ZeroDivisionError) as e:
                        logger.error(f"❌ [PB计算-第2层异常] 计算失败: {e}")
                        metrics["pb"] = "N/A"
                else:
                    # 第三层降级：直接使用 latest_indicators 中的 pb 字段
                    pb_static = latest_indicators.get('pb') or latest_indicators.get('pb_mrq')
                    if pb_static is not None and str(pb_static) != 'nan' and pb_static != '--':
                        try:
                            metrics["pb"] = f"{float(pb_static):.2f}倍"
                            logger.info(f"✅ [PB计算-第3层成功] 使用静态PB: {metrics['pb']}")
                            logger.info(f"   └─ 数据来源: stock_basic_info.pb")
                        except (ValueError, TypeError):
                            metrics["pb"] = "N/A"
                    else:
                        metrics["pb"] = "N/A"

            # 资产负债率
            debt_ratio = latest_indicators.get('debt_to_assets')
            if debt_ratio is not None and str(debt_ratio) != 'nan' and debt_ratio != '--':
                try:
                    metrics["debt_ratio"] = f"{float(debt_ratio):.1f}%"
                except (ValueError, TypeError):
                    metrics["debt_ratio"] = "N/A"
            else:
                metrics["debt_ratio"] = "N/A"

            # 计算 PS - 市销率（使用TTM营业收入）
            # 优先使用 TTM 营业收入，如果没有则使用单期营业收入
            revenue_ttm = latest_indicators.get('revenue_ttm')
            revenue = latest_indicators.get('revenue')

            # 选择使用哪个营业收入数据
            revenue_for_ps = revenue_ttm if revenue_ttm and revenue_ttm > 0 else revenue
            revenue_type = "TTM" if revenue_ttm and revenue_ttm > 0 else "单期"

            if revenue_for_ps and revenue_for_ps > 0:
                try:
                    # 使用市值/营业收入计算PS
                    money_cap = latest_indicators.get('money_cap')
                    if money_cap and money_cap > 0:
                        ps_calculated = money_cap / revenue_for_ps
                        metrics["ps"] = f"{ps_calculated:.2f}倍"
                        logger.debug(f"✅ 计算PS({revenue_type}): 市值{money_cap}万元 / 营业收入{revenue_for_ps}万元 = {metrics['ps']}")
                    else:
                        metrics["ps"] = "N/A"
                except (ValueError, TypeError, ZeroDivisionError):
                    metrics["ps"] = "N/A"
            else:
                metrics["ps"] = "N/A"

            # 股息收益率 - 暂时设为N/A，需要股息数据
            metrics["dividend_yield"] = "N/A"
            metrics["current_ratio"] = latest_indicators.get('current_ratio', 'N/A')
            metrics["quick_ratio"] = latest_indicators.get('quick_ratio', 'N/A')
            metrics["cash_ratio"] = latest_indicators.get('cash_ratio', 'N/A')

            # 添加评分字段（使用默认值）
            metrics["fundamental_score"] = 7.0  # 基于真实数据的默认评分
            metrics["valuation_score"] = 6.5
            metrics["growth_score"] = 7.0
            metrics["risk_level"] = "中等"

            logger.info(f"✅ MongoDB 财务数据解析成功: ROE={metrics.get('roe')}, ROA={metrics.get('roa')}, 毛利率={metrics.get('gross_margin')}, 净利率={metrics.get('net_margin')}")
            return metrics

        except Exception as e:
            logger.error(f"❌ MongoDB财务数据解析失败: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # AKShare 财务数据解析
    # ------------------------------------------------------------------

    def _parse_akshare_financial_data(self, financial_data: dict, stock_info: dict, price_value: float) -> dict:
        """解析AKShare财务数据为指标"""
        try:
            # 获取最新的财务数据
            balance_sheet = financial_data.get('balance_sheet', [])
            income_statement = financial_data.get('income_statement', [])
            cash_flow = financial_data.get('cash_flow', [])
            main_indicators = financial_data.get('main_indicators')

            # main_indicators 可能是 DataFrame 或 list（to_dict('records') 的结果）
            if main_indicators is None:
                logger.warning("AKShare主要财务指标为空")
                return None

            # 检查是否为空
            if isinstance(main_indicators, list):
                if not main_indicators:
                    logger.warning("AKShare主要财务指标列表为空")
                    return None
                # 列表格式：[{指标: 值, ...}, ...]
                # 转换为 DataFrame 以便统一处理
                import pandas as pd
                main_indicators = pd.DataFrame(main_indicators)
            elif hasattr(main_indicators, 'empty') and main_indicators.empty:
                logger.warning("AKShare主要财务指标DataFrame为空")
                return None

            # main_indicators是DataFrame，需要转换为字典格式便于查找
            # 获取最新数据列（第3列，索引为2）
            latest_col = main_indicators.columns[2] if len(main_indicators.columns) > 2 else None
            if not latest_col:
                logger.warning("AKShare主要财务指标缺少数据列")
                return None

            logger.info(f"📅 使用AKShare最新数据期间: {latest_col}")

            # 创建指标名称到值的映射
            indicators_dict = {}
            for _, row in main_indicators.iterrows():
                indicator_name = row['指标']
                value = row[latest_col]
                indicators_dict[indicator_name] = value

            logger.debug(f"AKShare主要财务指标数量: {len(indicators_dict)}")

            # 计算财务指标
            metrics = {}

            # 🔥 优先尝试使用实时 PE/PB 计算（与 MongoDB 解析保持一致）
            pe_value = None
            pe_ttm_value = None
            pb_value = None

            try:
                # 获取股票代码
                stock_code = stock_info.get('code', '').replace('.SH', '').replace('.SZ', '').zfill(6)
                if stock_code:
                    logger.info(f"📊 [AKShare-PE计算-第1层] 尝试使用实时PE/PB计算: {stock_code}")

                    from sinoquant.config.database_manager import get_database_manager
                    from sinoquant.dataflows.realtime_metrics import get_pe_pb_with_fallback

                    db_manager = get_database_manager()
                    if db_manager.is_mongodb_available():
                        client = db_manager.get_mongodb_client()

                        # 获取实时PE/PB
                        realtime_metrics = get_pe_pb_with_fallback(stock_code, client)

                        if realtime_metrics:
                            # 获取总市值
                            market_cap = realtime_metrics.get('market_cap')
                            if market_cap is not None and market_cap > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["total_mv"] = f"{market_cap:.2f}亿元{realtime_tag}"
                                logger.info(f"✅ [AKShare-总市值获取成功] 总市值={market_cap:.2f}亿元 | 实时={is_realtime}")

                            # 使用实时PE
                            pe_value = realtime_metrics.get('pe')
                            if pe_value is not None and pe_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pe"] = f"{pe_value:.1f}倍{realtime_tag}"
                                logger.info(f"✅ [AKShare-PE计算-第1层成功] PE={pe_value:.2f}倍 | 来源={realtime_metrics.get('source')} | 实时={is_realtime}")

                            # 使用实时PE_TTM
                            pe_ttm_value = realtime_metrics.get('pe_ttm')
                            if pe_ttm_value is not None and pe_ttm_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pe_ttm"] = f"{pe_ttm_value:.1f}倍{realtime_tag}"
                                logger.info(f"✅ [AKShare-PE_TTM计算-第1层成功] PE_TTM={pe_ttm_value:.2f}倍")

                            # 使用实时PB
                            pb_value = realtime_metrics.get('pb')
                            if pb_value is not None and pb_value > 0:
                                is_realtime = realtime_metrics.get('is_realtime', False)
                                realtime_tag = " (实时)" if is_realtime else ""
                                metrics["pb"] = f"{pb_value:.2f}倍{realtime_tag}"
                                logger.info(f"✅ [AKShare-PB计算-第1层成功] PB={pb_value:.2f}倍")
                        else:
                            logger.warning(f"⚠️ [AKShare-PE计算-第1层失败] 实时计算返回空结果，将尝试降级计算")
            except Exception as e:
                logger.warning(f"⚠️ [AKShare-PE计算-第1层异常] 实时计算失败: {e}，将尝试降级计算")

            # 获取ROE - 直接从指标中获取
            roe_value = indicators_dict.get('净资产收益率(ROE)')
            if roe_value is not None and str(roe_value) != 'nan' and roe_value != '--':
                try:
                    roe_val = float(roe_value)
                    # ROE通常是百分比形式
                    metrics["roe"] = f"{roe_val:.1f}%"
                    logger.debug(f"✅ 获取ROE: {metrics['roe']}")
                except (ValueError, TypeError):
                    metrics["roe"] = "N/A"
            else:
                metrics["roe"] = "N/A"

            # 如果实时计算失败，尝试从 stock_info 获取总市值
            if "total_mv" not in metrics:
                logger.info(f"📊 [AKShare-总市值-第2层] 尝试从 stock_info 获取")
                total_mv_static = stock_info.get('total_mv')
                if total_mv_static is not None and total_mv_static > 0:
                    metrics["total_mv"] = f"{total_mv_static:.2f}亿元"
                    logger.info(f"✅ [AKShare-总市值-第2层成功] 总市值={total_mv_static:.2f}亿元")
                else:
                    metrics["total_mv"] = "N/A"
                    logger.warning(f"⚠️ [AKShare-总市值-全部失败] 无可用总市值数据")

            # 🔥 如果实时计算失败，降级到传统计算方式
            if pe_value is None:
                logger.info(f"📊 [AKShare-PE计算-第2层] 尝试使用股价/EPS计算")

                # 计算 PE - 优先使用 TTM 数据
                # 尝试从 main_indicators DataFrame 计算 TTM EPS
                ttm_eps = None
                try:
                    # main_indicators 是 DataFrame，包含多期数据
                    # 尝试计算 TTM EPS
                    if '基本每股收益' in main_indicators['指标'].values:
                        # 提取基本每股收益的所有期数数据
                        eps_row = main_indicators[main_indicators['指标'] == '基本每股收益']
                        if not eps_row.empty:
                            # 获取所有数值列（排除'指标'列）
                            value_cols = [col for col in eps_row.columns if col != '指标']

                            # 构建 DataFrame 用于 TTM 计算
                            import pandas as pd
                            eps_data = []
                            for col in value_cols:
                                eps_val = eps_row[col].iloc[0]
                                if eps_val is not None and str(eps_val) != 'nan' and eps_val != '--':
                                    eps_data.append({'报告期': col, '基本每股收益': eps_val})

                            if len(eps_data) >= 2:
                                eps_df = pd.DataFrame(eps_data)
                                # 使用 TTM 计算函数
                                from scripts.sync_financial_data import _calculate_ttm_metric
                                ttm_eps = _calculate_ttm_metric(eps_df, '基本每股收益')
                                if ttm_eps:
                                    logger.info(f"✅ 计算 TTM EPS: {ttm_eps:.4f} 元")
                except Exception as e:
                    logger.debug(f"计算 TTM EPS 失败: {e}")

                # 使用 TTM EPS 或单期 EPS 计算 PE
                eps_for_pe = ttm_eps if ttm_eps else None
                pe_type = "TTM" if ttm_eps else "单期"

                if not eps_for_pe:
                    # 降级到单期 EPS
                    eps_value = indicators_dict.get('基本每股收益')
                    if eps_value is not None and str(eps_value) != 'nan' and eps_value != '--':
                        try:
                            eps_for_pe = float(eps_value)
                        except (ValueError, TypeError):
                            pass

                if eps_for_pe and eps_for_pe > 0:
                    pe_val = price_value / eps_for_pe
                    metrics["pe"] = f"{pe_val:.1f}倍"
                    logger.info(f"✅ [AKShare-PE计算-第2层成功] PE({pe_type}): 股价{price_value} / EPS{eps_for_pe:.4f} = {metrics['pe']}")
                elif eps_for_pe and eps_for_pe <= 0:
                    metrics["pe"] = "N/A（亏损）"
                    logger.warning(f"⚠️ [AKShare-PE计算-第2层失败] 亏损股票，EPS={eps_for_pe}")
                else:
                    metrics["pe"] = "N/A"
                    logger.error(f"❌ [AKShare-PE计算-全部失败] 无可用EPS数据")

            # 🔥 如果实时PB计算失败，降级到传统计算方式
            if pb_value is None:
                logger.info(f"📊 [AKShare-PB计算-第2层] 尝试使用股价/BPS计算")

                # 获取每股净资产 - 用于计算PB
                bps_value = indicators_dict.get('每股净资产_最新股数')
                if bps_value is not None and str(bps_value) != 'nan' and bps_value != '--':
                    try:
                        bps_val = float(bps_value)
                        if bps_val > 0:
                            # 计算PB = 股价 / 每股净资产
                            pb_val = price_value / bps_val
                            metrics["pb"] = f"{pb_val:.2f}倍"
                            logger.info(f"✅ [AKShare-PB计算-第2层成功] PB: 股价{price_value} / BPS{bps_val} = {metrics['pb']}")
                        else:
                            metrics["pb"] = "N/A"
                            logger.warning(f"⚠️ [AKShare-PB计算-第2层失败] BPS无效: {bps_val}")
                    except (ValueError, TypeError) as e:
                        metrics["pb"] = "N/A"
                        logger.error(f"❌ [AKShare-PB计算-第2层异常] {e}")
                else:
                    metrics["pb"] = "N/A"
                    logger.error(f"❌ [AKShare-PB计算-全部失败] 无可用BPS数据")

            # 尝试获取其他指标
            # 总资产收益率(ROA)
            roa_value = indicators_dict.get('总资产报酬率')
            if roa_value is not None and str(roa_value) != 'nan' and roa_value != '--':
                try:
                    roa_val = float(roa_value)
                    metrics["roa"] = f"{roa_val:.1f}%"
                except (ValueError, TypeError):
                    metrics["roa"] = "N/A"
            else:
                metrics["roa"] = "N/A"

            # 毛利率
            gross_margin_value = indicators_dict.get('毛利率')
            if gross_margin_value is not None and str(gross_margin_value) != 'nan' and gross_margin_value != '--':
                try:
                    gross_margin_val = float(gross_margin_value)
                    metrics["gross_margin"] = f"{gross_margin_val:.1f}%"
                except (ValueError, TypeError):
                    metrics["gross_margin"] = "N/A"
            else:
                metrics["gross_margin"] = "N/A"

            # 销售净利率
            net_margin_value = indicators_dict.get('销售净利率')
            if net_margin_value is not None and str(net_margin_value) != 'nan' and net_margin_value != '--':
                try:
                    net_margin_val = float(net_margin_value)
                    metrics["net_margin"] = f"{net_margin_val:.1f}%"
                except (ValueError, TypeError):
                    metrics["net_margin"] = "N/A"
            else:
                metrics["net_margin"] = "N/A"

            # 资产负债率
            debt_ratio_value = indicators_dict.get('资产负债率')
            if debt_ratio_value is not None and str(debt_ratio_value) != 'nan' and debt_ratio_value != '--':
                try:
                    debt_ratio_val = float(debt_ratio_value)
                    metrics["debt_ratio"] = f"{debt_ratio_val:.1f}%"
                except (ValueError, TypeError):
                    metrics["debt_ratio"] = "N/A"
            else:
                metrics["debt_ratio"] = "N/A"

            # 流动比率
            current_ratio_value = indicators_dict.get('流动比率')
            if current_ratio_value is not None and str(current_ratio_value) != 'nan' and current_ratio_value != '--':
                try:
                    current_ratio_val = float(current_ratio_value)
                    metrics["current_ratio"] = f"{current_ratio_val:.2f}"
                except (ValueError, TypeError):
                    metrics["current_ratio"] = "N/A"
            else:
                metrics["current_ratio"] = "N/A"

            # 速动比率
            quick_ratio_value = indicators_dict.get('速动比率')
            if quick_ratio_value is not None and str(quick_ratio_value) != 'nan' and quick_ratio_value != '--':
                try:
                    quick_ratio_val = float(quick_ratio_value)
                    metrics["quick_ratio"] = f"{quick_ratio_val:.2f}"
                except (ValueError, TypeError):
                    metrics["quick_ratio"] = "N/A"
            else:
                metrics["quick_ratio"] = "N/A"

            # 计算 PS - 市销率（优先使用 TTM 营业收入）
            # 尝试从 main_indicators DataFrame 计算 TTM 营业收入
            ttm_revenue = None
            try:
                if '营业收入' in main_indicators['指标'].values:
                    revenue_row = main_indicators[main_indicators['指标'] == '营业收入']
                    if not revenue_row.empty:
                        value_cols = [col for col in revenue_row.columns if col != '指标']

                        import pandas as pd
                        revenue_data = []
                        for col in value_cols:
                            rev_val = revenue_row[col].iloc[0]
                            if rev_val is not None and str(rev_val) != 'nan' and rev_val != '--':
                                revenue_data.append({'报告期': col, '营业收入': rev_val})

                        if len(revenue_data) >= 2:
                            revenue_df = pd.DataFrame(revenue_data)
                            from scripts.sync_financial_data import _calculate_ttm_metric
                            ttm_revenue = _calculate_ttm_metric(revenue_df, '营业收入')
                            if ttm_revenue:
                                logger.info(f"✅ 计算 TTM 营业收入: {ttm_revenue:.2f} 万元")
            except Exception as e:
                logger.debug(f"计算 TTM 营业收入失败: {e}")

            # 计算 PS
            revenue_for_ps = ttm_revenue if ttm_revenue else None
            ps_type = "TTM" if ttm_revenue else "单期"

            if not revenue_for_ps:
                # 降级到单期营业收入
                revenue_value = indicators_dict.get('营业收入')
                if revenue_value is not None and str(revenue_value) != 'nan' and revenue_value != '--':
                    try:
                        revenue_for_ps = float(revenue_value)
                    except (ValueError, TypeError):
                        pass

            if revenue_for_ps and revenue_for_ps > 0:
                # 获取总股本计算市值
                total_share = stock_info.get('total_share') if stock_info else None
                if total_share and total_share > 0:
                    # 市值（万元）= 股价（元）× 总股本（万股）
                    market_cap = price_value * total_share
                    ps_val = market_cap / revenue_for_ps
                    metrics["ps"] = f"{ps_val:.2f}倍"
                    logger.info(f"✅ 计算PS({ps_type}): 市值{market_cap:.2f}万元 / 营业收入{revenue_for_ps:.2f}万元 = {metrics['ps']}")
                else:
                    metrics["ps"] = "N/A（无总股本数据）"
                    logger.warning(f"⚠️ 无法计算PS: 缺少总股本数据")
            else:
                metrics["ps"] = "N/A"

            # 补充其他指标的默认值
            metrics.update({
                "dividend_yield": "待查询",
                "cash_ratio": "待分析"
            })

            # 评分（基于AKShare数据的简化评分）
            fundamental_score = self._calculate_fundamental_score(metrics, stock_info)
            valuation_score = self._calculate_valuation_score(metrics)
            growth_score = self._calculate_growth_score(metrics, stock_info)
            risk_level = self._calculate_risk_level(metrics, stock_info)

            metrics.update({
                "fundamental_score": fundamental_score,
                "valuation_score": valuation_score,
                "growth_score": growth_score,
                "risk_level": risk_level,
                "data_source": "AKShare"
            })

            logger.info(f"✅ AKShare财务数据解析成功: PE={metrics['pe']}, PB={metrics['pb']}, ROE={metrics['roe']}")
            return metrics

        except Exception as e:
            logger.error(f"❌ AKShare财务数据解析失败: {e}")
            return None

    # ------------------------------------------------------------------
    # Tushare 财务数据解析
    # ------------------------------------------------------------------

    def _parse_financial_data(self, financial_data: dict, stock_info: dict, price_value: float) -> dict:
        """解析财务数据为指标"""
        try:
            # 获取最新的财务数据
            balance_sheet = financial_data.get('balance_sheet', [])
            income_statement = financial_data.get('income_statement', [])
            cash_flow = financial_data.get('cash_flow', [])

            if not (balance_sheet or income_statement):
                return None

            latest_balance = balance_sheet[0] if balance_sheet else {}
            latest_income = income_statement[0] if income_statement else {}
            latest_cash = cash_flow[0] if cash_flow else {}

            # 计算财务指标
            metrics = {}

            # 基础数据
            total_assets = latest_balance.get('total_assets', 0) or 0
            total_liab = latest_balance.get('total_liab', 0) or 0
            total_equity = latest_balance.get('total_hldr_eqy_exc_min_int', 0) or 0

            # 计算 TTM 营业收入和净利润
            # Tushare income_statement 的数据是累计值（从年初到报告期）
            # 需要使用 TTM 公式计算
            ttm_revenue = None
            ttm_net_income = None

            try:
                if len(income_statement) >= 2:
                    # 准备数据用于 TTM 计算
                    import pandas as pd

                    # 构建营业收入 DataFrame
                    revenue_data = []
                    for stmt in income_statement:
                        end_date = stmt.get('end_date')
                        revenue = stmt.get('total_revenue')
                        if end_date and revenue is not None:
                            revenue_data.append({'报告期': str(end_date), '营业收入': float(revenue)})

                    if len(revenue_data) >= 2:
                        revenue_df = pd.DataFrame(revenue_data)
                        from scripts.sync_financial_data import _calculate_ttm_metric
                        ttm_revenue = _calculate_ttm_metric(revenue_df, '营业收入')
                        if ttm_revenue:
                            logger.info(f"✅ Tushare 计算 TTM 营业收入: {ttm_revenue:.2f} 万元")

                    # 构建净利润 DataFrame
                    profit_data = []
                    for stmt in income_statement:
                        end_date = stmt.get('end_date')
                        profit = stmt.get('n_income')
                        if end_date and profit is not None:
                            profit_data.append({'报告期': str(end_date), '净利润': float(profit)})

                    if len(profit_data) >= 2:
                        profit_df = pd.DataFrame(profit_data)
                        ttm_net_income = _calculate_ttm_metric(profit_df, '净利润')
                        if ttm_net_income:
                            logger.info(f"✅ Tushare 计算 TTM 净利润: {ttm_net_income:.2f} 万元")
            except Exception as e:
                logger.warning(f"⚠️ Tushare TTM 计算失败: {e}")

            # 降级到单期数据
            total_revenue = ttm_revenue if ttm_revenue else (latest_income.get('total_revenue', 0) or 0)
            net_income = ttm_net_income if ttm_net_income else (latest_income.get('n_income', 0) or 0)
            operate_profit = latest_income.get('operate_profit', 0) or 0

            revenue_type = "TTM" if ttm_revenue else "单期"
            profit_type = "TTM" if ttm_net_income else "单期"

            # 获取实际总股本计算市值
            # 优先从 stock_info 获取，如果没有则无法计算准确的估值指标
            total_share = stock_info.get('total_share') if stock_info else None

            if total_share and total_share > 0:
                # 市值（元）= 股价（元）× 总股本（万股）× 10000
                market_cap = price_value * total_share * 10000
                market_cap_yi = market_cap / 100000000  # 转换为亿元
                metrics["total_mv"] = f"{market_cap_yi:.2f}亿元"
                logger.info(f"✅ [Tushare-总市值计算成功] 总市值={market_cap_yi:.2f}亿元 (股价{price_value}元 × 总股本{total_share}万股)")
            else:
                logger.error(f"❌ {stock_info.get('code', 'Unknown')} 无法获取总股本，无法计算准确的估值指标")
                market_cap = None
                metrics["total_mv"] = "N/A"

            # 计算各项指标（只有在有准确市值时才计算）
            if market_cap:
                # PE比率（优先使用 TTM 净利润）
                if net_income > 0:
                    pe_ratio = market_cap / (net_income * 10000)  # 转换单位
                    metrics["pe"] = f"{pe_ratio:.1f}倍"
                    logger.info(f"✅ Tushare 计算PE({profit_type}): 市值{market_cap/100000000:.2f}亿元 / 净利润{net_income:.2f}万元 = {pe_ratio:.1f}倍")
                else:
                    metrics["pe"] = "N/A（亏损）"

                # PB比率（净资产使用最新期数据，相对准确）
                if total_equity > 0:
                    pb_ratio = market_cap / (total_equity * 10000)
                    metrics["pb"] = f"{pb_ratio:.2f}倍"
                else:
                    metrics["pb"] = "N/A"

                # PS比率（优先使用 TTM 营业收入）
                if total_revenue > 0:
                    ps_ratio = market_cap / (total_revenue * 10000)
                    metrics["ps"] = f"{ps_ratio:.1f}倍"
                    logger.info(f"✅ Tushare 计算PS({revenue_type}): 市值{market_cap/100000000:.2f}亿元 / 营业收入{total_revenue:.2f}万元 = {ps_ratio:.1f}倍")
                else:
                    metrics["ps"] = "N/A"
            else:
                # 无法获取总股本，无法计算估值指标
                metrics["pe"] = "N/A（无总股本数据）"
                metrics["pb"] = "N/A（无总股本数据）"
                metrics["ps"] = "N/A（无总股本数据）"

            # ROE
            if total_equity > 0 and net_income > 0:
                roe = (net_income / total_equity) * 100
                metrics["roe"] = f"{roe:.1f}%"
            else:
                metrics["roe"] = "N/A"

            # ROA
            if total_assets > 0 and net_income > 0:
                roa = (net_income / total_assets) * 100
                metrics["roa"] = f"{roa:.1f}%"
            else:
                metrics["roa"] = "N/A"

            # 净利率
            if total_revenue > 0 and net_income > 0:
                net_margin = (net_income / total_revenue) * 100
                metrics["net_margin"] = f"{net_margin:.1f}%"
            else:
                metrics["net_margin"] = "N/A"

            # 资产负债率
            if total_assets > 0:
                debt_ratio = (total_liab / total_assets) * 100
                metrics["debt_ratio"] = f"{debt_ratio:.1f}%"
            else:
                metrics["debt_ratio"] = "N/A"

            # 其他指标设为默认值
            metrics.update({
                "dividend_yield": "待查询",
                "gross_margin": "待计算",
                "current_ratio": "待计算",
                "quick_ratio": "待计算",
                "cash_ratio": "待分析"
            })

            # 评分（基于真实数据的简化评分）
            fundamental_score = self._calculate_fundamental_score(metrics, stock_info)
            valuation_score = self._calculate_valuation_score(metrics)
            growth_score = self._calculate_growth_score(metrics, stock_info)
            risk_level = self._calculate_risk_level(metrics, stock_info)

            metrics.update({
                "fundamental_score": fundamental_score,
                "valuation_score": valuation_score,
                "growth_score": growth_score,
                "risk_level": risk_level
            })

            return metrics

        except Exception as e:
            logger.error(f"解析财务数据失败: {e}")
            return None
