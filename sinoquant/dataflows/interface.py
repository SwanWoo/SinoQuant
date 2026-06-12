from typing import Annotated
import time
import os
from datetime import datetime

from .news.google_news import *


# 导入统一日志系统
from sinoquant.utils.logging_init import setup_dataflow_logging

# 导入日志模块
from sinoquant.utils.logging_manager import get_logger
logger = get_logger('agents')
logger = setup_dataflow_logging()

from dateutil.relativedelta import relativedelta
import pandas as pd
from openai import OpenAI
from sinoquant.config.config_manager import config_manager

# 获取数据目录
DATA_DIR = config_manager.get_data_dir()

def get_config():
    """获取配置（兼容性包装）"""
    return config_manager.load_settings()

def set_config(config):
    """设置配置（兼容性包装）"""
    config_manager.save_settings(config)


def get_google_news(
    query: Annotated[str, "Query to search with"],
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"] = 7,
) -> str:
    # 本地数据模式：先从MongoDB查新闻，禁止调Google API
    try:
        from sinoquant.config.runtime_settings import local_data_only_enabled
        if local_data_only_enabled():
            import re
            code_match = re.search(r'\b(\d{6})\b', query)
            if code_match:
                stock_code = code_match.group(1)
                from sinoquant.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                adapter = get_mongodb_cache_adapter()
                news_data = adapter.get_news_data(stock_code, hours_back=look_back_days*24, limit=20)
                if news_data and len(news_data) > 0:
                    news_str = ""
                    for news in news_data:
                        title = news.get('title', '无标题')
                        source_name = news.get('source', '未知来源')
                        snippet = (news.get('content', '') or news.get('summary', ''))[:200]
                        news_str += f"### {title} (source: {source_name}) \n\n{snippet}\n\n"
                    if news_str:
                        logger.info(f"[本地新闻] 从MongoDB获取{stock_code}新闻: {len(news_data)}条")
                        return f"## {query} 本地新闻数据:\n\n{news_str}"
            logger.warning(f"[本地数据模式] MongoDB中没有{query}的新闻数据，禁止调用Google News")
            return ""
    except Exception as e:
        logger.warning(f"[本地数据模式] MongoDB新闻查询失败: {e}")

    # 判断是否为A股查询
    is_china_stock = False
    if any(code in query for code in ['SH', 'SZ', 'XSHE', 'XSHG']) or query.isdigit() or (len(query) == 6 and query[:6].isdigit()):
        is_china_stock = True

    # 尝试使用StockUtils判断
    try:
        from sinoquant.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(query.split()[0])
        if market_info['is_china']:
            is_china_stock = True
    except Exception:
        # 如果StockUtils判断失败，使用上面的简单判断
        pass

    # 对A股查询添加中文关键词
    if is_china_stock:
        logger.info(f"[Google新闻] 检测到A股查询: {query}，使用中文搜索")
        if '股票' not in query and '股价' not in query and '公司' not in query:
            query = f"{query} 股票 公司 财报 新闻"

    query = query.replace(" ", "+")

    start_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    logger.info(f"[Google新闻] 开始获取新闻，查询: {query}, 时间范围: {before} 至 {curr_date}")
    news_results = getNewsData(query, before, curr_date)

    news_str = ""

    for news in news_results:
        news_str += (
            f"### {news['title']} (source: {news['source']}) \n\n{news['snippet']}\n\n"
        )

    if len(news_results) == 0:
        logger.warning(f"[Google新闻] 未找到相关新闻，查询: {query}")
        return ""

    logger.info(f"[Google新闻] 成功获取 {len(news_results)} 条新闻，查询: {query}")
    return f"## {query.replace('+', ' ')} Google News, from {before} to {curr_date}:\n\n{news_str}"


def get_stock_news_openai(ticker, curr_date):
    try:
        from sinoquant.config.runtime_settings import local_data_only_enabled
        if local_data_only_enabled():
            logger.warning(f"[本地数据模式] 禁止调用OpenAI搜索新闻: {ticker}")
            return f"[本地数据模式] MongoDB中没有{ticker}的新闻数据。请先同步新闻数据。"
    except Exception:
        pass

    config = get_config()
    client = OpenAI(base_url=config["backend_url"])

    response = client.responses.create(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Can you search Social Media for {ticker} from 7 days before {curr_date} to {curr_date}? Make sure you only get the data posted during that period.",
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    return response.output[1].content[0].text


def get_global_news_openai(curr_date):
    try:
        from sinoquant.config.runtime_settings import local_data_only_enabled
        if local_data_only_enabled():
            logger.warning("[本地数据模式] 禁止调用OpenAI搜索全球新闻")
            return "[本地数据模式] 本地模式不支持全球新闻搜索。"
    except Exception:
        pass

    config = get_config()
    client = OpenAI(base_url=config["backend_url"])

    response = client.responses.create(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Can you search global or macroeconomics news from 7 days before {curr_date} to {curr_date} that would be informative for trading purposes? Make sure you only get the data posted during that period.",
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    return response.output[1].content[0].text


# ==================== Tushare数据接口 ====================

def get_china_stock_data_tushare(
    ticker: Annotated[str, "中国股票代码，如：000001、600036等"],
    start_date: Annotated[str, "开始日期，格式：YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式：YYYY-MM-DD"]
) -> str:
    """
    使用Tushare获取中国A股历史数据
    重定向到data_source_manager，避免循环调用

    Args:
        ticker: 股票代码
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        str: 格式化的股票数据报告
    """
    try:
        from .data_source_manager import get_data_source_manager

        logger.debug(f"📊 [Tushare] 获取{ticker}股票数据...")

        # 添加详细的股票代码追踪日志
        logger.info(f"🔍 [股票代码追踪] get_china_stock_data_tushare 接收到的股票代码: '{ticker}' (类型: {type(ticker)})")
        logger.info(f"🔍 [股票代码追踪] 重定向到data_source_manager")

        manager = get_data_source_manager()
        return manager.get_china_stock_data_tushare(ticker, start_date, end_date)

    except Exception as e:
        logger.error(f"❌ [Tushare] 获取股票数据失败: {e}")
        return f"❌ 获取{ticker}股票数据失败: {e}"


def get_china_stock_info_tushare(
    ticker: Annotated[str, "中国股票代码，如：000001、600036等"]
) -> str:
    """
    使用Tushare获取中国A股基本信息
    直接调用 Tushare 适配器，避免循环调用

    Args:
        ticker: 股票代码

    Returns:
        str: 格式化的股票基本信息
    """
    try:
        from .data_source_manager import get_data_source_manager

        logger.debug(f"📊 [Tushare] 获取{ticker}股票信息...")
        logger.info(f"🔍 [股票代码追踪] get_china_stock_info_tushare 接收到的股票代码: '{ticker}' (类型: {type(ticker)})")
        logger.info(f"🔍 [股票代码追踪] 直接调用 Tushare 适配器")

        manager = get_data_source_manager()

        # 直接调用 _get_tushare_stock_info()，避免循环调用
        # 不要调用 get_stock_info()，因为它会再次调用 get_china_stock_info_tushare()
        info = manager._get_tushare_stock_info(ticker)

        # 格式化返回字符串
        if info and isinstance(info, dict):
            return f"""股票代码: {info.get('symbol', ticker)}
股票名称: {info.get('name', '未知')}
所属行业: {info.get('industry', '未知')}
上市日期: {info.get('list_date', '未知')}
交易所: {info.get('exchange', '未知')}"""
        else:
            return f"❌ 未找到{ticker}的股票信息"

    except Exception as e:
        logger.error(f"❌ [Tushare] 获取股票信息失败: {e}")
        return f"❌ 获取{ticker}股票信息失败: {e}"


def get_china_stock_fundamentals_tushare(
    ticker: Annotated[str, "中国股票代码，如：000001、600036等"]
) -> str:
    """
    获取中国A股基本面数据（统一接口）
    支持多数据源：MongoDB → Tushare → AKShare → 生成分析

    Args:
        ticker: 股票代码

    Returns:
        str: 基本面分析报告
    """
    try:
        from .data_source_manager import get_data_source_manager

        logger.debug(f"📊 获取{ticker}基本面数据...")
        logger.info(f"🔍 [股票代码追踪] 重定向到data_source_manager.get_fundamentals_data")

        manager = get_data_source_manager()
        # 使用新的统一接口，支持多数据源和自动降级
        return manager.get_fundamentals_data(ticker)

    except Exception as e:
        logger.error(f"❌ 获取基本面数据失败: {e}")
        return f"❌ 获取{ticker}基本面数据失败: {e}"


# ==================== 统一数据源接口 ====================

def get_china_stock_data_unified(
    ticker: Annotated[str, "中国股票代码，如：000001、600036等"],
    start_date: Annotated[str, "开始日期，格式：YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式：YYYY-MM-DD"]
) -> str:
    """
    统一的中国A股数据获取接口
    自动使用配置的数据源（默认Tushare），支持备用数据源

    Args:
        ticker: 股票代码
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        str: 格式化的股票数据报告
    """
    # 智能日期范围处理：自动扩展到配置的回溯天数，处理周末/节假日
    from sinoquant.utils.dataflow_utils import get_trading_date_range
    from app.core.config import get_settings

    original_start_date = start_date
    original_end_date = end_date

    # 从配置获取市场分析回溯天数（默认30天）
    try:
        settings = get_settings()
        lookback_days = settings.MARKET_ANALYST_LOOKBACK_DAYS
        logger.info(f"📅 [配置验证] ===== MARKET_ANALYST_LOOKBACK_DAYS 配置检查 =====")
        logger.info(f"📅 [配置验证] 从配置文件读取: {lookback_days}天")
        logger.info(f"📅 [配置验证] 配置来源: app.core.config.Settings")
        logger.info(f"📅 [配置验证] 环境变量: MARKET_ANALYST_LOOKBACK_DAYS={lookback_days}")
    except Exception as e:
        lookback_days = 30  # 默认30天
        logger.warning(f"⚠️ [配置验证] 无法获取配置，使用默认值: {lookback_days}天")
        logger.warning(f"⚠️ [配置验证] 错误详情: {e}")

    # 使用 end_date 作为目标日期，向前回溯指定天数
    start_date, end_date = get_trading_date_range(end_date, lookback_days=lookback_days)

    logger.info(f"📅 [智能日期] ===== 日期范围计算结果 =====")
    logger.info(f"📅 [智能日期] 原始输入: {original_start_date} 至 {original_end_date}")
    logger.info(f"📅 [智能日期] 回溯天数: {lookback_days}天")
    logger.info(f"📅 [智能日期] 计算结果: {start_date} 至 {end_date}")
    logger.info(f"📅 [智能日期] 实际天数: {(datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days}天")
    logger.info(f"💡 [智能日期] 说明: 自动扩展日期范围以处理周末、节假日和数据延迟")

    # 记录详细的输入参数
    logger.info(f"📊 [统一接口] 开始获取中国股票数据",
               extra={
                   'function': 'get_china_stock_data_unified',
                   'ticker': ticker,
                   'start_date': start_date,
                   'end_date': end_date,
                   'event_type': 'unified_data_call_start'
               })

    # 添加详细的股票代码追踪日志
    logger.info(f"🔍 [股票代码追踪] get_china_stock_data_unified 接收到的原始股票代码: '{ticker}' (类型: {type(ticker)})")
    logger.info(f"🔍 [股票代码追踪] 股票代码长度: {len(str(ticker))}")
    logger.info(f"🔍 [股票代码追踪] 股票代码字符: {list(str(ticker))}")

    start_time = time.time()

    try:
        from .data_source_manager import get_china_stock_data_unified

        result = get_china_stock_data_unified(ticker, start_date, end_date)

        # 记录详细的输出结果
        duration = time.time() - start_time
        result_length = len(result) if result else 0
        is_success = result and "❌" not in result and "错误" not in result

        if is_success:
            logger.info(f"✅ [统一接口] 中国股票数据获取成功",
                       extra={
                           'function': 'get_china_stock_data_unified',
                           'ticker': ticker,
                           'start_date': start_date,
                           'end_date': end_date,
                           'duration': duration,
                           'result_length': result_length,
                           'result_preview': result[:300] + '...' if result_length > 300 else result,
                           'event_type': 'unified_data_call_success'
                       })
        else:
            logger.warning(f"⚠️ [统一接口] 中国股票数据质量异常",
                          extra={
                              'function': 'get_china_stock_data_unified',
                              'ticker': ticker,
                              'start_date': start_date,
                              'end_date': end_date,
                              'duration': duration,
                              'result_length': result_length,
                              'result_preview': result[:300] + '...' if result_length > 300 else result,
                              'event_type': 'unified_data_call_warning'
                          })

        return result

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"❌ [统一接口] 获取股票数据失败: {e}",
                    extra={
                        'function': 'get_china_stock_data_unified',
                        'ticker': ticker,
                        'start_date': start_date,
                        'end_date': end_date,
                        'duration': duration,
                        'error': str(e),
                        'event_type': 'unified_data_call_error'
                    }, exc_info=True)
        return f"❌ 获取{ticker}股票数据失败: {e}"


def get_china_stock_info_unified(
    ticker: Annotated[str, "中国股票代码，如：000001、600036等"]
) -> str:
    """
    统一的中国A股基本信息获取接口
    自动使用配置的数据源（默认Tushare）

    Args:
        ticker: 股票代码

    Returns:
        str: 股票基本信息
    """
    try:
        from .data_source_manager import get_china_stock_info_unified

        logger.info(f"📊 [统一接口] 获取{ticker}基本信息...")

        info = get_china_stock_info_unified(ticker)

        if info and info.get('name'):
            result = f"股票代码: {ticker}\n"
            result += f"股票名称: {info.get('name', '未知')}\n"
            result += f"所属地区: {info.get('area', '未知')}\n"
            result += f"所属行业: {info.get('industry', '未知')}\n"
            result += f"上市市场: {info.get('market', '未知')}\n"
            result += f"上市日期: {info.get('list_date', '未知')}\n"
            # 附加快照行情（若存在）
            cp = info.get('current_price')
            pct = info.get('change_pct')
            vol = info.get('volume')
            if cp is not None:
                result += f"当前价格: {cp}\n"
            if pct is not None:
                try:
                    pct_str = f"{float(pct):+.2f}%"
                except Exception:
                    pct_str = str(pct)
                result += f"涨跌幅: {pct_str}\n"
            if vol is not None:
                result += f"成交量: {vol}\n"
            result += f"数据来源: {info.get('source', 'unknown')}\n"

            return result
        else:
            return f"❌ 未能获取{ticker}的基本信息"

    except Exception as e:
        logger.error(f"❌ [统一接口] 获取股票信息失败: {e}")
        return f"❌ 获取{ticker}股票信息失败: {e}"


def switch_china_data_source(
    source: Annotated[str, "数据源名称：tushare, akshare, baostock"]
) -> str:
    """
    切换中国股票数据源

    Args:
        source: 数据源名称

    Returns:
        str: 切换结果
    """
    try:
        from .data_source_manager import get_data_source_manager, ChinaDataSource

        # 映射字符串到枚举（TDX 已移除）
        source_mapping = {
            'tushare': ChinaDataSource.TUSHARE,
            'akshare': ChinaDataSource.AKSHARE,
            'baostock': ChinaDataSource.BAOSTOCK,
        }

        if source.lower() not in source_mapping:
            return f"❌ 不支持的数据源: {source}。支持的数据源: {list(source_mapping.keys())}"

        manager = get_data_source_manager()
        target_source = source_mapping[source.lower()]

        if manager.set_current_source(target_source):
            return f"✅ 数据源已切换到: {source}"
        else:
            return f"❌ 数据源切换失败: {source} 不可用"

    except Exception as e:
        logger.error(f"❌ 数据源切换失败: {e}")
        return f"❌ 数据源切换失败: {e}"


def get_current_china_data_source() -> str:
    """
    获取当前中国股票数据源

    Returns:
        str: 当前数据源信息
    """
    try:
        from .data_source_manager import get_data_source_manager

        manager = get_data_source_manager()
        current = manager.get_current_source()
        available = manager.available_sources

        result = f"当前数据源: {current.value}\n"
        result += f"可用数据源: {[s.value for s in available]}\n"
        result += f"默认数据源: {manager.default_source.value}\n"

        return result

    except Exception as e:
        logger.error(f"❌ 获取数据源信息失败: {e}")
        return f"❌ 获取数据源信息失败: {e}"
