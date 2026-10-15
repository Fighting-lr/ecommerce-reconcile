# /* StarOfCAU-BEGIN: Full-Stack Web API Server (Zero-Dependency REST & Static) */
"""
小微电商对账系统 - 交互式 Web API 与静态服务引擎
作者: StarOfCAU
特性:
1. 基于 Python 标准库 http.server 原生构建高可用 RESTful API，零第三方框架负担
2. 全流程集成 SQLite 持久化、Session RBAC 角色鉴权拦截（Admin/Auditor/Operator）与状态机
3. 支持单笔实时流水录入立即触发对账判定、CSV 账单批量上传、穿透对比 (Drill-Down)、审计底稿 CSV 导出与对账规则配置
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
from core.models import (
    money, OrderRecord, PaymentRecord, LogisticsRecord,
    ReconcileConfig, ReconcileStatus, DiscrepancyType
)
from core.reconciliation_engine import ReconciliationEngine
from core.accounting_balancer import AccountingBalancer
from core.dirty_data_generator import DirtyDataGenerator


class ReconcileHttpHandler(BaseHTTPRequestHandler):
    db: DatabaseManager = None
    auth: AuthManager = None
    config: ReconcileConfig = ReconcileConfig()
    engine: ReconciliationEngine = ReconciliationEngine(config=config)
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

    def _send_csv(self, filename: str, csv_content: str):
        # 添加 UTF-8 BOM 以保证 Excel 直接双击打开不乱码
        body = ("\ufeff" + csv_content).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
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

        # 2. 对账大盘核心 KPI (联动人工调账后平账率与待查差错)
        if path == "/api/dashboard":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            orders, payments, logistics = self.db.load_all_records()
            reconcile_res = self.engine.reconcile(orders, payments, logistics)
            self.db.save_discrepancies(reconcile_res["discrepancies"])
            balance_res = self.balancer.verify_trial_balance(reconcile_res)

            with self.db.get_connection() as conn:
                pending_count = conn.execute("SELECT COUNT(*) FROM discrepancies WHERE process_status = 'PENDING'").fetchone()[0]
                resolved_count = conn.execute("SELECT COUNT(*) FROM discrepancies WHERE process_status = 'RESOLVED'").fetchone()[0]

            sum_data = reconcile_res["summary"]
            tot_orders = sum_data["total_orders"]
            auto_matched = sum_data["matched_count"]
            adjusted_match_rate = round(((auto_matched + resolved_count) / tot_orders * 100), 2) if tot_orders > 0 else 0.0

            sum_data["pending_discrepancies"] = pending_count
            sum_data["resolved_discrepancies"] = resolved_count
            sum_data["adjusted_match_rate"] = adjusted_match_rate

            return self._send_json({
                "code": 0,
                "data": {
                    "summary": sum_data,
                    "balance": balance_res,
                    "config": {
                        "cutoff_date": self.config.cutoff_date,
                        "initial_cash_balance": str(self.config.initial_cash_balance),
                        "fee_baseline_rate": str(self.config.fee_baseline_rate),
                        "commission_jump_threshold": str(self.config.commission_jump_threshold),
                        "insurance_claim_standard": str(self.config.insurance_claim_standard),
                        "tolerance": str(self.config.tolerance),
                    },
                    "db_counts": {
                        "orders": len(orders),
                        "payments": len(payments),
                        "logistics": len(logistics),
                        "total_discrepancies": pending_count + resolved_count,
                        "pending_discrepancies": pending_count,
                        "resolved_discrepancies": resolved_count
                    }
                }
            })

        # 3. 审计差错明细清单 (多维筛选与单号搜索)
        if path == "/api/discrepancies":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            status_filter = query.get("status", [None])[0]
            type_filter = query.get("type", [None])[0]
            search_kw = query.get("search", [None])[0]

            sql = "SELECT * FROM discrepancies WHERE 1=1"
            params = []
            if status_filter:
                sql += " AND process_status = ?"
                params.append(status_filter)
            if type_filter:
                sql += " AND discrepancy_type = ?"
                params.append(type_filter)
            if search_kw:
                sql += " AND order_id LIKE ?"
                params.append(f"%{search_kw.strip()}%")

            sql += " ORDER BY id DESC"

            with self.db.get_connection() as conn:
                rows = conn.execute(sql, params).fetchall()
                items = [dict(r) for r in rows]

            return self._send_json({"code": 0, "data": items})

        # 4. 导出审计差错底稿 (CSV / Excel 兼容)
        if path == "/api/discrepancies/export":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            with self.db.get_connection() as conn:
                rows = conn.execute("SELECT * FROM discrepancies ORDER BY id DESC").fetchall()

            csv_lines = [
                "ID,业务订单号,对账状态,差错分类,差异金额,业务应收,渠道实收,物流总费,排查依据,建议会计分录,调账状态,调账说明,经办人,调账时间,检出时间"
            ]
            for r in rows:
                csv_lines.append(
                    f'{r["id"]},"{r["order_id"]}","{r["reconcile_status"]}","{r["discrepancy_type"]}",'
                    f'{r["diff_amount"]},{r["biz_amount"]},{r["channel_amount"]},{r["logistics_amount"]},'
                    f'"{r["suspect_reason"]}","{r["accounting_advice"]}","{r["process_status"]}",'
                    f'"{r["resolved_note"] or ""}","{r["resolved_by"] or ""}","{r["resolved_at"] or ""}","{r["detected_at"]}"'
                )

            filename = f"discrepancy_audit_workpaper_{datetime.now().strftime('%Y%m%d%H%M')}.csv"
            return self._send_csv(filename, "\n".join(csv_lines))

        # 5. 三方单据穿透对比 (Drill-Down 抽屉详情)
        if path == "/api/discrepancy/drill-down":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            order_id = query.get("order_id", [""])[0]
            if not order_id:
                return self._send_json({"code": 400, "msg": "必须提供 order_id"}, 400)

            detail = self.db.get_order_drill_down(order_id)
            return self._send_json({"code": 0, "data": detail})

        # 6. 资金流水明细查看 (三方视图切换、模糊搜索、分页)
        if path == "/api/transactions":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)

            tab = query.get("tab", ["orders"])[0]
            limit = int(query.get("limit", [50])[0])
            page = int(query.get("page", [1])[0])
            search = query.get("search", [""])[0].strip()
            offset = (page - 1) * limit

            with self.db.get_connection() as conn:
                if tab == "orders":
                    cond = "WHERE order_id LIKE ? OR product_name LIKE ?" if search else ""
                    params = [f"%{search}%", f"%{search}%"] if search else []
                    tot = conn.execute(f"SELECT COUNT(*) FROM orders {cond}", params).fetchone()[0]
                    rows = conn.execute(f"SELECT * FROM orders {cond} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
                elif tab == "payments":
                    cond = "WHERE order_id LIKE ? OR pay_flow_id LIKE ?" if search else ""
                    params = [f"%{search}%", f"%{search}%"] if search else []
                    tot = conn.execute(f"SELECT COUNT(*) FROM payments {cond}", params).fetchone()[0]
                    rows = conn.execute(f"SELECT * FROM payments {cond} ORDER BY trans_time DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
                else:  # logistics
                    cond = "WHERE order_id LIKE ? OR tracking_no LIKE ?" if search else ""
                    params = [f"%{search}%", f"%{search}%"] if search else []
                    tot = conn.execute(f"SELECT COUNT(*) FROM logistics {cond}", params).fetchone()[0]
                    rows = conn.execute(f"SELECT * FROM logistics {cond} ORDER BY ship_time DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()

                items = [dict(r) for r in rows]

            return self._send_json({
                "code": 0,
                "data": {
                    "tab": tab,
                    "total": tot,
                    "page": page,
                    "limit": limit,
                    "items": items
                }
            })

        # 7. 系统操作审计日志查询
        if path == "/api/audit-logs":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)
            logs = self.db.get_audit_logs(limit=100)
            return self._send_json({"code": 0, "data": logs})

        # 8. 对账规则参数查询
        if path == "/api/config":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未授权"}, 401)
            return self._send_json({
                "code": 0,
                "data": {
                    "cutoff_date": self.config.cutoff_date,
                    "initial_cash_balance": str(self.config.initial_cash_balance),
                    "fee_baseline_rate": str(self.config.fee_baseline_rate),
                    "commission_jump_threshold": str(self.config.commission_jump_threshold),
                    "insurance_claim_standard": str(self.config.insurance_claim_standard),
                    "tolerance": str(self.config.tolerance),
                }
            })

        # 9. 静态文件分发 (前端 SPA)
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

        # 3. 核心功能：单笔交易实时录入并触发即时对账 (补齐所有财务与履约字段)
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
                goods_cost = money(body.get("goods_cost", "32.00"))
                order_status = body.get("order_status", "PAID")
                refund_amt = money(body.get("refund_amount", "0.00"))
                refund_time = body.get("refund_time", None)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 构建业务订单
                order = OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=actual_payable, order_status=order_status,
                    goods_cost=goods_cost, refund_amount=refund_amt, refund_time=refund_time,
                    created_at=now_str
                )
                self.db.insert_order(order)

                # 构建支付流水
                pay_amt = money(body.get("pay_amount", actual_payable))
                pay_fee = money(body.get("channel_fee", pay_amt * Decimal("0.006")))
                trans_time = body.get("trans_time", now_str)
                pay = PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id,
                    channel=body.get("channel", "ALIPAY"), trans_type="PAY",
                    trans_amount=pay_amt, channel_fee=pay_fee,
                    channel_status="SUCCESS", trans_time=trans_time
                )
                self.db.insert_payment(pay)

                # 构建物流履约
                ship_fee = money(body.get("shipping_fee", "4.50"))
                overweight = money(body.get("overweight_fee", "0.00"))
                ins_fee = money(body.get("freight_insurance_fee", "0.80"))
                ins_claim = money(body.get("insurance_claim_amount", "0.00"))
                delivery_status = body.get("delivery_status", "DELIVERED")
                log = LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id,
                    carrier=body.get("carrier", "SF"), shipping_fee=ship_fee,
                    overweight_fee=overweight, freight_insurance_fee=ins_fee,
                    insurance_claim_amount=ins_claim, ship_time=now_str,
                    delivery_status=delivery_status
                )
                self.db.insert_logistics(log)

                # 运行单笔对账判定
                single_res = self.engine.reconcile([order], [pay], [log])
                discrepancy = single_res["discrepancies"][0] if single_res["discrepancies"] else None

                # 全量重新对账并智能合并差错表（不覆盖已有调账状态）
                all_o, all_p, all_l = self.db.load_all_records()
                all_res = self.engine.reconcile(all_o, all_p, all_l)
                self.db.save_discrepancies(all_res["discrepancies"])

                self.db.record_audit_log(user["username"], "REALTIME_SUBMIT", f"实时录入订单: {order_id}")

                return self._send_json({
                    "code": 0,
                    "msg": "单据已成功入库并完成三方对账判定",
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

        # 4. 批量生成脏数据压测流水 (RBAC: 仅管理员与店长运营可操作)
        if path == "/api/generate-batch":
            if user["role"] == "auditor":
                return self._send_json({"code": 403, "msg": "只读审计员无权执行数据库重构与压测生成操作"}, 403)

            count = int(body.get("count", 500))
            generator = DirtyDataGenerator()
            orders, payments, logistics, _ = generator.generate_batch(total_count=count)

            self.db.clear_business_data()
            for o in orders:
                self.db.insert_order(o)
            for p in payments:
                self.db.insert_payment(p)
            for l in logistics:
                self.db.insert_logistics(l)

            # 触发全量核算并持久化差异
            reconcile_res = self.engine.reconcile(orders, payments, logistics)
            self.db.save_discrepancies(reconcile_res["discrepancies"])

            self.db.record_audit_log(user["username"], "BATCH_GENERATE", f"压测生成 {count} 笔真实脏数据并完成全量对账")
            return self._send_json({"code": 0, "msg": f"已成功注入 {count} 笔测试流水并完成持久化对账"})

        # 5. 标为已调账 (支持填写调账备注与处理凭证)
        if path == "/api/discrepancy/resolve":
            disc_id = body.get("id")
            note = body.get("note", "").strip() or "已核对原始凭证并完成账务调账"
            self.db.resolve_discrepancy(disc_id, user["full_name"], note)
            return self._send_json({"code": 0, "msg": "已成功记录调账凭证并更新为已调账"})

        # 6. 撤销调账状态
        if path == "/api/discrepancy/unresolve":
            disc_id = body.get("id")
            self.db.unresolve_discrepancy(disc_id, user["full_name"])
            return self._send_json({"code": 0, "msg": "已撤销调账状态"})

        # 7. CSV 账单批量上传解析导入
        if path == "/api/upload-csv":
            file_type = body.get("file_type")  # "orders", "payments", "logistics"
            content = body.get("content", "")
            if not file_type or not content:
                return self._send_json({"code": 400, "msg": "必须指定 file_type 与 content"}, 400)

            try:
                imported_cnt = self.db.import_csv_data(file_type, content)
                # 重新计算对账
                orders, payments, logistics = self.db.load_all_records()
                all_res = self.engine.reconcile(orders, payments, logistics)
                self.db.save_discrepancies(all_res["discrepancies"])

                self.db.record_audit_log(user["username"], "CSV_UPLOAD", f"上传并解析 {file_type}.csv 共 {imported_cnt} 行")
                return self._send_json({"code": 0, "msg": f"成功解析并导入 {imported_cnt} 笔 {file_type} 数据！"})
            except Exception as e:
                return self._send_json({"code": 500, "msg": f"CSV 解析导入失败: {str(e)}"}, 500)

        # 8. 对账规则与参数动态配置
        if path == "/api/config":
            if user["role"] not in ["admin", "auditor"]:
                return self._send_json({"code": 403, "msg": "仅主审审计师或超级管理员有权修改对账规则参数"}, 403)

            cutoff_date = body.get("cutoff_date")
            initial_cash = body.get("initial_cash_balance")
            fee_rate = body.get("fee_baseline_rate")
            jump_threshold = body.get("commission_jump_threshold")
            claim_std = body.get("insurance_claim_standard")
            tol = body.get("tolerance")

            if cutoff_date is not None:
                self.config.cutoff_date = cutoff_date.strip() if cutoff_date.strip() else None
            if initial_cash is not None:
                self.config.initial_cash_balance = Decimal(str(initial_cash))
                self.balancer.initial_cash = self.config.initial_cash_balance
            if fee_rate is not None:
                self.config.fee_baseline_rate = Decimal(str(fee_rate))
            if jump_threshold is not None:
                self.config.commission_jump_threshold = Decimal(str(jump_threshold))
            if claim_std is not None:
                self.config.insurance_claim_standard = Decimal(str(claim_std))
            if tol is not None:
                self.config.tolerance = Decimal(str(tol))

            self.engine.config = self.config

            # 重新跑对账
            orders, payments, logistics = self.db.load_all_records()
            all_res = self.engine.reconcile(orders, payments, logistics)
            self.db.save_discrepancies(all_res["discrepancies"])

            self.db.record_audit_log(user["username"], "CONFIG_UPDATE", f"更新对账规则参数: cutoff={self.config.cutoff_date}")
            return self._send_json({"code": 0, "msg": "对账规则参数已更新并完成试算平衡重新对账！"})

        # 9. 清空数据库 (RBAC: 仅管理员有权操作)
        if path == "/api/clear-data":
            if user["role"] != "admin":
                return self._send_json({"code": 403, "msg": "清空数据库属于高危操作，仅超级管理员拥有此权限"}, 403)
            self.db.clear_business_data()
            self.db.record_audit_log(user["username"], "CLEAR_DATA", "清空业务交易流水数据")
            return self._send_json({"code": 0, "msg": "业务数据库已成功清空"})

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
    print(f"🚀 企业级全栈对账交互工作台 v2.1 已在本地启动!")
    print(f"👉 访问地址: http://localhost:{port}")
    print(f"🔑 默认账号密码:")
    print(f"   - 审计员 (Auditor)  : auditor  / auditor123")
    print(f"   - 运营主管 (Operator): operator / operator123")
    print(f"   - 超级管理员 (Admin): admin    / admin123")
    print(f"=======================================================\n")
    httpd.serve_forever()

# /* StarOfCAU-END */
