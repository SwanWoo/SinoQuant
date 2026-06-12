"""
Alpha 量化交易相关的 Pydantic 数据模型

对应 MongoDB 集合: alpha_strategies, alpha_backtests, paper_accounts, paper_orders, paper_positions, paper_trades
"""

from pydantic import BaseModel, Field
from typing import Optional


class GenerateStrategyRequest(BaseModel):
    """生成量化策略请求 — LLM 根据分析报告生成 vnpy AlphaStrategy 子类代码"""
    analysis_task_id: Optional[str] = None
    symbol: str = Field(..., description="6位股票代码")
    market_report: str = ""
    sentiment_report: str = ""
    news_report: str = ""
    fundamentals_report: str = ""
    trade_decision: dict = Field(default_factory=dict)
    model_name: Optional[str] = None


class UpdateStrategyRequest(BaseModel):
    """更新策略代码请求 — 用户手动修改 LLM 生成的策略代码"""
    code: str = Field(..., description="Python 策略代码")


class RunBacktestRequest(BaseModel):
    """运行历史回测请求 — 用 vnpy BacktestingEngine 在历史数据上验证策略"""
    strategy_id: str
    symbols: list[str] = Field(..., min_length=1, description="6位股票代码列表")
    start_date: str = Field(..., description="回测开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="回测结束日期 YYYY-MM-DD")
    capital: float = Field(default=1_000_000, ge=10000)
    strategy_params: dict = Field(default_factory=dict)


class StartSimulationRequest(BaseModel):
    """启动模拟盘交易请求 — 用 SimulationEngine 实时模拟策略执行"""
    strategy_id: str
    symbols: list[str] = Field(..., min_length=1)
    capital: float = Field(default=1_000_000, ge=10000)
    strategy_params: dict = Field(default_factory=dict)


class QuickBacktestRequest(BaseModel):
    """快速回测请求 — 自动取最近N个交易日回测，无需手动选日期"""
    strategy_id: str
    symbols: list[str] = Field(..., min_length=1, description="6位股票代码列表")
    trading_days: int = Field(default=5, ge=1, le=30, description="回测交易日天数")
    capital: float = Field(default=1_000_000, ge=10000)
    strategy_params: dict = Field(default_factory=dict)


class SyncDataRequest(BaseModel):
    """同步行情数据请求 — 从数据源拉取指定股票的历史行情用于回测"""
    symbols: list[str] = Field(..., min_length=1, description="6位股票代码列表")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
