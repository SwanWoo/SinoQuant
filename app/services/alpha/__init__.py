from .strategy_generator import StrategyGeneratorService
from .code_validator import CodeValidator

# vnpy-dependent modules — imported lazily (vnpy not available in all environments)


def __getattr__(name):
    if name == "AlphaDataBridge":
        from .data_bridge import AlphaDataBridge
        return AlphaDataBridge
    if name == "BacktestService":
        from .backtest_service import BacktestService
        return BacktestService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
