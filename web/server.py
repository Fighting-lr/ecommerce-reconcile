# /* StarOfCAU-BEGIN: Full-Stack Web API Server (Zero-Dependency REST & Static) */
"""
小微电商对账系统 - 交互式 Web API 与静态服务引擎
作者: StarOfCAU (李锐)
特性:
1. 基于 Python 标准库 http.server 原生构建高可用 RESTful API，零第三方框架安装负担
2. 全流程集成 SQLite 持久化、Session RBAC 鉴权与实时对账状态机
3. 支持单笔实时流水录入立即触发对账判定、批量脏数据压测生成、资金调节平衡表计算
"""

import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal
from datetime import datetime
from typing import Any, Optional, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import DatabaseManager
from core.auth import AuthManager
from core.models import money, OrderRecord, PaymentRecord, LogisticsRecord
from core.reconciliation_engine import ReconciliationEngine
from core.accounting_balancer import AccountingBalancer
from core.dirty_data_generator import DirtyDataGenerator


class ReconcileHttpHandler(BaseHTTPRequestHandler):
    db: DatabaseManager = None
    auth: AuthManager = None
    engine: ReconciliationEngine = ReconciliationEngine()
    balancer: AccountingBalancer = AccountingBalancer(Decimal("50000.00"))
    static_dir: str = os.path.join(os.path.dirname(__file__), "static")

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _get_auth_user(self) -> Optional[Dict[str, Any]]:
        auth_header = self.headers.get("Authorization") or self.headers.get("X-Session-Token")
        if not auth_header:
            return None
        token = auth_header.replace("Bearer ", "").strip()
        return self.auth.verify_token(token)

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        return json.loads(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. 登录会话探活
        if path == "/api/me":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未登录或会话已过期"}, 401)
            return self._send_json({"code": 0, "data": user})

        # 2. 对账大盘核心 KPI
        if path == "/api/dashboard":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            orders, payments, logistics = self.db.load_all_records()
            reconcile_res = self.engine.reconcile(orders, payments, logistics)
            balance_res = self.balancer.verify_trial_balance(reconcile_res)

            return self._send_json({
                "code": 0,
                "data": {
                    "summary": reconcile_res["summary"],
                    "balance": balance_res,
                    "db_counts": {
                        "orders": len(orders),
                        "payments": len(payments),
                        "logistics": len(logistics),
                        "discrepancies": len(reconcile_res["discrepancies"])
                    }
                }
            })

        # 3. 审计差错明细清单
        if path == "/api/discrepancies":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            status_filter = query.get("status", [None])[0]
            with self.db.get_connection() as conn:
                if status_filter:
                    rows = conn.execute("SELECT * FROM discrepancies WHERE process_status = ? ORDER BY id DESC", (status_filter,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM discrepancies ORDER BY id DESC").fetchall()

                items = [dict(r) for r in rows]
            return self._send_json({"code": 0, "data": items})

        # 4. 资金流水明细查看
        if path == "/api/transactions":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            limit = int(query.get("limit", [50])[0])
            with self.db.get_connection() as conn:
                orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]
                payments = [dict(r) for r in conn.execute("SELECT * FROM payments ORDER BY trans_time DESC LIMIT ?", (limit,)).fetchall()]
                logistics = [dict(r) for r in conn.execute("SELECT * FROM logistics ORDER BY ship_time DESC LIMIT ?", (limit,)).fetchall()]

            return self._send_json({
                "code": 0,
                "data": {"orders": orders, "payments": payments, "logistics": logistics}
            })

        # 5. 静态文件分发 (前端 SPA)
        if path == "/" or path == "/index.html":
            file_path = os.path.join(self.static_dir, "index.html")
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        self.send_error(404, "File Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        # 1. 用户登录
        if path == "/api/login":
            username = body.get("username", "").strip()
            password = body.get("password", "").strip()
            session = self.auth.authenticate(username, password)
            if not session:
                return self._send_json({"code": 400, "msg": "用户名或密码错误"}, 400)
            return self._send_json({"code": 0, "msg": "登录成功", "data": session})

        # 2. 用户退出
        if path == "/api/logout":
            token = self.headers.get("X-Session-Token", "")
            self.auth.logout(token)
            return self._send_json({"code": 0, "msg": "已退出登录"})

        # 鉴权防护
        user = self._get_auth_user()
        if not user:
            return self._send_json({"code": 401, "msg": "需要登录后访问"}, 401)

        # 3. 核心功能：单笔交易实时录入并触发即时对账
        if path == "/api/submit-single":
            try:
                order_id = body.get("order_id", f"TB_LIVE_{int(datetime.now().timestamp())}")
                user_id = body.get("user_id", "USR_LIVE")
                prod_name = body.get("product_name", "小微电商实测商品")
                goods_amt = money(body.get("goods_amount", "99.00"))
                shop_disc = money(body.get("shop_discount", "0.00"))
                plat_coupon = money(body.get("platform_coupon", "0.00"))
                freight = money(body.get("freight_fee", "0.00"))
                actual_payable = money(goods_amt - shop_disc - plat_coupon + freight)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 构建业务订单
                order = OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=actual_payable, order_status="PAID",
                    created_at=now_str
                )
                self.db.insert_order(order)

                # 构建支付流水
                pay_amt = money(body.get("pay_amount", actual_payable))
                pay_fee = money(body.get("channel_fee", pay_amt * Decimal("0.006")))
                pay = PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id,
                    channel=body.get("channel", "ALIPAY"), trans_type="PAY",
                    trans_amount=pay_amt, channel_fee=pay_fee,
                    channel_status="SUCCESS", trans_time=now_str
                )
                self.db.insert_payment(pay)

                # 构建物流履约
                ship_fee = money(body.get("shipping_fee", "4.50"))
                overweight = money(body.get("overweight_fee", "0.00"))
                ins_fee = money(body.get("freight_insurance_fee", "0.80"))
                ins_claim = money(body.get("insurance_claim_amount", "0.00"))
                log = LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id,
                    carrier=body.get("carrier", "SF"), shipping_fee=ship_fee,
                    overweight_fee=overweight, freight_insurance_fee=ins_fee,
                    insurance_claim_amount=ins_claim, ship_time=now_str,
                    delivery_status="DELIVERED"
                )
                self.db.insert_logistics(log)

                # 立即针对这一笔运行对撞判定
                single_res = self.engine.reconcile([order], [pay], [log])
                discrepancy = single_res["discrepancies"][0] if single_res["discrepancies"] else None

                # 全量重新对账以保持库中差错最新
                all_o, all_p, all_l = self.db.load_all_records()
                all_res = self.engine.reconcile(all_o, all_p, all_l)
                self.db.save_discrepancies(all_res["discrepancies"])

                self.db.record_audit_log(user["username"], "REALTIME_SUBMIT", f"实时录入订单: {order_id}")

                return self._send_json({
                    "code": 0,
                    "msg": "实时录入成功并完成三方对账",
                    "data": {
                        "order_id": order_id,
                        "is_matched": discrepancy is None,
                        "discrepancy": {
                            "status": discrepancy.reconcile_status.value,
                            "type": discrepancy.discrepancy_type.value,
                            "diff_amount": str(discrepancy.diff_amount),
                            "reason": discrepancy.suspect_reason,
                            "advice": discrepancy.accounting_advice
                        } if discrepancy else None
                    }
                })
            except Exception as e:
                return self._send_json({"code": 500, "msg": f"录入失败: {str(e)}"}, 500)

        # 4. 批量生成脏数据压测流水并入库
        if path == "/api/generate-batch":
            count = int(body.get("count", 500))
            generator = DirtyDataGenerator()
            orders, payments, logistics, _ = generator.generate_batch(total_count=count)

            # 清空并批量写入
            self.db.clear_business_data()
            for o in orders:
                self.db.insert_order(o)
            for p in payments:
                self.db.insert_payment(p)
            for l in logistics:
                self.db.insert_logistics(l)

            # 触发全量核算并保存差异
            reconcile_res = self.engine.reconcile(orders, payments, logistics)
            self.db.save_discrepancies(reconcile_res["discrepancies"])

            self.db.record_audit_log(user["username"], "BATCH_GENERATE", f"压测生成 {count} 笔真实脏数据并对账完成")
            return self._send_json({"code": 0, "msg": f"已成功注入 {count} 笔测试流水并完成持久化对账"})

        # 5. 标记差错为已解决/已调账
        if path == "/api/discrepancy/resolve":
            disc_id = body.get("id")
            with self.db.get_connection() as conn:
                conn.execute("UPDATE discrepancies SET process_status = 'RESOLVED' WHERE id = ?", (disc_id,))
                conn.commit()
            self.db.record_audit_log(user["username"], "RESOLVE_DISCREPANCY", f"调账差错记录 ID: {disc_id}")
            return self._send_json({"code": 0, "msg": "已成功更新差错状态为已调账"})

        self.send_error(404, "API Endpoint Not Found")


def run_web_server(port: int = 8088, db_path: str = "data/reconcile.db"):
    db = DatabaseManager(db_path)
    auth = AuthManager(db)

    # 预载一次压测数据，保证开箱即有数据可看
    orders, payments, logistics = db.load_all_records()
    if not orders:
        print("[DB] 检测到数据库为空，正在自动注入初始 300 笔真实业务流水...")
        gen = DirtyDataGenerator()
        o_list, p_list, l_list, _ = gen.generate_batch(total_count=300)
        for o in o_list:
            db.insert_order(o)
        for p in p_list:
            db.insert_payment(p)
        for l in l_list:
            db.insert_logistics(l)
        res = ReconciliationEngine().reconcile(o_list, p_list, l_list)
        db.save_discrepancies(res["discrepancies"])
        print("[DB] 初始流水注入与对账完成！")

    ReconcileHttpHandler.db = db
    ReconcileHttpHandler.auth = auth

    server_address = ("", port)
    httpd = HTTPServer(server_address, ReconcileHttpHandler)
    print(f"\n=======================================================")
    print(f"🚀 企业级全栈对账交互看板已在本地启动!")
    print(f"👉 访问地址: http://localhost:{port}")
    print(f"🔑 默认账号密码:")
    print(f"   - 审计员 (Auditor)  : auditor  / auditor123")
    print(f"   - 运营主管 (Operator): operator / operator123")
    print(f"   - 超级管理员 (Admin): admin    / admin123")
    print(f"=======================================================\n")
    httpd.serve_forever()

# /* StarOfCAU-END */
