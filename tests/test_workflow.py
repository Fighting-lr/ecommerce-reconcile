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

    def test_duplicate_order_collision_protection(self):
        """测试单号冲突防静默覆盖机制 (P0 核心安全保障)"""
        order1 = OrderRecord(
            order_id="TB_DUP_001", user_id="USR_01", product_name="初次录入大衣",
            goods_amount=Decimal("500.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("500.00"), order_status="PAID",
            created_at="2026-10-01 10:00:00"
        )
        # 首次录入成功
        success, existing = self.db.insert_order(order1, overwrite=False)
        self.assertTrue(success)
        self.assertIsNone(existing)

        # 二次录入相同单号且不允许覆盖，应拒绝写入并返回已有单据快照
        order2 = OrderRecord(
            order_id="TB_DUP_001", user_id="USR_01", product_name="二次篡改单据",
            goods_amount=Decimal("50.00"), shop_discount=Decimal("0.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("50.00"), order_status="PAID",
            created_at="2026-10-01 11:00:00"
        )
        success2, existing2 = self.db.insert_order(order2, overwrite=False)
        self.assertFalse(success2, "单号冲突且 overwrite=False 时应明确拒绝写入")
        self.assertIsNotNone(existing2)
        self.assertEqual(existing2["actual_payable"], "500.00")
        self.assertEqual(existing2["product_name"], "初次录入大衣")

        # 查库验证原始 500 元未被静默抹掉
        stored = self.db.get_order("TB_DUP_001")
        self.assertEqual(stored["actual_payable"], "500.00")

        # 当用户明确在弹窗中确认覆盖时 (overwrite=True)，允许写入并返回旧数据
        success3, existing3 = self.db.insert_order(order2, overwrite=True)
        self.assertTrue(success3)
        self.assertEqual(existing3["actual_payable"], "500.00")
        stored_after = self.db.get_order("TB_DUP_001")
        self.assertEqual(stored_after["actual_payable"], "50.00")

    def test_money_parsing_and_thousands_separators(self):
        """测试千分位逗号、货币符号兼容与非法金额校验 (P1 金额安全)"""
        # 千分位解析
        self.assertEqual(money("1,000.50"), Decimal("1000.50"))
        self.assertEqual(money("12,345,678.90"), Decimal("12345678.90"))
        # 货币符号剥离
        self.assertEqual(money("¥1,000.00"), Decimal("1000.00"))
        self.assertEqual(money("$99.99"), Decimal("99.99"))
        self.assertEqual(money("￥ 520.00"), Decimal("520.00"))
        # 非法字符抛出用户友好异常
        with self.assertRaises(ValueError) as ctx:
            money("abc")
        self.assertIn("无法将 'abc' 解析为有效财务金额", str(ctx.exception))

    def test_chinese_platform_headers_csv_import(self):
        """测试主流电商与支付平台中文列名别名自动映射与千分位清洗导入 (P1 CSV 体验)"""
        # 千牛/抖店订单导出常用列名
        csv_orders_cn = """订单编号,买家会员名,商品名称,商品标价总额,店铺自主优惠,平台跨店满减补贴,运费收取,买家实付金额,订单状态,商品采购成本
TB_CN_001,tb_buyer_88,冬季羊绒围巾,"1,299.00",99.00,0.00,0.00,"1,200.00",PAID,380.00
"""
        count_o = self.db.import_csv_data("orders", csv_orders_cn)
        self.assertEqual(count_o, 1)

        # 支付宝/微信商户导出常用列名
        csv_pays_cn = """商户订单号,业务订单号,支付渠道,交易类型,结算金额,渠道手续费,支付状态
PAY_CN_001,TB_CN_001,支付宝,PAY,"1,200.00",7.20,SUCCESS
"""
        count_p = self.db.import_csv_data("payments", csv_pays_cn)
        self.assertEqual(count_p, 1)

        # 顺丰/菜鸟物流导出常用列名
        csv_logistics_cn = """运单号,关联订单号,承运商,基础运费,超重补扣费,运费险保费代扣,运费险理赔到账金额,履约状态
SF_CN_001,TB_CN_001,顺丰速运,4.50,0.00,0.80,0.00,正常签收
"""
        count_l = self.db.import_csv_data("logistics", csv_logistics_cn)
        self.assertEqual(count_l, 1)

        # 校验入库数据精度与别名映射完整性
        drill = self.db.get_order_drill_down("TB_CN_001")
        self.assertEqual(drill["order"]["product_name"], "冬季羊绒围巾")
        self.assertEqual(drill["order"]["actual_payable"], "1200.00")
        self.assertEqual(drill["payments"][0]["trans_amount"], "1200.00")
        self.assertEqual(drill["payments"][0]["channel_fee"], "7.20")
        self.assertEqual(drill["logistics"][0]["shipping_fee"], "4.50")

    def test_illegal_file_type_csv_import_rejected(self):
        """测试非法 file_type 传入时严格拒绝，杜绝假成功 (P1 安全)"""
        with self.assertRaises(ValueError) as ctx:
            self.db.import_csv_data("hack", "col1,col2\n1,2\n")
        self.assertIn("不支持的单据类型 'hack'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */

