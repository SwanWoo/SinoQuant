# SinoQuant

面向中文投资场景的多智能体量化分析系统。结合 LangGraph 多智能体辩论引擎、FastAPI 后端和 Vue 3 前端，提供 A 股 / 港股 / 美股的 AI 分析、量化策略生成与回测、模拟交易等完整能力。

## 核心能力

- **多智能体股票分析** — 市场、基本面、消息面、社交舆情四大分析师 + 多空研究员辩论 → 交易决策
- **量化策略生成** — LLM 自动生成 vnpy AlphaStrategy 代码，安全沙箱验证后直接回测
- **策略回测 & 模拟交易** — vnpy BacktestingEngine 历史验证 + 实时模拟跟踪
- **多数据源同步** — AKShare / BaoStock / Tushare 自动同步，优先级降级 + 定时任务
- **股票筛选 & 批量分析** — 多维度条件筛选，后台批量任务
- **报告导出** — Markdown / Word / PDF
- **Web 配置管理** — 多 LLM 供应商、数据源、定时任务全界面管理
- **实时推送** — SSE 分析进度 + WebSocket 系统通知

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI + APScheduler + MongoDB (motor) + Redis |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus + Pinia |
| 分析引擎 | LangGraph 多智能体辩论 (`sinoquant/`) |
| 量化模块 | vnpy AlphaStrategy + BacktestingEngine |
| 部署 | Docker Compose + Nginx |
| 包管理 | 后端 uv · 前端 npm |

## 环境要求

- Python ≥ 3.10
- Node.js ≥ 18
- MongoDB ≥ 4.4
- Redis ≥ 7
- 至少一个 LLM API Key（推荐 DeepSeek）

## 快速开始

### 1. 安装

```bash
git clone https://github.com/SwanWoo/SinoQuant.git
cd SinoQuant

# 后端依赖（推荐 uv）
uv sync
# 或 pip install -e .

# 前端依赖
cd frontend && npm install && cd ..
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，必填项：

```env
MONGODB_HOST=localhost
MONGODB_PORT=27017
MONGODB_DATABASE=sinoquant
REDIS_HOST=localhost
REDIS_PORT=6379
JWT_SECRET=your-random-secret-key

# 至少配置一个 LLM Key
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

> 其他可选：`OPENAI_API_KEY`、`GOOGLE_API_KEY`、`DASHSCOPE_API_KEY`、`ANTHROPIC_API_KEY`

### 3. 初始化数据

首次运行需同步股票列表（选一个免费数据源即可）：

```bash
python cli/akshare_init.py     # AKShare（推荐，免费）
# python cli/baostock_init.py  # BaoStock（免费）
# python cli/tushare_init.py   # Tushare（需 Token）
```

### 4. 启动

```bash
# 后端
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 前端（新终端）
cd frontend && npm run dev
```

- 后端：`http://localhost:8000` （API 文档：`/docs`）
- 前端：`http://localhost:3000`（自动代理 `/api` → 后端）

### 5. 登录

| 用户名 | 角色 | 密码 |
|--------|------|------|
| `admin` | 管理员 | `qwerty` |
| `user1` | 普通用户 | `qwerty` |
| `user2` | 普通用户 | `qwerty` |

登录后进入 **设置 → 供应商配置** 添加 LLM 供应商并填入 API Key，即可开始分析。

## Docker 部署

```bash
cp .env.example .env.docker
# 编辑 .env.docker 填入真实密码和 API Key

docker compose up -d --build          # 生产环境
docker compose --profile management up -d  # + Redis Commander + Mongo Express
docker compose -f docker-compose.dev.yml up -d  # 开发热重载
```

| 服务 | 端口 | 说明 |
|------|------|------|
| backend | 8000 | FastAPI 后端 |
| frontend | 3000 | Vue 3 前端 (Nginx) |
| mongodb | 27017 | MongoDB |
| redis | 6379 | Redis |
| nginx | 80/443 | 反向代理 |

## 目录结构

```
app/                FastAPI 后端
  core/             配置、数据库、日志
  routers/          REST API 路由
  services/         业务逻辑
    alpha/          量化策略（生成、回测、模拟）
  models/           Pydantic 模型
sinoquant/      多智能体分析引擎
  graph/            LangGraph 图编排
  agents/           分析师、研究员、交易员、风控
  llm_adapters/     LLM 适配器（OpenAI 兼容）
  dataflows/        数据源（AKShare/BaoStock/Tushare）
frontend/           Vue 3 前端
config/             运行时配置
cli/                数据初始化脚本
tests/              测试套件
docker/             Dockerfile + Nginx 配置
```

## 使用指南

### 股票分析

- **单股分析**：输入股票代码 → 多智能体自动分析 → SSE 实时推送进度
- **批量分析**：多只股票提交后台任务 → 任务中心查看进度

### 量化策略

1. 分析完成后让 LLM 自动生成策略代码
2. 安全沙箱验证 → 设置参数 → 提交回测
3. 查看收益率、Sharpe、最大回撤等指标
4. 启动模拟交易实时跟踪

### 其他功能

自选股、分析报告导出、股票筛选、任务中心、模拟交易、系统设置

## 支持的 LLM 供应商

DeepSeek · DashScope (通义千问) · OpenAI · Google Gemini · Anthropic Claude · SiliconFlow · OpenRouter · 302.AI · Qianfan (文心) · 任何 OpenAI 兼容接口

## 测试

```bash
python -m pytest tests/unit -q       # 单元测试
python -m pytest -m integration      # 集成测试（需 API Key）
```

## 许可证

| 部分 | 许可证 |
|------|--------|
| `sinoquant/`, `cli/`, `tests/` | Apache 2.0 |
| `app/`, `frontend/` | 专有（需商业授权） |

详见 `LICENSE`。

## 风险提示

本项目仅用于研究与学习，不构成任何投资建议。投资有风险，决策需谨慎。
