# 导入新闻模块
try:
    from .news import getNewsData
except ImportError:
    try:
        from .news.google_news import getNewsData
    except ImportError:
        getNewsData = None

# 导入日志模块
from sinoquant.utils.logging_manager import get_logger
logger = get_logger('agents')

from .interface import (
    # News and sentiment functions
    get_google_news,
    # Tushare data functions
    get_china_stock_data_tushare,
    get_china_stock_fundamentals_tushare,
    # Unified China data functions (recommended)
    get_china_stock_data_unified,
    get_china_stock_info_unified,
    switch_china_data_source,
    get_current_china_data_source,
)

__all__ = [
    # News and sentiment functions
    "get_google_news",
    # Tushare data functions
    "get_china_stock_data_tushare",
    "get_china_stock_fundamentals_tushare",
    # Unified China data functions
    "get_china_stock_data_unified",
    "get_china_stock_info_unified",
    "switch_china_data_source",
    "get_current_china_data_source",
]
