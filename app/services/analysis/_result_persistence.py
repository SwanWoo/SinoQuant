"""
结果持久化 mixin — MongoDB 保存、本地文件保存、任务状态更新
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from app.core.database import get_mongo_db
from app.models.analysis import AnalysisStatus
from sinoquant.utils.text_utils import (
    clean_llm_response,
    normalize_report_content,
    remove_thinking_content,
    sanitize_report_modules,
)

from ._analysis_config import _apply_report_visibility_policy

logger = logging.getLogger("app.services.simple_analysis_service")


class ResultPersistenceMixin:
    """提供分析结果保存和任务状态更新功能的 mixin。"""

    # ------------------------------------------------------------------
    # 任务状态更新
    # ------------------------------------------------------------------

    async def _update_task_status(
        self,
        task_id: str,
        status: AnalysisStatus,
        progress: int,
        error_message: str = None
    ):
        """更新任务状态"""
        try:
            db = get_mongo_db()
            update_data = {
                "status": status,
                "progress": progress,
                "updated_at": datetime.utcnow()
            }

            if status == AnalysisStatus.PROCESSING and progress == 10:
                update_data["started_at"] = datetime.utcnow()
            elif status == AnalysisStatus.COMPLETED:
                update_data["completed_at"] = datetime.utcnow()
            elif status == AnalysisStatus.FAILED:
                update_data["last_error"] = error_message
                update_data["completed_at"] = datetime.utcnow()

            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data}
            )

            logger.debug(f"📊 任务状态已更新: {task_id} -> {status} ({progress}%)")

        except Exception as e:
            logger.error(f"❌ 更新任务状态失败: {task_id} - {e}")

    # ------------------------------------------------------------------
    # 分析结果保存（原始方法）
    # ------------------------------------------------------------------

    async def _save_analysis_result(self, task_id: str, result: Dict[str, Any]):
        """保存分析结果（原始方法）"""
        try:
            db = get_mongo_db()
            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"result": result}}
            )
            logger.debug(f"💾 分析结果已保存: {task_id}")
        except Exception as e:
            logger.error(f"❌ 保存分析结果失败: {task_id} - {e}")

    # ------------------------------------------------------------------
    # 分析结果保存（web 风格）
    # ------------------------------------------------------------------

    async def _save_analysis_result_web_style(self, task_id: str, result: Dict[str, Any]):
        """保存分析结果 - 采用web目录的方式，保存到analysis_reports集合"""
        try:
            db = get_mongo_db()

            # 生成分析ID（与web目录保持一致）
            from datetime import datetime
            timestamp = datetime.utcnow()  # 存储 UTC 时间（标准做法）
            stock_symbol = result.get('stock_symbol') or result.get('stock_code') or 'UNKNOWN'
            analysis_id = f"{stock_symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}"

            # 处理reports字段 - 从state中提取所有分析报告
            reports = {}
            if 'state' in result:
                try:
                    state = result['state']

                    # 定义所有可能的报告字段
                    report_fields = [
                        'market_report',
                        'sentiment_report',
                        'news_report',
                        'fundamentals_report',
                        'investment_plan',
                        'trader_investment_plan',
                        'final_trade_decision'
                    ]

                    # 从state中提取报告内容
                    for field in report_fields:
                        if hasattr(state, field):
                            value = getattr(state, field, "")
                        elif isinstance(state, dict) and field in state:
                            value = state[field]
                        else:
                            value = ""

                        normalized_value = normalize_report_content(value, field)
                        if normalized_value:
                            reports[field] = normalized_value

                    # 处理研究团队辩论状态报告
                    if hasattr(state, 'investment_debate_state') or (isinstance(state, dict) and 'investment_debate_state' in state):
                        debate_state = getattr(state, 'investment_debate_state', None) if hasattr(state, 'investment_debate_state') else state.get('investment_debate_state')
                        if debate_state:
                            # 提取多头研究员历史
                            if hasattr(debate_state, 'bull_history'):
                                bull_content = getattr(debate_state, 'bull_history', "")
                            elif isinstance(debate_state, dict) and 'bull_history' in debate_state:
                                bull_content = debate_state['bull_history']
                            else:
                                bull_content = ""

                            normalized_bull = normalize_report_content(bull_content, 'bull_researcher')
                            if normalized_bull:
                                reports['bull_researcher'] = normalized_bull

                            # 提取空头研究员历史
                            if hasattr(debate_state, 'bear_history'):
                                bear_content = getattr(debate_state, 'bear_history', "")
                            elif isinstance(debate_state, dict) and 'bear_history' in debate_state:
                                bear_content = debate_state['bear_history']
                            else:
                                bear_content = ""

                            normalized_bear = normalize_report_content(bear_content, 'bear_researcher')
                            if normalized_bear:
                                reports['bear_researcher'] = normalized_bear

                            # 提取研究经理决策
                            if hasattr(debate_state, 'judge_decision'):
                                decision_content = getattr(debate_state, 'judge_decision', "")
                            elif isinstance(debate_state, dict) and 'judge_decision' in debate_state:
                                decision_content = debate_state['judge_decision']
                            else:
                                decision_content = str(debate_state)

                            normalized_decision = normalize_report_content(decision_content, 'research_team_decision')
                            if normalized_decision:
                                reports['research_team_decision'] = normalized_decision

                    # 处理风险管理团队辩论状态报告
                    if hasattr(state, 'risk_debate_state') or (isinstance(state, dict) and 'risk_debate_state' in state):
                        risk_state = getattr(state, 'risk_debate_state', None) if hasattr(state, 'risk_debate_state') else state.get('risk_debate_state')
                        if risk_state:
                            # 提取激进分析师历史
                            if hasattr(risk_state, 'risky_history'):
                                risky_content = getattr(risk_state, 'risky_history', "")
                            elif isinstance(risk_state, dict) and 'risky_history' in risk_state:
                                risky_content = risk_state['risky_history']
                            else:
                                risky_content = ""

                            normalized_risky = normalize_report_content(risky_content, 'risky_analyst')
                            if normalized_risky:
                                reports['risky_analyst'] = normalized_risky

                            # 提取保守分析师历史
                            if hasattr(risk_state, 'safe_history'):
                                safe_content = getattr(risk_state, 'safe_history', "")
                            elif isinstance(risk_state, dict) and 'safe_history' in risk_state:
                                safe_content = risk_state['safe_history']
                            else:
                                safe_content = ""

                            normalized_safe = normalize_report_content(safe_content, 'safe_analyst')
                            if normalized_safe:
                                reports['safe_analyst'] = normalized_safe

                            # 提取中性分析师历史
                            if hasattr(risk_state, 'neutral_history'):
                                neutral_content = getattr(risk_state, 'neutral_history', "")
                            elif isinstance(risk_state, dict) and 'neutral_history' in risk_state:
                                neutral_content = risk_state['neutral_history']
                            else:
                                neutral_content = ""

                            normalized_neutral = normalize_report_content(neutral_content, 'neutral_analyst')
                            if normalized_neutral:
                                reports['neutral_analyst'] = normalized_neutral

                            # 提取投资组合经理决策
                            if hasattr(risk_state, 'judge_decision'):
                                risk_decision = getattr(risk_state, 'judge_decision', "")
                            elif isinstance(risk_state, dict) and 'judge_decision' in risk_state:
                                risk_decision = risk_state['judge_decision']
                            else:
                                risk_decision = str(risk_state)

                            normalized_risk_decision = normalize_report_content(
                                risk_decision,
                                'risk_management_decision',
                            )
                            if normalized_risk_decision:
                                reports['risk_management_decision'] = normalized_risk_decision

                    logger.info(f"📊 从state中提取到 {len(reports)} 个报告: {list(reports.keys())}")

                    # 分级精简输出：按研究深度保留对应报告模块
                    original_count = len(reports)
                    reports = _apply_report_visibility_policy(
                        reports=reports,
                        research_depth=result.get("research_depth", "标准"),
                        selected_analysts=result.get("analysts", []),
                    )
                    if len(reports) != original_count:
                        logger.info(
                            f"📉 [MongoDB保存] 分级精简策略生效: {original_count} -> {len(reports)} "
                            f"(depth={result.get('research_depth')}, analysts={result.get('analysts', [])})"
                        )
                    reports = sanitize_report_modules(reports)

                except Exception as e:
                    logger.warning(f"⚠️ 处理state中的reports时出错: {e}")
                    # 降级到从detailed_analysis提取
                    if 'detailed_analysis' in result:
                        try:
                            detailed_analysis = result['detailed_analysis']
                            if isinstance(detailed_analysis, dict):
                                for key, value in detailed_analysis.items():
                                    if isinstance(value, str) and len(value) > 50:
                                        reports[key] = normalize_report_content(value, str(key))
                                logger.info(f"📊 降级：从detailed_analysis中提取到 {len(reports)} 个报告")
                            reports = sanitize_report_modules(reports)
                        except Exception as fallback_error:
                            logger.warning(f"⚠️ 降级提取也失败: {fallback_error}")

            # 🔥 根据股票代码推断市场类型
            from sinoquant.utils.stock_utils import StockUtils
            market_info = StockUtils.get_market_info(stock_symbol)
            market_type_map = {
                "china_a": "A股",
                "unknown": "A股"  # 默认为A股
            }
            market_type = market_type_map.get(market_info.get("market", "unknown"), "A股")
            logger.info(f"📊 推断市场类型: {stock_symbol} -> {market_type}")

            # 🔥 获取股票名称
            stock_name = stock_symbol  # 默认使用股票代码
            try:
                if market_info.get("market") == "china_a":
                    # A股：使用统一接口获取股票信息
                    from sinoquant.dataflows.interface import get_china_stock_info_unified
                    stock_info = get_china_stock_info_unified(stock_symbol)
                    logger.debug(f"📊 获取股票信息返回: {stock_info[:200] if stock_info else 'None'}...")

                    if stock_info and "股票名称:" in stock_info:
                        stock_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                        logger.info(f"✅ 获取A股名称: {stock_symbol} -> {stock_name}")
                    else:
                        # 降级方案：尝试直接从数据源管理器获取
                        logger.warning(f"⚠️ 无法从统一接口解析股票名称: {stock_symbol}，尝试降级方案")
                        try:
                            from sinoquant.dataflows.data_source_manager import get_china_stock_info_unified as get_info_dict
                            info_dict = get_info_dict(stock_symbol)
                            if info_dict and info_dict.get('name'):
                                stock_name = info_dict['name']
                                logger.info(f"✅ 降级方案成功获取股票名称: {stock_symbol} -> {stock_name}")
                        except Exception as fallback_e:
                            logger.error(f"❌ 降级方案也失败: {fallback_e}")

                else:
                    stock_name = stock_symbol
            except Exception as e:
                logger.warning(f"⚠️ 获取股票名称失败: {stock_symbol} - {e}")
                stock_name = stock_symbol

            # 构建文档（与web目录的MongoDBReportManager保持一致）
            document = {
                "analysis_id": analysis_id,
                "stock_symbol": stock_symbol,
                "stock_name": stock_name,  # 🔥 添加股票名称字段
                "market_type": market_type,  # 🔥 添加市场类型字段
                "model_info": result.get("model_info", "Unknown"),  # 🔥 添加模型信息字段
                "analysis_date": timestamp.strftime('%Y-%m-%d'),
                "timestamp": timestamp,
                "status": "completed",
                "source": "api",

                # 分析结果摘要
                "summary": remove_thinking_content(result.get("summary", "")),
                "analysts": result.get("analysts", []),
                "research_depth": result.get("research_depth", 1),

                # 报告内容
                "reports": reports,

                # 🔥 关键修复：添加格式化后的decision字段！
                "decision": clean_llm_response(result.get("decision", {})),

                # 元数据
                "created_at": timestamp,
                "updated_at": timestamp,

                # API特有字段
                "task_id": task_id,
                "recommendation": remove_thinking_content(result.get("recommendation", "")),
                "confidence_score": result.get("confidence_score", 0.0),
                "risk_level": result.get("risk_level", "中等"),
                "key_points": result.get("key_points", []),
                "execution_time": result.get("execution_time", 0),
                "tokens_used": result.get("tokens_used", 0),

                # 🆕 性能指标数据
                "performance_metrics": result.get("performance_metrics", {})
            }

            # 保存到analysis_reports集合（与web目录保持一致）
            result_insert = await db.analysis_reports.insert_one(document)

            if result_insert.inserted_id:
                logger.info(f"✅ 分析报告已保存到MongoDB analysis_reports: {analysis_id}")

                # 同时更新analysis_tasks集合中的result字段，保持API兼容性
                await db.analysis_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"result": {
                        "analysis_id": analysis_id,
                        "stock_symbol": stock_symbol,
                        "stock_code": result.get('stock_code', stock_symbol),
                        "analysis_date": result.get('analysis_date'),
                        "summary": remove_thinking_content(result.get("summary", "")),
                        "recommendation": remove_thinking_content(result.get("recommendation", "")),
                        "confidence_score": result.get("confidence_score", 0.0),
                        "risk_level": result.get("risk_level", "中等"),
                        "key_points": result.get("key_points", []),
                        "detailed_analysis": result.get("detailed_analysis", {}),
                        "execution_time": result.get("execution_time", 0),
                        "tokens_used": result.get("tokens_used", 0),
                        "reports": reports,  # 包含提取的报告内容
                        # 🔥 关键修复：添加格式化后的decision字段！
                        "decision": clean_llm_response(result.get("decision", {}))
                    }}}
                )
                logger.info(f"💾 分析结果已保存 (web风格): {task_id}")
            else:
                logger.error("❌ MongoDB插入失败")

        except Exception as e:
            logger.error(f"❌ 保存分析结果失败: {task_id} - {e}")
            # 降级到简单保存
            try:
                simple_result = {
                    'task_id': task_id,
                    'success': result.get('success', True),
                    'error': str(e),
                    'completed_at': datetime.utcnow().isoformat()
                }
                await db.analysis_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"result": simple_result}}
                )
                logger.info(f"💾 使用简化结果保存: {task_id}")
            except Exception as fallback_error:
                logger.error(f"❌ 简化保存也失败: {task_id} - {fallback_error}")

    # ------------------------------------------------------------------
    # 完整保存（数据库 + 本地文件）
    # ------------------------------------------------------------------

    async def _save_analysis_results_complete(self, task_id: str, result: Dict[str, Any]):
        """完整的分析结果保存 - 完全采用web目录的双重保存方式"""
        try:
            # 调试：打印result中的所有键
            logger.info(f"🔍 [调试] result中的所有键: {list(result.keys())}")
            logger.info(f"🔍 [调试] stock_code: {result.get('stock_code', 'NOT_FOUND')}")
            logger.info(f"🔍 [调试] stock_symbol: {result.get('stock_symbol', 'NOT_FOUND')}")

            # 优先使用stock_symbol，如果没有则使用stock_code
            stock_symbol = result.get('stock_symbol') or result.get('stock_code') or 'UNKNOWN'
            logger.info(f"💾 开始完整保存分析结果: {stock_symbol}")

            # 1. 保存分模块报告到本地目录
            logger.info(f"📁 [本地保存] 开始保存分模块报告到本地目录")
            local_files = await self._save_modular_reports_to_data_dir(result, stock_symbol)
            if local_files:
                logger.info(f"✅ [本地保存] 已保存 {len(local_files)} 个本地报告文件")
                for module, path in local_files.items():
                    logger.info(f"  - {module}: {path}")
            else:
                logger.warning(f"⚠️ [本地保存] 本地报告文件保存失败")

            # 2. 保存分析报告到数据库
            logger.info(f"🗄️ [数据库保存] 开始保存分析报告到数据库")
            await self._save_analysis_result_web_style(task_id, result)
            logger.info(f"✅ [数据库保存] 分析报告已成功保存到数据库")

            # 3. 记录保存结果
            if local_files:
                logger.info(f"✅ 分析报告已保存到数据库和本地文件")
            else:
                logger.warning(f"⚠️ 数据库保存成功，但本地文件保存失败")

        except Exception as save_error:
            logger.error(f"❌ [完整保存] 保存分析报告时发生错误: {str(save_error)}")
            # 降级到仅数据库保存
            try:
                await self._save_analysis_result_web_style(task_id, result)
                logger.info(f"💾 降级保存成功 (仅数据库): {task_id}")
            except Exception as fallback_error:
                logger.error(f"❌ 降级保存也失败: {task_id} - {fallback_error}")

    # ------------------------------------------------------------------
    # 本地分模块报告文件保存
    # ------------------------------------------------------------------

    async def _save_modular_reports_to_data_dir(self, result: Dict[str, Any], stock_symbol: str) -> Dict[str, str]:
        """保存分模块报告到data目录 - 完全采用web目录的文件结构"""
        try:
            from pathlib import Path
            from datetime import datetime

            # 获取项目根目录
            project_root = Path(__file__).parent.parent.parent

            # 确定results目录路径 - 与web目录保持一致
            results_dir_env = os.getenv("TRADINGAGENTS_RESULTS_DIR")
            if results_dir_env:
                if not os.path.isabs(results_dir_env):
                    results_dir = project_root / results_dir_env
                else:
                    results_dir = Path(results_dir_env)
            else:
                # 默认使用data目录而不是results目录
                results_dir = project_root / "data" / "analysis_results"

            # 创建股票专用目录 - 完全按照web目录的结构
            analysis_date_raw = result.get('analysis_date', datetime.now())

            # 确保 analysis_date 是字符串格式
            if isinstance(analysis_date_raw, datetime):
                analysis_date_str = analysis_date_raw.strftime('%Y-%m-%d')
            elif isinstance(analysis_date_raw, str):
                # 如果已经是字符串，检查格式
                try:
                    # 尝试解析日期字符串，确保格式正确
                    parsed_date = datetime.strptime(analysis_date_raw, '%Y-%m-%d')
                    analysis_date_str = analysis_date_raw
                except ValueError:
                    # 如果格式不正确，使用当前日期
                    analysis_date_str = datetime.now().strftime('%Y-%m-%d')
            else:
                # 其他类型，使用当前日期
                analysis_date_str = datetime.now().strftime('%Y-%m-%d')

            stock_dir = results_dir / stock_symbol / analysis_date_str
            reports_dir = stock_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)

            # 创建message_tool.log文件 - 与web目录保持一致
            log_file = stock_dir / "message_tool.log"
            log_file.touch(exist_ok=True)

            logger.info(f"📁 创建分析结果目录: {reports_dir}")
            logger.info(f"🔍 [调试] analysis_date_raw 类型: {type(analysis_date_raw)}, 值: {analysis_date_raw}")
            logger.info(f"🔍 [调试] analysis_date_str: {analysis_date_str}")
            logger.info(f"🔍 [调试] 完整路径: {os.path.normpath(str(reports_dir))}")

            state = result.get('state', {})
            saved_files = {}

            # 定义报告模块映射 - 完全按照web目录的定义
            report_modules = {
                'market_report': {
                    'filename': 'market_report.md',
                    'title': f'{stock_symbol} 股票技术分析报告',
                    'state_key': 'market_report'
                },
                'sentiment_report': {
                    'filename': 'sentiment_report.md',
                    'title': f'{stock_symbol} 市场情绪分析报告',
                    'state_key': 'sentiment_report'
                },
                'news_report': {
                    'filename': 'news_report.md',
                    'title': f'{stock_symbol} 新闻事件分析报告',
                    'state_key': 'news_report'
                },
                'fundamentals_report': {
                    'filename': 'fundamentals_report.md',
                    'title': f'{stock_symbol} 基本面分析报告',
                    'state_key': 'fundamentals_report'
                },
                'investment_plan': {
                    'filename': 'investment_plan.md',
                    'title': f'{stock_symbol} 投资决策报告',
                    'state_key': 'investment_plan'
                },
                'trader_investment_plan': {
                    'filename': 'trader_investment_plan.md',
                    'title': f'{stock_symbol} 交易计划报告',
                    'state_key': 'trader_investment_plan'
                },
                'final_trade_decision': {
                    'filename': 'final_trade_decision.md',
                    'title': f'{stock_symbol} 最终投资决策',
                    'state_key': 'final_trade_decision'
                },
                'investment_debate_state': {
                    'filename': 'research_team_decision.md',
                    'title': f'{stock_symbol} 研究团队决策报告',
                    'state_key': 'investment_debate_state'
                },
                'risk_debate_state': {
                    'filename': 'risk_management_decision.md',
                    'title': f'{stock_symbol} 风险管理团队决策报告',
                    'state_key': 'risk_debate_state'
                }
            }

            # 保存各模块报告 - 完全按照web目录的方式
            for module_key, module_info in report_modules.items():
                try:
                    state_key = module_info['state_key']
                    if state_key in state:
                        # 提取模块内容
                        module_content = state[state_key]
                        report_content = normalize_report_content(module_content, module_key)
                        if not report_content:
                            continue

                        if not report_content.lstrip().startswith("#"):
                            report_content = f"# {module_info['title']}\n\n{report_content}"

                        # 保存到文件 - 使用web目录的文件名
                        file_path = reports_dir / module_info['filename']
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(report_content)

                        saved_files[module_key] = str(file_path)
                        logger.info(f"✅ 保存模块报告: {file_path}")

                except Exception as e:
                    logger.warning(f"⚠️ 保存模块 {module_key} 失败: {e}")

            # 保存最终决策报告 - 完全按照web目录的方式
            decision = result.get('decision', {})
            if decision:
                decision_content = f"# {stock_symbol} 最终投资决策\n\n"

                if isinstance(decision, dict):
                    decision_content += f"## 投资建议\n\n"
                    decision_content += f"**行动**: {decision.get('action', 'N/A')}\n\n"
                    decision_content += f"**置信度**: {decision.get('confidence', 0):.1%}\n\n"
                    decision_content += f"**风险评分**: {decision.get('risk_score', 0):.1%}\n\n"
                    decision_content += f"**目标价位**: {decision.get('target_price', 'N/A')}\n\n"
                    reasoning = remove_thinking_content(str(decision.get('reasoning', '暂无分析推理')))
                    decision_content += f"## 分析推理\n\n{reasoning}\n\n"
                else:
                    decision_content += f"{remove_thinking_content(str(decision))}\n\n"

                decision_file = reports_dir / "final_trade_decision.md"
                with open(decision_file, 'w', encoding='utf-8') as f:
                    f.write(decision_content)

                saved_files['final_trade_decision'] = str(decision_file)
                logger.info(f"✅ 保存最终决策: {decision_file}")

            # 保存分析元数据文件 - 完全按照web目录的方式
            metadata = {
                'stock_symbol': stock_symbol,
                'analysis_date': analysis_date_str,
                'timestamp': datetime.now().isoformat(),
                'research_depth': result.get('research_depth', 1),
                'analysts': result.get('analysts', []),
                'status': 'completed',
                'reports_count': len(saved_files),
                'report_types': list(saved_files.keys())
            }

            metadata_file = reports_dir.parent / "analysis_metadata.json"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            logger.info(f"✅ 保存分析元数据: {metadata_file}")
            logger.info(f"✅ 分模块报告保存完成，共保存 {len(saved_files)} 个文件")
            logger.info(f"📁 保存目录: {os.path.normpath(str(reports_dir))}")

            return saved_files

        except Exception as e:
            logger.error(f"❌ 保存分模块报告失败: {e}")
            import traceback
            logger.error(f"❌ 详细错误: {traceback.format_exc()}")
            return {}
