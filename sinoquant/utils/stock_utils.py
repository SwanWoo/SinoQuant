"""
股票工具函数
提供股票代码识别、分类和处理功能
"""

import re
from typing import Dict, Tuple, Optional
from enum import Enum

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


class StockMarket(Enum):
    """股票市场枚举"""
    CHINA_A = "china_a"      # 中国A股
    UNKNOWN = "unknown"      # 未知


class StockUtils:
    """股票工具类"""

    @staticmethod
    def identify_stock_market(ticker: str) -> StockMarket:
        """
        识别股票代码所属市场

        Args:
            ticker: 股票代码

        Returns:
            StockMarket: 股票市场类型
        """
        if not ticker:
            return StockMarket.UNKNOWN

        ticker = str(ticker).strip().upper()

        # 中国A股：6位数字
        if re.match(r'^\d{6}$', ticker):
            return StockMarket.CHINA_A

        return StockMarket.UNKNOWN

    @staticmethod
    def is_china_stock(ticker: str) -> bool:
        """
        判断是否为中国A股

        Args:
            ticker: 股票代码

        Returns:
            bool: 是否为中国A股
        """
        return StockUtils.identify_stock_market(ticker) == StockMarket.CHINA_A

    @staticmethod
    def get_currency_info(ticker: str) -> Tuple[str, str]:
        """
        根据股票代码获取货币信息

        Args:
            ticker: 股票代码

        Returns:
            Tuple[str, str]: (货币名称, 货币符号)
        """
        market = StockUtils.identify_stock_market(ticker)

        if market == StockMarket.CHINA_A:
            return "人民币", "¥"
        else:
            return "未知", "?"

    @staticmethod
    def get_data_source(ticker: str) -> str:
        """
        根据股票代码获取推荐的数据源

        Args:
            ticker: 股票代码

        Returns:
            str: 数据源名称
        """
        market = StockUtils.identify_stock_market(ticker)

        if market == StockMarket.CHINA_A:
            return "china_unified"  # 使用统一的中国股票数据源
        else:
            return "unknown"

    @staticmethod
    def get_market_info(ticker: str) -> Dict:
        """
        获取股票市场的详细信息

        Args:
            ticker: 股票代码

        Returns:
            Dict: 市场信息字典
        """
        market = StockUtils.identify_stock_market(ticker)
        currency_name, currency_symbol = StockUtils.get_currency_info(ticker)
        data_source = StockUtils.get_data_source(ticker)

        market_names = {
            StockMarket.CHINA_A: "中国A股",
            StockMarket.UNKNOWN: "未知市场"
        }

        return {
            "ticker": ticker,
            "market": market.value,
            "market_name": market_names[market],
            "currency_name": currency_name,
            "currency_symbol": currency_symbol,
            "data_source": data_source,
            "is_china": market == StockMarket.CHINA_A,
        }


# 便捷函数，保持向后兼容
def is_china_stock(ticker: str) -> bool:
    """判断是否为中国A股（向后兼容）"""
    return StockUtils.is_china_stock(ticker)


def get_stock_market_info(ticker: str) -> Dict:
    """获取股票市场信息"""
    return StockUtils.get_market_info(ticker)
