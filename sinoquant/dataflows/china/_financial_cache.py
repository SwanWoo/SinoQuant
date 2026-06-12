"""
财务缓存 mixin

提供财务数据的 MongoDB 缓存读写方法，包括原始财务数据的缓存存取、
股票基本信息缓存查询、过期缓存回退等功能。
"""

from datetime import datetime, timedelta

from sinoquant.config.runtime_settings import use_app_cache_enabled
from sinoquant.utils.logging_manager import get_logger

logger = get_logger('agents')


class FinancialCacheMixin:
    """财务缓存 mixin — 将之前通过 _add_financial_cache_methods 猴子补丁注入的方法正规化为 mixin"""

    # ------------------------------------------------------------------
    # 读取缓存
    # ------------------------------------------------------------------

    def _get_cached_raw_financial_data(self, symbol: str) -> dict:
        """从数据库缓存获取原始财务数据"""
        try:
            from ..cache.app_adapter import get_mongodb_client
            client = get_mongodb_client()
            if not client:
                logger.debug(f"📊 [财务缓存] MongoDB客户端不可用")
                return None

            db = client.get_database('sinoquant')

            # 第一优先级：从 stock_financial_data 集合读取（定时任务同步的持久化数据）
            stock_financial_collection = db.stock_financial_data

            # 尝试使用 symbol 或 code 字段查询（兼容不同的同步服务）
            financial_doc = stock_financial_collection.find_one({
                '$or': [
                    {'symbol': symbol},
                    {'code': symbol}
                ]
            }, sort=[('updated_at', -1)])

            if financial_doc:
                logger.info(f"✅ [财务数据] 从 stock_financial_data 集合获取{symbol}财务数据")
                # 将数据库文档转换为财务数据格式
                financial_data = {}

                # 提取各类财务数据
                # 第一优先级：检查 raw_data 字段（Tushare 同步服务使用的结构）
                if 'raw_data' in financial_doc and isinstance(financial_doc['raw_data'], dict):
                    raw_data = financial_doc['raw_data']
                    # 映射字段名：raw_data 中使用 cashflow_statement，我们需要 cash_flow
                    if 'balance_sheet' in raw_data and raw_data['balance_sheet']:
                        financial_data['balance_sheet'] = raw_data['balance_sheet']
                    if 'income_statement' in raw_data and raw_data['income_statement']:
                        financial_data['income_statement'] = raw_data['income_statement']
                    if 'cashflow_statement' in raw_data and raw_data['cashflow_statement']:
                        financial_data['cash_flow'] = raw_data['cashflow_statement']  # 注意字段名映射
                    if 'financial_indicators' in raw_data and raw_data['financial_indicators']:
                        financial_data['main_indicators'] = raw_data['financial_indicators']  # 注意字段名映射
                    if 'main_business' in raw_data and raw_data['main_business']:
                        financial_data['main_business'] = raw_data['main_business']

                # 第二优先级：检查 financial_data 嵌套字段
                elif 'financial_data' in financial_doc and isinstance(financial_doc['financial_data'], dict):
                    nested_data = financial_doc['financial_data']
                    if 'balance_sheet' in nested_data:
                        financial_data['balance_sheet'] = nested_data['balance_sheet']
                    if 'income_statement' in nested_data:
                        financial_data['income_statement'] = nested_data['income_statement']
                    if 'cash_flow' in nested_data:
                        financial_data['cash_flow'] = nested_data['cash_flow']
                    if 'main_indicators' in nested_data:
                        financial_data['main_indicators'] = nested_data['main_indicators']

                # 第三优先级：直接从文档根级别读取
                else:
                    if 'balance_sheet' in financial_doc and financial_doc['balance_sheet']:
                        financial_data['balance_sheet'] = financial_doc['balance_sheet']
                    if 'income_statement' in financial_doc and financial_doc['income_statement']:
                        financial_data['income_statement'] = financial_doc['income_statement']
                    if 'cash_flow' in financial_doc and financial_doc['cash_flow']:
                        financial_data['cash_flow'] = financial_doc['cash_flow']
                    if 'main_indicators' in financial_doc and financial_doc['main_indicators']:
                        financial_data['main_indicators'] = financial_doc['main_indicators']

                if financial_data:
                    logger.info(f"📊 [财务数据] 成功提取{symbol}的财务数据，包含字段: {list(financial_data.keys())}")
                    return financial_data
                else:
                    logger.warning(f"⚠️ [财务数据] {symbol}的 stock_financial_data 记录存在但无有效财务数据字段")
            else:
                logger.debug(f"📊 [财务数据] stock_financial_data 集合中未找到{symbol}的记录")

            # 第二优先级：从 financial_data_cache 集合读取（临时缓存）
            collection = db.financial_data_cache

            # 查找缓存的原始财务数据
            cache_doc = collection.find_one({
                'symbol': symbol,
                'cache_type': 'raw_financial_data'
            }, sort=[('updated_at', -1)])

            if cache_doc:
                # 检查缓存是否过期（24小时）
                cache_time = cache_doc.get('updated_at')
                if cache_time and datetime.now() - cache_time < timedelta(hours=24):
                    financial_data = cache_doc.get('financial_data', {})
                    if financial_data:
                        logger.info(f"✅ [财务缓存] 从 financial_data_cache 获取{symbol}原始财务数据")
                        return financial_data
                else:
                    logger.debug(f"📊 [财务缓存] {symbol}原始财务数据缓存已过期")
            else:
                logger.debug(f"📊 [财务缓存] 未找到{symbol}原始财务数据缓存")

        except Exception as e:
            logger.debug(f"📊 [财务缓存] 获取{symbol}原始财务数据缓存失败: {e}")

        return None

    def _get_cached_stock_info(self, symbol: str) -> dict:
        """从数据库缓存获取股票基本信息"""
        try:
            from ..cache.app_adapter import get_mongodb_client
            client = get_mongodb_client()
            if not client:
                return {}

            db = client.get_database('sinoquant')
            collection = db.stock_basic_info

            # 查找股票基本信息
            doc = collection.find_one({'code': symbol})
            if doc:
                return {
                    'symbol': symbol,
                    'name': doc.get('name', ''),
                    'industry': doc.get('industry', ''),
                    'market': doc.get('market', ''),
                    'source': 'database_cache'
                }
        except Exception as e:
            logger.debug(f"📊 获取{symbol}股票基本信息缓存失败: {e}")

        return {}

    def _restore_financial_data_format(self, cached_data: dict) -> dict:
        """将缓存的财务数据恢复为DataFrame格式"""
        try:
            import pandas as pd
            restored_data = {}

            for key, value in cached_data.items():
                if isinstance(value, list) and value:  # 如果是list格式的数据
                    # 转换回DataFrame
                    restored_data[key] = pd.DataFrame(value)
                else:
                    restored_data[key] = value

            return restored_data
        except Exception as e:
            logger.debug(f"📊 恢复财务数据格式失败: {e}")
            return cached_data

    # ------------------------------------------------------------------
    # 写入缓存
    # ------------------------------------------------------------------

    def _cache_raw_financial_data(self, symbol: str, financial_data: dict, stock_info: dict):
        """将原始财务数据缓存到数据库"""
        try:
            if not use_app_cache_enabled(False):
                logger.debug(f"📊 [财务缓存] 应用缓存未启用，跳过缓存保存")
                return

            from ..cache.app_adapter import get_mongodb_client
            client = get_mongodb_client()
            if not client:
                logger.debug(f"📊 [财务缓存] MongoDB客户端不可用")
                return

            db = client.get_database('sinoquant')
            collection = db.financial_data_cache

            # 将DataFrame转换为可序列化的格式
            serializable_data = {}
            for key, value in financial_data.items():
                if hasattr(value, 'to_dict'):  # pandas DataFrame
                    serializable_data[key] = value.to_dict('records')
                else:
                    serializable_data[key] = value

            cache_doc = {
                'symbol': symbol,
                'cache_type': 'raw_financial_data',
                'financial_data': serializable_data,
                'stock_info': stock_info,
                'updated_at': datetime.now()
            }

            # 使用upsert更新或插入
            collection.replace_one(
                {'symbol': symbol, 'cache_type': 'raw_financial_data'},
                cache_doc,
                upsert=True
            )

            logger.info(f"✅ [财务缓存] {symbol}原始财务数据已缓存到数据库")

        except Exception as e:
            logger.debug(f"📊 [财务缓存] 缓存{symbol}原始财务数据失败: {e}")
