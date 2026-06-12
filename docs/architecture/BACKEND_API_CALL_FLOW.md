# 后端架构与接口调用链路

本文档基于当前代码仓（截至 2026-03-06）梳理 SinaQuant 的后端实现方式，以及前端如何调用后端接口。

## 1. 总体架构

- 后端框架：`FastAPI + Uvicorn`
- 数据层：`MongoDB (业务数据)` + `Redis (队列/进度/发布订阅)`
- 前端调用：`Vue3 + axios` 统一请求封装
- 实时通道：`WebSocket` + `SSE`

核心入口文件：

- `app/main.py`：应用创建、生命周期、路由注册、中间件注册
- `app/core/database.py`：MongoDB/Redis 初始化与连接管理
- `app/routers/*.py`：路由层
- `app/services/*.py`：业务层

## 2. 后端启动流程

在 `app/main.py` 的 `lifespan` 中：

1. 初始化日志
2. 校验启动配置
3. 初始化 MongoDB/Redis 连接
4. 应用动态配置（日志级别、监控开关）
5. 启动调度器（数据同步、行情入库、新闻同步等）

关键代码位置：

- `app/main.py:214`（`lifespan`）
- `app/main.py:229`（`init_db()`）
- `app/main.py:269`（调度任务初始化）
- `app/core/database.py:189`（数据库初始化）

## 3. 请求生命周期（统一模式）

一次典型 API 请求经过：

1. 前端 `ApiClient` 发请求（axios）
2. 请求拦截器自动注入：
   - `Authorization: Bearer <token>`
   - `X-Request-ID`
3. FastAPI 中间件处理：
   - RequestIDMiddleware 生成/透传 trace_id
   - OperationLogMiddleware 记录操作日志
4. Router 处理参数与鉴权
5. Service 执行业务逻辑
6. 访问 MongoDB/Redis/外部数据源
7. 返回统一响应（多数是 `{ success, data, message }`）

关键代码位置：

- `frontend/src/api/request.ts:95`（请求拦截）
- `frontend/src/api/request.ts:174`（响应拦截）
- `app/middleware/request_id.py:19`
- `app/middleware/operation_log_middleware.py:27`

## 4. 鉴权调用链

### 4.1 登录

调用链：

`frontend authApi.login -> POST /api/auth/login -> user_service.authenticate_user -> AuthService.create_access_token -> 返回 access_token/refresh_token`

关键代码位置：

- `frontend/src/api/auth.ts:14`
- `frontend/src/stores/auth.ts:183`
- `app/routers/auth_db.py:125`
- `app/services/auth_service.py:15`

### 4.2 受保护接口访问

调用链：

`前端携带 Bearer token -> get_current_user 解析 Header -> verify_token -> 查询用户 -> 放行`

关键代码位置：

- `app/routers/auth_db.py:78`
- `app/services/auth_service.py:27`

## 5. 分析接口调用链（核心）

### 5.1 提交单股分析

调用链：

`SingleAnalysis.vue -> analysisApi.startSingleAnalysis -> POST /api/analysis/single -> create_analysis_task -> BackgroundTasks 异步执行 execute_analysis_background`

关键代码位置：

- `frontend/src/views/Analysis/SingleAnalysis.vue:971`
- `frontend/src/api/analysis.ts:126`
- `app/routers/analysis.py:40`
- `app/services/simple_analysis_service.py:730`
- `app/services/simple_analysis_service.py:809`

行为说明：

- 接口会立即返回 `task_id`（不阻塞等待分析完成）
- 任务状态先写入内存管理器，再 upsert 到 `analysis_tasks` 集合

### 5.2 后台执行与结果落库

后台任务执行主要步骤：

1. 验证股票代码与数据可用性
2. 初始化进度跟踪器（Redis）
3. 执行分析（线程池中运行 SinaQuantGraph）
4. 更新状态（内存 + MongoDB）
5. 保存结果到 `analysis_reports`，并回写 `analysis_tasks.result`

关键代码位置：

- `app/services/simple_analysis_service.py:857`（股票校验）
- `app/services/simple_analysis_service.py:914`（进度跟踪器）
- `app/services/simple_analysis_service.py:962`（实际执行）
- `app/services/simple_analysis_service.py:2340`（状态更新）
- `app/services/simple_analysis_service.py:2625`（写入 `analysis_reports`）

### 5.3 查询任务状态/结果

状态查询优先级：

1. 内存任务状态
2. Redis 进度细节
3. MongoDB 兜底（`analysis_tasks` / `analysis_reports`）

结果查询优先级：

1. 内存 `result_data`
2. `analysis_reports`
3. `analysis_tasks.result`（兼容旧结构）

关键代码位置：

- `app/routers/analysis.py:105`（状态接口）
- `app/services/simple_analysis_service.py:1850`（状态服务）
- `app/routers/analysis.py:221`（结果接口）

## 6. 普通数据接口调用链（股票详情/筛选）

### 6.1 股票详情接口

前端调用：

- `GET /api/stocks/{code}/quote`
- `GET /api/stocks/{code}/fundamentals`
- `GET /api/stocks/{code}/kline`
- `GET /api/stocks/{code}/news`

关键代码位置：

- `frontend/src/api/stocks.ts:83`
- `app/routers/stocks.py:30`
- `app/routers/stocks.py:144`
- `app/routers/stocks.py:327`
- `app/routers/stocks.py:495`

实现特点：

- 行情/基本面按数据源优先级查询（Tushare/AkShare/BaoStock）
- K 线优先 MongoDB 缓存，兜底外部数据源
- 部分指标（如振幅）在接口层实时计算

### 6.2 筛选接口

前端调用：

`POST /api/screening/run`

后端流程：

`router 将旧条件格式转换 -> enhanced_screening_service.screen_stocks -> 返回 total/items`

关键代码位置：

- `frontend/src/api/screening.ts:58`
- `app/routers/screening.py:156`
- `app/routers/screening.py:74`（旧格式条件转换）

## 7. 实时推送链路

### 7.1 任务进度

项目中同时存在：

- WebSocket 任务进度：`/api/analysis/ws/task/{task_id}`
- SSE 任务进度：`/api/stream/tasks/{task_id}`

关键代码位置：

- `app/routers/analysis.py:1062`
- `app/routers/sse.py:18`
- `frontend/src/views/Tasks/TaskCenter.vue:193`（任务页 WebSocket）

### 7.2 通知推送

通知 WebSocket：

- 前端连接：`/api/ws/notifications?token=<jwt>`
- 后端按 user_id 管理连接并推送通知

关键代码位置：

- `frontend/src/stores/notifications.ts:76`
- `frontend/src/stores/notifications.ts:96`
- `app/routers/websocket_notifications.py:109`

## 8. 兼容队列接口（旧路径）

分析路由中保留了兼容性端点：

- `/api/analysis/analyze`
- `/api/analysis/analyze/batch`
- `/api/analysis/batches/{batch_id}`

这些端点通过 `QueueService` 读写 Redis 队列结构。

关键代码位置：

- `app/routers/analysis.py:875`
- `app/services/queue_service.py:363`

## 9. 主要数据集合与职责

MongoDB 常见集合：

- `analysis_tasks`：任务状态、进度、兼容结果字段
- `analysis_reports`：正式分析报告
- `market_quotes`：行情快照
- `stock_basic_info`：股票基础信息
- `stock_financial_data`：财务数据

Redis 主要用途：

- 队列
- 任务进度
- Pub/Sub（SSE/通知相关）

## 10. 一句话总结

该项目后端是“路由薄、服务厚”的实现：前端统一 API 层发起请求，后端 Router 负责鉴权和参数，核心逻辑在 Service，状态与结果通过内存 + Redis + MongoDB 多层协同，实现了“异步分析 + 可查询进度 + 实时推送”的完整闭环。
