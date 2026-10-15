# /* StarOfCAU-BEGIN: Accounting Balancer & Trial Balance Verification */
"""
财会复式记账试算平衡与损益侵蚀分析引擎
作者: StarOfCAU
专业依据: 
1. 中华人民共和国《企业会计准则》- 收入准则与货币资金内部控制规范
2. 试算平衡原理（借贷必相等）与《资金对账调节表》自动平衡闭环验算
3. 纳入商品采购成本 (COGS) 计算小微电商真实经营毛利 (~5%-8% 微利常态)
"""

from decimal import Decimal
from typing import Dict, Any, List
from collections import defaultdict
from .models import money, DiscrepancyItem, DiscrepancyType, ReconcileStatus


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
        total_cogs = summary.get("total_cogs", Decimal("0.00"))

        # 1. 差异科目分类归集
        type_summary: Dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for d in discrepancies:
            type_summary[d.discrepancy_type.value] += abs(d.diff_amount)

        # 2. 核心试算平衡项计算（严格按借贷资金勾稽公式闭环）
        # 渠道单边长款（企业未入账，渠道已收）
        unilateral_channel = money(sum(
            d.diff_amount for d in discrepancies if d.reconcile_status == ReconcileStatus.UNILATERAL_CHANNEL
        ))
        # 业务单边短款（企业已记录，渠道未扣款）
        unilateral_biz = money(sum(
            abs(d.diff_amount) for d in discrepancies if d.reconcile_status == ReconcileStatus.UNILATERAL_BIZ
        ))
        # 跨期退款待冲销（当期已收全款，下期发生退货退款冲红）
        cross_period_refund = money(sum(
            d.diff_amount for d in discrepancies if d.discrepancy_type == DiscrepancyType.CROSS_PERIOD_REFUND
        ))
        # 在途资金未结算（当期成交，款项在途，渠道期后入账）
        timing_in_transit = money(sum(
            abs(d.diff_amount) for d in discrepancies if d.discrepancy_type == DiscrepancyType.TIMING_IN_TRANSIT
        ))
        # 优惠券与分摊结算尾差 (仅限影响收单金额的尾差，不混入手续费跳档)
        amount_mismatch_diff = money(sum(
            d.diff_amount for d in discrepancies if d.reconcile_status == ReconcileStatus.AMOUNT_MISMATCH
        ))

        # 编制《资金调节平衡表》调节推导：
        # 调节后渠道应达金额 = 业务应收净额 + 渠道单边长款 - 业务单边短款 + 跨期退款调节 - 在途未结算调节 + 结算尾差调节
        reconciled_channel_turnover = money(
            biz_turnover + unilateral_channel - unilateral_biz + cross_period_refund - timing_in_transit + amount_mismatch_diff
        )
        unexplained_diff = money(channel_turnover - reconciled_channel_turnover)
        is_balanced = (unexplained_diff == Decimal("0.00"))

        # 3. 经营利润与损益侵蚀分析（纳入 COGS 真实反映小微电商毛利）
        # 净经营毛利 = 渠道流水 - 采购成本 (COGS) - 渠道手续费 - 物流履约支出
        gross_profit = money(channel_turnover - total_cogs - channel_fees - shipping_costs)
        base_gmv = channel_turnover if channel_turnover > Decimal("0.00") else Decimal("1.00")
        actual_profit_margin = round(float(gross_profit / base_gmv * 100), 2)

        # 隐藏损益侵蚀（运费超重 + 平台佣金跳档 + 运费险滞后未赔）
        overweight_erosion = type_summary.get(DiscrepancyType.LOGISTICS_OVERWEIGHT_FEE.value, Decimal("0.00"))
        insurance_lag_erosion = type_summary.get(DiscrepancyType.FREIGHT_INSURANCE_LAG.value, Decimal("0.00"))
        # 佣金跳档暗扣 (属于 FEE_DISCREPANCY)
        fee_erosion = money(sum(
            d.diff_amount for d in discrepancies if d.reconcile_status == ReconcileStatus.FEE_DISCREPANCY
        ))
        total_hidden_loss = money(overweight_erosion + insurance_lag_erosion + fee_erosion)
        erosion_rate = round(float(total_hidden_loss / base_gmv * 100), 2)

        # 结构化调节表行项
        reconcile_rows = [
            {"item": "一、业务系统账面应收净额", "amount": str(biz_turnover), "note": "主营业务收入基线 (订单实际应付减当期退款)"},
            {"item": "加：渠道单边已入账（企业未入账款项）", "amount": f"+{unilateral_channel}", "note": "其他应付款-待查长款 (外部掉单挂账)"},
            {"item": "减：业务单边未扣款（渠道漏单款项）", "amount": f"-{unilateral_biz}", "note": "应收账款-渠道结算 (已发货未结算短款)"},
            {"item": "加：跨期退款冲销调节（下期退款当期多收）", "amount": f"+{cross_period_refund}", "note": "跨期退款时间性差异 (当期全款到账，下期冲红)"},
            {"item": "减：在途资金未结算（款项跨期在途结算）", "amount": f"-{timing_in_transit}", "note": "其他货币资金-在途货币资金 (T+1 截止日后入账)"},
            {"item": "加/减：优惠券分摊与结算尾差", "amount": f"{amount_mismatch_diff:+}", "note": "财务费用-结算尾差 (小额分摊舍入)"},
            {"item": "二、调节后渠道与银行应到金额", "amount": str(reconciled_channel_turnover), "note": "调节后借贷试算平衡理论值"},
            {"item": "三、渠道及银行当期实际到账净额", "amount": str(channel_turnover), "note": "收单通道对账单实际流水汇总"},
        ]

        return {
            "initial_cash": self.initial_cash,
            "ending_cash": money(self.initial_cash + gross_profit),
            "biz_turnover": biz_turnover,
            "channel_turnover": channel_turnover,
            "channel_fees": channel_fees,
            "shipping_costs": shipping_costs,
            "total_cogs": total_cogs,
            "gross_profit": gross_profit,
            "actual_profit_margin": actual_profit_margin,
            "total_hidden_loss": total_hidden_loss,
            "erosion_rate": erosion_rate,
            "is_balanced": is_balanced,
            "raw_gap": money(channel_turnover - biz_turnover),
            "unexplained_diff": unexplained_diff,
            "type_summary": {k: str(v) for k, v in type_summary.items()},
            "reconcile_rows": reconcile_rows,
            "details": {
                "unilateral_channel": str(unilateral_channel),
                "unilateral_biz": str(unilateral_biz),
                "cross_period_refund": str(cross_period_refund),
                "timing_in_transit": str(timing_in_transit),
                "amount_mismatch_diff": str(amount_mismatch_diff),
            }
        }

# /* StarOfCAU-END */
