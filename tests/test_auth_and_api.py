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


if __name__ == "__main__":
    unittest.main()
# /* StarOfCAU-END */
