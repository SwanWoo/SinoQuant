"""
缓存管理模块

支持文件缓存（默认）和应用缓存适配器。
"""

import os
from sinoquant.utils.logging_manager import get_logger
logger = get_logger('agents')

try:
    from .file_cache import StockDataCache
except ImportError:
    StockDataCache = None

try:
    from .app_adapter import get_basics_from_cache, get_market_quote_dataframe
except ImportError:
    get_basics_from_cache = None
    get_market_quote_dataframe = None

try:
    from .mongodb_cache_adapter import MongoDBCacheAdapter
except ImportError:
    MongoDBCacheAdapter = None

_cache_instance = None

def get_cache():
    """获取文件缓存实例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = StockDataCache()
        logger.info("✅ 使用文件缓存系统")
    return _cache_instance

__all__ = [
    'get_cache',
    'StockDataCache',
    'get_basics_from_cache',
    'get_market_quote_dataframe',
    'MongoDBCacheAdapter',
]
