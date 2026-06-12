"""
数据源和数据库配置测试 Mixin
"""

import os
import time
import logging
from typing import Dict, Any

import requests
from app.models.config import DataSourceConfig, DatabaseConfig

logger = logging.getLogger(__name__)


class DataSourceTesterMixin:
    """数据源配置和数据库配置测试"""

    async def test_data_source_config(self, ds_config: DataSourceConfig) -> Dict[str, Any]:
        """测试数据源配置 - 真实调用API进行验证"""
        start_time = time.time()
        try:
            ds_type = ds_config.type.value if hasattr(ds_config.type, 'value') else str(ds_config.type)

            logger.info(f"🧪 [TEST] Testing data source config: {ds_config.name} ({ds_type})")

            # 优先使用配置中的 API Key，如果没有或被截断，则从数据库获取
            api_key = ds_config.api_key
            used_db_credentials = False
            used_env_credentials = False

            logger.info(f"🔍 [TEST] Received API Key from config: {repr(api_key)} (type: {type(api_key).__name__}, length: {len(api_key) if api_key else 0})")

            # 根据不同的数据源类型进行测试
            if ds_type == "tushare":
                # 如果配置中的 API Key 包含 "..."（截断标记），需要验证是否是未修改的原值
                if api_key and "..." in api_key:
                    logger.info(f"🔍 [TEST] API Key contains '...' (truncated), checking if it matches database value")

                    # 从数据库中获取完整的 API Key
                    system_config = await self.get_system_config()
                    db_config = None
                    if system_config:
                        for ds in system_config.data_source_configs:
                            if ds.name == ds_config.name:
                                db_config = ds
                                break

                    if db_config and db_config.api_key:
                        # 对数据库中的完整 API Key 进行相同的截断处理
                        truncated_db_key = self._truncate_api_key(db_config.api_key)
                        logger.info(f"🔍 [TEST] Database API Key truncated: {truncated_db_key}")
                        logger.info(f"🔍 [TEST] Received API Key: {api_key}")

                        # 比较截断后的值
                        if api_key == truncated_db_key:
                            # 相同，说明用户没有修改，使用数据库中的完整值
                            api_key = db_config.api_key
                            used_db_credentials = True
                            logger.info(f"✅ [TEST] Truncated values match, using complete API Key from database (length: {len(api_key)})")
                        else:
                            # 不同，说明用户修改了但修改得不完整
                            logger.error(f"❌ [TEST] Truncated API Key doesn't match database value, user may have modified it incorrectly")
                            return {
                                "success": False,
                                "message": "API Key 格式错误：检测到截断标记但与数据库中的值不匹配，请输入完整的 API Key",
                                "response_time": time.time() - start_time,
                                "details": {
                                    "error": "truncated_key_mismatch",
                                    "received": api_key,
                                    "expected": truncated_db_key
                                }
                            }
                    else:
                        # 数据库中没有有效的 API Key，尝试从环境变量获取
                        logger.info(f"⚠️  [TEST] No valid API Key in database, trying environment variable")
                        env_token = os.getenv('TUSHARE_TOKEN')
                        if env_token:
                            api_key = env_token.strip().strip('"').strip("'")
                            used_env_credentials = True
                            logger.info(f"🔑 [TEST] Using TUSHARE_TOKEN from environment (length: {len(api_key)})")
                        else:
                            logger.error(f"❌ [TEST] No valid API Key in database or environment")
                            return {
                                "success": False,
                                "message": "API Key 无效：数据库和环境变量中均未配置有效的 Token",
                                "response_time": time.time() - start_time,
                                "details": None
                            }

                # 如果 API Key 为空，尝试从数据库或环境变量获取
                elif not api_key:
                    logger.info(f"⚠️  [TEST] API Key is empty, trying to get from database")

                    # 从数据库中获取完整的 API Key
                    system_config = await self.get_system_config()
                    db_config = None
                    if system_config:
                        for ds in system_config.data_source_configs:
                            if ds.name == ds_config.name:
                                db_config = ds
                                break

                    if db_config and db_config.api_key and "..." not in db_config.api_key:
                        api_key = db_config.api_key
                        used_db_credentials = True
                        logger.info(f"🔑 [TEST] Using API Key from database (length: {len(api_key)})")
                    else:
                        # 如果数据库中也没有，尝试从环境变量获取
                        logger.info(f"⚠️  [TEST] No valid API Key in database, trying environment variable")
                        env_token = os.getenv('TUSHARE_TOKEN')
                        if env_token:
                            api_key = env_token.strip().strip('"').strip("'")
                            used_env_credentials = True
                            logger.info(f"🔑 [TEST] Using TUSHARE_TOKEN from environment (length: {len(api_key)})")
                        else:
                            logger.error(f"❌ [TEST] No valid API Key in config, database, or environment")
                            return {
                                "success": False,
                                "message": "API Key 无效：配置、数据库和环境变量中均未配置有效的 Token",
                                "response_time": time.time() - start_time,
                                "details": None
                            }
                else:
                    # API Key 是完整的，直接使用
                    logger.info(f"✅ [TEST] Using complete API Key from config (length: {len(api_key)})")

                # 测试 Tushare API
                try:
                    logger.info(f"🔌 [TEST] Calling Tushare API with token (length: {len(api_key)})")
                    import tushare as ts
                    ts.set_token(api_key)
                    pro = ts.pro_api()
                    # 获取交易日历（轻量级测试）
                    df = pro.trade_cal(exchange='SSE', start_date='20240101', end_date='20240101')

                    if df is not None and len(df) > 0:
                        response_time = time.time() - start_time
                        logger.info(f"✅ [TEST] Tushare API call successful (response time: {response_time:.2f}s)")

                        # 构建消息，说明使用了哪个来源的凭证
                        credential_source = "配置"
                        if used_db_credentials:
                            credential_source = "数据库"
                        elif used_env_credentials:
                            credential_source = "环境变量"

                        return {
                            "success": True,
                            "message": f"成功连接到 Tushare 数据源（使用{credential_source}中的凭证）",
                            "response_time": response_time,
                            "details": {
                                "type": ds_type,
                                "test_result": "获取交易日历成功",
                                "credential_source": credential_source,
                                "used_db_credentials": used_db_credentials,
                                "used_env_credentials": used_env_credentials
                            }
                        }
                    else:
                        logger.error(f"❌ [TEST] Tushare API returned empty data")
                        return {
                            "success": False,
                            "message": "Tushare API 返回数据为空",
                            "response_time": time.time() - start_time,
                            "details": None
                        }
                except ImportError:
                    logger.error(f"❌ [TEST] Tushare library not installed")
                    return {
                        "success": False,
                        "message": "Tushare 库未安装，请运行: pip install tushare",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    logger.error(f"❌ [TEST] Tushare API call failed: {e}")
                    return {
                        "success": False,
                        "message": f"Tushare API 调用失败: {str(e)}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif ds_type == "akshare":
                # AKShare 不需要 API Key，直接测试
                try:
                    import akshare as ak
                    # 使用更轻量级的接口测试 - 获取交易日历
                    # 这个接口数据量小，响应快，更适合测试连接
                    df = ak.tool_trade_date_hist_sina()

                    if df is not None and len(df) > 0:
                        response_time = time.time() - start_time
                        return {
                            "success": True,
                            "message": f"成功连接到 AKShare 数据源",
                            "response_time": response_time,
                            "details": {
                                "type": ds_type,
                                "test_result": f"获取交易日历成功（{len(df)} 条记录）"
                            }
                        }
                    else:
                        return {
                            "success": False,
                            "message": "AKShare API 返回数据为空",
                            "response_time": time.time() - start_time,
                            "details": None
                        }
                except ImportError:
                    return {
                        "success": False,
                        "message": "AKShare 库未安装，请运行: pip install akshare",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "message": f"AKShare API 调用失败: {str(e)}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif ds_type == "baostock":
                # BaoStock 不需要 API Key，直接测试登录
                try:
                    import baostock as bs
                    # 测试登录
                    lg = bs.login()

                    if lg.error_code == '0':
                        # 登录成功，测试获取数据
                        try:
                            # 获取交易日历（轻量级测试）
                            rs = bs.query_trade_dates(start_date="2024-01-01", end_date="2024-01-01")

                            if rs.error_code == '0':
                                response_time = time.time() - start_time
                                bs.logout()
                                return {
                                    "success": True,
                                    "message": f"成功连接到 BaoStock 数据源",
                                    "response_time": response_time,
                                    "details": {
                                        "type": ds_type,
                                        "test_result": "登录成功，获取交易日历成功"
                                    }
                                }
                            else:
                                bs.logout()
                                return {
                                    "success": False,
                                    "message": f"BaoStock 数据获取失败: {rs.error_msg}",
                                    "response_time": time.time() - start_time,
                                    "details": None
                                }
                        except Exception as e:
                            bs.logout()
                            return {
                                "success": False,
                                "message": f"BaoStock 数据获取异常: {str(e)}",
                                "response_time": time.time() - start_time,
                                "details": None
                            }
                    else:
                        return {
                            "success": False,
                            "message": f"BaoStock 登录失败: {lg.error_msg}",
                            "response_time": time.time() - start_time,
                            "details": None
                        }
                except ImportError:
                    return {
                        "success": False,
                        "message": "BaoStock 库未安装，请运行: pip install baostock",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "message": f"BaoStock API 调用失败: {str(e)}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif ds_type == "alpha_vantage":
                # 如果配置中的 API Key 包含 "..."（截断标记），需要验证是否是未修改的原值
                if api_key and "..." in api_key:
                    logger.info(f"🔍 [TEST] API Key contains '...' (truncated), checking if it matches database value")

                    # 从数据库中获取完整的 API Key
                    system_config = await self.get_system_config()
                    db_config = None
                    if system_config:
                        for ds in system_config.data_source_configs:
                            if ds.name == ds_config.name:
                                db_config = ds
                                break

                    if db_config and db_config.api_key:
                        # 对数据库中的完整 API Key 进行相同的截断处理
                        truncated_db_key = self._truncate_api_key(db_config.api_key)
                        logger.info(f"🔍 [TEST] Database API Key truncated: {truncated_db_key}")
                        logger.info(f"🔍 [TEST] Received API Key: {api_key}")

                        # 比较截断后的值
                        if api_key == truncated_db_key:
                            # 相同，说明用户没有修改，使用数据库中的完整值
                            api_key = db_config.api_key
                            used_db_credentials = True
                            logger.info(f"✅ [TEST] Truncated values match, using complete API Key from database (length: {len(api_key)})")
                        else:
                            # 不同，说明用户修改了但修改得不完整
                            logger.error(f"❌ [TEST] Truncated API Key doesn't match database value")
                            return {
                                "success": False,
                                "message": "API Key 格式错误：检测到截断标记但与数据库中的值不匹配，请输入完整的 API Key",
                                "response_time": time.time() - start_time,
                                "details": {
                                    "error": "truncated_key_mismatch",
                                    "received": api_key,
                                    "expected": truncated_db_key
                                }
                            }
                    else:
                        # 数据库中没有有效的 API Key，尝试从环境变量获取
                        logger.info(f"⚠️  [TEST] No valid API Key in database, trying environment variable")
                        env_key = os.getenv('ALPHA_VANTAGE_API_KEY')
                        if env_key:
                            api_key = env_key.strip().strip('"').strip("'")
                            used_env_credentials = True
                            logger.info(f"🔑 [TEST] Using ALPHA_VANTAGE_API_KEY from environment (length: {len(api_key)})")
                        else:
                            logger.error(f"❌ [TEST] No valid API Key in database or environment")
                            return {
                                "success": False,
                                "message": "API Key 无效：数据库和环境变量中均未配置有效的 API Key",
                                "response_time": time.time() - start_time,
                                "details": None
                            }

                # 如果 API Key 为空，尝试从数据库或环境变量获取
                elif not api_key:
                    logger.info(f"⚠️  [TEST] API Key is empty, trying to get from database")

                    # 从数据库中获取完整的 API Key
                    system_config = await self.get_system_config()
                    db_config = None
                    if system_config:
                        for ds in system_config.data_source_configs:
                            if ds.name == ds_config.name:
                                db_config = ds
                                break

                    if db_config and db_config.api_key and "..." not in db_config.api_key:
                        api_key = db_config.api_key
                        used_db_credentials = True
                        logger.info(f"🔑 [TEST] Using API Key from database (length: {len(api_key)})")
                    else:
                        # 如果数据库中也没有，尝试从环境变量获取
                        logger.info(f"⚠️  [TEST] No valid API Key in database, trying environment variable")
                        env_key = os.getenv('ALPHA_VANTAGE_API_KEY')
                        if env_key:
                            api_key = env_key.strip().strip('"').strip("'")
                            used_env_credentials = True
                            logger.info(f"🔑 [TEST] Using ALPHA_VANTAGE_API_KEY from environment (length: {len(api_key)})")
                        else:
                            logger.error(f"❌ [TEST] No valid API Key in config, database, or environment")
                            return {
                                "success": False,
                                "message": "API Key 无效：配置、数据库和环境变量中均未配置有效的 API Key",
                                "response_time": time.time() - start_time,
                                "details": None
                            }
                else:
                    # API Key 是完整的，直接使用
                    logger.info(f"✅ [TEST] Using complete API Key from config (length: {len(api_key)})")

                # 测试 Alpha Vantage API
                endpoint = ds_config.endpoint or "https://www.alphavantage.co"
                url = f"{endpoint}/query"
                params = {
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": "IBM",
                    "interval": "5min",
                    "apikey": api_key
                }

                try:
                    logger.info(f"🔌 [TEST] Calling Alpha Vantage API with key (length: {len(api_key)})")
                    response = requests.get(url, params=params, timeout=10)

                    if response.status_code == 200:
                        data = response.json()
                        if "Time Series (5min)" in data or "Meta Data" in data:
                            response_time = time.time() - start_time
                            logger.info(f"✅ [TEST] Alpha Vantage API call successful (response time: {response_time:.2f}s)")

                            # 构建消息，说明使用了哪个来源的凭证
                            credential_source = "配置"
                            if used_db_credentials:
                                credential_source = "数据库"
                            elif used_env_credentials:
                                credential_source = "环境变量"

                            return {
                                "success": True,
                                "message": f"成功连接到 Alpha Vantage 数据源（使用{credential_source}中的凭证）",
                                "response_time": response_time,
                                "details": {
                                    "type": ds_type,
                                    "endpoint": endpoint,
                                    "test_result": "API 密钥有效",
                                    "credential_source": credential_source,
                                    "used_db_credentials": used_db_credentials,
                                    "used_env_credentials": used_env_credentials
                                }
                            }
                        elif "Error Message" in data:
                            return {
                                "success": False,
                                "message": f"Alpha Vantage API 错误: {data['Error Message']}",
                                "response_time": time.time() - start_time,
                                "details": None
                            }
                        elif "Note" in data:
                            return {
                                "success": False,
                                "message": "API 调用频率超限，请稍后再试",
                                "response_time": time.time() - start_time,
                                "details": None
                            }

                    return {
                        "success": False,
                        "message": f"Alpha Vantage API 返回错误: HTTP {response.status_code}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "message": f"Alpha Vantage API 调用失败: {str(e)}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            else:
                # 其他数据源类型 - 尝试从环境变量获取 API Key（如果需要）
                # 支持的环境变量映射
                env_key_map = {
                    "finnhub": "FINNHUB_API_KEY",
                    "polygon": "POLYGON_API_KEY",
                    "iex": "IEX_API_KEY",
                    "quandl": "QUANDL_API_KEY",
                }

                # 如果配置中没有 API Key，尝试从环境变量获取
                if ds_type in env_key_map and (not api_key or "..." in api_key):
                    env_var_name = env_key_map[ds_type]
                    env_key = os.getenv(env_var_name)
                    if env_key:
                        api_key = env_key.strip()
                        used_env_credentials = True
                        logger.info(f"🔑 使用环境变量中的 {ds_type.upper()} API Key ({env_var_name})")

                # 基本的端点测试
                if ds_config.endpoint:
                    try:
                        # 如果有 API Key，添加到请求中
                        headers = {}
                        params = {}

                        if api_key:
                            # 根据不同数据源的认证方式添加 API Key
                            if ds_type == "finnhub":
                                params["token"] = api_key
                            elif ds_type in ["polygon", "alpha_vantage"]:
                                params["apiKey"] = api_key
                            elif ds_type == "iex":
                                params["token"] = api_key
                            else:
                                # 默认使用 header 认证
                                headers["Authorization"] = f"Bearer {api_key}"

                        response = requests.get(ds_config.endpoint, params=params, headers=headers, timeout=10)
                        response_time = time.time() - start_time

                        if response.status_code < 500:
                            return {
                                "success": True,
                                "message": f"成功连接到数据源 {ds_config.name}",
                                "response_time": response_time,
                                "details": {
                                    "type": ds_type,
                                    "endpoint": ds_config.endpoint,
                                    "status_code": response.status_code,
                                    "used_env_credentials": used_env_credentials
                                }
                            }
                        else:
                            return {
                                "success": False,
                                "message": f"数据源返回服务器错误: HTTP {response.status_code}",
                                "response_time": response_time,
                                "details": None
                            }
                    except Exception as e:
                        return {
                            "success": False,
                            "message": f"连接失败: {str(e)}",
                            "response_time": time.time() - start_time,
                            "details": None
                        }
                else:
                    return {
                        "success": False,
                        "message": f"不支持的数据源类型: {ds_type}，且未配置端点",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"❌ 测试数据源配置失败: {e}")
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "response_time": response_time,
                "details": None
            }

    async def test_database_config(self, db_config: DatabaseConfig) -> Dict[str, Any]:
        """测试数据库配置 - 真实连接测试"""
        start_time = time.time()
        try:
            db_type = db_config.type.value if hasattr(db_config.type, 'value') else str(db_config.type)

            logger.info(f"🧪 测试数据库配置: {db_config.name} ({db_type})")
            logger.info(f"📍 连接地址: {db_config.host}:{db_config.port}")

            # 根据不同的数据库类型进行测试
            if db_type == "mongodb":
                try:
                    from motor.motor_asyncio import AsyncIOMotorClient

                    # 优先使用环境变量中的完整连接信息（包括host、用户名、密码）
                    host = db_config.host
                    port = db_config.port
                    username = db_config.username
                    password = db_config.password
                    database = db_config.database
                    auth_source = None
                    used_env_config = False

                    # 检测是否在 Docker 环境中
                    is_docker = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER') == 'true'

                    # 如果配置中没有用户名密码，尝试从环境变量获取完整配置
                    if not username or not password:
                        env_host = os.getenv('MONGODB_HOST')
                        env_port = os.getenv('MONGODB_PORT')
                        env_username = os.getenv('MONGODB_USERNAME')
                        env_password = os.getenv('MONGODB_PASSWORD')
                        env_auth_source = os.getenv('MONGODB_AUTH_SOURCE', 'admin')

                        if env_username and env_password:
                            username = env_username
                            password = env_password
                            auth_source = env_auth_source
                            used_env_config = True

                            # 如果环境变量中有 host 配置，也使用它
                            if env_host:
                                host = env_host
                                # Docker 环境下，将 localhost 替换为 mongodb
                                if is_docker and host == 'localhost':
                                    host = 'mongodb'
                                    logger.info(f"🐳 检测到 Docker 环境，将 host 从 localhost 改为 mongodb")

                            if env_port:
                                port = int(env_port)

                            logger.info(f"🔑 使用环境变量中的 MongoDB 配置 (host={host}, port={port}, authSource={auth_source})")

                    # 如果配置中没有数据库名，尝试从环境变量获取
                    if not database:
                        env_database = os.getenv('MONGODB_DATABASE')
                        if env_database:
                            database = env_database
                            logger.info(f"📦 使用环境变量中的数据库名: {database}")

                    # 从连接参数中获取 authSource（如果有）
                    if not auth_source and db_config.connection_params:
                        auth_source = db_config.connection_params.get('authSource')

                    # 构建连接字符串
                    if username and password:
                        connection_string = f"mongodb://{username}:{password}@{host}:{port}"
                    else:
                        connection_string = f"mongodb://{host}:{port}"

                    if database:
                        connection_string += f"/{database}"

                    # 添加连接参数
                    params_list = []

                    # 如果有 authSource，添加到参数中
                    if auth_source:
                        params_list.append(f"authSource={auth_source}")

                    # 添加其他连接参数
                    if db_config.connection_params:
                        for k, v in db_config.connection_params.items():
                            if k != 'authSource':  # authSource 已经添加过了
                                params_list.append(f"{k}={v}")

                    if params_list:
                        connection_string += f"?{'&'.join(params_list)}"

                    logger.info(f"🔗 连接字符串: {connection_string.replace(password or '', '***') if password else connection_string}")

                    # 创建客户端并测试连接
                    client = AsyncIOMotorClient(
                        connection_string,
                        serverSelectionTimeoutMS=5000  # 5秒超时
                    )

                    # 如果指定了数据库，测试该数据库的访问权限
                    if database:
                        # 测试指定数据库的访问（不需要管理员权限）
                        db = client[database]
                        # 尝试列出集合（如果没有权限会报错）
                        collections = await db.list_collection_names()
                        test_result = f"数据库 '{database}' 可访问，包含 {len(collections)} 个集合"
                    else:
                        # 如果没有指定数据库，只执行 ping 命令
                        await client.admin.command('ping')
                        test_result = "连接成功"

                    response_time = time.time() - start_time

                    # 关闭连接
                    client.close()

                    return {
                        "success": True,
                        "message": f"成功连接到 MongoDB 数据库",
                        "response_time": response_time,
                        "details": {
                            "type": db_type,
                            "host": host,
                            "port": port,
                            "database": database,
                            "auth_source": auth_source,
                            "test_result": test_result,
                            "used_env_config": used_env_config
                        }
                    }
                except ImportError:
                    return {
                        "success": False,
                        "message": "Motor 库未安装，请运行: pip install motor",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"❌ MongoDB 连接测试失败: {error_msg}")

                    if "Authentication failed" in error_msg or "auth failed" in error_msg.lower():
                        message = "认证失败，请检查用户名和密码"
                    elif "requires authentication" in error_msg.lower():
                        message = "需要认证，请配置用户名和密码"
                    elif "not authorized" in error_msg.lower():
                        message = "权限不足，请检查用户权限配置"
                    elif "Connection refused" in error_msg:
                        message = "连接被拒绝，请检查主机地址和端口"
                    elif "timed out" in error_msg.lower():
                        message = "连接超时，请检查网络和防火墙设置"
                    elif "No servers found" in error_msg:
                        message = "找不到服务器，请检查主机地址和端口"
                    else:
                        message = f"连接失败: {error_msg}"

                    return {
                        "success": False,
                        "message": message,
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif db_type == "redis":
                try:
                    import redis.asyncio as aioredis

                    # 优先使用环境变量中的完整 Redis 配置（包括host、密码）
                    host = db_config.host
                    port = db_config.port
                    password = db_config.password
                    database = db_config.database
                    used_env_config = False

                    # 检测是否在 Docker 环境中
                    is_docker = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER') == 'true'

                    # 如果配置中没有密码，尝试从环境变量获取完整配置
                    if not password:
                        env_host = os.getenv('REDIS_HOST')
                        env_port = os.getenv('REDIS_PORT')
                        env_password = os.getenv('REDIS_PASSWORD')

                        if env_password:
                            password = env_password
                            used_env_config = True

                            # 如果环境变量中有 host 配置，也使用它
                            if env_host:
                                host = env_host
                                # Docker 环境下，将 localhost 替换为 redis
                                if is_docker and host == 'localhost':
                                    host = 'redis'
                                    logger.info(f"🐳 检测到 Docker 环境，将 Redis host 从 localhost 改为 redis")

                            if env_port:
                                port = int(env_port)

                            logger.info(f"🔑 使用环境变量中的 Redis 配置 (host={host}, port={port})")

                    # 如果配置中没有数据库编号，尝试从环境变量获取
                    if database is None:
                        env_db = os.getenv('REDIS_DB')
                        if env_db:
                            database = int(env_db)
                            logger.info(f"📦 使用环境变量中的 Redis 数据库编号: {database}")

                    # 构建连接参数
                    redis_params = {
                        "host": host,
                        "port": port,
                        "decode_responses": True,
                        "socket_connect_timeout": 5
                    }

                    if password:
                        redis_params["password"] = password

                    if database is not None:
                        redis_params["db"] = int(database)

                    # 创建连接并测试
                    redis_client = await aioredis.from_url(
                        f"redis://{host}:{port}",
                        **redis_params
                    )

                    # 执行 PING 命令
                    await redis_client.ping()

                    # 获取服务器信息
                    info = await redis_client.info("server")

                    response_time = time.time() - start_time

                    # 关闭连接
                    await redis_client.close()

                    return {
                        "success": True,
                        "message": f"成功连接到 Redis 数据库",
                        "response_time": response_time,
                        "details": {
                            "type": db_type,
                            "host": host,
                            "port": port,
                            "database": database,
                            "redis_version": info.get("redis_version", "unknown"),
                            "used_env_config": used_env_config
                        }
                    }
                except ImportError:
                    return {
                        "success": False,
                        "message": "Redis 库未安装，请运行: pip install redis",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    error_msg = str(e)
                    if "WRONGPASS" in error_msg or "Authentication" in error_msg:
                        message = "认证失败，请检查密码"
                    elif "Connection refused" in error_msg:
                        message = "连接被拒绝，请检查主机地址和端口"
                    elif "timed out" in error_msg.lower():
                        message = "连接超时，请检查网络和防火墙设置"
                    else:
                        message = f"连接失败: {error_msg}"

                    return {
                        "success": False,
                        "message": message,
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif db_type == "mysql":
                try:
                    import aiomysql

                    # 创建连接
                    conn = await aiomysql.connect(
                        host=db_config.host,
                        port=db_config.port,
                        user=db_config.username,
                        password=db_config.password,
                        db=db_config.database,
                        connect_timeout=5
                    )

                    # 执行测试查询
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT VERSION()")
                        version = await cursor.fetchone()

                    response_time = time.time() - start_time

                    # 关闭连接
                    conn.close()

                    return {
                        "success": True,
                        "message": f"成功连接到 MySQL 数据库",
                        "response_time": response_time,
                        "details": {
                            "type": db_type,
                            "host": db_config.host,
                            "port": db_config.port,
                            "database": db_config.database,
                            "version": version[0] if version else "unknown"
                        }
                    }
                except ImportError:
                    return {
                        "success": False,
                        "message": "aiomysql 库未安装，请运行: pip install aiomysql",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    error_msg = str(e)
                    if "Access denied" in error_msg:
                        message = "访问被拒绝，请检查用户名和密码"
                    elif "Unknown database" in error_msg:
                        message = f"数据库 '{db_config.database}' 不存在"
                    elif "Can't connect" in error_msg:
                        message = "无法连接，请检查主机地址和端口"
                    else:
                        message = f"连接失败: {error_msg}"

                    return {
                        "success": False,
                        "message": message,
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif db_type == "postgresql":
                try:
                    import asyncpg

                    # 创建连接
                    conn = await asyncpg.connect(
                        host=db_config.host,
                        port=db_config.port,
                        user=db_config.username,
                        password=db_config.password,
                        database=db_config.database,
                        timeout=5
                    )

                    # 执行测试查询
                    version = await conn.fetchval("SELECT version()")

                    response_time = time.time() - start_time

                    # 关闭连接
                    await conn.close()

                    return {
                        "success": True,
                        "message": f"成功连接到 PostgreSQL 数据库",
                        "response_time": response_time,
                        "details": {
                            "type": db_type,
                            "host": db_config.host,
                            "port": db_config.port,
                            "database": db_config.database,
                            "version": version.split()[1] if version else "unknown"
                        }
                    }
                except ImportError:
                    return {
                        "success": False,
                        "message": "asyncpg 库未安装，请运行: pip install asyncpg",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    error_msg = str(e)
                    if "password authentication failed" in error_msg:
                        message = "密码认证失败，请检查用户名和密码"
                    elif "does not exist" in error_msg:
                        message = f"数据库 '{db_config.database}' 不存在"
                    elif "Connection refused" in error_msg:
                        message = "连接被拒绝，请检查主机地址和端口"
                    else:
                        message = f"连接失败: {error_msg}"

                    return {
                        "success": False,
                        "message": message,
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            elif db_type == "sqlite":
                try:
                    import aiosqlite

                    # SQLite 使用文件路径，不需要 host/port
                    db_path = db_config.database or db_config.host

                    # 创建连接
                    async with aiosqlite.connect(db_path, timeout=5) as conn:
                        # 执行测试查询
                        async with conn.execute("SELECT sqlite_version()") as cursor:
                            version = await cursor.fetchone()

                    response_time = time.time() - start_time

                    return {
                        "success": True,
                        "message": f"成功连接到 SQLite 数据库",
                        "response_time": response_time,
                        "details": {
                            "type": db_type,
                            "database": db_path,
                            "version": version[0] if version else "unknown"
                        }
                    }
                except ImportError:
                    return {
                        "success": False,
                        "message": "aiosqlite 库未安装，请运行: pip install aiosqlite",
                        "response_time": time.time() - start_time,
                        "details": None
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "message": f"连接失败: {str(e)}",
                        "response_time": time.time() - start_time,
                        "details": None
                    }

            else:
                return {
                    "success": False,
                    "message": f"不支持的数据库类型: {db_type}",
                    "response_time": time.time() - start_time,
                    "details": None
                }

        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"❌ 测试数据库配置失败: {e}")
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "response_time": response_time,
                "details": None
            }
