# SinaQuant 股票数据获取方法速查表

## 🚀 快速开始

### 最推荐的统一接口

```python
# 自动识别市场类型，一个接口搞定所有股票
from sinoquant.dataflows.interface import get_stock_data_by_market

# A股: 000001, 002475
# 港股: 0700.HK, 0941.HK  
# 美股: AAPL, TSLA
data = get_stock_data_by_market("000001", "2024-01-01", "2024-12-31")
```

## 📊 按数据类型分类

### 1. 历史价格数据

| 方法 | 适用市场 | 推荐度 | 说明 |
|------|----------|--------|------|
| `get_stock_data_by_market()` | 全市场 | ⭐⭐⭐⭐⭐ | 自动识别市场，最推荐 |
| `get_china_stock_data_unified()` | A股 | ⭐⭐⭐⭐ | A股专用，支持多数据源 |
| `get_us_stock_data_cached()` | 美股 | ⭐⭐⭐⭐ | 美股专用，带缓存 |
| `get_hk_stock_data_unified()` | 港股 | ⭐⭐⭐⭐ | 港股专用 |

### 2. 股票基本信息

| 方法 | 适用市场 | 推荐度 | 返回数据 |
|------|----------|--------|----------|
| `get_china_stock_info_unified()` | A股 | ⭐⭐⭐⭐⭐ | 名称、行业、市场、上市日期 |
| `get_stock_info()` | 全市场 | ⭐⭐⭐⭐ | 基础信息字典 |

### 3. 基本面分析

| 方法 | 适用市场 | 推荐度 | 返回数据 |
|------|----------|--------|----------|
| `get_china_fundamentals_cached()` | A股 | ⭐⭐⭐⭐⭐ | 完整基本面分析报告 |
| `get_china_stock_fundamentals_tushare()` | A股 | ⭐⭐⭐⭐ | Tushare基本面数据 |

### 4. 财务数据

| 方法 | 适用市场 | 推荐度 | 返回数据 |
|------|----------|--------|----------|
| `get_financial_data()` | A股 | ⭐⭐⭐⭐ | 原始财务数据 |
| `get_balance_sheet()` | A股 | ⭐⭐⭐ | 资产负债表 |
| `get_income_statement()` | A股 | ⭐⭐⭐ | 利润表 |
| `get_cashflow_statement()` | A股 | ⭐⭐⭐ | 现金流量表 |

### 5. 实时数据

| 方法 | 适用市场 | 推荐度 | 返回数据 |
|------|----------|--------|----------|
| `get_realtime_quotes()` | A股 | ⭐⭐⭐⭐ | 实时行情快照 |
| `get_realtime_data()` | A股 | ⭐⭐⭐ | 单只股票实时数据 |

### 6. 新闻数据

| 方法 | 适用市场 | 推荐度 | 返回数据 |
|------|----------|--------|----------|
| `get_realtime_stock_news()` | 全市场 | ⭐⭐⭐⭐⭐ | 实时股票新闻 |
| `get_finnhub_news()` | 美股 | ⭐⭐⭐⭐ | Finnhub新闻 |
| `get_google_news()` | 全市场 | ⭐⭐⭐ | Google新闻搜索 |

## 🔧 按使用场景分类

### 场景1: 股票分析师 - 基本面分析

```python
from sinoquant.dataflows.optimized_china_data import get_china_fundamentals_cached

# 获取完整的基本面分析报告
report = get_china_fundamentals_cached("000001")  # 平安银行
print(report)
```

**获取的数据包括:**
- 公司基本信息 (名称、行业、市场)
- 财务指标 (PE、PB、ROE、ROA)
- 盈利能力分析
- 财务健康状况
- 行业对比

### 场景2: 量化交易员 - 历史数据分析

```python
from sinoquant.dataflows.interface import get_stock_data_by_market

# 获取历史价格数据
data = get_stock_data_by_market("000001", "2024-01-01", "2024-12-31")
print(data)
```

**获取的数据包括:**
- 每日开盘价、收盘价、最高价、最低价
- 成交量、成交额
- 涨跌幅、涨跌额
- 技术指标计算基础数据

### 场景3: 新闻分析师 - 情绪分析

```python
from sinoquant.dataflows.realtime_news_utils import RealtimeNewsAggregator

aggregator = RealtimeNewsAggregator()
news = aggregator.get_realtime_stock_news("AAPL", hours_back=24, max_news=10)
```

**获取的数据包括:**
- 最新股票相关新闻
- 新闻来源和时间
- 新闻标题和摘要
- 情绪分析标签

### 场景4: 风险管理员 - 实时监控

```python
from sinoquant.dataflows.akshare_utils import get_akshare_provider

provider = get_akshare_provider()
quotes = provider.get_realtime_quotes()  # 全市场实时行情
```

**获取的数据包括:**
- 实时价格和涨跌幅
- 成交量和成交额
- 市场热点股票
- 异常波动提醒

## 🎯 数据源选择指南

### 按数据质量排序

**A股数据源质量排序:**
1. **Tushare** ⭐⭐⭐⭐⭐ - 专业级，需要token
2. **AKShare** ⭐⭐⭐⭐ - 开源免费，质量高
3. **BaoStock** ⭐⭐⭐ - 免费，基础数据
4. **TDX** ⭐⭐ - 个人接口，将淘汰

**美股数据源质量排序:**
1. **Yahoo Finance** ⭐⭐⭐⭐ - 免费，数据全面
2. **Finnhub** ⭐⭐⭐⭐⭐ - 专业级，付费

**港股数据源质量排序:**
1. **AKShare** ⭐⭐⭐⭐ - 港股支持好
2. **Yahoo Finance** ⭐⭐⭐ - 国际数据

### 按使用成本排序

**免费数据源:**
- AKShare (A股、港股)
- Yahoo Finance (美股、港股)
- BaoStock (A股)

**付费数据源:**
- Tushare (A股) - 需要积分或付费
- Finnhub (美股) - 专业API付费

## ⚡ 性能优化技巧

### 1. 启用数据库缓存
```bash
export TA_USE_APP_CACHE=true
```

### 2. 批量获取数据
```python
# 推荐：批量处理
symbols = ['000001', '000002', '000858']
results = {}
for symbol in symbols:
    results[symbol] = get_china_fundamentals_cached(symbol)
```

### 3. 合理设置API调用间隔
```bash
export TA_CHINA_MIN_API_INTERVAL_SECONDS=0.5  # A股API间隔
export TA_US_MIN_API_INTERVAL_SECONDS=1.0     # 美股API间隔
```

## 🚨 常见问题解决

### 问题1: 数据获取失败
**解决方案:**
1. 检查网络连接
2. 确认股票代码格式正确
3. 检查API token配置
4. 查看日志错误信息

### 问题2: 数据更新不及时
**解决方案:**
1. 使用 `force_refresh=True` 强制刷新
2. 检查缓存过期时间设置
3. 切换到实时数据接口

### 问题3: API调用频率限制
**解决方案:**
1. 增加API调用间隔时间
2. 启用缓存减少API调用
3. 使用批量接口

## 📞 技术支持

- **文档**: `docs/STOCK_DATA_METHODS_ANALYSIS.md`
- **示例**: `examples/` 目录
- **测试**: `tests/` 目录
- **日志**: 查看控制台输出和日志文件

---

*快速参考 - 最后更新: 2025-09-28*
