# 本地 Docker 部署记录（2026-03-06）

## 1. 部署目标

在本机通过 `docker compose` 启动 SinaQuant 的前后端与依赖服务，并确认服务可用性。

## 2. 使用环境

- 项目路径：`/path/to/SinoQuant`
- Docker：`29.2.1`
- Docker Compose：`v5.0.2`
- 部署文件：`docker-compose.yml`

## 3. 执行步骤

### 3.1 启动服务

```bash
cd /path/to/SinoQuant
docker compose up -d
```

### 3.2 查看服务状态

```bash
docker compose ps
```

初始状态中，`backend/mongodb/redis` 为 `healthy`，`frontend` 为 `unhealthy`。

### 3.3 排查与修复前端健康检查

排查发现前端容器中的健康检查命令使用了 `http://localhost`，在容器内会走 IPv6 并导致连接拒绝。  
修复方式：将 `frontend.healthcheck.test` 改为 `http://127.0.0.1/health`。

修改前：

```yaml
test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost"]
```

修改后：

```yaml
test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://127.0.0.1/health"]
```

### 3.4 重新加载前端服务

```bash
docker compose up -d frontend
docker compose ps
```

## 4. 最终运行结果

`docker compose ps` 显示以下核心服务均为 `healthy`：

- `sinoquant-backend`（端口 `8000`）
- `sinoquant-frontend`（端口 `3000`）
- `sinoquant-mongodb`（端口 `27017`）
- `sinoquant-redis`（端口 `6379`）

## 5. 连通性验证

容器内健康检查验证通过：

```bash
docker exec sinoquant-backend sh -c 'curl -sS http://127.0.0.1:8000/api/health'
docker exec sinoquant-frontend sh -c 'wget -qO- http://127.0.0.1/health'
```

结果：

- 后端返回：`{"success":true,...,"message":"服务运行正常"}`
- 前端返回：`ok`

## 6. 本地访问地址

- 前端：`http://localhost:3000`
- 后端 API：`http://localhost:8000`
- 后端 Swagger：`http://localhost:8000/docs`

## 7. 常用维护命令

```bash
# 查看全部日志
docker compose logs -f

# 查看某个服务日志
docker compose logs -f backend
docker compose logs -f frontend

# 停止服务
docker compose down

# 停止并删除数据卷（谨慎）
docker compose down -v
```

