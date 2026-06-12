"""
数据模型模块
"""

# 导入股票数据模型
from .stock_models import (
    StockBasicInfoExtended,
    MarketQuotesExtended,
    MarketInfo,
    TechnicalIndicators,
    StockBasicInfoResponse,
    MarketQuotesResponse,
    StockListResponse,
    MarketType,
    ExchangeType,
    CurrencyType,
    StockStatus
)

# 导入厂商配置模型
from .vendor_config import (
    VendorConfig,
    VendorConfigRequest,
    VendorConfigResponse,
    VendorConfigListItem,
    VendorTestRequest,
    VendorTestResponse,
    VendorBulkImportRequest,
    VendorBulkImportResponse,
    VendorTypeInfo,
    VendorAuthTypeInfo,
    VendorType,
    ApiAuthType,
    VendorStatus
)

__all__ = [
    # 股票数据模型
    "StockBasicInfoExtended",
    "MarketQuotesExtended",
    "MarketInfo",
    "TechnicalIndicators",
    "StockBasicInfoResponse",
    "MarketQuotesResponse",
    "StockListResponse",
    "MarketType",
    "ExchangeType",
    "CurrencyType",
    "StockStatus",
    # 厂商配置模型
    "VendorConfig",
    "VendorConfigRequest",
    "VendorConfigResponse",
    "VendorConfigListItem",
    "VendorTestRequest",
    "VendorTestResponse",
    "VendorBulkImportRequest",
    "VendorBulkImportResponse",
    "VendorTypeInfo",
    "VendorAuthTypeInfo",
    "VendorType",
    "ApiAuthType",
    "VendorStatus"
]
