"""
财务评分与分析 mixin

包含基本面评分、估值评分、成长性评分、风险等级计算，
以及估值分析、成长性分析、风险分析、投资建议生成等方法。
"""


class FinancialScoringMixin:
    """财务评分与分析 mixin — 提供评分、分析和投资建议生成能力"""

    # ------------------------------------------------------------------
    # 评分计算
    # ------------------------------------------------------------------

    def _calculate_fundamental_score(self, metrics: dict, stock_info: dict) -> float:
        """计算基本面评分"""
        score = 5.0  # 基础分

        # ROE评分
        roe_str = metrics.get("roe", "N/A")
        if roe_str != "N/A":
            try:
                roe = float(roe_str.replace("%", ""))
                if roe > 15:
                    score += 1.5
                elif roe > 10:
                    score += 1.0
                elif roe > 5:
                    score += 0.5
            except:
                pass

        # 净利率评分
        net_margin_str = metrics.get("net_margin", "N/A")
        if net_margin_str != "N/A":
            try:
                net_margin = float(net_margin_str.replace("%", ""))
                if net_margin > 20:
                    score += 1.0
                elif net_margin > 10:
                    score += 0.5
            except:
                pass

        return min(score, 10.0)

    def _calculate_valuation_score(self, metrics: dict) -> float:
        """计算估值评分"""
        score = 5.0  # 基础分

        # PE评分
        pe_str = metrics.get("pe", "N/A")
        if pe_str != "N/A" and "亏损" not in pe_str:
            try:
                pe = float(pe_str.replace("倍", ""))
                if pe < 15:
                    score += 2.0
                elif pe < 25:
                    score += 1.0
                elif pe > 50:
                    score -= 1.0
            except:
                pass

        # PB评分
        pb_str = metrics.get("pb", "N/A")
        if pb_str != "N/A":
            try:
                pb = float(pb_str.replace("倍", ""))
                if pb < 1.5:
                    score += 1.0
                elif pb < 3:
                    score += 0.5
                elif pb > 5:
                    score -= 0.5
            except:
                pass

        return min(max(score, 1.0), 10.0)

    def _calculate_growth_score(self, metrics: dict, stock_info: dict) -> float:
        """计算成长性评分"""
        score = 6.0  # 基础分

        # 根据行业调整
        industry = stock_info.get('industry', '')
        if '科技' in industry or '软件' in industry or '互联网' in industry:
            score += 1.0
        elif '银行' in industry or '保险' in industry:
            score -= 0.5

        return min(max(score, 1.0), 10.0)

    def _calculate_risk_level(self, metrics: dict, stock_info: dict) -> str:
        """计算风险等级"""
        # 资产负债率
        debt_ratio_str = metrics.get("debt_ratio", "N/A")
        if debt_ratio_str != "N/A":
            try:
                debt_ratio = float(debt_ratio_str.replace("%", ""))
                if debt_ratio > 70:
                    return "较高"
                elif debt_ratio > 50:
                    return "中等"
                else:
                    return "较低"
            except:
                pass

        # 根据行业判断
        industry = stock_info.get('industry', '')
        if '银行' in industry:
            return "中等"
        elif '科技' in industry or '创业板' in industry:
            return "较高"

        return "中等"

    # ------------------------------------------------------------------
    # 分析与建议
    # ------------------------------------------------------------------

    def _analyze_valuation(self, financial_estimates: dict) -> str:
        """分析估值水平"""
        valuation_score = financial_estimates['valuation_score']

        if valuation_score >= 8:
            return "当前估值水平较为合理，具有一定的投资价值。市盈率和市净率相对较低，安全边际较高。"
        elif valuation_score >= 6:
            return "估值水平适中，需要结合基本面和成长性综合判断投资价值。"
        else:
            return "当前估值偏高，投资需谨慎。建议等待更好的买入时机。"

    def _analyze_growth_potential(self, symbol: str, industry_info: dict) -> str:
        """分析成长潜力"""
        if symbol.startswith(('000001', '600036')):
            return "银行业整体增长稳定，受益于经济发展和金融深化。数字化转型和财富管理业务是主要增长点。"
        elif symbol.startswith('300'):
            return "创业板公司通常具有较高的成长潜力，但也伴随着较高的风险。需要关注技术创新和市场拓展能力。"
        else:
            return "成长潜力需要结合具体行业和公司基本面分析。建议关注行业发展趋势和公司竞争优势。"

    def _analyze_risks(self, symbol: str, financial_estimates: dict, industry_info: dict) -> str:
        """分析投资风险"""
        risk_level = financial_estimates['risk_level']

        risk_analysis = f"**风险等级**: {risk_level}\n\n"

        if symbol.startswith(('000001', '600036')):
            risk_analysis += """**主要风险**:
- 利率环境变化对净息差的影响
- 信贷资产质量风险
- 监管政策变化风险
- 宏观经济下行对银行业的影响"""
        elif symbol.startswith('300'):
            risk_analysis += """**主要风险**:
- 技术更新换代风险
- 市场竞争加剧风险
- 估值波动较大
- 业绩不确定性较高"""
        else:
            risk_analysis += """**主要风险**:
- 行业周期性风险
- 宏观经济环境变化
- 市场竞争风险
- 政策调整风险"""

        return risk_analysis

    def _generate_investment_advice(self, financial_estimates: dict, industry_info: dict) -> str:
        """生成投资建议"""
        fundamental_score = financial_estimates['fundamental_score']
        valuation_score = financial_estimates['valuation_score']
        growth_score = financial_estimates['growth_score']

        total_score = (fundamental_score + valuation_score + growth_score) / 3

        if total_score >= 7.5:
            return """**投资建议**: 🟢 **买入**
- 基本面良好，估值合理，具有较好的投资价值
- 建议分批建仓，长期持有
- 适合价值投资者和稳健型投资者"""
        elif total_score >= 6.0:
            return """**投资建议**: 🟡 **观望**
- 基本面一般，需要进一步观察
- 可以小仓位试探，等待更好时机
- 适合有经验的投资者"""
        else:
            return """**投资建议**: 🔴 **回避**
- 当前风险较高，不建议投资
- 建议等待基本面改善或估值回落
- 风险承受能力较低的投资者应避免"""
