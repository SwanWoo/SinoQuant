# 缓存配置指南

## 📋 概述

SinaQuant 支持多种缓存策略，可以根据部署环境和性能需求灵活选择。

---

## 🎯 缓存策略对比

| 策略 | 存储方式 | 性能 | 依赖 | 适用场景 |
|------|---------|------|------|---------|
| **文件缓存** | 本地文件 | ⭐⭐⭐ | 无 | 单机部署、开发环境 |
| **集成缓存** | MongoDB + Redis + File | ⭐⭐⭐⭐⭐ | MongoDB/Redis（可选） | 生产环境、分布式部署 |

---

## 🚀 快速开始

### 默认配置（文件缓存）

无需任何配置，开箱即用：

```python
from sinoquant.dataflows.cache import get_cache

cache = get_cache()  # 自动使用文件缓存
```

**特点**：
- ✅ 无需外部依赖
- ✅ 简单稳定
- ✅ 适合单机部署

---

## 🔧 启用集成缓存

集成缓存支持 MongoDB + Redis，性能更好，支持分布式部署。

### 方法 1: 环境变量（推荐）

#### Linux / Mac
```bash
export TA_CACHE_STRATEGY=integrated
```

#### Windows (PowerShell)
```powershell
$env:TA_CACHE_STRATEGY='integrated'
```

#### Windows (CMD)
```cmd
set TA_CACHE_STRATEGY=integrated
```

### 方法 2: .env 文件

在项目根目录创建或编辑 `.env` 文件：

```env
# 缓存策略
TA_CACHE_STRATEGY=integrated

# 数据库配置（可选）
MONGODB_URL=mongodb://localhost:27017
REDIS_URL=redis://localhost:6379
```

### 方法 3: 代码中指定

```python
from sinoquant.dataflows.cache import IntegratedCacheManager

# 直接使用集成缓存
cache = IntegratedCacheManager()
```

---

## 📊 集成缓存配置

### 数据库要求

集成缓存需要配置数据库连接（可选）：

#### MongoDB（推荐）
```bash
# 环境变量
export MONGODB_URL=mongodb://localhost:27017

# 或在 .env 文件中
MONGODB_URL=mongodb://localhost:27017
```

**用途**：
- 持久化缓存数据
- 支持分布式访问
- 自动过期管理

#### Redis（可选）
```bash
# 环境变量
export REDIS_URL=redis://localhost:6379

# 或在 .env 文件中
REDIS_URL=redis://localhost:6379
```

**用途**：
- 高速内存缓存
- 减少数据库查询
- 提升响应速度

### 自动降级

如果 MongoDB/Redis 不可用，集成缓存会**自动降级到文件缓存**，不会影响系统运行。

```
集成缓存初始化流程：
1. 尝试连接 MongoDB/Redis
2. 如果成功 → 使用数据库缓存
3. 如果失败 → 自动降级到文件缓存
4. 系统继续正常运行 ✅
```

---

## 💻 使用示例

### 基本使用

```python
from sinoquant.dataflows.cache import get_cache

# 获取缓存实例（自动选择策略）
cache = get_cache()

# 保存数据
cache.save_stock_data(
    symbol="000001",
    data=df,
    market="china",
    category="stock_data"
)

# 读取数据
cached_data = cache.get_stock_data(
    symbol="000001",
    market="china",
    category="stock_data"
)
```

### 高级使用

```python
from sinoquant.dataflows.cache import (
    get_cache,
    StockDataCache,
    IntegratedCacheManager
)

# 方式 1: 使用统一入口（推荐）
cache = get_cache()

# 方式 2: 直接指定文件缓存
cache = StockDataCache()

# 方式 3: 直接指定集成缓存
cache = IntegratedCacheManager()
```

---

## 🔍 验证配置

### 检查当前缓存策略

```python
from sinoquant.dataflows.cache import get_cache

cache = get_cache()
print(f"当前缓存类型: {type(cache).__name__}")

# 输出示例：
# 文件缓存: StockDataCache
# 集成缓存: IntegratedCacheManager
```

### 检查缓存统计

```python
from sinoquant.dataflows.cache import get_cache

cache = get_cache()

# 如果是集成缓存，可以查看统计信息
if hasattr(cache, 'get_cache_stats'):
    stats = cache.get_cache_stats()
    print(stats)
```

---

## 🎛️ 配置参数

### 环境变量列表

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TA_CACHE_STRATEGY` | `file` | 缓存策略：`file` 或 `integrated` |
| `MONGODB_URL` | - | MongoDB 连接字符串 |
| `REDIS_URL` | - | Redis 连接字符串 |

### 缓存策略值

| 值 | 说明 |
|----|------|
| `file` | 使用文件缓存（默认） |
| `integrated` | 使用集成缓存（MongoDB + Redis + File） |
| `adaptive` | 同 `integrated`（别名） |

---

## 🐛 故障排查

### 问题 1: 集成缓存不可用

**现象**：
```
⚠️ 集成缓存不可用，使用文件缓存
```

**原因**：
- 缺少 `database_manager` 模块
- MongoDB/Redis 连接失败

**解决**：
1. 检查是否安装了必要的依赖
2. 检查 MongoDB/Redis 是否运行
3. 检查连接字符串是否正确
4. 如果不需要数据库缓存，使用文件缓存即可

### 问题 2: 导入错误

**现象**：
```
ImportError: cannot import name 'get_cache'
```

**解决**：
```python
# 正确的导入方式
from sinoquant.dataflows.cache import get_cache

# 错误的导入方式（已废弃）
from sinoquant.dataflows.cache_manager import get_cache
```

---

## 📈 性能优化建议

### 开发环境
- 使用文件缓存
- 简单快速，无需配置

### 生产环境
- 使用集成缓存
- 配置 MongoDB + Redis
- 获得最佳性能

### 分布式部署
- 必须使用集成缓存
- 共享 MongoDB/Redis
- 多个实例共享缓存

---

## 🔄 迁移指南

### 从旧版本迁移

如果你的代码使用了旧的导入方式：

```python
# 旧代码
from sinoquant.dataflows.cache_manager import get_cache
cache = get_cache()
```

**迁移步骤**：

1. 更新导入路径：
```python
# 新代码
from sinoquant.dataflows.cache import get_cache
cache = get_cache()
```

2. 测试验证：
```bash
python -c "from sinoquant.dataflows.cache import get_cache; cache = get_cache(); print('✅ 迁移成功')"
```

3. 可选：启用集成缓存
```bash
export TA_CACHE_STRATEGY=integrated
```

---

## 📚 相关文档

- [缓存系统分析](./CACHE_SYSTEM_BUSINESS_ANALYSIS.md)
- [缓存系统解决方案](./CACHE_SYSTEM_SOLUTION.md)
- [第二阶段优化总结](./PHASE2_REORGANIZATION_SUMMARY.md)

---

## 💡 最佳实践

1. **开发环境**：使用文件缓存，简单快速
2. **生产环境**：使用集成缓存，性能更好
3. **统一入口**：始终使用 `from sinoquant.dataflows.cache import get_cache`
4. **环境变量**：通过环境变量切换缓存策略，不修改代码
5. **自动降级**：依赖集成缓存的自动降级机制，确保系统稳定

---

## 🎉 总结

- ✅ 统一的缓存入口：`get_cache()`
- ✅ 灵活的策略选择：文件缓存 / 集成缓存
- ✅ 自动降级机制：确保系统稳定
- ✅ 简单的配置方式：环境变量 / .env 文件
- ✅ 向后兼容：不破坏现有代码

**开始使用**：
```python
from sinoquant.dataflows.cache import get_cache
cache = get_cache()  # 就这么简单！
```

