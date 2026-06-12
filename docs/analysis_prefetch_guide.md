# 数据预取版实验脚本使用文档

## 概述

`run_analysis_test.py` 已重写为**数据预取版**，核心改进：在运行 LangGraph 多Agent分析流程之前，一次性获取所有股票数据并缓存，避免重复网络请求。

## 改了什么

### 1. 新增：数据预取基础设施

| 组件 | 说明 |
|------|------|
| `_data_cache` | 全局缓存字典，key=(数据类型, 股票代码)，value=缓存结果 |
| `_originals` | 保存原始函数引用，用于恢复补丁 |
| `prefetch_stock_data()` | 预取单只股票的5类数据，支持重试和指数退避 |
| `apply_data_cache_patches()` | 应用 monkey-patch，将接口函数替换为缓存版本 |
| `clear_data_cache_patches()` | 清除补丁，恢复原始函数 |

### 2. 新增：5类数据预取

| 数据类型 | 获取方式 | 被调用次数/股 | 预取后 |
|---------|---------|-------------|--------|
| 股票基本信息 | `interface.get_china_stock_info_unified()` | 6-7次 | 1次 |
| 市场数据 | `interface.get_china_stock_data_unified()` | 2-3次 | 1次 |
| 基本面报告 | `OptimizedChinaDataProvider()._generate_fundamentals_report()` | 1-2次 | 1次 |
| 新闻数据 | `UnifiedNewsAnalyzer.get_stock_news_unified()` | 1-2次 | 1次 |
| 情绪数据 | 模板占位（原为stub） | 1次 | 1次 |

### 3. 新增：4个 monkey-patch

| 补丁目标 | 作用 |
|---------|------|
| `interface.get_china_stock_data_unified` | 市场数据缓存命中时直接返回，不请求网络 |
| `interface.get_china_stock_info_unified` | 股票信息缓存命中时直接返回，不请求网络 |
| `FundamentalsReportMixin._generate_fundamentals_report` | 基本面报告缓存命中时直接返回 |
| `UnifiedNewsAnalyzer.get_stock_news_unified` | 新闻数据缓存命中时直接返回 |

### 4. 改动：主流程重构

旧版流程：
```
MACD全部股票 → 单一LLM全部股票 → SinoQuant全部股票
```

新版流程：
```
阶段0: 预取所有股票数据（带重试）→ 应用缓存补丁
阶段1: MACD基准（直接用AKShare，不受补丁影响）
阶段2: 单一LLM基准（通过补丁获取缓存数据）
阶段3: SinoQuant分析（通过补丁获取缓存数据）
阶段4: 统计分析 → 清除补丁
```

### 5. 改动：重试机制

预取阶段每个数据类型支持最多5次重试，指数退避（10s → 20s → 40s → 80s → 160s）。缓存未命中时回退到原始函数（向后兼容）。

### 6. 未改动

以下函数/逻辑未修改：
- `run_macd_baseline()` — 直接用 AKShare，不经过 interface
- `compute_statistics()` — 统计逻辑不变
- `save_results_to_docs()` — 新增了预取统计字段，其余不变
- `get_actual_changes()` — 获取实际涨跌幅逻辑不变

## 怎么跑

### 前提条件

- AKShare 网络可用（预取阶段需要成功获取数据）
- 至少一个 LLM API Key 配置（当前使用 modelverse deepseek-v4-pro）

### 运行命令

```bash
# 从项目根目录运行
python run_analysis_test.py
```

### 运行流程说明

1. **阶段0 - 数据预取**：逐股票获取5类数据，每类有5次重试机会。如果某类数据获取失败，该类在分析阶段会回退到实时获取（可能也失败，但不会崩溃）。预取进度实时打印。

2. **阶段1 - MACD基准**：直接调用 AKShare 获取历史K线计算MACD指标，不经过缓存补丁。最快，约10-20秒完成5只股票。

3. **阶段2 - 单一LLM基准**：通过缓存补丁获取股票信息和市场数据，直接调用LLM生成分析报告。

4. **阶段3 - SinoQuant全量分析**：4分析师 + 3轮投资辩论 + 3轮风险辩论。所有数据请求通过缓存补丁返回，不再重复网络调用。

5. **阶段4 - 统计与保存**：计算方向准确率、二项检验、错误归因，保存 JSON 和 Markdown 报告到 `docs/analysis/`。

### 输出文件

- `docs/analysis/full_analysis_deepseek-v4-pro_YYYYMMDD_HHMMSS.json` — 完整实验数据
- `docs/analysis/full_analysis_deepseek-v4-pro_YYYYMMDD_HHMMSS.md` — Markdown报告

### 预计耗时

| 阶段 | 预计耗时 |
|------|---------|
| 数据预取 | 2-5分钟（网络正常时），最长约30分钟（含重试） |
| MACD基准 | 10-20秒 |
| 单一LLM基准 | 5-15分钟 |
| SinoQuant分析 | 40-90分钟（5只股票，每只约10-20分钟） |
| **总计** | **约50-120分钟** |

### 缓存命中标识

运行时可通过以下输出确认缓存是否生效：

```
📦 [缓存命中] 市场数据: 600519    ← 缓存生效，无网络请求
🌐 [缓存未命中] 获取市场数据: 600519  ← 缓存无数据，回退到实时获取
```

## 技术原理

### Monkey-Patch 为什么有效

Toolkit 工具方法和研究员函数内部使用**局部导入**（`from sinoquant.dataflows.interface import get_china_stock_data_unified`），Python 在每次函数调用时解析模块属性。因此在调用前替换模块属性，局部导入会获取到补丁版本。

已验证：
- `interface` 模块函数补丁 → 局部导入正确传播 ✅
- `FundamentalsReportMixin` 类方法补丁 → 子类 `OptimizedChinaDataProvider` 实例正确传播 ✅
- `UnifiedNewsAnalyzer` 类方法补丁 → 新创建的实例正确传播 ✅

### 仅修改脚本文件

所有改动集中在 `run_analysis_test.py`，未修改 `sinoquant/` 下的任何项目源文件。补丁在脚本退出时自动清除（`finally` 块）。
