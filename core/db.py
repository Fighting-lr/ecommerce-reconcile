# /* StarOfCAU-BEGIN: SQLite Persistence & Database Engine */
"""
小微电商对账系统 - 数据库持久化层 (SQLite ACID Engine)
作者: StarOfCAU
特性: 
1. 原生 SQLite 驱动，单文件持久化，零外部服务端依赖
2. 包含用户鉴权表、订单表（支持 COGS 成本）、支付表、物流表、审计差异表（支持调账工作流与凭证号）、操作审计日志表
3. 调账状态持久化合并（UPSERT/MERGE 算法，避免单笔录入冲掉历史调账结果）
4. 支持 CSV 批量账单解析导入与三方订单穿透详情（Drill-Down）检索
"""

import sqlite3
import os
import csv
import io
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from .models import (
    money, OrderRecord, PaymentRecord, LogisticsRecord,
    DiscrepancyItem, ReconcileStatus, DiscrepancyType
)


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
        """初始化数据库表结构并自动执行增量迁移"""
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

            # 2. 业务订单表 (包含 goods_cost 采购成本)
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
                goods_cost TEXT NOT NULL DEFAULT '0.00',
                refund_amount TEXT NOT NULL DEFAULT '0.00',
                refund_time TEXT,
                created_at TEXT NOT NULL
            )
            """)

            # 增量字段迁移: orders.goods_cost
            cursor.execute("PRAGMA table_info(orders)")
            order_cols = [r["name"] for r in cursor.fetchall()]
            if "goods_cost" not in order_cols:
                cursor.execute("ALTER TABLE orders ADD COLUMN goods_cost TEXT NOT NULL DEFAULT '0.00'")

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

            # 5. 审计差错明细表 (支持调账工作流与审计留痕)
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
                resolved_note TEXT,
                resolved_by TEXT,
                resolved_at TEXT,
                detected_at TEXT NOT NULL
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_disc_order ON discrepancies(order_id)")

            # 增量字段迁移: discrepancies 调账扩展字段
            cursor.execute("PRAGMA table_info(discrepancies)")
            disc_cols = [r["name"] for r in cursor.fetchall()]
            if "resolved_note" not in disc_cols:
                cursor.execute("ALTER TABLE discrepancies ADD COLUMN resolved_note TEXT")
            if "resolved_by" not in disc_cols:
                cursor.execute("ALTER TABLE discrepancies ADD COLUMN resolved_by TEXT")
            if "resolved_at" not in disc_cols:
                cursor.execute("ALTER TABLE discrepancies ADD COLUMN resolved_at TEXT")

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

    def get_audit_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def insert_order(self, order: OrderRecord):
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO orders (
                order_id, user_id, product_name, goods_amount, shop_discount,
                platform_coupon, freight_fee, actual_payable, order_status,
                goods_cost, refund_amount, refund_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.order_id, order.user_id, order.product_name,
                str(order.goods_amount), str(order.shop_discount), str(order.platform_coupon),
                str(order.freight_fee), str(order.actual_payable), order.order_status,
                str(order.goods_cost), str(order.refund_amount), order.refund_time, order.created_at
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
        """
        持久化差错列表（智能状态合并机制）：
        绝不粗暴清空全表！保留已有人工标记的 'RESOLVED' 调账状态、调账凭证备注与经办人，
        仅新增新检出的差错或更新现有待调账项目。
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 读取现有差错记录映射
            existing_rows = cursor.execute("SELECT * FROM discrepancies").fetchall()
            existing_map: Dict[Tuple[str, str], Dict[str, Any]] = {
                (r["order_id"], r["discrepancy_type"]): dict(r) for r in existing_rows
            }

            current_keys = set()
            for d in discrepancies:
                key = (d.order_id, d.discrepancy_type.value)
                current_keys.add(key)

                if key in existing_map:
                    # 保留原有的 process_status, resolved_note, resolved_by, resolved_at
                    old = existing_map[key]
                    cursor.execute("""
                    UPDATE discrepancies SET
                        reconcile_status = ?,
                        diff_amount = ?,
                        biz_amount = ?,
                        channel_amount = ?,
                        logistics_amount = ?,
                        suspect_reason = ?,
                        accounting_advice = ?
                    WHERE id = ?
                    """, (
                        d.reconcile_status.value,
                        str(d.diff_amount),
                        str(d.biz_amount),
                        str(d.channel_amount),
                        str(d.logistics_amount),
                        d.suspect_reason,
                        d.accounting_advice,
                        old["id"]
                    ))
                else:
                    # 新发现的差错，插入为 PENDING
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

            # 清理在最新对账中已经完全平账、不复存在的差错
            for key, old in existing_map.items():
                if key not in current_keys:
                    cursor.execute("DELETE FROM discrepancies WHERE id = ?", (old["id"],))

            conn.commit()

    def resolve_discrepancy(self, disc_id: int, operator: str, note: str = ""):
        """标记差错已调账，记录调账说明与经办人"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.get_connection() as conn:
            conn.execute("""
            UPDATE discrepancies 
            SET process_status = 'RESOLVED', resolved_note = ?, resolved_by = ?, resolved_at = ?
            WHERE id = ?
            """, (note or "已出具会计调账凭证并冲销", operator, now_str, disc_id))
            conn.commit()
        self.record_audit_log(operator, "RESOLVE_DISCREPANCY", f"调账审核通过 ID: {disc_id}, 备注: {note}")

    def unresolve_discrepancy(self, disc_id: int, operator: str):
        """撤销调账状态，重新置为 PENDING"""
        with self.get_connection() as conn:
            conn.execute("""
            UPDATE discrepancies 
            SET process_status = 'PENDING', resolved_note = NULL, resolved_by = NULL, resolved_at = NULL
            WHERE id = ?
            """, (disc_id,))
            conn.commit()
        self.record_audit_log(operator, "UNRESOLVE_DISCREPANCY", f"撤销调账状态 ID: {disc_id}")

    def get_order_drill_down(self, order_id: str) -> Dict[str, Any]:
        """三方单据穿透对比详情（Drill-Down 抽屉）"""
        with self.get_connection() as conn:
            order_row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            pay_rows = conn.execute("SELECT * FROM payments WHERE order_id = ? ORDER BY trans_time ASC", (order_id,)).fetchall()
            log_rows = conn.execute("SELECT * FROM logistics WHERE order_id = ? ORDER BY ship_time ASC", (order_id,)).fetchall()
            disc_rows = conn.execute("SELECT * FROM discrepancies WHERE order_id = ?", (order_id,)).fetchall()

            return {
                "order_id": order_id,
                "order": dict(order_row) if order_row else None,
                "payments": [dict(r) for r in pay_rows],
                "logistics": [dict(r) for r in log_rows],
                "discrepancies": [dict(r) for r in disc_rows],
            }

    def load_all_records(self) -> Tuple[List[OrderRecord], List[PaymentRecord], List[LogisticsRecord]]:
        """从 SQLite 全量加载记录用于状态机核算"""
        orders = []
        payments = []
        logistics = []

        with self.get_connection() as conn:
            for r in conn.execute("SELECT * FROM orders").fetchall():
                cost_val = Decimal(r["goods_cost"]) if "goods_cost" in r.keys() and r["goods_cost"] else Decimal("0.00")
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
                    goods_cost=cost_val,
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

    def import_csv_data(self, file_type: str, csv_content: str) -> int:
        """从 CSV 文本批量导入单据数据 (orders, payments, logistics)"""
        reader = csv.DictReader(io.StringIO(csv_content))
        count = 0
        with self.get_connection() as conn:
            if file_type == "orders":
                for row in reader:
                    cost = row.get("goods_cost", "0.00")
                    conn.execute("""
                    INSERT OR REPLACE INTO orders (
                        order_id, user_id, product_name, goods_amount, shop_discount,
                        platform_coupon, freight_fee, actual_payable, order_status,
                        goods_cost, refund_amount, refund_time, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row["order_id"], row.get("user_id", "USR_CSV"), row["product_name"],
                        row["goods_amount"], row.get("shop_discount", "0.00"),
                        row.get("platform_coupon", "0.00"), row.get("freight_fee", "0.00"),
                        row["actual_payable"], row.get("order_status", "PAID"),
                        cost, row.get("refund_amount", "0.00"),
                        row.get("refund_time", None), row.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    ))
                    count += 1

            elif file_type == "payments":
                for row in reader:
                    conn.execute("""
                    INSERT OR REPLACE INTO payments (
                        pay_flow_id, order_id, channel, trans_type, trans_amount,
                        channel_fee, channel_status, trans_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row["pay_flow_id"], row["order_id"], row.get("channel", "ALIPAY"),
                        row.get("trans_type", "PAY"), row["trans_amount"],
                        row.get("channel_fee", "0.00"), row.get("channel_status", "SUCCESS"),
                        row.get("trans_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    ))
                    count += 1

            elif file_type == "logistics":
                for row in reader:
                    conn.execute("""
                    INSERT OR REPLACE INTO logistics (
                        tracking_no, order_id, carrier, shipping_fee, overweight_fee,
                        freight_insurance_fee, insurance_claim_amount, ship_time, delivery_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row["tracking_no"], row["order_id"], row.get("carrier", "SF"),
                        row.get("shipping_fee", "4.50"), row.get("overweight_fee", "0.00"),
                        row.get("freight_insurance_fee", "0.80"), row.get("insurance_claim_amount", "0.00"),
                        row.get("ship_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        row.get("delivery_status", "DELIVERED")
                    ))
                    count += 1

            conn.commit()
        return count

    def clear_business_data(self):
        """清空业务数据（保留用户与系统配置）"""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM payments")
            conn.execute("DELETE FROM logistics")
            conn.execute("DELETE FROM discrepancies")
            conn.commit()

# /* StarOfCAU-END */
