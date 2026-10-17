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

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """获取单个订单记录"""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def insert_order(self, order: OrderRecord, overwrite: bool = True) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        插入或更新订单记录。
        如果订单已存在且 overwrite=False，则拒绝静默覆盖，返回 (False, existing_order_dict)；
        如果允许覆盖或记录为新增，则执行写入并返回 (True, existing_order_dict)。
        """
        with self.get_connection() as conn:
            existing = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order.order_id,)).fetchone()
            if existing and not overwrite:
                return False, dict(existing)

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
            return True, dict(existing) if existing else None

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
        """
        从 CSV 文本批量导入单据数据 (orders, payments, logistics)。
        支持主流电商平台（千牛、抖店、微信商户、支付宝、顺丰、菜鸟）列名别名自动映射与金额千分位清洗。
        """
        valid_types = ["orders", "payments", "logistics"]
        if file_type not in valid_types:
            raise ValueError(f"不支持的单据类型 '{file_type}'，仅支持 orders, payments, logistics")

        # 过滤 UTF-8 BOM
        if csv_content.startswith("\ufeff"):
            csv_content = csv_content[1:]

        reader = csv.DictReader(io.StringIO(csv_content))
        if not reader.fieldnames:
            return 0

        def get_val(row: Dict[str, Any], aliases: List[str], default: Any = None) -> Any:
            for alias in aliases:
                for k, v in row.items():
                    if k and k.strip().lower() == alias.lower():
                        val = str(v).strip()
                        if val != "":
                            return val
            return default

        count = 0
        with self.get_connection() as conn:
            if file_type == "orders":
                aliases_order_id = ["order_id", "订单编号", "主订单编号", "业务单号", "订单号", "商户订单号", "订单ID"]
                aliases_user_id = ["user_id", "买家会员名", "买家昵称", "客户ID", "用户标识", "买家账号", "用户ID"]
                aliases_prod_name = ["product_name", "商品名称", "商品标题", "宝贝标题", "商品描述", "商品"]
                aliases_goods_amt = ["goods_amount", "商品标价总额", "商品总价", "标价", "商品金额", "标价总额"]
                aliases_shop_disc = ["shop_discount", "店铺自主优惠", "店铺优惠", "商家优惠", "店铺减免", "优惠金额"]
                aliases_plat_coupon = ["platform_coupon", "平台跨店满减补贴", "平台优惠", "跨店满减", "平台补贴", "平台券"]
                aliases_freight = ["freight_fee", "运费收取", "运费金额", "运费", "买家付运费"]
                aliases_payable = ["actual_payable", "买家实付应收", "买家实付金额", "买家应付金额", "实付金额", "订单实付金额", "买家实际支付金额"]
                aliases_status = ["order_status", "订单状态", "交易状态", "状态"]
                aliases_cost = ["goods_cost", "商品采购成本", "采购成本", "成本价", "商品成本", "成本"]
                aliases_refund_amt = ["refund_amount", "退款金额", "退款总额", "累计退款", "已退金额"]
                aliases_refund_time = ["refund_time", "退款时间", "退款申请时间", "退款成功时间"]
                aliases_created_at = ["created_at", "下单时间", "创建时间", "交易创建时间", "订单创建时间"]

                for row in reader:
                    oid = get_val(row, aliases_order_id)
                    if not oid:
                        continue
                    prod = get_val(row, aliases_prod_name, "标准电商商品")
                    g_amt = money(get_val(row, aliases_goods_amt, "0.00"))
                    d_amt = money(get_val(row, aliases_shop_disc, "0.00"))
                    c_amt = money(get_val(row, aliases_plat_coupon, "0.00"))
                    f_amt = money(get_val(row, aliases_freight, "0.00"))
                    raw_pay = get_val(row, aliases_payable)
                    if raw_pay is not None:
                        act_pay = money(raw_pay)
                    else:
                        act_pay = money(max(Decimal("0.00"), g_amt - d_amt - c_amt + f_amt))
                    cost = money(get_val(row, aliases_cost, "0.00"))
                    ref_amt = money(get_val(row, aliases_refund_amt, "0.00"))
                    ref_time = get_val(row, aliases_refund_time)
                    stat = get_val(row, aliases_status, "PAID")
                    crt = get_val(row, aliases_created_at, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                    conn.execute("""
                    INSERT OR REPLACE INTO orders (
                        order_id, user_id, product_name, goods_amount, shop_discount,
                        platform_coupon, freight_fee, actual_payable, order_status,
                        goods_cost, refund_amount, refund_time, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        oid, get_val(row, aliases_user_id, "USR_CSV"), prod,
                        str(g_amt), str(d_amt), str(c_amt), str(f_amt), str(act_pay),
                        stat, str(cost), str(ref_amt), ref_time, crt
                    ))
                    count += 1

            elif file_type == "payments":
                aliases_pay_flow = ["pay_flow_id", "商户订单号", "平台流水号", "交易流水号", "渠道流水号", "外部交易号", "支付宝交易号", "微信支付单号", "支付单号", "流水号"]
                aliases_order_id = ["order_id", "业务订单号", "商户业务订单号", "商户订单号", "关联订单号", "订单号", "订单编号"]
                aliases_channel = ["channel", "支付渠道", "交易渠道", "渠道", "收款方式"]
                aliases_trans_type = ["trans_type", "业务类型", "交易类型", "收支类型", "收支类别", "类型"]
                aliases_trans_amount = ["trans_amount", "结算金额", "交易金额", "收支金额", "金额", "入账金额", "实际金额"]
                aliases_channel_fee = ["channel_fee", "渠道手续费", "手续费", "服务费", "平台提成", "费率扣除"]
                aliases_channel_status = ["channel_status", "支付状态", "交易状态", "状态", "结算状态"]
                aliases_trans_time = ["trans_time", "划款时间", "入账时间", "交易时间", "完成时间", "支付时间"]

                for row in reader:
                    flow_id = get_val(row, aliases_pay_flow)
                    oid = get_val(row, aliases_order_id)
                    if not flow_id or not oid:
                        continue
                    t_amt = money(get_val(row, aliases_trans_amount, "0.00"))
                    c_fee = money(get_val(row, aliases_channel_fee, "0.00"))
                    c_stat = get_val(row, aliases_channel_status, "SUCCESS")
                    t_time = get_val(row, aliases_trans_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    conn.execute("""
                    INSERT OR REPLACE INTO payments (
                        pay_flow_id, order_id, channel, trans_type, trans_amount,
                        channel_fee, channel_status, trans_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        flow_id, oid, get_val(row, aliases_channel, "ALIPAY"),
                        get_val(row, aliases_trans_type, "PAY"), str(t_amt),
                        str(c_fee), c_stat, t_time
                    ))
                    count += 1

            elif file_type == "logistics":
                aliases_tracking_no = ["tracking_no", "运单号", "快递单号", "物流单号", "包裹单号", "面单号"]
                aliases_order_id = ["order_id", "关联订单号", "业务单号", "订单号", "商户订单号", "订单编号"]
                aliases_carrier = ["carrier", "承运商", "快递公司", "物流公司", "物流承运商", "承运人"]
                aliases_shipping_fee = ["shipping_fee", "基础运费", "运费", "实收运费", "配送费", "运费金额"]
                aliases_overweight_fee = ["overweight_fee", "超重费", "超重补扣费", "超重运费", "续重运费"]
                aliases_ins_fee = ["freight_insurance_fee", "运费险保费代扣", "运费险保费", "保费", "运费险"]
                aliases_claim_amt = ["insurance_claim_amount", "运费险理赔到账金额", "理赔金额", "赔付金额", "理赔款"]
                aliases_ship_time = ["ship_time", "发货时间", "寄件时间", "交寄时间", "揽件时间"]
                aliases_delivery_status = ["delivery_status", "履约状态", "签收状态", "物流状态", "配送状态"]

                for row in reader:
                    track_no = get_val(row, aliases_tracking_no)
                    oid = get_val(row, aliases_order_id)
                    if not track_no or not oid:
                        continue
                    s_fee = money(get_val(row, aliases_shipping_fee, "4.50"))
                    ow_fee = money(get_val(row, aliases_overweight_fee, "0.00"))
                    i_fee = money(get_val(row, aliases_ins_fee, "0.80"))
                    c_amt = money(get_val(row, aliases_claim_amt, "0.00"))
                    s_time = get_val(row, aliases_ship_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    d_stat = get_val(row, aliases_delivery_status, "DELIVERED")

                    conn.execute("""
                    INSERT OR REPLACE INTO logistics (
                        tracking_no, order_id, carrier, shipping_fee, overweight_fee,
                        freight_insurance_fee, insurance_claim_amount, ship_time, delivery_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        track_no, oid, get_val(row, aliases_carrier, "SF"),
                        str(s_fee), str(ow_fee), str(i_fee), str(c_amt),
                        s_time, d_stat
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
