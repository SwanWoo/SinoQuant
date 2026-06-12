"""
analysis 子包 — 拆分自 simple_analysis_service.py

子模块导出:
    create_analysis_config, get_provider_by_model_name (模块级函数)
    AnalysisRunnerMixin, TaskManagerMixin, ResultPersistenceMixin (mixin 类)
    _get_stock_info_safe, _looks_like_meta_text, _build_summary_from_text, _pick_best_summary (文本工具)

SimpleAnalysisService 和 get_simple_analysis_service 仍在 facade 文件
(app.services.simple_analysis_service) 中定义，所有外部调用者继续从该模块导入。
"""

from ._analysis_config import create_analysis_config, get_provider_by_model_name  # noqa: F401
from ._analysis_runner import AnalysisRunnerMixin  # noqa: F401
from ._result_persistence import ResultPersistenceMixin  # noqa: F401
from ._task_manager import TaskManagerMixin  # noqa: F401
from ._text_utils import (  # noqa: F401
    _get_stock_info_safe,
    _looks_like_meta_text,
    _build_summary_from_text,
    _pick_best_summary,
)
