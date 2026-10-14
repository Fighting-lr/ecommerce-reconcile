# /* StarOfCAU-BEGIN: Core Three-Way Reconciliation State Machine */
"""
电商全链路三方核对与差异分流引擎
作者: StarOfCAU (李锐)
算法设计:
1. 采用复合主键聚合策略（同一 OrderID 的多笔支付/退款/多次补发流水合并）
2. 借贷试算与三方闭环核验（业务订单 vs 支付渠道 vs 物流履约）
3. 自动生成审计底稿级的差错分类与分录调账建议
"""

from decimal import Decimal
from typing import List, Dict, Any, Tuple
from collections import defaultdict
from .models import (
    money, OrderRecord, PaymentRecord, LogisticsRecord,
    ReconcileStatus, DiscrepancyType, DiscrepancyItem
)


class ReconciliationEngine:
    def __init__(self, tolerance: Decimal = Decimal("0.00")):
        """
        tolerance: 允许的抹零误差阈值（默认为 0.00，不允许任何未经说明的分钱差错）
        """
        self.tolerance = tolerance

    def reconcile(
        self,
        orders: List[OrderRecord],
        payments: List[PaymentRecord],
        logistics: List[LogisticsRecord]
    ) -> Dict[str, Any]:
        """
        执行全量三方对账
        返回综合对账结果，包含：统计大盘、平账清单、异常明细（DiscrepancyItem 列表）
        """
        order_map: Dict[str, OrderRecord] = {o.order_id: o for o in orders}
        pay_map: Dict[str, List[PaymentRecord]] = defaultdict(list)
        for p in payments:
            pay_map[p.order_id].append(p)

        log_map: Dict[str, List[LogisticsRecord]] = defaultdict(list)
        for l in logistics:
            log_map[l.order_id].append(l)

        all_order_ids = set(order_map.keys()) | set(pay_map.keys()) | set(log_map.keys())

        matched_items: List[Dict[str, Any]] = []
        discrepancies: List[DiscrepancyItem] = []

        total_biz_turnover = Decimal("0.00")
        total_channel_turnover = Decimal("0.00")
        total_channel_fees = Decimal("0.00")
        total_shipping_costs = Decimal("0.00")

        for oid in all_order_ids:
            order = order_map.get(oid)
            plist = pay_map.get(oid, [])
            llist = log_map.get(oid, [])

            # 1. 汇总支付渠道金额
            net_channel_amount = Decimal("0.00")
            channel_fee_sum = Decimal("0.00")
            for p in plist:
                net_channel_amount += p.trans_amount
                channel_fee_sum += p.channel_fee
            net_channel_amount = money(net_channel_amount)
            channel_fee_sum = money(channel_fee_sum)

            # 2. 汇总物流费用
            shipping_sum = money(sum(l.shipping_fee + l.overweight_fee + l.freight_insurance_fee - l.insurance_claim_amount for l in llist))

            total_channel_turnover += net_channel_amount
            total_channel_fees += channel_fee_sum
            total_shipping_costs += shipping_sum

            # ---------------- 判定单边账 ----------------
            if order is None:
                # 渠道单边账（业务无此单，渠道有款 -> 掉单/短款）
                discrepancies.append(DiscrepancyItem(
                    order_id=oid,
                    reconcile_status=ReconcileStatus.UNILATERAL_CHANNEL,
                    discrepancy_type=DiscrepancyType.UNILATERAL_SUSPENSE,
                    diff_amount=net_channel_amount,
                    biz_amount=Decimal("0.00"),
                    channel_amount=net_channel_amount,
                    logistics_amount=Decimal("0.00"),
                    suspect_reason="渠道扣款成功但电商订单中心缺失该单据（系统掉单或跨平台异步回调中断）",
                    accounting_advice="借：银行存款 | 贷：其他应付款-待查长款（查明后补录订单或原路退回买家）"
                ))
                continue

            if order.order_status == "CLOSED":
                # 买家未付款已关闭/取消订单，不产生应收账款与财务流水
                continue

            # 业务端应收净额
            expected_biz_net = money(order.actual_payable - order.refund_amount)
            total_biz_turnover += expected_biz_net

            if not plist:
                if order.order_status in ["PAID", "SHIPPED"]:
                    # 业务单边账（业务记录已付，渠道流水为空 -> 虚假发货或结算漏单）
                    diff = money(Decimal("0.00") - expected_biz_net)
                    discrepancies.append(DiscrepancyItem(
                        order_id=oid,
                        reconcile_status=ReconcileStatus.UNILATERAL_BIZ,
                        discrepancy_type=DiscrepancyType.UNILATERAL_SUSPENSE,
                        diff_amount=diff,
                        biz_amount=expected_biz_net,
                        channel_amount=Decimal("0.00"),
                        logistics_amount=shipping_sum,
                        suspect_reason="业务系统标记为已支付/发货，但收单网关无该笔交易扣款流水",
                        accounting_advice="借：应收账款-渠道结算 | 贷：主营业务收入（挂起该订单，暂停结算并调取支付通道日志）"
                    ))
                continue

            # ---------------- 判定金额差异 ----------------
            diff_amount = money(net_channel_amount - expected_biz_net)

            if abs(diff_amount) > self.tolerance:
                # 金额不平
                if order.refund_amount > Decimal("0.00") and order.refund_time and "2026-10" in order.refund_time:
                    # 跨月退款场景
                    discrepancies.append(DiscrepancyItem(
                        order_id=oid,
                        reconcile_status=ReconcileStatus.TIMING_LAG,
                        discrepancy_type=DiscrepancyType.CROSS_PERIOD_REFUND,
                        diff_amount=diff_amount,
                        biz_amount=expected_biz_net,
                        channel_amount=net_channel_amount,
                        logistics_amount=shipping_sum,
                        suspect_reason="上月交易在本月发生退货退款，属于跨期会计冲销，导致当期收单净流水与原始订单存在时间性差异",
                        accounting_advice="借：主营业务收入（红字） 应交税费-应交增值税(销项红字) | 贷：银行存款"
                    ))
                else:
                    # 优惠分摊舍入误差或恶意少付
                    discrepancies.append(DiscrepancyItem(
                        order_id=oid,
                        reconcile_status=ReconcileStatus.AMOUNT_MISMATCH,
                        discrepancy_type=DiscrepancyType.PLATFORM_COMMISSION_DIFF,
                        diff_amount=diff_amount,
                        biz_amount=expected_biz_net,
                        channel_amount=net_channel_amount,
                        logistics_amount=shipping_sum,
                        suspect_reason=f"渠道实收净额（¥{net_channel_amount}）与订单应收净额（¥{expected_biz_net}）存在 ¥{diff_amount} 差异（多为平台多重优惠券退款折算舍入导致）",
                        accounting_advice="借：财务费用-结算尾差 | 贷：应收账款-渠道结算（小额自动平账进入损益）"
                    ))
                continue

            # ---------------- 判定扣点与手续费异常 ----------------
            expected_fee = money(order.actual_payable * Decimal("0.006"))
            fee_diff = money(channel_fee_sum - expected_fee)
            if fee_diff > Decimal("0.50"):
                # 扣点大幅超标（例如 5% 抽佣跳档）
                discrepancies.append(DiscrepancyItem(
                    order_id=oid,
                    reconcile_status=ReconcileStatus.FEE_DISCREPANCY,
                    discrepancy_type=DiscrepancyType.PLATFORM_COMMISSION_DIFF,
                    diff_amount=fee_diff,
                    biz_amount=expected_biz_net,
                    channel_amount=channel_fee_sum,
                    logistics_amount=shipping_sum,
                    suspect_reason=f"平台划扣手续费/佣金 ¥{channel_fee_sum} 远超标准千六基线（预期 ¥{expected_fee}），触发平台类目佣金阶梯跳档暗扣",
                    accounting_advice="借：销售费用-平台服务佣金 | 贷：银行存款/渠道预扣款（需向平台运营申诉核对类目提点合同）"
                ))
                continue

            # ---------------- 判定物流与运费险异常 ----------------
            overweight_total = money(sum(l.overweight_fee for l in llist))
            if overweight_total > Decimal("0.00"):
                discrepancies.append(DiscrepancyItem(
                    order_id=oid,
                    reconcile_status=ReconcileStatus.LOGISTICS_MISMATCH,
                    discrepancy_type=DiscrepancyType.LOGISTICS_OVERWEIGHT_FEE,
                    diff_amount=overweight_total,
                    biz_amount=expected_biz_net,
                    channel_amount=net_channel_amount,
                    logistics_amount=shipping_sum,
                    suspect_reason=f"承运商后补扣收超重运费 ¥{overweight_total}，实物打包重量与初始面单报备不符",
                    accounting_advice="借：销售费用-仓储物流费（超重补差） | 贷：应付账款-顺丰/菜鸟物流"
                ))
                continue

            # 运费险赔付延迟（退货后保费扣了，但赔款未到账）
            has_return = any(l.delivery_status == "RETURNED" for l in llist)
            insurance_claimed = money(sum(l.insurance_claim_amount for l in llist))
            if has_return and insurance_claimed == Decimal("0.00"):
                discrepancies.append(DiscrepancyItem(
                    order_id=oid,
                    reconcile_status=ReconcileStatus.LOGISTICS_MISMATCH,
                    discrepancy_type=DiscrepancyType.FREIGHT_INSURANCE_LAG,
                    diff_amount=Decimal("8.00"),  # 标准运费险理赔保额
                    biz_amount=expected_biz_net,
                    channel_amount=net_channel_amount,
                    logistics_amount=shipping_sum,
                    suspect_reason="买家退货已完成，运费险代扣已生效，但保险公司 T+3 赔付金（预计 ¥8.00）在当前结算期内尚未入账",
                    accounting_advice="借：其他应收款-华泰/众安运费险理赔 | 贷：销售费用-运费补贴（待保险入账冲减）"
                ))
                continue

            # 破损多次发货产生多笔运费
            if len(llist) > 1:
                extra_ship_fee = money(sum(l.shipping_fee for l in llist[1:]))
                discrepancies.append(DiscrepancyItem(
                    order_id=oid,
                    reconcile_status=ReconcileStatus.LOGISTICS_MISMATCH,
                    discrepancy_type=DiscrepancyType.LOGISTICS_OVERWEIGHT_FEE,
                    diff_amount=extra_ship_fee,
                    biz_amount=expected_biz_net,
                    channel_amount=net_channel_amount,
                    logistics_amount=shipping_sum,
                    suspect_reason=f"订单存在 {len(llist)} 次物流履约发货记录（破损重发/分批发货），产生额外 ¥{extra_ship_fee} 运费支出",
                    accounting_advice="借：营业外支出-运输破损损耗 | 贷：应付账款-物流运费"
                ))
                continue

            # ---------------- 平账项 ----------------
            matched_items.append({
                "order_id": oid,
                "amount": expected_biz_net,
                "fee": channel_fee_sum,
                "shipping": shipping_sum
            })

        matched_count = len(matched_items)
        discrepancy_count = len(discrepancies)
        total_count = matched_count + discrepancy_count
        matched_rate = (matched_count / total_count * 100) if total_count > 0 else 0.0

        return {
            "summary": {
                "total_orders": total_count,
                "matched_count": matched_count,
                "discrepancy_count": discrepancy_count,
                "matched_rate": round(matched_rate, 2),
                "total_biz_turnover": total_biz_turnover,
                "total_channel_turnover": total_channel_turnover,
                "total_channel_fees": total_channel_fees,
                "total_shipping_costs": total_shipping_costs,
            },
            "matched_items": matched_items,
            "discrepancies": discrepancies
        }

# /* StarOfCAU-END */
