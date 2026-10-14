# /* StarOfCAU-BEGIN: Unit Tests for Reconciliation Engine */
"""
对账核心算法与 12 种异常检测单元测试
作者: StarOfCAU (李锐)
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

    def test_decimal_safety_no_float_drift(self):
        """验证全流程数值计算的高精度，拒绝浮点数截断漂移"""
        val1 = money("0.10")
        val2 = money("0.20")
        self.assertEqual(val1 + val2, Decimal("0.30"))
        self.assertNotEqual(str(val1 + val2), "0.30000000000000004")


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */
