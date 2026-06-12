# SinaQuant/graph/propagation.py
# 状态初始化与图执行参数配置模块
# 负责创建 LangGraph 工作流的初始状态，以及配置图的流式执行参数

from typing import Dict, Any

# 导入统一日志系统
from sinoquant.utils.logging_init import get_logger
logger = get_logger("default")
from sinoquant.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)


class Propagator:
    """状态初始化与图执行参数管理器

    职责：
    1. 创建多智能体工作流的初始状态（AgentState），包括消息、辩论状态、报告占位等
    2. 配置 LangGraph 流式执行的参数（stream_mode、递归限制等）
    """

    def __init__(self, max_recur_limit=100):
        """初始化传播器

        Args:
            max_recur_limit: LangGraph 最大递归深度，防止图执行陷入死循环。
                             默认 100，足以覆盖完整分析流程（4分析师+辩论+交易+风险评估）
        """
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self, company_name: str, trade_date: str
    ) -> Dict[str, Any]:
        """创建多智能体工作流的初始状态

        构建完整的 AgentState 字典，包含：
        - messages: 初始的人类消息，明确描述分析任务
        - company_of_interest: 待分析的股票代码
        - trade_date: 分析日期
        - investment_debate_state: 投资辩论的空初始状态
        - risk_debate_state: 风险辩论的空初始状态
        - 各分析师报告字段：初始为空字符串，由各分析师节点填充

        Args:
            company_name: 股票代码（如 "600036"）
            trade_date: 交易日期（如 "2024-01-15"）

        Returns:
            完整的初始状态字典，将作为 LangGraph stream() 的输入
        """
        from langchain_core.messages import HumanMessage

        # 创建明确的分析请求消息，确保所有LLM（包括DeepSeek）都能理解任务
        # 只传股票代码可能导致部分模型无法正确解析分析意图
        analysis_request = f"请对股票 {company_name} 进行全面分析，交易日期为 {trade_date}。"

        return {
            "messages": [HumanMessage(content=analysis_request)],  # 初始人类消息，触发分析师节点执行
            "company_of_interest": company_name,  # 股票代码，贯穿所有节点
            "trade_date": str(trade_date),  # 分析日期，用于数据获取的时间范围
            "investment_debate_state": InvestDebateState(
                {"history": "", "current_response": "", "count": 0}
                # current_response 初始为空，首次辩论时设为 "Bull" 触发看涨→看跌轮转
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "history": "",
                    "current_risky_response": "",
                    "current_safe_response": "",
                    "current_neutral_response": "",
                    "count": 0,  # 计数器归零，每次发言递增
                }
            ),
            # 以下四个报告字段由对应的分析师节点在工具调用后填充
            "market_report": "",           # 市场分析师 → 技术指标、价格趋势分析
            "fundamentals_report": "",     # 基本面分析师 → 财务数据、估值分析
            "sentiment_report": "",        # 社交媒体分析师 → 投资者情绪分析
            "news_report": "",             # 新闻分析师 → 新闻事件影响分析
        }

    def get_graph_args(self, use_progress_callback: bool = False) -> Dict[str, Any]:
        """获取 LangGraph 执行参数

        根据是否需要进度回调选择不同的流式模式：
        - updates 模式：每次节点执行完毕返回增量更新 {node_name: state_update}，
                        适合逐节点推送进度（前端实时展示"市场分析师工作中…"等）
        - values 模式：每次节点执行完毕返回完整状态快照，
                       适合调试和离线分析

        Args:
            use_progress_callback: True 则使用 updates 模式（用于前端进度推送），
                                   False 则使用 values 模式

        Returns:
            传给 graph.stream() 的参数字典，包含 stream_mode 和 recursion_limit
        """
        stream_mode = "updates" if use_progress_callback else "values"

        return {
            "stream_mode": stream_mode,
            "config": {"recursion_limit": self.max_recur_limit},  # 限制递归深度，防止死循环
        }
