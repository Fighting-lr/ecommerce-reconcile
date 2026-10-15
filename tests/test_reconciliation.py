# /* StarOfCAU-BEGIN: Unit Tests for Reconciliation Engine */
"""
对账核心算法与 12 种异常检测单元测试
作者: StarOfCAU (StarOfCAU)
"""

import unittest
from decimal import Decimal
from core.models import (
    money, OrderRecord, PaymentRecord, LogisticsRecord,
    ReconcileStatus, DiscrepancyType
)
from core.reconciliation_engine import ReconciliationEngine
from core.dirty_data_generator import DirtyDataGenerator


class TestReconciliationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ReconciliationEngine(tolerance=Decimal("0.00"))

    def test_normal_matched_order(self):
        """测试正常订单的三方完全平账"""
        order = OrderRecord(
            order_id="TB_NORM_001", user_id="USR_01", product_name="测试商品",
            goods_amount=Decimal("100.00"), shop_discount=Decimal("10.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("90.00"), order_status="PAID",
            created_at="2026-09-01 10:00:00"
        )
        payment = PaymentRecord(
            pay_flow_id="PAY_NORM_001", order_id="TB_NORM_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("90.00"),
            channel_fee=money(Decimal("90.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-01 10:00:00"
        )
        logistics = LogisticsRecord(
            tracking_no="SF_NORM_001", order_id="TB_NORM_001",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-01 10:00:00", delivery_status="DELIVERED"
        )

        res = self.engine.reconcile([order], [payment], [logistics])
        self.assertEqual(res["summary"]["matched_count"], 1)
        self.assertEqual(res["summary"]["discrepancy_count"], 0)
        self.assertEqual(res["summary"]["matched_rate"], 100.0)

    def test_commission_discrepancy(self):
        """测试平台佣金跳档暗扣异常检测"""
        order = OrderRecord(
            order_id="TB_COMM_001", user_id="USR_01", product_name="测试商品",
            goods_amount=Decimal("100.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("100.00"), order_status="PAID",
            created_at="2026-09-01 10:00:00"
        )
        # 实际划扣了 5% 佣金 (¥5.00)，基线预期为千分之六 (¥0.60)，差额 ¥4.40 > ¥0.50
        payment = PaymentRecord(
            pay_flow_id="PAY_COMM_001", order_id="TB_COMM_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("100.00"),
            channel_fee=Decimal("5.00"),
            channel_status="SUCCESS", trans_time="2026-09-01 10:00:00"
        )
        logistics = LogisticsRecord(
            tracking_no="SF_COMM_001", order_id="TB_COMM_001",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-01 10:00:00", delivery_status="DELIVERED"
        )

        res = self.engine.reconcile([order], [payment], [logistics])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.reconcile_status, ReconcileStatus.FEE_DISCREPANCY)
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.PLATFORM_COMMISSION_DIFF)
        self.assertEqual(disc.diff_amount, Decimal("4.40"))

    def test_logistics_overweight_penalty(self):
        """测试快递超重异常扣款检测"""
        order = OrderRecord(
            order_id="TB_LOG_001", user_id="USR_01", product_name="测试商品",
            goods_amount=Decimal("50.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("50.00"), order_status="PAID",
            created_at="2026-09-01 10:00:00"
        )
        payment = PaymentRecord(
            pay_flow_id="PAY_LOG_001", order_id="TB_LOG_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("50.00"),
            channel_fee=money(Decimal("50.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-01 10:00:00"
        )
        # 顺丰复称超重扣了 6.00 元
        logistics = LogisticsRecord(
            tracking_no="SF_LOG_001", order_id="TB_LOG_001",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("6.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-01 10:00:00", delivery_status="DELIVERED"
        )

        res = self.engine.reconcile([order], [payment], [logistics])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.reconcile_status, ReconcileStatus.LOGISTICS_MISMATCH)
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.LOGISTICS_OVERWEIGHT_FEE)
        self.assertEqual(disc.diff_amount, Decimal("6.00"))

    def test_unilateral_channel_dropped_order(self):
        """测试渠道单边账（用户已扣款但业务系统掉单）"""
        payment = PaymentRecord(
            pay_flow_id="PAY_DROP_001", order_id="TB_DROP_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("129.00"),
            channel_fee=Decimal("0.77"),
            channel_status="SUCCESS", trans_time="2026-09-01 10:00:00"
        )
        # 订单中心无此单据
        res = self.engine.reconcile([], [payment], [])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.reconcile_status, ReconcileStatus.UNILATERAL_CHANNEL)
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.UNILATERAL_SUSPENSE)
        self.assertEqual(disc.diff_amount, Decimal("129.00"))

    def test_timing_in_transit(self):
        """测试 T+1 跨期在途资金结算（截止日前订单，截止日后到账）"""
        order = OrderRecord(
            order_id="TB_TRANSIT_001", user_id="USR_01", product_name="在途商品",
            goods_amount=Decimal("89.90"), shop_discount=Decimal("5.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("84.90"), order_status="PAID",
            created_at="2026-09-30 22:00:00"
        )
        # 付款结算于 10 月 1 日入账 (跨过 2026-09-30 截止日)
        payment = PaymentRecord(
            pay_flow_id="PAY_TRANSIT_001", order_id="TB_TRANSIT_001",
            channel="UNIONPAY", trans_type="PAY", trans_amount=Decimal("84.90"),
            channel_fee=money(Decimal("84.90") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-10-01 10:00:00"
        )
        res = self.engine.reconcile([order], [payment], [])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.reconcile_status, ReconcileStatus.TIMING_LAG)
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.TIMING_IN_TRANSIT)

    def test_cross_period_refund(self):
        """测试跨期退款冲销差异（当期收款，下期退货冲销）"""
        order = OrderRecord(
            order_id="TB_REFUND_001", user_id="USR_01", product_name="跨期退款商品",
            goods_amount=Decimal("129.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("129.00"), order_status="REFUNDED",
            created_at="2026-09-15 10:00:00", refund_amount=Decimal("129.00"),
            refund_time="2026-10-05 14:00:00"
        )
        pay1 = PaymentRecord(
            pay_flow_id="PAY_REFUND_001", order_id="TB_REFUND_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("129.00"),
            channel_fee=money(Decimal("129.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-15 10:00:00"
        )
        pay2 = PaymentRecord(
            pay_flow_id="REF_REFUND_001", order_id="TB_REFUND_001",
            channel="ALIPAY", trans_type="REFUND", trans_amount=Decimal("-129.00"),
            channel_fee=money(Decimal("-129.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-10-05 14:00:00"
        )
        res = self.engine.reconcile([order], [pay1, pay2], [])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.reconcile_status, ReconcileStatus.TIMING_LAG)
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.CROSS_PERIOD_REFUND)

    def test_freight_insurance_lag(self):
        """测试运费险理赔滞后（退货已签收，已扣保费，理赔尚未入账）"""
        order = OrderRecord(
            order_id="TB_INS_001", user_id="USR_01", product_name="退货运费险商品",
            goods_amount=Decimal("50.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("50.00"), order_status="REFUNDED",
            created_at="2026-09-05 10:00:00", refund_amount=Decimal("50.00"),
            refund_time="2026-09-08 10:00:00"
        )
        pay1 = PaymentRecord(
            pay_flow_id="PAY_INS_001", order_id="TB_INS_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("50.00"),
            channel_fee=money(Decimal("50.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-05 10:00:00"
        )
        pay2 = PaymentRecord(
            pay_flow_id="REF_INS_001", order_id="TB_INS_001",
            channel="ALIPAY", trans_type="REFUND", trans_amount=Decimal("-50.00"),
            channel_fee=money(Decimal("-50.00") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-08 10:00:00"
        )
        log = LogisticsRecord(
            tracking_no="SF_INS_001", order_id="TB_INS_001",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-05 10:00:00", delivery_status="RETURNED"
        )
        res = self.engine.reconcile([order], [pay1, pay2], [log])
        self.assertEqual(res["summary"]["discrepancy_count"], 1)
        disc = res["discrepancies"][0]
        self.assertEqual(disc.discrepancy_type, DiscrepancyType.FREIGHT_INSURANCE_LAG)
        self.assertEqual(disc.diff_amount, Decimal("8.00"))


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */
