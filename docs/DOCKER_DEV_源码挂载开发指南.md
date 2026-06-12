# Docker 开发模式（源码挂载）指南

本文说明本项目在本机开发时，如何使用 `docker-compose.dev.yml` 启动、改代码后如何生效，以及常见问题排查。

## 1. 适用场景

- 你希望使用 Docker 跑环境（MongoDB/Redis/Backend/Frontend）。
- 你希望修改源码后尽量不重建镜像，快速看到效果。

本模式使用源码挂载：
- 后端：`uvicorn --reload` 自动重载
- 前端：`vite dev` 热更新

## 2. 使用的配置文件

- 开发模式编排文件：`docker-compose.dev.yml`
- 项目根目录：`/path/to/SinoQuant`

## 3. 首次启动（含代理）

如果网络不稳定，先设置代理（按你当前环境）：

```bash
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
export all_proxy=socks5://127.0.0.1:7890
```

然后启动：

```bash
cd /path/to/SinoQuant
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps
```

访问地址：
- 前端：`http://localhost:3000`
- 后端健康检查：`http://localhost:8000/api/health`

## 4. 日常开发流程

### 4.1 修改后端代码

主要目录：
- `app/`
- `sinoquant/`

生效方式：
- 通常会被 `uvicorn --reload` 自动检测并重载，无需重启容器。

### 4.2 修改前端代码

主要目录：
- `frontend/src/`

生效方式：
- Vite 热更新，通常保存后页面自动更新。
- 若某些页面未自动更新，手动刷新浏览器即可。

## 5. 什么时候需要重启容器

以下变更通常需要重启（或重建）：

1. 修改 `docker-compose.dev.yml`
2. 修改环境变量文件（如 `.env.docker`）
3. 修改前端 Vite 配置（如 `frontend/vite.config.ts`）
4. 新增/升级依赖
   - 前端：`frontend/package.json`、`frontend/yarn.lock`
   - 后端：`pyproject.toml`、`requirements*.txt`
5. 修改 Dockerfile（此时应重建镜像）

常用命令：

```bash
# 仅重启前端
docker compose -f docker-compose.dev.yml up -d frontend

# 仅重启后端
docker compose -f docker-compose.dev.yml up -d backend

# 重启全部
docker compose -f docker-compose.dev.yml up -d
```

## 6. 查看状态与日志

```bash
# 查看容器状态
docker compose -f docker-compose.dev.yml ps

# 看后端日志
docker logs -f sinoquant-backend

# 看前端日志
docker logs -f sinoquant-frontend
```

## 7. 常见问题排查

### 7.1 页面提示“后端服务连接失败”

先确认：

```bash
docker compose -f docker-compose.dev.yml ps
```

要求 `backend` 为 `healthy`。

再看后端健康检查：

```bash
curl http://localhost:8000/api/health
```

如果后端健康但前端仍报连接失败，重点检查前端代理目标是否正确：
- `frontend/vite.config.ts` 读取 `VITE_PROXY_TARGET`
- `docker-compose.dev.yml` 中 `frontend` 环境变量应为：
  - `VITE_PROXY_TARGET=http://backend:8000`

然后重启前端容器：

```bash
docker compose -f docker-compose.dev.yml up -d frontend
```

### 7.2 前端启动时 yarn/corepack 下载失败

这是网络问题，通常重试可恢复。建议：

1. 确认代理已导出（见第 3 节）
2. 重新启动前端容器：

```bash
docker compose -f docker-compose.dev.yml up -d frontend
```

### 7.3 彻底重启开发环境

```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d
```

## 8. 退出开发环境

```bash
docker compose -f docker-compose.dev.yml down
```

如果你还要继续开发，建议不要 `down -v`，避免清掉数据库卷数据。
