"""
配置管理服务包

Re-exports config_service and ConfigService for backward compatibility.
"""

__all__ = ["config_service", "ConfigService"]


def __getattr__(name):
    if name in __all__:
        from app.services.config_service import config_service, ConfigService
        if name == "config_service":
            return config_service
        if name == "ConfigService":
            return ConfigService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
