# /* StarOfCAU-BEGIN: Unit Tests for Accounting Balancer */
"""
财会复式记账试算平衡单元测试
作者: StarOfCAU
"""

import unittest
from decimal import Decimal
from core.dirty_data_generator import DirtyDataGenerator
from core.reconciliation_engine import ReconciliationEngine
from core.accounting_balancer import AccountingBalancer


class TestAccountingBalancer(unittest.TestCase):
    def setUp(self):
        self.generator = DirtyDataGenerator(seed=42)
        self.engine = ReconciliationEngine()
        self.balancer = AccountingBalancer(initial_cash_balance=Decimal("50000.00"))

    def test_trial_balance_exact_zero_difference(self):
        """测试 500 笔复杂压测流水下，借贷试算平衡必须严格对齐到分 (0.00)"""
        orders, payments, logistics, _ = self.generator.generate_batch(total_count=500)
        reconcile_res = self.engine.reconcile(orders, payments, logistics)
        balance_res = self.balancer.verify_trial_balance(reconcile_res)

        self.assertTrue(balance_res["is_balanced"], "试算平衡必须闭合")
        self.assertEqual(balance_res["unexplained_diff"], Decimal("0.00"), "未解释差异必须为绝对 0.00 元")

    def test_gross_profit_and_erosion(self):
        """测试毛利计算逻辑（纳入 COGS 采购成本）与隐藏损益侵蚀率测算"""
        orders, payments, logistics, _ = self.generator.generate_batch(total_count=100)
        reconcile_res = self.engine.reconcile(orders, payments, logistics)
        balance_res = self.balancer.verify_trial_balance(reconcile_res)

        # 验证毛利 = 渠道流水 - 采购成本(COGS) - 渠道手续费 - 物流履约支出
        expected_profit = (
            balance_res["channel_turnover"] -
            balance_res["total_cogs"] -
            balance_res["channel_fees"] -
            balance_res["shipping_costs"]
        )
        self.assertEqual(balance_res["gross_profit"], expected_profit)
        # 验证计入 COGS 后的真实毛利率远低于未计成本时的 96%
        self.assertGreater(balance_res["actual_profit_margin"], 0)
        self.assertLess(balance_res["actual_profit_margin"], 70)


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */
