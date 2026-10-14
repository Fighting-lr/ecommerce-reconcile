# /* StarOfCAU-BEGIN: SQLite Persistence & Database Engine */
"""
小微电商对账系统 - 数据库持久化层 (SQLite ACID Engine)
作者: StarOfCAU (李锐)
特点: 
1. 原生 SQLite 驱动，单文件持久化，零外部服务端依赖
2. 包含用户鉴权表、订单表、支付表、物流表、审计差异表、操作审计日志表
3. 严格事务保护与 Decimal 字符串定点存储（防止浮点精度丢失）
"""

import sqlite3
import os
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import money, OrderRecord, PaymentRecord, LogisticsRecord, DiscrepancyItem, ReconcileStatus, DiscrepancyType


class DatabaseManager:
    def __init__(self, db_path: str = "data/reconcile.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self):
        """初始化数据库表结构与默认用户"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. 用户表 (RBAC 权限)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL, -- 'admin', 'auditor', 'operator'
                full_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)

            # 2. 业务订单表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                goods_amount TEXT NOT NULL,
                shop_discount TEXT NOT NULL,
                platform_coupon TEXT NOT NULL,
                freight_fee TEXT NOT NULL,
                actual_payable TEXT NOT NULL,
                order_status TEXT NOT NULL,
                refund_amount TEXT NOT NULL DEFAULT '0.00',
                refund_time TEXT,
                created_at TEXT NOT NULL
            )
            """)

            # 3. 支付渠道流水表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                pay_flow_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                trans_type TEXT NOT NULL, -- 'PAY', 'REFUND'
                trans_amount TEXT NOT NULL,
                channel_fee TEXT NOT NULL,
                channel_status TEXT NOT NULL,
                trans_time TEXT NOT NULL
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pay_order ON payments(order_id)")

            # 4. 仓储物流表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS logistics (
                tracking_no TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                carrier TEXT NOT NULL,
                shipping_fee TEXT NOT NULL,
                overweight_fee TEXT NOT NULL,
                freight_insurance_fee TEXT NOT NULL,
                insurance_claim_amount TEXT NOT NULL,
                ship_time TEXT NOT NULL,
                delivery_status TEXT NOT NULL
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_log_order ON logistics(order_id)")

            # 5. 审计差错明细表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS discrepancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                reconcile_status TEXT NOT NULL,
                discrepancy_type TEXT NOT NULL,
                diff_amount TEXT NOT NULL,
                biz_amount TEXT NOT NULL,
                channel_amount TEXT NOT NULL,
                logistics_amount TEXT NOT NULL,
                suspect_reason TEXT NOT NULL,
                accounting_advice TEXT NOT NULL,
                process_status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'RESOLVED'
                detected_at TEXT NOT NULL
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_disc_order ON discrepancies(order_id)")

            # 6. 系统操作审计日志表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)

            conn.commit()

    def record_audit_log(self, operator: str, action: str, detail: str):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO audit_logs (operator, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (operator, action, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()

    def insert_order(self, order: OrderRecord):
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO orders (
                order_id, user_id, product_name, goods_amount, shop_discount,
                platform_coupon, freight_fee, actual_payable, order_status,
                refund_amount, refund_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.order_id, order.user_id, order.product_name,
                str(order.goods_amount), str(order.shop_discount), str(order.platform_coupon),
                str(order.freight_fee), str(order.actual_payable), order.order_status,
                str(order.refund_amount), order.refund_time, order.created_at
            ))
            conn.commit()

    def insert_payment(self, pay: PaymentRecord):
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO payments (
                pay_flow_id, order_id, channel, trans_type, trans_amount,
                channel_fee, channel_status, trans_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pay.pay_flow_id, pay.order_id, pay.channel, pay.trans_type,
                str(pay.trans_amount), str(pay.channel_fee), pay.channel_status, pay.trans_time
            ))
            conn.commit()

    def insert_logistics(self, log: LogisticsRecord):
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO logistics (
                tracking_no, order_id, carrier, shipping_fee, overweight_fee,
                freight_insurance_fee, insurance_claim_amount, ship_time, delivery_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log.tracking_no, log.order_id, log.carrier, str(log.shipping_fee),
                str(log.overweight_fee), str(log.freight_insurance_fee),
                str(log.insurance_claim_amount), log.ship_time, log.delivery_status
            ))
            conn.commit()

    def save_discrepancies(self, discrepancies: List[DiscrepancyItem]):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM discrepancies")  # 刷新本期差错
            for d in discrepancies:
                cursor.execute("""
                INSERT INTO discrepancies (
                    order_id, reconcile_status, discrepancy_type, diff_amount,
                    biz_amount, channel_amount, logistics_amount, suspect_reason,
                    accounting_advice, process_status, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """, (
                    d.order_id, d.reconcile_status.value, d.discrepancy_type.value,
                    str(d.diff_amount), str(d.biz_amount), str(d.channel_amount),
                    str(d.logistics_amount), d.suspect_reason, d.accounting_advice,
                    d.detected_at
                ))
            conn.commit()

    def load_all_records(self) -> (List[OrderRecord], List[PaymentRecord], List[LogisticsRecord]):
        """从 SQLite 全量加载记录用于状态机核算"""
        orders = []
        payments = []
        logistics = []

        with self.get_connection() as conn:
            for r in conn.execute("SELECT * FROM orders").fetchall():
                orders.append(OrderRecord(
                    order_id=r["order_id"],
                    user_id=r["user_id"],
                    product_name=r["product_name"],
                    goods_amount=Decimal(r["goods_amount"]),
                    shop_discount=Decimal(r["shop_discount"]),
                    platform_coupon=Decimal(r["platform_coupon"]),
                    freight_fee=Decimal(r["freight_fee"]),
                    actual_payable=Decimal(r["actual_payable"]),
                    order_status=r["order_status"],
                    refund_amount=Decimal(r["refund_amount"]),
                    refund_time=r["refund_time"],
                    created_at=r["created_at"]
                ))

            for r in conn.execute("SELECT * FROM payments").fetchall():
                payments.append(PaymentRecord(
                    pay_flow_id=r["pay_flow_id"],
                    order_id=r["order_id"],
                    channel=r["channel"],
                    trans_type=r["trans_type"],
                    trans_amount=Decimal(r["trans_amount"]),
                    channel_fee=Decimal(r["channel_fee"]),
                    channel_status=r["channel_status"],
                    trans_time=r["trans_time"]
                ))

            for r in conn.execute("SELECT * FROM logistics").fetchall():
                logistics.append(LogisticsRecord(
                    tracking_no=r["tracking_no"],
                    order_id=r["order_id"],
                    carrier=r["carrier"],
                    shipping_fee=Decimal(r["shipping_fee"]),
                    overweight_fee=Decimal(r["overweight_fee"]),
                    freight_insurance_fee=Decimal(r["freight_insurance_fee"]),
                    insurance_claim_amount=Decimal(r["insurance_claim_amount"]),
                    ship_time=r["ship_time"],
                    delivery_status=r["delivery_status"]
                ))

        return orders, payments, logistics

    def clear_business_data(self):
        """清空业务数据（保留用户与系统配置）"""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM payments")
            conn.execute("DELETE FROM logistics")
            conn.execute("DELETE FROM discrepancies")
            conn.commit()

# /* StarOfCAU-END */
