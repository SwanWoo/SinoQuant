"""
数据源编码统一定义
所有数据源的编码、名称、描述等信息都在这里定义

添加新数据源的步骤：
1. 在 DataSourceCode 枚举中添加新的数据源编码
2. 在 DATA_SOURCE_REGISTRY 中注册数据源信息
3. 在对应的 provider 中实现数据源接口
4. 更新前端的数据源类型选项（如果需要）
"""

from enum import Enum
from typing import Dict, List, Optional
from dataclasses import dataclass


class DataSourceCode(str, Enum):
    """
    数据源编码枚举

    命名规范：
    - 使用大写字母和下划线
    - 值使用小写字母和下划线
    - 保持简洁明了
    """

    # ==================== 缓存数据源 ====================
    MONGODB = "mongodb"  # MongoDB 数据库缓存（最高优先级）

    # ==================== 中国市场数据源 ====================
    TUSHARE = "tushare"      # Tushare - 专业A股数据
    AKSHARE = "akshare"      # AKShare - 开源金融数据
    BAOSTOCK = "baostock"    # BaoStock - 免费A股数据

    # ==================== 专业数据源 ====================
    WIND = "wind"        # Wind 万得 - 专业金融终端
    CHOICE = "choice"    # 东方财富 Choice - 专业金融数据

    # ==================== 其他数据源 ====================
    QUANDL = "quandl"        # Quandl - 经济和金融数据
    LOCAL_FILE = "local_file"  # 本地文件数据源
    CUSTOM = "custom"        # 自定义数据源


@dataclass
class DataSourceInfo:
    """数据源信息"""
    code: str  # 数据源编码
    name: str  # 数据源名称
    display_name: str  # 显示名称
    provider: str  # 提供商
    description: str  # 描述
    supported_markets: List[str]  # 支持的市场（a_shares, etc.）
    requires_api_key: bool  # 是否需要 API 密钥
    is_free: bool  # 是否免费
    official_website: Optional[str] = None  # 官方网站
    documentation_url: Optional[str] = None  # 文档地址
    features: List[str] = None  # 特性列表

    def __post_init__(self):
        if self.features is None:
            self.features = []


# ==================== 数据源注册表 ====================
DATA_SOURCE_REGISTRY: Dict[str, DataSourceInfo] = {
    # MongoDB 缓存
    DataSourceCode.MONGODB: DataSourceInfo(
        code=DataSourceCode.MONGODB,
        name="MongoDB",
        display_name="MongoDB 缓存",
        provider="MongoDB Inc.",
        description="本地 MongoDB 数据库缓存，最高优先级数据源",
        supported_markets=["a_shares", "crypto", "futures"],
        requires_api_key=False,
        is_free=True,
        features=["本地缓存", "最快速度", "离线可用"],
    ),

    # Tushare
    DataSourceCode.TUSHARE: DataSourceInfo(
        code=DataSourceCode.TUSHARE,
        name="Tushare",
        display_name="Tushare",
        provider="Tushare",
        description="专业的A股数据接口，提供高质量的历史数据和实时行情",
        supported_markets=["a_shares"],
        requires_api_key=True,
        is_free=False,  # 免费版有限制，专业版需付费
        official_website="https://tushare.pro",
        documentation_url="https://tushare.pro/document/2",
        features=["历史行情", "实时行情", "财务数据", "基本面数据", "新闻公告"],
    ),

    # AKShare
    DataSourceCode.AKSHARE: DataSourceInfo(
        code=DataSourceCode.AKSHARE,
        name="AKShare",
        display_name="AKShare",
        provider="AKFamily",
        description="开源的金融数据接口，支持A股，完全免费",
        supported_markets=["a_shares"],
        requires_api_key=False,
        is_free=True,
        official_website="https://akshare.akfamily.xyz",
        documentation_url="https://akshare.akfamily.xyz/introduction.html",
        features=["历史行情", "实时行情", "财务数据", "新闻资讯", "完全免费"],
    ),

    # BaoStock
    DataSourceCode.BAOSTOCK: DataSourceInfo(
        code=DataSourceCode.BAOSTOCK,
        name="BaoStock",
        display_name="BaoStock",
        provider="BaoStock",
        description="免费的A股数据接口，提供稳定的历史数据",
        supported_markets=["a_shares"],
        requires_api_key=False,
        is_free=True,
        official_website="http://baostock.com",
        documentation_url="http://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3",
        features=["历史行情", "财务数据", "完全免费", "数据稳定"],
    ),

    # Wind
    DataSourceCode.WIND: DataSourceInfo(
        code=DataSourceCode.WIND,
        name="Wind",
        display_name="Wind 万得",
        provider="Wind 万得",
        description="专业金融终端，提供全面的金融数据和分析工具",
        supported_markets=["a_shares"],
        requires_api_key=True,
        is_free=False,  # 专业版需付费
        official_website="https://www.wind.com.cn",
        features=["专业数据", "全市场覆盖", "高质量数据", "专业分析"],
    ),

    # Choice
    DataSourceCode.CHOICE: DataSourceInfo(
        code=DataSourceCode.CHOICE,
        name="Choice",
        display_name="东方财富 Choice",
        provider="东方财富",
        description="专业金融数据终端，提供全面的A股数据",
        supported_markets=["a_shares"],
        requires_api_key=True,
        is_free=False,  # 专业版需付费
        official_website="http://choice.eastmoney.com",
        features=["专业数据", "A股专注", "高质量数据", "专业分析"],
    ),

    # Quandl
    DataSourceCode.QUANDL: DataSourceInfo(
        code=DataSourceCode.QUANDL,
        name="Quandl",
        display_name="Quandl",
        provider="Nasdaq",
        description="经济和金融数据平台，提供全球经济数据",
        supported_markets=["a_shares"],
        requires_api_key=True,
        is_free=True,  # 有免费版
        official_website="https://www.quandl.com",
        documentation_url="https://docs.quandl.com",
        features=["经济数据", "金融数据", "全球覆盖"],
    ),

    # Local File
    DataSourceCode.LOCAL_FILE: DataSourceInfo(
        code=DataSourceCode.LOCAL_FILE,
        name="Local File",
        display_name="本地文件",
        provider="本地",
        description="从本地文件读取数据",
        supported_markets=["a_shares"],
        requires_api_key=False,
        is_free=True,
        features=["离线可用", "自定义数据", "完全免费"],
    ),

    # Custom
    DataSourceCode.CUSTOM: DataSourceInfo(
        code=DataSourceCode.CUSTOM,
        name="Custom",
        display_name="自定义数据源",
        provider="自定义",
        description="自定义数据源接口",
        supported_markets=["a_shares"],
        requires_api_key=False,
        is_free=True,
        features=["自定义接口", "灵活配置"],
    ),
}


# ==================== 辅助函数 ====================

def get_data_source_info(code: str) -> Optional[DataSourceInfo]:
    """
    获取数据源信息

    Args:
        code: 数据源编码

    Returns:
        数据源信息，如果不存在则返回 None
    """
    return DATA_SOURCE_REGISTRY.get(code)


def list_all_data_sources() -> List[DataSourceInfo]:
    """
    列出所有数据源

    Returns:
        所有数据源信息列表
    """
    return list(DATA_SOURCE_REGISTRY.values())


def list_data_sources_by_market(market: str) -> List[DataSourceInfo]:
    """
    列出支持指定市场的数据源

    Args:
        market: 市场类型（a_shares, etc.）

    Returns:
        支持该市场的数据源列表
    """
    return [
        info for info in DATA_SOURCE_REGISTRY.values()
        if market in info.supported_markets
    ]


def list_free_data_sources() -> List[DataSourceInfo]:
    """
    列出所有免费数据源

    Returns:
        免费数据源列表
    """
    return [
        info for info in DATA_SOURCE_REGISTRY.values()
        if info.is_free
    ]


def is_data_source_supported(code: str) -> bool:
    """
    检查数据源是否支持

    Args:
        code: 数据源编码

    Returns:
        是否支持
    """
    return code in DATA_SOURCE_REGISTRY
