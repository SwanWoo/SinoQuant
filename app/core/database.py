"""
数据库连接管理模块

职责：管理 MongoDB 和 Redis 的连接生命周期，提供全局访问入口。

双客户端架构：
  - motor（异步客户端）：FastAPI 路由层使用，所有 async 函数通过 get_mongo_db() 获取
  - pymongo（同步客户端）：回测线程池等非异步场景使用，通过 get_mongo_db_sync() 获取
  两个客户端连接同一个 MongoDB 实例，但连接池独立。

Redis 用途：
  - 缓存：分析结果、股票数据等热数据的缓存层
  - 会话：JWT token 黑名单、用户会话管理
  - 进度追踪：分析任务的实时进度信息（RedisProgressTracker）
  - 发布/订阅：WebSocket 通知的消息通道

连接池配置说明：
  - MongoDB: maxPoolSize/minPoolSize 控制连接池大小，避免过多连接耗尽资源
  - Redis: max_connections 控制连接池大小，decode_responses=True 自动解码为字符串
  - 两个数据库都配置了超时参数，防止连接泄漏导致资源耗尽
"""

import logging
import asyncio
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.database import Database
from redis.asyncio import Redis, ConnectionPool
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from redis.exceptions import ConnectionError as RedisConnectionError
from .config import settings

logger = logging.getLogger(__name__)

# ---- 全局连接实例 ----
# 这些变量在 init_database() 中初始化，在 close_database() 中销毁
# 应用生命周期：FastAPI lifespan 启动时 init → 请求处理时 get → 关闭时 close

mongo_client: Optional[AsyncIOMotorClient] = None   # motor 异步 MongoDB 客户端
mongo_db: Optional[AsyncIOMotorDatabase] = None     # motor 异步数据库实例（sinoquant 库）
redis_client: Optional[Redis] = None                # aioredis 异步 Redis 客户端
redis_pool: Optional[ConnectionPool] = None         # Redis 连接池

# 同步 MongoDB 连接（用于非异步上下文，如回测线程池中）
# 回测服务在 ThreadPoolExecutor 中运行，无法使用 motor 异步客户端，
# 因此需要独立的 pymongo 同步客户端
_sync_mongo_client: Optional[MongoClient] = None
_sync_mongo_db: Optional[Database] = None


class DatabaseManager:
    """数据库连接管理器

    封装 MongoDB 和 Redis 的初始化、关闭、健康检查逻辑。
    在 FastAPI 的 lifespan 中通过 init_database() 间接调用。

    使用方式：
      1. 应用启动时调用 init_mongodb() + init_redis()
      2. 请求处理时通过 get_mongo_db() / get_redis_client() 获取连接
      3. 应用关闭时调用 close_connections()
      4. 监控系统定期调用 health_check() 检查连接状态
    """

    def __init__(self):
        self.mongo_client: Optional[AsyncIOMotorClient] = None   # motor 异步客户端
        self.mongo_db: Optional[AsyncIOMotorDatabase] = None     # 数据库实例
        self.redis_client: Optional[Redis] = None                # Redis 客户端
        self.redis_pool: Optional[ConnectionPool] = None         # Redis 连接池
        self._mongo_healthy = False   # MongoDB 健康状态标志
        self._redis_healthy = False   # Redis 健康状态标志

    async def init_mongodb(self):
        """初始化 MongoDB 异步连接（motor）

        配置说明：
          - maxPoolSize: 最大连接数，防止连接过多耗尽 MongoDB 资源
          - minPoolSize: 最小空闲连接数，减少连接建立的开销
          - maxIdleTimeMS: 空闲连接超时时间（30秒），自动回收空闲连接
          - serverSelectionTimeoutMS: 服务器选择超时，控制集群环境下主节点选举的等待时间
          - connectTimeoutMS: TCP 连接超时，防止网络故障时长时间阻塞
          - socketTimeoutMS: 读写超时，防止慢查询阻塞连接
        """
        try:
            logger.info("🔄 正在初始化MongoDB连接...")

            # 创建 MongoDB 异步客户端（motor），配置连接池参数
            # motor 是 pymongo 的异步封装，API 与 pymongo 一致但支持 await
            self.mongo_client = AsyncIOMotorClient(
                settings.MONGO_URI,
                maxPoolSize=settings.MONGO_MAX_CONNECTIONS,
                minPoolSize=settings.MONGO_MIN_CONNECTIONS,
                maxIdleTimeMS=30000,  # 30秒空闲超时
                serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,  # 服务器选择超时
                connectTimeoutMS=settings.MONGO_CONNECT_TIMEOUT_MS,  # 连接超时
                socketTimeoutMS=settings.MONGO_SOCKET_TIMEOUT_MS,  # 套接字超时
            )

            # 获取数据库实例
            self.mongo_db = self.mongo_client[settings.MONGO_DB]

            # 测试连接
            await self.mongo_client.admin.command('ping')
            self._mongo_healthy = True

            logger.info("✅ MongoDB连接成功建立")
            logger.info(f"📊 数据库: {settings.MONGO_DB}")
            logger.info(f"🔗 连接池: {settings.MONGO_MIN_CONNECTIONS}-{settings.MONGO_MAX_CONNECTIONS}")
            logger.info(f"⏱️  超时配置: connectTimeout={settings.MONGO_CONNECT_TIMEOUT_MS}ms, socketTimeout={settings.MONGO_SOCKET_TIMEOUT_MS}ms")

        except Exception as e:
            logger.error(f"❌ MongoDB连接失败: {e}")
            self._mongo_healthy = False
            raise

    async def init_redis(self):
        """初始化 Redis 异步连接

        Redis 在项目中的用途：
          1. 缓存层：分析结果、股票数据的短期缓存，减少 MongoDB 读取
          2. 进度追踪：分析任务执行时，RedisProgressTracker 实时写入进度
          3. 会话管理：JWT token 黑名单、用户登录状态
          4. 发布/订阅：WebSocket 实时通知的消息通道

        连接池配置说明：
          - decode_responses=True: 自动将 Redis 返回的 bytes 解码为 str，
            省去手动 decode 的麻烦
          - retry_on_timeout: 超时后自动重试，提高网络波动时的可用性
        """
        try:
            logger.info("🔄 正在初始化Redis连接...")

            # 创建Redis连接池
            self.redis_pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
                decode_responses=True,
                socket_connect_timeout=5,  # 5秒连接超时
                socket_timeout=10,  # 10秒套接字超时
            )

            # 创建Redis客户端
            self.redis_client = Redis(connection_pool=self.redis_pool)

            # 测试连接
            await self.redis_client.ping()
            self._redis_healthy = True

            logger.info("✅ Redis连接成功建立")
            logger.info(f"🔗 连接池大小: {settings.REDIS_MAX_CONNECTIONS}")

        except Exception as e:
            logger.error(f"❌ Redis连接失败: {e}")
            self._redis_healthy = False
            raise

    async def close_connections(self):
        """关闭所有数据库连接

        在 FastAPI lifespan 的 shutdown 阶段调用。
        依次关闭 MongoDB 客户端、Redis 客户端和 Redis 连接池。
        """
        logger.info("🔄 正在关闭数据库连接...")

        # 关闭MongoDB连接
        if self.mongo_client:
            try:
                self.mongo_client.close()
                self._mongo_healthy = False
                logger.info("✅ MongoDB连接已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭MongoDB连接时出错: {e}")

        # 关闭Redis连接
        if self.redis_client:
            try:
                await self.redis_client.close()
                self._redis_healthy = False
                logger.info("✅ Redis连接已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭Redis连接时出错: {e}")

        # 关闭Redis连接池
        if self.redis_pool:
            try:
                await self.redis_pool.disconnect()
                logger.info("✅ Redis连接池已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭Redis连接池时出错: {e}")

    async def health_check(self) -> dict:
        """数据库健康检查

        通过 ping 命令检测 MongoDB 和 Redis 的连接状态。
        返回每个数据库的 status（healthy/unhealthy/disconnected）和详细信息。
        用于 /api/health 端点和监控系统。
        """
        health_status = {
            "mongodb": {"status": "unknown", "details": None},
            "redis": {"status": "unknown", "details": None}
        }

        # 检查MongoDB
        try:
            if self.mongo_client:
                result = await self.mongo_client.admin.command('ping')
                health_status["mongodb"] = {
                    "status": "healthy",
                    "details": {"ping": result, "database": settings.MONGO_DB}
                }
                self._mongo_healthy = True
            else:
                health_status["mongodb"]["status"] = "disconnected"
        except Exception as e:
            health_status["mongodb"] = {
                "status": "unhealthy",
                "details": {"error": str(e)}
            }
            self._mongo_healthy = False

        # 检查Redis
        try:
            if self.redis_client:
                result = await self.redis_client.ping()
                health_status["redis"] = {
                    "status": "healthy",
                    "details": {"ping": result}
                }
                self._redis_healthy = True
            else:
                health_status["redis"]["status"] = "disconnected"
        except Exception as e:
            health_status["redis"] = {
                "status": "unhealthy",
                "details": {"error": str(e)}
            }
            self._redis_healthy = False

        return health_status

    @property
    def is_healthy(self) -> bool:
        """检查所有数据库连接是否健康（MongoDB 和 Redis 都正常才返回 True）"""
        return self._mongo_healthy and self._redis_healthy


# 全局数据库管理器实例（整个应用共享一个）
db_manager = DatabaseManager()


async def init_database():
    """初始化所有数据库连接

    在 FastAPI 的 lifespan startup 阶段调用（app/main.py）。
    初始化顺序：先 MongoDB，再 Redis（MongoDB 是主要存储，优先级更高）。
    """
    global mongo_client, mongo_db, redis_client, redis_pool

    try:
        # 初始化MongoDB
        await db_manager.init_mongodb()
        mongo_client = db_manager.mongo_client
        mongo_db = db_manager.mongo_db

        # 初始化Redis
        await db_manager.init_redis()
        redis_client = db_manager.redis_client
        redis_pool = db_manager.redis_pool

        logger.info("🎉 所有数据库连接初始化完成")

    except Exception as e:
        logger.error(f"💥 数据库初始化失败: {e}")
        raise


async def close_database():
    """关闭所有数据库连接

    在 FastAPI 的 lifespan shutdown 阶段调用。
    清空全局变量，确保引用断开，避免内存泄漏。
    """
    global mongo_client, mongo_db, redis_client, redis_pool

    await db_manager.close_connections()

    # 清空全局变量
    mongo_client = None
    mongo_db = None
    redis_client = None
    redis_pool = None


def get_mongo_client() -> AsyncIOMotorClient:
    """获取 motor 异步 MongoDB 客户端（用于高级操作，如直接管理集合索引等）"""
    if mongo_client is None:
        raise RuntimeError("MongoDB客户端未初始化")
    return mongo_client


def get_mongo_db() -> AsyncIOMotorDatabase:
    """获取 motor 异步 MongoDB 数据库实例

    这是后端代码中最常用的数据库访问方法。
    所有 async 路由和服务都通过此函数获取数据库实例，
    然后通过 db["collection_name"] 访问集合。

    常用集合：
      - analysis_tasks: 分析任务记录
      - stock_daily_quotes: 股票日线行情数据
      - alpha_strategies: LLM 生成的量化策略代码
      - alpha_backtests: 回测结果记录
      - alpha_simulations: 模拟交易状态
      - system_configs: 系统配置（LLM 厂家、数据源等）

    注意：此函数返回的是 motor 异步客户端，所有集合操作必须使用 await。
    """
    if mongo_db is None:
        raise RuntimeError("MongoDB数据库未初始化")
    return mongo_db


def get_mongo_db_sync() -> Database:
    """获取 pymongo 同步 MongoDB 数据库实例

    专门用于非异步上下文，主要是回测线程池中的代码。
    回测服务在 ThreadPoolExecutor 中运行，无法 await motor 的异步操作，
    因此需要独立的 pymongo 同步客户端。

    使用场景：
      - backtest_service.py: 回测线程中读写 alpha_backtests 集合
      - 其他无法使用 async/await 的同步函数

    注意：同步客户端和异步客户端连接同一个 MongoDB 实例，
    但连接池独立，互不影响。
    """
    global _sync_mongo_client, _sync_mongo_db

    if _sync_mongo_db is not None:
        return _sync_mongo_db

    # 创建同步 MongoDB 客户端
    if _sync_mongo_client is None:
        _sync_mongo_client = MongoClient(
            settings.MONGO_URI,
            maxPoolSize=settings.MONGO_MAX_CONNECTIONS,
            minPoolSize=settings.MONGO_MIN_CONNECTIONS,
            maxIdleTimeMS=30000,
            serverSelectionTimeoutMS=5000
        )

    _sync_mongo_db = _sync_mongo_client[settings.MONGO_DB]
    return _sync_mongo_db


def get_redis_client() -> Redis:
    """获取异步 Redis 客户端

    Redis 在项目中的主要用途：
      1. 缓存：分析结果、股票数据等热数据
      2. 进度追踪：RedisProgressTracker 实时写入分析进度
      3. 会话：JWT token 黑名单
      4. 发布/订阅：WebSocket 通知
    """
    if redis_client is None:
        raise RuntimeError("Redis客户端未初始化")
    return redis_client


async def get_database_health() -> dict:
    """获取数据库健康状态（用于 /api/health 端点和 Docker 健康检查）"""
    return await db_manager.health_check()


# 兼容性别名（部分旧代码使用 init_db / close_db 命名）
init_db = init_database
close_db = close_database


def get_database():
    """获取 MongoDB 数据库实例（兼容旧接口，新代码请使用 get_mongo_db）"""
    if db_manager.mongo_client is None:
        raise RuntimeError("MongoDB客户端未初始化")
    return db_manager.mongo_client.sinoquant