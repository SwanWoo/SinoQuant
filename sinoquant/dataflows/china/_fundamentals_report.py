"""
基本面报告生成 mixin

包含基本面分析报告生成、行业信息查询、市场类型判断、
备用数据生成、特殊股票信息等功能。
"""

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from sinoquant.config.runtime_settings import get_timezone_name, use_app_cache_enabled
from sinoquant.utils.logging_manager import get_logger

logger = get_logger('agents')


class FundamentalsReportMixin:
    """基本面报告生成 mixin — 提供报告生成、行业分析、备用数据等功能"""

    # ------------------------------------------------------------------
    # 基本面报告生成
    # ------------------------------------------------------------------

    def _generate_fundamentals_report(self, symbol: str, stock_data: str, analysis_modules: str = "standard") -> str:
        """基于股票数据生成真实的基本面分析报告

        Args:
            symbol: 股票代码
            stock_data: 股票数据
            analysis_modules: 分析模块级别 ("basic", "standard", "full", "detailed", "comprehensive")
        """

        # 添加详细的股票代码追踪日志
        logger.debug(f"🔍 [股票代码追踪] _generate_fundamentals_report 接收到的股票代码: '{symbol}' (类型: {type(symbol)})")
        logger.debug(f"🔍 [股票代码追踪] 股票代码长度: {len(str(symbol))}")
        logger.debug(f"🔍 [股票代码追踪] 股票代码字符: {list(str(symbol))}")
        logger.debug(f"🔍 [股票代码追踪] 接收到的股票数据前200字符: {stock_data[:200] if stock_data else 'None'}")

        # 从股票数据中提取信息
        company_name = "未知公司"
        current_price = "N/A"
        volume = "N/A"
        change_pct = "N/A"

        # 首先尝试从统一接口获取股票基本信息
        try:
            logger.debug(f"🔍 [股票代码追踪] 尝试获取{symbol}的基本信息...")
            from ..interface import get_china_stock_info_unified
            stock_info = get_china_stock_info_unified(symbol)
            logger.debug(f"🔍 [股票代码追踪] 获取到的股票信息: {stock_info}")

            if "股票名称:" in stock_info:
                lines = stock_info.split('\n')
                for line in lines:
                    if "股票名称:" in line:
                        company_name = line.split(':')[1].strip()
                        logger.debug(f"🔍 [股票代码追踪] 从统一接口获取到股票名称: {company_name}")
                        break
        except Exception as e:
            logger.warning(f"⚠️ 获取股票基本信息失败: {e}")

        # 若仍缺失当前价格/涨跌幅/成交量，且启用app缓存，则直接读取 market_quotes 兜底
        try:
            if (current_price == "N/A" or change_pct == "N/A" or volume == "N/A"):
                from sinoquant.config.runtime_settings import use_app_cache_enabled  # type: ignore
                if use_app_cache_enabled(False):
                    from ..cache.app_adapter import get_market_quote_dataframe
                    df_q = get_market_quote_dataframe(symbol)
                    if df_q is not None and not df_q.empty:
                        row_q = df_q.iloc[-1]
                        if current_price == "N/A" and row_q.get('close') is not None:
                            current_price = str(row_q.get('close'))
                            logger.debug(f"🔍 [股票代码追踪] 从market_quotes补齐当前价格: {current_price}")
                        if change_pct == "N/A" and row_q.get('pct_chg') is not None:
                            try:
                                change_pct = f"{float(row_q.get('pct_chg')):+.2f}%"
                            except Exception:
                                change_pct = str(row_q.get('pct_chg'))
                            logger.debug(f"🔍 [股票代码追踪] 从market_quotes补齐涨跌幅: {change_pct}")
                        if volume == "N/A" and row_q.get('volume') is not None:
                            volume = str(row_q.get('volume'))
                            logger.debug(f"🔍 [股票代码追踪] 从market_quotes补齐成交量: {volume}")
        except Exception as _qe:
            logger.debug(f"🔍 [股票代码追踪] 读取market_quotes失败（忽略）: {_qe}")

        # 然后从股票数据中提取价格信息
        if "股票名称:" in stock_data:
            lines = stock_data.split('\n')
            for line in lines:
                if "股票名称:" in line and company_name == "未知公司":
                    company_name = line.split(':')[1].strip()
                elif "当前价格:" in line:
                    current_price = line.split(':')[1].strip()
                elif "最新价格:" in line or "💰 最新价格:" in line:
                    # 兼容另一种模板输出
                    try:
                        current_price = line.split(':', 1)[1].strip().lstrip('¥').strip()
                    except Exception:
                        current_price = line.split(':')[-1].strip()
                elif "涨跌幅:" in line:
                    change_pct = line.split(':')[1].strip()
                elif "成交量:" in line:
                    volume = line.split(':')[1].strip()

        # 尝试从股票数据表格中提取最新价格信息
        if current_price == "N/A" and stock_data:
            try:
                lines = stock_data.split('\n')
                for i, line in enumerate(lines):
                    if "最新数据:" in line and i + 1 < len(lines):
                        # 查找数据行
                        for j in range(i + 1, min(i + 5, len(lines))):
                            data_line = lines[j].strip()
                            if data_line and not data_line.startswith('日期') and not data_line.startswith('-'):
                                # 尝试解析数据行
                                parts = data_line.split()
                                if len(parts) >= 4:
                                    try:
                                        # 假设格式: 日期 股票代码 开盘 收盘 最高 最低 成交量 成交额...
                                        current_price = parts[3]  # 收盘价
                                        logger.debug(f"🔍 [股票代码追踪] 从数据表格提取到收盘价: {current_price}")
                                        break
                                    except (IndexError, ValueError):
                                        continue
                        break
            except Exception as e:
                logger.debug(f"🔍 [股票代码追踪] 解析股票数据表格失败: {e}")

        # 根据股票代码判断行业和基本信息
        logger.debug(f"🔍 [股票代码追踪] 调用 _get_industry_info，传入参数: '{symbol}'")
        industry_info = self._get_industry_info(symbol)
        logger.debug(f"🔍 [股票代码追踪] _get_industry_info 返回结果: {industry_info}")

        # 尝试获取财务指标，如果失败则返回简化的基本面报告
        logger.debug(f"🔍 [股票代码追踪] 调用 _estimate_financial_metrics，传入参数: '{symbol}'")
        try:
            financial_estimates = self._estimate_financial_metrics(symbol, current_price)
            logger.debug(f"🔍 [股票代码追踪] _estimate_financial_metrics 返回结果: {financial_estimates}")
        except Exception as e:
            logger.warning(f"⚠️ [基本面分析] 无法获取财务指标: {e}")
            logger.info(f"📊 [基本面分析] 返回简化的基本面报告（无财务指标）")

            # 返回简化的基本面报告（不包含财务指标）
            simplified_report = f"""# 中国A股基本面分析报告 - {symbol} (简化版)

## 📊 基本信息
- **股票代码**: {symbol}
- **公司名称**: {company_name}
- **所属行业**: {industry_info.get('industry', '未知')}
- **当前价格**: {current_price}
- **涨跌幅**: {change_pct}
- **成交量**: {volume}

## 📈 行业分析
{industry_info.get('analysis', '暂无行业分析')}

## ⚠️ 数据说明
由于无法获取完整的财务数据，本报告仅包含基本价格信息和行业分析。
建议：
1. 查看公司最新财报获取详细财务数据
2. 关注行业整体走势
3. 结合技术分析进行综合判断

---
**生成时间**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
**数据来源**: 基础市场数据
"""
            return simplified_report.strip()

        logger.debug(f"🔍 [股票代码追踪] 开始生成报告，使用股票代码: '{symbol}'")

        # 检查数据来源并生成相应说明
        data_source_note = ""
        data_source = financial_estimates.get('data_source', '')

        if any("（估算值）" in str(v) for v in financial_estimates.values() if isinstance(v, str)):
            data_source_note = "\n⚠️ **数据说明**: 部分财务指标为估算值，建议结合最新财报数据进行分析"
        elif data_source == "AKShare":
            data_source_note = "\n✅ **数据说明**: 财务指标基于AKShare真实财务数据计算"
        elif data_source == "Tushare":
            data_source_note = "\n✅ **数据说明**: 财务指标基于Tushare真实财务数据计算"
        else:
            data_source_note = "\n✅ **数据说明**: 财务指标基于真实财务数据计算"

        # 根据分析模块级别调整报告内容
        logger.debug(f"🔍 [基本面分析] 使用分析模块级别: {analysis_modules}")

        if analysis_modules == "basic":
            # 基础模式：只包含核心财务指标
            report = f"""# 中国A股基本面分析报告 - {symbol} (基础版)

## 📊 股票基本信息
- **股票代码**: {symbol}
- **股票名称**: {company_name}
- **当前股价**: {current_price}
- **涨跌幅**: {change_pct}
- **分析日期**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y年%m月%d日')}{data_source_note}

## 💰 核心财务指标
- **总市值**: {financial_estimates.get('total_mv', 'N/A')}
- **市盈率(PE)**: {financial_estimates.get('pe', 'N/A')}
- **市盈率TTM(PE_TTM)**: {financial_estimates.get('pe_ttm', 'N/A')}
- **市净率(PB)**: {financial_estimates.get('pb', 'N/A')}
- **净资产收益率(ROE)**: {financial_estimates.get('roe', 'N/A')}
- **资产负债率**: {financial_estimates.get('debt_ratio', 'N/A')}

## 💡 基础评估
- **基本面评分**: {financial_estimates['fundamental_score']}/10
- **风险等级**: {financial_estimates['risk_level']}

---
**重要声明**: 本报告基于公开数据和模型估算生成，仅供参考，不构成投资建议。
**数据来源**: {data_source if data_source else "多源数据"}数据接口
**生成时间**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
"""
        elif analysis_modules in ["standard", "full"]:
            # 标准/完整模式：包含详细分析
            report = f"""# 中国A股基本面分析报告 - {symbol}

## 📊 股票基本信息
- **股票代码**: {symbol}
- **股票名称**: {company_name}
- **所属行业**: {industry_info['industry']}
- **市场板块**: {industry_info['market']}
- **当前股价**: {current_price}
- **涨跌幅**: {change_pct}
- **成交量**: {volume}
- **分析日期**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y年%m月%d日')}{data_source_note}

## 💰 财务数据分析

### 估值指标
- **总市值**: {financial_estimates.get('total_mv', 'N/A')}
- **市盈率(PE)**: {financial_estimates.get('pe', 'N/A')}
- **市盈率TTM(PE_TTM)**: {financial_estimates.get('pe_ttm', 'N/A')}
- **市净率(PB)**: {financial_estimates.get('pb', 'N/A')}
- **市销率(PS)**: {financial_estimates.get('ps', 'N/A')}
- **股息收益率**: {financial_estimates.get('dividend_yield', 'N/A')}

### 盈利能力指标
- **净资产收益率(ROE)**: {financial_estimates['roe']}
- **总资产收益率(ROA)**: {financial_estimates['roa']}
- **毛利率**: {financial_estimates['gross_margin']}
- **净利率**: {financial_estimates['net_margin']}

### 财务健康度
- **资产负债率**: {financial_estimates['debt_ratio']}
- **流动比率**: {financial_estimates['current_ratio']}
- **速动比率**: {financial_estimates['quick_ratio']}
- **现金比率**: {financial_estimates['cash_ratio']}

## 📈 行业分析
{industry_info['analysis']}

## 🎯 投资价值评估
### 估值水平分析
{self._analyze_valuation(financial_estimates)}

### 成长性分析
{self._analyze_growth_potential(symbol, industry_info)}

## 💡 投资建议
- **基本面评分**: {financial_estimates['fundamental_score']}/10
- **估值吸引力**: {financial_estimates['valuation_score']}/10
- **成长潜力**: {financial_estimates['growth_score']}/10
- **风险等级**: {financial_estimates['risk_level']}

{self._generate_investment_advice(financial_estimates, industry_info)}

---
**重要声明**: 本报告基于公开数据和模型估算生成，仅供参考，不构成投资建议。
**数据来源**: {data_source if data_source else "多源数据"}数据接口
**生成时间**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
"""
        else:  # detailed, comprehensive
            # 详细/全面模式：包含最完整的分析
            report = f"""# 中国A股基本面分析报告 - {symbol} (全面版)

## 📊 股票基本信息
- **股票代码**: {symbol}
- **股票名称**: {company_name}
- **所属行业**: {industry_info['industry']}
- **市场板块**: {industry_info['market']}
- **当前股价**: {current_price}
- **涨跌幅**: {change_pct}
- **成交量**: {volume}
- **分析日期**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y年%m月%d日')}{data_source_note}

## 💰 财务数据分析

### 估值指标
- **总市值**: {financial_estimates.get('total_mv', 'N/A')}
- **市盈率(PE)**: {financial_estimates.get('pe', 'N/A')}
- **市盈率TTM(PE_TTM)**: {financial_estimates.get('pe_ttm', 'N/A')}
- **市净率(PB)**: {financial_estimates.get('pb', 'N/A')}
- **市销率(PS)**: {financial_estimates.get('ps', 'N/A')}
- **股息收益率**: {financial_estimates.get('dividend_yield', 'N/A')}

### 盈利能力指标
- **净资产收益率(ROE)**: {financial_estimates.get('roe', 'N/A')}
- **总资产收益率(ROA)**: {financial_estimates.get('roa', 'N/A')}
- **毛利率**: {financial_estimates.get('gross_margin', 'N/A')}
- **净利率**: {financial_estimates.get('net_margin', 'N/A')}

### 财务健康度
- **资产负债率**: {financial_estimates['debt_ratio']}
- **流动比率**: {financial_estimates['current_ratio']}
- **速动比率**: {financial_estimates['quick_ratio']}
- **现金比率**: {financial_estimates['cash_ratio']}

## 📈 行业分析

### 行业地位
{industry_info['analysis']}

### 竞争优势
- **市场份额**: {industry_info['market_share']}
- **品牌价值**: {industry_info['brand_value']}
- **技术优势**: {industry_info['tech_advantage']}

## 🎯 投资价值评估

### 估值水平分析
{self._analyze_valuation(financial_estimates)}

### 成长性分析
{self._analyze_growth_potential(symbol, industry_info)}

### 风险评估
{self._analyze_risks(symbol, financial_estimates, industry_info)}

## 💡 投资建议

### 综合评分
- **基本面评分**: {financial_estimates['fundamental_score']}/10
- **估值吸引力**: {financial_estimates['valuation_score']}/10
- **成长潜力**: {financial_estimates['growth_score']}/10
- **风险等级**: {financial_estimates['risk_level']}

### 操作建议
{self._generate_investment_advice(financial_estimates, industry_info)}

### 绝对估值
- **DCF估值**：基于现金流贴现的内在价值
- **资产价值**：净资产重估价值
- **分红收益率**：股息回报分析

## 风险分析
### 系统性风险
- **宏观经济风险**：经济周期对公司的影响
- **政策风险**：行业政策变化的影响
- **市场风险**：股市波动对估值的影响

### 非系统性风险
- **经营风险**：公司特有的经营风险
- **财务风险**：债务结构和偿债能力风险
- **管理风险**：管理层变动和决策风险

## 投资建议
### 综合评价
基于以上分析，该股票的投资价值评估：

**优势：**
- A股市场上市公司，监管相对完善
- 具备一定的市场地位和品牌价值
- 财务信息透明度较高

**风险：**
- 需要关注宏观经济环境变化
- 行业竞争加剧的影响
- 政策调整对业务的潜在影响

### 操作建议
- **投资策略**：建议采用价值投资策略，关注长期基本面
- **仓位建议**：根据风险承受能力合理配置仓位
- **关注指标**：重点关注ROE、PE、现金流等核心指标

---
**重要声明**: 本报告基于公开数据和模型估算生成，仅供参考，不构成投资建议。
实际投资决策请结合最新财报数据和专业分析师意见。

**数据来源**: {data_source if data_source else "多源数据"}数据接口 + 基本面分析模型
**生成时间**: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
"""

        return report

    # ------------------------------------------------------------------
    # 行业信息与市场类型
    # ------------------------------------------------------------------

    def _get_industry_info(self, symbol: str) -> dict:
        """根据股票代码获取行业信息（优先使用数据库真实数据）"""

        # 添加详细的股票代码追踪日志
        logger.debug(f"🔍 [股票代码追踪] _get_industry_info 接收到的股票代码: '{symbol}' (类型: {type(symbol)})")
        logger.debug(f"🔍 [股票代码追踪] 股票代码长度: {len(str(symbol))}")
        logger.debug(f"🔍 [股票代码追踪] 股票代码字符: {list(str(symbol))}")

        # 首先尝试从数据库获取真实的行业信息
        try:
            from ..cache.app_adapter import get_basics_from_cache
            doc = get_basics_from_cache(symbol)
            if doc:
                # 只记录关键字段，避免打印完整文档
                logger.debug(f"🔍 [股票代码追踪] 从数据库获取到基础信息: code={doc.get('code')}, name={doc.get('name')}, industry={doc.get('industry')}")

                # 规范化行业与板块（避免把"中小板/创业板"等板块值误作行业）
                board_labels = {'主板', '中小板', '创业板', '科创板'}
                raw_industry = (doc.get('industry') or doc.get('industry_name') or '').strip()
                sec_or_cat = (doc.get('sec') or doc.get('category') or '').strip()
                market_val = (doc.get('market') or '').strip()
                industry_val = raw_industry or sec_or_cat or '未知'

                # 如果industry字段是板块名，则将其用于market；industry改用更细分类（sec/category）
                if raw_industry in board_labels:
                    if not market_val:
                        market_val = raw_industry
                    if sec_or_cat:
                        industry_val = sec_or_cat
                    logger.debug(f"🔧 [字段归一化] industry原值='{raw_industry}' → 行业='{industry_val}', 市场/板块='{market_val}'")

                # 构建行业信息
                info = {
                    "industry": industry_val or '未知',
                    "market": market_val or doc.get('market', '未知'),
                    "type": self._get_market_type_by_code(symbol)
                }

                logger.debug(f"🔍 [股票代码追踪] 从数据库获取的行业信息: {info}")

                # 添加特殊股票的详细分析
                if symbol in self._get_special_stocks():
                    info.update(self._get_special_stocks()[symbol])
                else:
                    info.update({
                        "analysis": f"该股票属于{info['industry']}行业，在{info['market']}上市交易。",
                        "market_share": "待分析",
                        "brand_value": "待评估",
                        "tech_advantage": "待分析"
                    })

                return info

        except Exception as e:
            logger.warning(f"⚠️ 从数据库获取行业信息失败: {e}")

        # 备用方案：使用代码前缀判断（但修正了行业/市场的映射）
        logger.debug(f"🔍 [股票代码追踪] 使用备用方案，基于代码前缀判断")
        code_prefix = symbol[:3]
        logger.debug(f"🔍 [股票代码追踪] 提取的代码前缀: '{code_prefix}'")

        # 修正后的映射表：区分行业和市场板块
        market_map = {
            "000": {"market": "主板", "exchange": "深圳证券交易所", "type": "综合"},
            "001": {"market": "主板", "exchange": "深圳证券交易所", "type": "综合"},
            "002": {"market": "主板", "exchange": "深圳证券交易所", "type": "成长型"},  # 002开头现在也是主板
            "003": {"market": "创业板", "exchange": "深圳证券交易所", "type": "创新型"},
            "300": {"market": "创业板", "exchange": "深圳证券交易所", "type": "高科技"},
            "600": {"market": "主板", "exchange": "上海证券交易所", "type": "大盘蓝筹"},
            "601": {"market": "主板", "exchange": "上海证券交易所", "type": "大盘蓝筹"},
            "603": {"market": "主板", "exchange": "上海证券交易所", "type": "中小盘"},
            "688": {"market": "科创板", "exchange": "上海证券交易所", "type": "科技创新"},
        }

        market_info = market_map.get(code_prefix, {
            "market": "未知市场",
            "exchange": "未知交易所",
            "type": "综合"
        })

        info = {
            "industry": "未知",  # 无法从代码前缀准确判断具体行业
            "market": market_info["market"],
            "type": market_info["type"]
        }

        # 特殊股票的详细信息
        special_stocks = self._get_special_stocks()
        if symbol in special_stocks:
            info.update(special_stocks[symbol])
        else:
            info.update({
                "analysis": f"该股票在{info['market']}上市交易，具体行业信息需要进一步查询。",
                "market_share": "待分析",
                "brand_value": "待评估",
                "tech_advantage": "待分析"
            })

        return info

    def _get_market_type_by_code(self, symbol: str) -> str:
        """根据股票代码判断市场类型"""
        code_prefix = symbol[:3]
        type_map = {
            "000": "综合", "001": "综合", "002": "成长型", "003": "创新型",
            "300": "高科技", "600": "大盘蓝筹", "601": "大盘蓝筹",
            "603": "中小盘", "688": "科技创新"
        }
        return type_map.get(code_prefix, "综合")

    def _get_special_stocks(self) -> dict:
        """获取特殊股票的详细信息"""
        return {
            "000001": {
                "industry": "银行业",
                "analysis": "平安银行是中国领先的股份制商业银行，在零售银行业务方面具有显著优势。",
                "market_share": "股份制银行前列",
                "brand_value": "知名金融品牌",
                "tech_advantage": "金融科技创新领先"
            },
            "600036": {
                "industry": "银行业",
                "analysis": "招商银行是中国优质的股份制银行，零售银行业务和财富管理业务领先。",
                "market_share": "股份制银行龙头",
                "brand_value": "优质银行品牌",
                "tech_advantage": "数字化银行先锋"
            },
            "000002": {
                "industry": "房地产",
                "analysis": "万科A是中国房地产行业龙头企业，在住宅开发领域具有领先地位。",
                "market_share": "房地产行业前三",
                "brand_value": "知名地产品牌",
                "tech_advantage": "绿色建筑技术"
            },
            "002475": {
                "industry": "元器件",
                "analysis": "立讯精密是全球领先的精密制造服务商，主要从事连接器、声学、无线充电等产品的研发制造。",
                "market_share": "消费电子连接器龙头",
                "brand_value": "精密制造知名品牌",
                "tech_advantage": "精密制造技术领先"
            }
        }

    # ------------------------------------------------------------------
    # 备用数据生成
    # ------------------------------------------------------------------

    def _generate_fallback_data(self, symbol: str, start_date: str, end_date: str, error_msg: str) -> str:
        """生成备用数据"""
        return f"""# {symbol} A股数据获取失败

## ❌ 错误信息
{error_msg}

## 📊 模拟数据（仅供演示）
- 股票代码: {symbol}
- 股票名称: 模拟公司
- 数据期间: {start_date} 至 {end_date}
- 模拟价格: ¥{random.uniform(10, 50):.2f}
- 模拟涨跌: {random.uniform(-5, 5):+.2f}%

## ⚠️ 重要提示
由于数据接口限制或网络问题，无法获取实时数据。
建议稍后重试或检查网络连接。

生成时间: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
"""

    def _generate_fallback_fundamentals(self, symbol: str, error_msg: str) -> str:
        """生成备用基本面数据"""
        return f"""# {symbol} A股基本面分析失败

## ❌ 错误信息
{error_msg}

## 📊 基本信息
- 股票代码: {symbol}
- 分析状态: 数据获取失败
- 建议: 稍后重试或检查网络连接

生成时间: {datetime.now(ZoneInfo(get_timezone_name())).strftime('%Y-%m-%d %H:%M:%S')}
"""
