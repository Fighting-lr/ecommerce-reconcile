# /* StarOfCAU-BEGIN: Unit Tests for Database and Authentication */
"""
数据库持久化、RBAC 用户权限与实时录入单元测试
作者: StarOfCAU (StarOfCAU)
"""

import unittest
import os
import tempfile
from decimal import Decimal
from core.db import DatabaseManager
from core.auth import AuthManager
from core.models import OrderRecord, PaymentRecord, LogisticsRecord


class TestDatabaseAndAuth(unittest.TestCase):
    def setUp(self):
        # 创建隔离的临时测试数据库
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_reconcile.db")
        self.db = DatabaseManager(self.db_path)
        self.auth = AuthManager(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_users_created(self):
        """验证系统初始化时默认三角色账号均已创建并加盐哈希"""
        with self.db.get_connection() as conn:
            users = conn.execute("SELECT username, role, password_hash, salt FROM users").fetchall()
            user_map = {u["username"]: u for u in users}

            self.assertIn("auditor", user_map)
            self.assertIn("operator", user_map)
            self.assertIn("admin", user_map)

            # 验证密码不是明文
            self.assertNotEqual(user_map["auditor"]["password_hash"], "auditor123")
            self.assertTrue(len(user_map["auditor"]["salt"]) > 10)

    def test_authentication_flow(self):
        """测试正常登录与密码错误拦截"""
        # 正常登录
        session = self.auth.authenticate("auditor", "auditor123")
        self.assertIsNotNone(session)
        self.assertEqual(session["role"], "auditor")
        self.assertTrue(session["token"].startswith("TOKEN_"))

        # 密码错误拦截
        wrong_session = self.auth.authenticate("auditor", "wrongpassword")
        self.assertIsNone(wrong_session)

        # 令牌有效性校验
        valid_user = self.auth.verify_token(session["token"])
        self.assertIsNotNone(valid_user)
        self.assertEqual(valid_user["username"], "auditor")

    def test_database_crud_and_transaction(self):
        """测试单据数据的持久化存取与类型精确性"""
        order = OrderRecord(
            order_id="TB_TEST_999", user_id="USR_TEST", product_name="测试大衣",
            goods_amount=Decimal("299.00"), shop_discount=Decimal("20.00"),
            platform_coupon=Decimal("0.00"), freight_fee=Decimal("0.00"),
            actual_payable=Decimal("279.00"), order_status="PAID",
            created_at="2026-10-14 12:00:00"
        )
        self.db.insert_order(order)

        orders, _, _ = self.db.load_all_records()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].order_id, "TB_TEST_999")
        self.assertEqual(orders[0].actual_payable, Decimal("279.00"))

    def test_amount_validation_helper(self):
        """测试 Web 服务层安全金额解析与业务边界防护 (P1 金额防穿透)"""
        from web.server import _parse_amount

        # 正常合法金额与千分位
        self.assertEqual(_parse_amount("1,000.00", "实付金额"), Decimal("1000.00"))
        self.assertEqual(_parse_amount("¥89.90", "实付金额"), Decimal("89.90"))
        self.assertEqual(_parse_amount("", "优惠金额"), Decimal("0.00"))
        self.assertEqual(_parse_amount(None, "优惠金额"), Decimal("0.00"))

        # 负数拦截 (杜绝负数单干崩借贷试算平衡)
        with self.assertRaises(ValueError) as ctx:
            _parse_amount("-50.00", "商品标价")
        self.assertIn("不能为负数", str(ctx.exception))

        # 非法字符拦截 (杜绝 ConversionSyntax 500 异常)
        with self.assertRaises(ValueError) as ctx:
            _parse_amount("abc", "商品标价")
        self.assertIn("格式不合法", str(ctx.exception))

        # 退款上限约束拦截 (退款金额不能超过订单应收)
        with self.assertRaises(ValueError) as ctx:
            _parse_amount("120.00", "退款金额", max_val=Decimal("100.00"))
        self.assertIn("不能大于订单实付金额上限", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */

