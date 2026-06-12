"""
LangGraph 多智能体工作流的状态定义

职责：定义工作流中所有节点共享的状态结构（TypedDict），以及两个辩论子状态。

状态层级：
  AgentState（全局状态）
    ├── InvestDebateState（投资辩论子状态：看涨 ⇄ 看跌 → 研究经理裁决）
    └── RiskDebateState（风险辩论子状态：激进 ⇄ 保守 ⇄ 中性 → 风险经理裁决）

状态流转方式：
  LangGraph 的每个节点读写 AgentState 中的字段进行通信：
    - 分析师节点：写入 market_report / sentiment_report / news_report / fundamentals_report
    - 研究员节点：读写 investment_debate_state（辩论历史累加）
    - 交易员节点：写入 trader_investment_plan 和 structured_decision
    - 风险评估节点：读写 risk_debate_state（辩论历史累加），写入 final_trade_decision

状态合并策略：
  InvestDebateState 和 RiskDebateState 使用 reducer 函数 `lambda x, y: y`，
  表示新值直接覆盖旧值（不累加），因为每次辩论发言是完整的替换而非增量。

工具调用计数器（tool_call_count）：
  每个分析师有独立的计数器，防止 LLM 陷入无限工具调用循环。
  当计数器 >= 3 时，条件路由强制将流程导向消息清理节点。
"""

from typing import Annotated, Sequence
from datetime import date, timedelta, datetime
from typing_extensions import TypedDict, Optional
from langchain_openai import ChatOpenAI
from sinoquant.agents import *
from langgraph.prebuilt import ToolNode
from langgraph.graph import END, StateGraph, START, MessagesState

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")


# 投资研究团队辩论状态 —— 看涨/看跌研究员交替辩论，研究经理做最终裁决
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "看涨研究员的辩论历史"
    ]
    bear_history: Annotated[
        str, "看跌研究员的辩论历史"
    ]
    history: Annotated[str, "完整的辩论历史记录"]  # 双方发言合并的完整历史
    current_response: Annotated[str, "当前发言人及内容标识"]  # 用于判断下一次轮到谁发言
    judge_decision: Annotated[str, "研究经理的最终裁决"]
    count: Annotated[int, "当前辩论发言总次数"]  # 达到 2×max_debate_rounds 时结束辩论


# 风险管理团队辩论状态 —— 激进/保守/中性分析师轮番讨论，风险经理做最终裁决
class RiskDebateState(TypedDict):
    risky_history: Annotated[
        str, "激进分析师的辩论历史"
    ]
    safe_history: Annotated[
        str, "保守分析师的辩论历史"
    ]
    neutral_history: Annotated[
        str, "中性分析师的辩论历史"
    ]
    history: Annotated[str, "完整的风险辩论历史记录"]
    latest_speaker: Annotated[str, "最后发言的分析师标识"]  # 用于轮转调度（Risky→Safe→Neutral→Risky…）
    current_risky_response: Annotated[
        str, "激进分析师的最新发言"
    ]
    current_safe_response: Annotated[
        str, "保守分析师的最新发言"
    ]
    current_neutral_response: Annotated[
        str, "中性分析师的最新发言"
    ]
    judge_decision: Annotated[str, "风险经理的最终裁决"]
    count: Annotated[int, "当前风险讨论发言总次数"]  # 达到 3×max_risk_discuss_rounds 时结束讨论


# LangGraph 全局状态 —— 贯穿整个多智能体工作流
class AgentState(MessagesState):
    company_of_interest: Annotated[str, "待分析的股票代码"]
    trade_date: Annotated[str, "分析交易日期"]

    sender: Annotated[str, "当前消息发送方标识"]

    # 交易员节点的结构化决策（避免额外调用 SignalProcessor）
    structured_decision: Annotated[dict, "交易员输出的结构化交易决策"]

    # 第一阶段：各分析师的报告
    market_report: Annotated[str, "市场分析师报告"]
    sentiment_report: Annotated[str, "社交媒体情绪分析师报告"]
    news_report: Annotated[
        str, "新闻分析师报告"
    ]
    fundamentals_report: Annotated[str, "基本面分析师报告"]

    # 死循环修复：各分析师的工具调用计数器，防止无限循环
    market_tool_call_count: Annotated[int, "市场分析师工具调用次数"]
    news_tool_call_count: Annotated[int, "新闻分析师工具调用次数"]
    sentiment_tool_call_count: Annotated[int, "社交媒体分析师工具调用次数"]
    fundamentals_tool_call_count: Annotated[int, "基本面分析师工具调用次数"]

    # 第二阶段：投资研究团队辩论
    investment_debate_state: Annotated[
        InvestDebateState, "投资辩论状态（看涨 vs 看跌）", lambda x, y: y
    ]
    investment_plan: Annotated[str, "研究经理生成的投资计划"]

    # 第三阶段：交易员决策
    trader_investment_plan: Annotated[str, "交易员的投资方案"]

    # 第四阶段：风险管理团队辩论
    risk_debate_state: Annotated[
        RiskDebateState, "风险辩论状态（激进 vs 保守 vs 中性）", lambda x, y: y
    ]
    final_trade_decision: Annotated[str, "风险经理的最终交易决策"]
