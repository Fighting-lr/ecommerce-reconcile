# /* StarOfCAU-BEGIN: Accounting Balancer & Trial Balance Verification */
"""
财会老法师复式记账试算平衡与损益侵蚀分析引擎
作者: StarOfCAU (李锐)
专业依据: 
1. 中华人民共和国《企业会计准则》- 收入准则与货币资金内部控制规范
2. 试算平衡原理（借贷必相等）与《资金对账调节表》自动平衡验算
"""

from decimal import Decimal
from typing import Dict, Any, List
from collections import defaultdict
from .models import money, DiscrepancyItem, DiscrepancyType


class AccountingBalancer:
    """
    负责从财会专业角度对对账结果进行复式试算平衡校验，并评估各项异常对真实毛利率的侵蚀。
    """
    def __init__(self, initial_cash_balance: Decimal = Decimal("50000.00")):
        self.initial_cash = money(initial_cash_balance)

    def verify_trial_balance(self, reconcile_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        进行试算平衡验算，编制资金对账调节表
        """
        summary = reconcile_result["summary"]
        discrepancies: List[DiscrepancyItem] = reconcile_result["discrepancies"]

        biz_turnover = summary["total_biz_turnover"]
        channel_turnover = summary["total_channel_turnover"]
        channel_fees = summary["total_channel_fees"]
        shipping_costs = summary["total_shipping_costs"]

        # 1. 差异科目分类归集
        type_summary: Dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for d in discrepancies:
            type_summary[d.discrepancy_type.value] += abs(d.diff_amount)

        # 2. 核心试算平衡逻辑（编制《资金对账调节表》）
        # 业务端记录净额 vs 渠道端实收净额之差
        raw_gap = money(channel_turnover - biz_turnover)

        # 归集解释项：渠道单边、业务单边、金额尾差、跨期在途
        turnover_statuses = {
            "UNILATERAL_CHANNEL", "UNILATERAL_BIZ", "AMOUNT_MISMATCH", "TIMING_LAG"
        }
        signed_diffs = [
            d.diff_amount for d in discrepancies if d.reconcile_status.value in turnover_statuses
        ]
        explained_gap = money(sum(signed_diffs))
        unexplained_diff = money(raw_gap - explained_gap)

        # 借贷必须精确对齐到分（0.00）
        is_balanced = (unexplained_diff == Decimal("0.00"))

        # 3. 经营利润与损益侵蚀分析（以典型电商 5% 毛利为基线）
        # 净经营毛利 = 渠道流水 - 渠道手续费 - 物流履约支出
        gross_profit = money(channel_turnover - channel_fees - shipping_costs)
        base_gmv = channel_turnover if channel_turnover > Decimal("0.00") else Decimal("1.00")
        actual_profit_margin = round(float(gross_profit / base_gmv * 100), 2)

        # 隐藏损益侵蚀（运费超重 + 平台佣金跳档 + 滞后未赔）
        overweight_erosion = type_summary.get(DiscrepancyType.LOGISTICS_OVERWEIGHT_FEE.value, Decimal("0.00"))
        insurance_lag_erosion = type_summary.get(DiscrepancyType.FREIGHT_INSURANCE_LAG.value, Decimal("0.00"))
        commission_diff = type_summary.get(DiscrepancyType.PLATFORM_COMMISSION_DIFF.value, Decimal("0.00"))
        total_hidden_loss = money(overweight_erosion + insurance_lag_erosion + commission_diff)
        erosion_rate = round(float(total_hidden_loss / base_gmv * 100), 2)

        return {
            "initial_cash": self.initial_cash,
            "ending_cash": money(self.initial_cash + gross_profit),
            "biz_turnover": biz_turnover,
            "channel_turnover": channel_turnover,
            "channel_fees": channel_fees,
            "shipping_costs": shipping_costs,
            "gross_profit": gross_profit,
            "actual_profit_margin": actual_profit_margin,
            "total_hidden_loss": total_hidden_loss,
            "erosion_rate": erosion_rate,
            "is_balanced": is_balanced,
            "raw_gap": raw_gap,
            "unexplained_diff": unexplained_diff,
            "type_summary": {k: str(v) for k, v in type_summary.items()},
        }

# /* StarOfCAU-END */
