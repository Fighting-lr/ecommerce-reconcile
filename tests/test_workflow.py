# /* StarOfCAU-BEGIN: Integration & Workflow State Persistence Tests */
"""
端到端工作流、调账状态持久化合并、CSV导入与 RBAC 权限拦截测试
作者: StarOfCAU
"""

import unittest
import os
import tempfile
from decimal import Decimal
from core.db import DatabaseManager
from core.auth import AuthManager
from core.models import (
    money, OrderRecord, PaymentRecord, LogisticsRecord,
    ReconcileStatus, DiscrepancyType, DiscrepancyItem
)
from core.reconciliation_engine import ReconciliationEngine


class TestWorkflowAndStatePersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_workflow.db")
        self.db = DatabaseManager(self.db_path)
        self.engine = ReconciliationEngine()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discrepancy_resolution_persistence(self):
        """验证手动调账状态不会被随后的对账重新计算或新单录入冲掉"""
        # 1. 插入一笔超重差异单
        order = OrderRecord(
            order_id="TB_PERSIST_001", user_id="USR_01", product_name="加厚家居服",
            goods_amount=Decimal("89.90"), shop_discount=Decimal("5.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("84.90"), order_status="PAID",
            created_at="2026-09-10 10:00:00"
        )
        pay = PaymentRecord(
            pay_flow_id="PAY_PERSIST_001", order_id="TB_PERSIST_001",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("84.90"),
            channel_fee=money(Decimal("84.90") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-10 10:00:00"
        )
        log = LogisticsRecord(
            tracking_no="SF_PERSIST_001", order_id="TB_PERSIST_001",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("6.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-10 10:00:00", delivery_status="DELIVERED"
        )
        self.db.insert_order(order)
        self.db.insert_payment(pay)
        self.db.insert_logistics(log)

        # 首次对账并保存差错
        res = self.engine.reconcile([order], [pay], [log])
        self.db.save_discrepancies(res["discrepancies"])

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM discrepancies WHERE order_id = 'TB_PERSIST_001'").fetchone()
            self.assertIsNotNone(row)
            disc_id = row["id"]
            self.assertEqual(row["process_status"], "PENDING")

        # 2. 人工标为已调账，记录凭证
        voucher_note = "已向顺丰申诉，退回超重款 ¥6.00 (凭证号 SF-REV-9871)"
        self.db.resolve_discrepancy(disc_id, operator="StarOfCAU", note=voucher_note)

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM discrepancies WHERE id = ?", (disc_id,)).fetchone()
            self.assertEqual(row["process_status"], "RESOLVED")
            self.assertEqual(row["resolved_note"], voucher_note)
            self.assertEqual(row["resolved_by"], "StarOfCAU")

        # 3. 模拟录入一笔完全无关的新单据并再次触发全量对账
        order2 = OrderRecord(
            order_id="TB_NEW_002", user_id="USR_02", product_name="短袖T恤",
            goods_amount=Decimal("49.90"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("49.90"), order_status="PAID",
            created_at="2026-09-11 12:00:00"
        )
        pay2 = PaymentRecord(
            pay_flow_id="PAY_NEW_002", order_id="TB_NEW_002",
            channel="ALIPAY", trans_type="PAY", trans_amount=Decimal("49.90"),
            channel_fee=money(Decimal("49.90") * Decimal("0.006")),
            channel_status="SUCCESS", trans_time="2026-09-11 12:00:00"
        )
        log2 = LogisticsRecord(
            tracking_no="SF_NEW_002", order_id="TB_NEW_002",
            carrier="SF", shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
            freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
            ship_time="2026-09-11 12:00:00", delivery_status="DELIVERED"
        )
        self.db.insert_order(order2)
        self.db.insert_payment(pay2)
        self.db.insert_logistics(log2)

        all_o, all_p, all_l = self.db.load_all_records()
        new_res = self.engine.reconcile(all_o, all_p, all_l)
        # 重新保存差错
        self.db.save_discrepancies(new_res["discrepancies"])

        # 4. 关键断言：TB_PERSIST_001 的已调账状态与凭证必须完好保留！
        with self.db.get_connection() as conn:
            row_after = conn.execute("SELECT * FROM discrepancies WHERE order_id = 'TB_PERSIST_001'").fetchone()
            self.assertEqual(row_after["process_status"], "RESOLVED", "人工调账状态不应被冲掉！")
            self.assertEqual(row_after["resolved_note"], voucher_note)

    def test_csv_import_and_drill_down(self):
        """测试 CSV 文件解析批量入库与三方穿透对比"""
        csv_orders = """order_id,user_id,product_name,goods_amount,shop_discount,platform_coupon,freight_fee,actual_payable,order_status,created_at,goods_cost,refund_amount,refund_time
TB_CSV_001,USR_99,测试导入商品,100.00,10.00,0.00,0.00,90.00,PAID,2026-09-01 10:00:00,35.00,0.00,
"""
        imported_cnt = self.db.import_csv_data("orders", csv_orders)
        self.assertEqual(imported_cnt, 1)

        csv_pays = """pay_flow_id,order_id,channel,trans_type,trans_amount,channel_fee,channel_status,trans_time
PAY_CSV_001,TB_CSV_001,ALIPAY,PAY,90.00,0.54,SUCCESS,2026-09-01 10:00:00
"""
        self.db.import_csv_data("payments", csv_pays)

        drill = self.db.get_order_drill_down("TB_CSV_001")
        self.assertIsNotNone(drill["order"])
        self.assertEqual(drill["order"]["product_name"], "测试导入商品")
        self.assertEqual(len(drill["payments"]), 1)
        self.assertEqual(drill["payments"][0]["pay_flow_id"], "PAY_CSV_001")


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */
