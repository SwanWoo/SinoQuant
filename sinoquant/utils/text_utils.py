#!/usr/bin/env python3
"""
文本处理工具函数
提供文本清理、格式化等通用功能
"""

import ast
import json
import re
from typing import Any, Dict


_THINK_TAG_RE = re.compile(
    r"<\s*(think|thinking|reasoning)\b[^>]*>.*?<\s*/\s*\1\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_UNFINISHED_THINK_TAG_RE = re.compile(
    r"<\s*(think|thinking|reasoning)\b[^>]*>.*$",
    flags=re.DOTALL | re.IGNORECASE,
)
_CLOSING_THINK_TAG_RE = re.compile(
    r"<\s*/\s*(think|thinking|reasoning)\s*>",
    flags=re.IGNORECASE,
)
_THINK_FENCE_RE = re.compile(
    r"```(?:thinking|reasoning)[\s\S]*?```",
    flags=re.IGNORECASE,
)
_ROLE_PREFIX_PATTERN = (
    r"(?:[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\-/\u4e00-\u9fff ]{0,40}\s*[:：-]\s*)?"
)
_REASONING_LINE_RE = re.compile(
    rf"(?i)^\s*(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*{_ROLE_PREFIX_PATTERN}"
    r"(thinking process|reasoning process|chain of thought|"
    r"analysis of the request|analyze the request|"
    r"wait,\s*i need to check|i need to check|let me think|"
    r"drafting strategy|content generation|review against constraints|final polish|"
    r"self-correction|user instruction block|tool output section|analysis plan|"
    r"constraint check|final output generation|final output construction|"
    r"思考过程|思维过程|推理过程|让我先想|我需要检查|先检查一下)"
    r"(?:\*\*)?\s*[:：-]?\s*$"
)
_REASONING_INLINE_RE = re.compile(
    r"(?i)\b(thinking process|reasoning process|chain of thought|"
    r"analysis of the request|analyze the request|"
    r"wait,\s*i need to check|i need to check|let me think|"
    r"drafting strategy|content generation|review against constraints|final polish|"
    r"self-correction|user instruction block|tool output section|analysis plan|"
    r"constraint check|final output generation|final output construction|final check|"
    r"思考过程|思维过程|推理过程|我需要检查|先检查一下)\b"
)
_REASONING_META_BULLET_RE = re.compile(
    r"(?i)^\s*(?:[-*]|\d+[.)])\s*(?:\*\*)?"
    r"(context|correction|decision|plan|priority|instruction|prompt)\s*[:：]"
)
_REASONING_SHORT_LINE_RE = re.compile(
    r"(?i)^\s*(?:[-*]\s*)?(let['’]?s write\.?|let['’]?s draft\.?|let['’]?s go\.?|"
    r"then it says|very complex prompt|this looks solid\.?|i will use this version\.?)\s*$"
)
_REASONING_BLOCK_START_RE = re.compile(
    rf"(?i)^\s*(?:[-*]|\d+[.)])?\s*(?:\*\*)?{_ROLE_PREFIX_PATTERN}"
    r"(thinking process|reasoning process|analysis of the request|"
    r"analyze the request|wait,\s*i need to check|"
    r"drafting strategy|content generation|review against constraints|final polish|"
    r"analysis plan|constraint check|final output generation|final output construction|"
    r"思考过程|思维过程|推理过程|我需要检查|先检查一下)"
)
_PLANNING_LINE_RE = re.compile(
    r"(?i)^\s*(structure|content|tone|length|constraint check|analysis plan|"
    r"review against constraints|final output generation|final output construction)\s*[:：]"
)
_CONTENT_HEADING_RE = re.compile(
    r"^\s*(#{1,6}\s+|[一二三四五六七八九十]+[、.]\s*|"
    r"\d+[、.]\s*|标题[:：]\s*|正文[:：]\s*|title[:：]\s*|body[:：]\s*|最终交易建议|投资建议|结论|摘要)"
)
_BLOCK_EXIT_HEADING_RE = re.compile(
    r"^\s*(#{1,6}\s+|[一二三四五六七八九十]+[、.]\s*|"
    r"标题[:：]\s*|正文[:：]\s*|title[:：]\s*|body[:：]\s*|最终交易建议|投资建议|结论|摘要)"
)
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+")
_META_PREFIX_RE = re.compile(
    r"(?i)(instruction|prompt|there is a section|this suggests|"
    r"please write all analysis|priority:|"
    r"你是一位|请使用中文撰写|以下是综合分析报告)"
)
_FINAL_MARKER_LINE_RE = re.compile(
    r"(?i)^\s*(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*"
    r"(final answer|final output|final output generation|"
    r"final output construction|最终答案|最终输出)"
    r"(?:\*\*)?\s*[:：.]?\s*(?:\(.*\))?\s*$"
)
_PAREN_REASONING_LINE_RE = re.compile(
    r"(?i)^\s*[\(\[（]\s*"
    r"(wait[,，]|i need to check|i should check|"
    r"let me think|one more check|final check|writing text|"
    r"我需要检查|先检查一下|让我先想)"
)
_SHORT_META_LINE_RE = re.compile(
    r"(?i)^\s*(okay,\s*(generating(?: now)?|writing|ready|let['’]?s go|final check.*)|"
    r"let['’]?s (do it|go)\.?|"
    r"final output\.\)?|revised draft:?|draft:?|count:\s*\d+|"
    r"writing text to ensure length.*)\s*$"
)
_CONSTRAINT_CHECK_LINE_RE = re.compile(
    r"(?i)^\s*\d+\.\s*(no emojis|specific title|specific sections|"
    r"min \d+ chars?|use provided data|use table|currency)"
)
_CONSTRAINT_TEMPLATE_LINE_RE = re.compile(
    r"(?i)^\s*(?:[-*]|\d+[.)])?\s*(?:\*+\s*)?"
    r"(title must|must contain|at least \d+|minimum \d+|must include|"
    r"currency symbol|word count|output only the final report|"
    r"title:|section\s*\d+\s*:|table:|drafting the content|"
    r"writing\s*&\s*refining|analyze the request|constraints?:)"
)
_TEMPLATE_PLAN_LINE_RE = re.compile(
    r"(?i)^\s*(title:\s*#|section\s*\d+\s*:|table\s*:|body\s*:|heading\s*:)"
)
_META_LINE_HINT_RE = re.compile(
    r"(?i)(thinking process|reasoning process|wait,\s*i need|wait,\s*one more check|"
    r"i need to check|i should check|"
    r"i need to ensure|i need to make sure|need to make sure|"
    r"okay,\s*generating|final output|drafting strategy|content generation|"
    r"review against constraints|final polish|revised draft|writing text to ensure length)"
)
# 匹配 LLM 在 content 中伪造的工具调用 XML 格式（如 deepseek-v4-flash 的幻觉输出）
# 格式1: <｜​｜tool_calls> ... </｜​｜tool_calls>  ← 含 ｜ 和零宽空格 ​
# 格式2: <｜｜DSML｜｜tool_calls> ... </｜｜DSML｜｜tool_calls>  ← DeepSeek v4 DSML格式
_FAKE_TOOL_TAG_NAME_RE = re.compile(
    r"<\s*(?:[｜\|][​]?\s*){0,3}(?:[A-Za-z]*[｜\|][​]?\s*){0,3}(?:tool_calls?|invoke|parameter|function_call)\s*(?:name\s*=|string\s*=|[>/\s])",
    flags=re.IGNORECASE,
)
_FAKE_TOOL_CLOSE_TAG_RE = re.compile(
    r"<\s*/\s*(?:[｜\|][​]?\s*){0,3}(?:[A-Za-z]*[｜\|][​]?\s*){0,3}(?:tool_calls?|invoke|parameter)\s*>",
    flags=re.IGNORECASE,
)
_FAKE_TOOL_ANY_TAG_RE = re.compile(
    r"<\s*/?\s*(?:[｜\|][​]?\s*){0,3}(?:[A-Za-z]*[｜\|][​]?\s*){0,3}(?:tool_calls?|invoke|parameter|function_call)\b",
    flags=re.IGNORECASE,
)


_TEAM_HISTORY_KEYS = {
    "history",
    "latest_speaker",
    "count",
    "current_risky_response",
    "current_safe_response",
    "current_neutral_response",
    "current_bull_response",
    "current_bear_response",
}

_TEAM_DECISION_KEYS = (
    "judge_decision",
    "final_trade_decision",
    "decision",
)

_TEAM_DEBATE_KEYS = (
    "bull_history",
    "bear_history",
    "risky_history",
    "safe_history",
    "neutral_history",
)

_ROLE_MARKERS_BY_MODULE = {
    "bull_researcher": ("Bull Analyst:", "看涨分析师:"),
    "bear_researcher": ("Bear Analyst:", "看跌分析师:"),
    "risky_analyst": ("Risky Analyst:", "激进风险分析师:"),
    "safe_analyst": ("Safe Analyst:", "保守风险分析师:"),
    "neutral_analyst": ("Neutral Analyst:", "中性风险分析师:"),
}


def _is_meta_reasoning_residue(text: str) -> bool:
    """判断文本是否几乎完全由思维草稿/约束检查语句构成。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    content_lines = 0
    meta_lines = 0

    for line in lines[:80]:
        if _CONTENT_HEADING_RE.search(line):
            content_lines += 1
            continue

        if (
            _META_LINE_HINT_RE.search(line)
            or _PAREN_REASONING_LINE_RE.search(line)
            or _SHORT_META_LINE_RE.search(line)
            or _CONSTRAINT_CHECK_LINE_RE.search(line)
            or _CONSTRAINT_TEMPLATE_LINE_RE.search(line)
            or _TEMPLATE_PLAN_LINE_RE.search(line)
        ):
            meta_lines += 1
            continue

        has_chinese_content = bool(re.search(r"[\u4e00-\u9fff]", line)) and len(line) >= 18
        english_word_count = len(re.findall(r"[A-Za-z]{3,}", line))
        has_english_content = english_word_count >= 6 and len(line) >= 40

        if has_chinese_content or has_english_content:
            content_lines += 1

    if content_lines == 0 and meta_lines >= max(2, min(8, len(lines) // 2)):
        return True

    if content_lines <= 1 and meta_lines >= max(3, len(lines) // 3):
        return True

    return False


def _strip_reasoning_lines(text: str) -> str:
    """移除常见思维链草稿行。"""
    if not text:
        return text

    filtered_lines = []
    in_reasoning_block = False

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            if not in_reasoning_block:
                filtered_lines.append("")
            continue

        if in_reasoning_block:
            if _FINAL_MARKER_LINE_RE.search(stripped):
                in_reasoning_block = False
                continue

            if (
                _PAREN_REASONING_LINE_RE.search(stripped)
                or _SHORT_META_LINE_RE.search(stripped)
                or _CONSTRAINT_CHECK_LINE_RE.search(stripped)
                or _CONSTRAINT_TEMPLATE_LINE_RE.search(stripped)
                or _TEMPLATE_PLAN_LINE_RE.search(stripped)
            ):
                continue

            if _BLOCK_EXIT_HEADING_RE.search(stripped):
                in_reasoning_block = False
                filtered_lines.append(line)
                continue

            if "报告" in stripped and len(stripped) <= 80 and not _REASONING_INLINE_RE.search(stripped):
                in_reasoning_block = False
                filtered_lines.append(line)
                continue

            # 仍在草稿块中：跳过明显推理草稿与规划条目
            if _REASONING_LINE_RE.search(stripped):
                continue
            if _REASONING_INLINE_RE.search(stripped):
                continue
            if _REASONING_SHORT_LINE_RE.search(stripped):
                continue
            if _PLANNING_LINE_RE.search(stripped):
                continue
            if _REASONING_META_BULLET_RE.search(stripped):
                continue
            if re.match(r"^(\*|-|\d+[.)])\s*", stripped):
                continue

            # 检测到中文正文段落，认为已进入最终内容
            looks_like_content_paragraph = (
                len(stripped) >= 20
                and re.search(r"[\u4e00-\u9fff]", stripped)
                and not _REASONING_INLINE_RE.search(stripped)
                and not _PLANNING_LINE_RE.search(stripped)
            )
            if looks_like_content_paragraph:
                in_reasoning_block = False
                filtered_lines.append(line)
            continue

        if _PAREN_REASONING_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _SHORT_META_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _CONSTRAINT_CHECK_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _CONSTRAINT_TEMPLATE_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _TEMPLATE_PLAN_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if stripped.lower().startswith("final output"):
            in_reasoning_block = True
            continue

        if _REASONING_BLOCK_START_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _REASONING_LINE_RE.search(stripped):
            in_reasoning_block = True
            continue

        if _FINAL_MARKER_LINE_RE.search(stripped):
            continue

        if _REASONING_SHORT_LINE_RE.search(stripped):
            continue

        if _PLANNING_LINE_RE.search(stripped):
            continue

        is_bullet_like = bool(re.match(r"^(\*|-|\d+[.)])\s*", stripped))
        if is_bullet_like and _REASONING_INLINE_RE.search(stripped):
            continue
        if _REASONING_META_BULLET_RE.search(stripped):
            continue

        filtered_lines.append(line)

    cleaned = "\n".join(filtered_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_fake_tool_blocks(text: str) -> str:
    """移除 DeepSeek v4 等模型在 content 中伪造的工具调用 XML 块。

    格式示例:
    <｜｜tool_calls>
    <｜｜invoke name="get_financial_data">
    <｜｜parameter name="ticker">600519</｜｜parameter>
    </｜｜invoke>
    </｜｜tool_calls>
    """
    if not text:
        return text

    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        # 跳过任何包含伪造工具调用标签的行
        if _FAKE_TOOL_ANY_TAG_RE.search(stripped):
            continue
        result.append(line)

    cleaned = "\n".join(result)
    # 清理多余的空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def remove_thinking_content(text: str) -> str:
    """
    移除思维链内容（如 <think>...</think> 标签内的内容）

    某些推理模型（如 DeepSeek R1、Qwen3-Thinking 等）会在响应中包含思维链内容，
    这些内容不需要显示在最终报告中。

    Args:
        text: 原始文本内容

    Returns:
        移除思维链内容后的文本
    """
    if not text:
        return text

    # 某些模型只输出 </think> 结束标签：优先保留其后内容
    closing_matches = list(_CLOSING_THINK_TAG_RE.finditer(text))
    if closing_matches:
        tail = text[closing_matches[-1].end():].strip()
        if tail:
            text = tail
        else:
            text = text[:closing_matches[-1].start()]

    text = _THINK_TAG_RE.sub("", text)
    text = _UNFINISHED_THINK_TAG_RE.sub("", text)
    text = _THINK_FENCE_RE.sub("", text)

    # 移除 LLM 在 content 中伪造的工具调用 XML 格式（DeepSeek v4 幻觉）
    # 格式示例: <｜​｜tool_calls> ... </｜​｜tool_calls>
    # 先整体移除块级标签对
    text = _strip_fake_tool_blocks(text)
    # 再逐行过滤残留的孤立标签
    if _FAKE_TOOL_ANY_TAG_RE.search(text):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not _FAKE_TOOL_ANY_TAG_RE.search(line))

    # 常见“最终答案”分隔符，优先保留后半段正文
    lower_text = text.lower()
    final_markers = [
        "final answer:",
        "final output:",
        "final answer",
        "final output",
        "final output generation",
        "final output construction",
        "最终答案：",
        "最终输出：",
        "最终答案",
        "最终输出",
    ]
    marker_positions = [lower_text.rfind(m) for m in final_markers if lower_text.rfind(m) != -1]
    if marker_positions:
        text = text[max(marker_positions):]
        # 去掉 marker 本身
        text = re.sub(r"(?i)^(final answer:|final output:|最终答案：|最终输出：)\s*", "", text)

    cleaned = _strip_reasoning_lines(text)

    # 若在正文标题前仍有元提示词/推理前缀，则仅保留首个 Markdown 标题之后内容
    heading_match = _MARKDOWN_HEADING_RE.search(cleaned)
    if heading_match and heading_match.start() > 0:
        prefix = cleaned[:heading_match.start()].strip()
        prefix_is_meta = (
            bool(_REASONING_LINE_RE.search(prefix))
            or bool(_META_PREFIX_RE.search(prefix))
            or bool(_META_LINE_HINT_RE.search(prefix))
            or bool(_CONSTRAINT_TEMPLATE_LINE_RE.search(prefix))
            or _is_meta_reasoning_residue(prefix)
        )
        if prefix and prefix_is_meta:
            cleaned = cleaned[heading_match.start():].lstrip()
            cleaned = _strip_reasoning_lines(cleaned)

    # 如果仍然明显带有思维链痕迹，优先从首个正文标题开始截取
    if _REASONING_LINE_RE.search(cleaned):
        lines = cleaned.splitlines()
        start_idx = -1
        for idx, line in enumerate(lines):
            s = line.strip()
            if not s:
                continue
            if _CONTENT_HEADING_RE.search(s) or ("报告" in s and len(s) <= 80):
                start_idx = idx
                break
        if start_idx != -1:
            cleaned = _strip_reasoning_lines("\n".join(lines[start_idx:]))

    cleaned = cleaned.strip()
    if cleaned and _is_meta_reasoning_residue(cleaned):
        return ""

    return cleaned


def _try_parse_structured_text(text: str) -> Any:
    """尝试将字符串解析为结构化对象（dict/list）。"""
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None

    for parser in (ast.literal_eval, json.loads):
        try:
            parsed = parser(stripped)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            continue

    return None


def _keep_latest_role_block(text: str, module_key: str) -> str:
    """对于多轮辩论文本，仅保留对应角色最后一轮发言。"""
    markers = _ROLE_MARKERS_BY_MODULE.get(module_key)
    if not markers:
        return text

    positions = [text.rfind(marker) for marker in markers]
    start = max(positions)
    if start <= 0:
        return text

    tail = text[start:].strip()
    # 避免误截断到极短片段
    if len(tail) < 20:
        return text
    return tail


def normalize_report_content(content: Any, module_key: str = "") -> str:
    """
    标准化报告模块内容，避免把内部 history / dict 原样透传给前端。

    处理规则：
    1. 字符串：移除思维链；若是序列化 dict/list，再递归清洗。
    2. 团队辩论结构：优先提取 judge_decision，丢弃 history 元数据。
    3. 其他 dict/list：递归清洗后转为可读字符串。
    """
    if content is None:
        return ""

    if isinstance(content, str):
        cleaned_text = remove_thinking_content(content).strip()
        cleaned_text = _keep_latest_role_block(cleaned_text, module_key)
        parsed = _try_parse_structured_text(cleaned_text)
        if parsed is not None:
            return normalize_report_content(parsed, module_key)
        return cleaned_text

    if isinstance(content, dict):
        has_team_payload = any(k in content for k in _TEAM_DEBATE_KEYS) or any(
            k in content for k in _TEAM_HISTORY_KEYS
        )
        for decision_key in _TEAM_DECISION_KEYS:
            decision_text = content.get(decision_key)
            if decision_text and (has_team_payload or module_key in {
                "risk_management_decision",
                "research_team_decision",
                "risk_debate_state",
                "investment_debate_state",
            }):
                return normalize_report_content(decision_text, module_key)

        cleaned_dict = remove_thinking_from_dict(content)
        filtered_dict = {
            key: value
            for key, value in cleaned_dict.items()
            if key not in _TEAM_HISTORY_KEYS and value not in (None, "", [], {})
        }

        if not filtered_dict:
            return ""

        # 常见包装字段优先解包
        for wrapper_key in ("content", "report", "text", "summary"):
            wrapper_value = filtered_dict.get(wrapper_key)
            if isinstance(wrapper_value, str) and wrapper_value.strip():
                return remove_thinking_content(wrapper_value).strip()

        if len(filtered_dict) == 1:
            only_value = next(iter(filtered_dict.values()))
            return normalize_report_content(only_value, module_key)

        try:
            return json.dumps(filtered_dict, ensure_ascii=False, indent=2)
        except Exception:
            return str(filtered_dict)

    if isinstance(content, list):
        parts = [
            normalize_report_content(item, module_key)
            for item in content
        ]
        parts = [part for part in parts if part]
        return "\n\n".join(parts)

    return remove_thinking_content(str(content)).strip()


def sanitize_report_modules(reports: Dict[str, Any]) -> Dict[str, str]:
    """标准化 reports 字典，统一输出为字符串内容。"""
    if not isinstance(reports, dict):
        return {}

    sanitized: Dict[str, str] = {}
    for key, value in reports.items():
        safe_key = str(key).strip()
        if not safe_key:
            continue
        normalized = normalize_report_content(value, safe_key)
        if normalized:
            sanitized[safe_key] = normalized

    return sanitized


def remove_thinking_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    递归移除字典中所有字符串值的思维链内容
    
    Args:
        data: 可能包含思维链内容的字典
        
    Returns:
        清理后的字典
    """
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = remove_thinking_content(value)
        elif isinstance(value, dict):
            result[key] = remove_thinking_from_dict(value)
        elif isinstance(value, list):
            result[key] = [
                remove_thinking_content(item) if isinstance(item, str) 
                else remove_thinking_from_dict(item) if isinstance(item, dict)
                else item 
                for item in value
            ]
        else:
            result[key] = value
    
    return result


def clean_llm_response(response: Any) -> Any:
    """
    清理 LLM 响应中的思维链内容
    
    支持字符串、字典、列表等多种类型
    
    Args:
        response: LLM 原始响应
        
    Returns:
        清理后的响应
    """
    if isinstance(response, str):
        return remove_thinking_content(response)
    elif isinstance(response, dict):
        return remove_thinking_from_dict(response)
    elif isinstance(response, list):
        return [
            clean_llm_response(item) for item in response
        ]
    else:
        return response
