from sinoquant.utils.text_utils import (
    clean_llm_response,
    normalize_report_content,
    remove_thinking_content,
    sanitize_report_modules,
)


def test_remove_thinking_content_removes_think_tag_block():
    text = "<think>internal reasoning</think>\n# 分析报告\n这是最终内容。"
    cleaned = remove_thinking_content(text)
    assert "internal reasoning" not in cleaned
    assert "# 分析报告" in cleaned
    assert "这是最终内容。" in cleaned


def test_remove_thinking_content_filters_reasoning_lines_and_keeps_report():
    text = """
Thinking Process:
1. Analyze the Request:
*   **Wait, I need to check the tool response again.**
*   **Constraint:** report length >= 800

# **科大讯飞（002230）技术分析报告**
## 一、摘要
这是最终报告正文。
""".strip()

    cleaned = remove_thinking_content(text)
    assert "Thinking Process" not in cleaned
    assert "Wait, I need to check" not in cleaned
    assert "Constraint:" not in cleaned
    assert "科大讯飞（002230）技术分析报告" in cleaned
    assert "这是最终报告正文。" in cleaned


def test_remove_thinking_content_returns_empty_for_pure_reasoning_draft():
    text = """
Thinking Process:
* Wait, I need to check the constraints.
* Wait, I need to check the tool response.
* Input Data: ...
* Tool Response: ...
""".strip()

    cleaned = remove_thinking_content(text)
    assert cleaned == ""


def test_remove_thinking_content_handles_parenthesized_wait_loops():
    text = """
final output.)

(Wait, I need to ensure the title is exactly as requested.)
Okay, generating now.
(Wait, I should check if I need to include the \"Important Reminder\" section in the output.)
Okay, final check on constraints:
1. No emojis.
2. Specific Title.
3. Specific Sections.
4. Min 800 chars.
5. Use provided data.
6. Use table for data.
7. Currency ¥.
Let's do it.
Okay, generating.
""".strip()

    cleaned = remove_thinking_content(text)
    assert cleaned == ""


def test_remove_thinking_content_keeps_report_after_parenthesized_wait_loops():
    text = """
final output.)
(Wait, I need to ensure the title is exactly as requested.)
Okay, generating now.

# **科大讯飞（002230）技术分析报告**
## 一、摘要
这是最终报告正文。
""".strip()

    cleaned = remove_thinking_content(text)
    assert "Wait, I need" not in cleaned
    assert "Okay, generating now" not in cleaned
    assert "科大讯飞（002230）技术分析报告" in cleaned
    assert "这是最终报告正文。" in cleaned


def test_remove_thinking_content_drops_constraint_template_prefix():
    text = """
1.  Title must be `# **科大讯飞（002230）技术分析报告**`.
2.  Must contain four sections (四个分节).
3.  At least 800 Chinese characters (至少 800 字).
2.  **Drafting the Content:**
*   **Title:** `# **科大讯飞（002230）技术分析报告**`
*   **Section 1: Market Overview & Trend**
*   **Table:** Need a table summarizing key technical data.

# **科大讯飞（002230）技术分析报告**
## 一、摘要
这是最终报告正文。
""".strip()

    cleaned = remove_thinking_content(text)
    assert "Title must be" not in cleaned
    assert "Drafting the Content" not in cleaned
    assert "Section 1:" not in cleaned
    assert "科大讯飞（002230）技术分析报告" in cleaned
    assert "这是最终报告正文。" in cleaned


def test_clean_llm_response_recursively_cleans_nested_structures():
    payload = {
        "summary": "<think>hidden</think>最终摘要",
        "reports": [
            "Thinking Process: should be removed",
            {"detail": "<thinking>xxx</thinking>可见内容"},
        ],
    }

    cleaned = clean_llm_response(payload)
    assert cleaned["summary"] == "最终摘要"
    assert cleaned["reports"][0] == ""
    assert cleaned["reports"][1]["detail"] == "可见内容"


def test_remove_thinking_content_removes_meta_prompt_residue():
    text = """
3.  **Drafting Strategy:**
    *   **Header:** Date, Source.
4.  **Content Generation:**
    *   Write the report in Chinese.
    *   Ensure all price fields use `未知（?）`.
5.  **Review against Constraints:**
    *   Check for Markdown formatting.
    (Self-Correction on Length): I need to make sure the text is long enough.
    Let's write.
""".strip()

    cleaned = remove_thinking_content(text)
    assert cleaned == ""


def test_remove_thinking_content_drops_meta_prefix_before_markdown_heading():
    text = """
5. There is a section "你是一位看跌分析师...".
4. Final instruction: "Please write all analysis content and advice in Chinese."
However, the prompt starts with "作为投资组合经理...".

# **股票 None（None）技术分析报告**
**分析日期：2026-03-12**
这是正文。
""".strip()

    cleaned = remove_thinking_content(text)
    assert cleaned.startswith("# **股票 None（None）技术分析报告**")
    assert "There is a section" not in cleaned
    assert "Final instruction" not in cleaned


def test_remove_thinking_content_drops_meta_prefix_before_h2_heading():
    text = """
5. There is a section "你是一位看跌分析师...".
4. Final instruction: "Please write all analysis content and advice in Chinese."

## 一、摘要
这是正文。
""".strip()

    cleaned = remove_thinking_content(text)
    assert cleaned.startswith("## 一、摘要")
    assert "There is a section" not in cleaned
    assert "Final instruction" not in cleaned


def test_remove_thinking_content_keeps_normal_business_text_with_constraint_words():
    text = """
# 风险评估报告

公司在监管约束下利润率短期承压，但现金流稳定。
- 交易计划：分三次建仓，控制仓位上限。
""".strip()

    cleaned = remove_thinking_content(text)
    assert "监管约束" in cleaned
    assert "交易计划" in cleaned


def test_normalize_report_content_extracts_judge_decision_from_serialized_team_payload():
    raw = """{
        'judge_decision': '最终建议：卖出',
        'history': 'Risky Analyst: 很长的辩论历史',
        'current_safe_response': '临时内容',
        'count': 9
    }"""

    cleaned = normalize_report_content(raw, "risk_management_decision")
    assert cleaned == "最终建议：卖出"
    assert "辩论历史" not in cleaned


def test_sanitize_report_modules_normalizes_mixed_report_payloads():
    reports = {
        "risk_management_decision": {
            "judge_decision": "<think>hidden</think>最终结论",
            "history": "debug only",
        },
        "market_report": "<think>hidden</think>市场正文",
        "empty": "",
    }

    cleaned = sanitize_report_modules(reports)
    assert cleaned["risk_management_decision"] == "最终结论"
    assert cleaned["market_report"] == "市场正文"
    assert "empty" not in cleaned


def test_normalize_report_content_keeps_latest_role_round_for_analyst_modules():
    raw = (
        "Safe Analyst: 第一轮观点。\n\n"
        "一些中间讨论。\n\n"
        "Safe Analyst: 第二轮观点（应保留）。"
    )
    cleaned = normalize_report_content(raw, "safe_analyst")
    assert cleaned.startswith("Safe Analyst:")
    assert "第二轮观点" in cleaned
    assert "第一轮观点" not in cleaned
