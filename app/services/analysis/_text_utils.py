"""
文本工具函数 — 纯函数，无类依赖
"""

import re
import logging
from typing import Any, Dict, List, Optional

from sinoquant.utils.text_utils import normalize_report_content

logger = logging.getLogger("app.services.simple_analysis_service")

# ---------------------------------------------------------------------------
# 股票基础信息获取（用于补充显示名称）
# ---------------------------------------------------------------------------
try:
    from sinoquant.dataflows.data_source_manager import get_data_source_manager
    _data_source_manager = get_data_source_manager()
    def _get_stock_info_safe(stock_code: str):
        """获取股票基础信息的安全封装"""
        return _data_source_manager.get_stock_basic_info(stock_code)
except Exception:
    _get_stock_info_safe = None


# ---------------------------------------------------------------------------
# 元文本检测 & 摘要构建
# ---------------------------------------------------------------------------

_SUMMARY_META_RE = re.compile(
    r"(?i)(thinking process|reasoning process|wait,\s*i need|i need to check|"
    r"okay,\s*generating|final output|drafting strategy|content generation|"
    r"review against constraints|final polish|revised draft)"
)


def _looks_like_meta_text(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_SUMMARY_META_RE.search(text))


def _build_summary_from_text(text: Any, max_chars: int = 220) -> str:
    """从报告正文中提炼可展示摘要，跳过明显元提示文本。"""
    if text is None:
        return ""

    normalized = normalize_report_content(text, "summary").strip()
    if not normalized:
        return ""

    lines: List[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip().strip("#*` ").strip()
        if not line:
            continue
        if _looks_like_meta_text(line):
            continue
        if line.startswith("|"):
            continue
        if re.match(r"(?i)^(title:\s*#|section\s*\d+\s*:|table\s*:|body\s*:|heading\s*:)", line):
            continue
        if line.lower() in {"摘要", "结论", "投资建议", "最终交易决策"}:
            continue
        lines.append(line)
        if len(" ".join(lines)) >= max_chars:
            break

    summary = " ".join(lines).strip()
    if not summary:
        summary = re.sub(r"\s+", " ", normalized.replace("#", " ").replace("*", " ")).strip()

    if _looks_like_meta_text(summary):
        return ""

    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."

    return summary


def _pick_best_summary(candidates: List[Any], max_chars: int = 220) -> str:
    """从候选文本中选择首个有效摘要。"""
    fallback = ""
    for candidate in candidates:
        summary = _build_summary_from_text(candidate, max_chars=max_chars)
        if not summary:
            continue
        if len(summary) >= 20:
            return summary
        if not fallback:
            fallback = summary
    return fallback
