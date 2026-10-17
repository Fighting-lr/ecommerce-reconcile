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


def _parse_amount(raw_val: Any, field_name: str, allow_negative: bool = False, max_val: Optional[Decimal] = None) -> Decimal:
    """安全解析金额并执行业务边界校验"""
    if raw_val is None or str(raw_val).strip() == "":
        return Decimal("0.00")
    try:
        amt = money(raw_val)
    except Exception:
        raise ValueError(f"【{field_name}】格式不合法，请输入标准有效数字（支持千分位格式如 1,000.00）")
    if not allow_negative and amt < Decimal("0.00"):
        raise ValueError(f"【{field_name}】不能为负数（输入值为: ¥{amt}），若为退款请在专门的退款字段中填写正数金额")
    if max_val is not None and amt > max_val:
        raise ValueError(f"【{field_name}】(¥{amt}) 不能大于订单实付金额上限 (¥{max_val})")
    return amt


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

        # 0. 标准 CSV 账单模板下载
        if path == "/api/templates/download":
            tmpl_type = query.get("type", ["orders"])[0]
            if tmpl_type == "orders":
                content = "订单编号,买家会员名,商品名称,商品标价总额,店铺自主优惠,平台跨店满减补贴,运费收取,买家实付金额,订单状态,商品采购成本,退款金额,退款申请时间,下单时间\nTB20261000001,USR_1001,冬季法兰绒加厚家居服,89.90,5.00,0.00,0.00,84.90,PAID,32.00,0.00,,2026-10-01 10:00:00\n"
                return self._send_csv("orders_template.csv", content)
            elif tmpl_type == "payments":
                content = "商户订单号,业务订单号,支付渠道,交易类型,结算金额,渠道手续费,支付状态,划款时间\nPAY_TB20261000001,TB20261000001,ALIPAY,PAY,84.90,0.51,SUCCESS,2026-10-01 10:00:00\n"
                return self._send_csv("payments_template.csv", content)
            elif tmpl_type == "logistics":
                content = "运单号,关联订单号,承运商,基础运费,超重补扣费,运费险保费代扣,运费险理赔到账金额,发货时间,履约状态\nSF_TB20261000001,TB20261000001,SF,4.50,0.00,0.80,0.00,2026-10-01 10:00:00,DELIVERED\n"
                return self._send_csv("logistics_template.csv", content)
            else:
                return self._send_json({"code": 400, "msg": "不支持的模板类型，仅支持 orders, payments, logistics"}, 400)

        # 1. 登录会话探活
        if path == "/api/me":
            user = self._get_auth_user()
            if not user:
                return self._send_json({"code": 401, "msg": "未登录或会话已过期"}, 401)
            return self._send_json({"code": 0, "data": user})

        # 2. 对账大盘核心 KPI (联动人工调账后平账率、待查差错与演示数据标记)
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
                # 检测当前是否为包含演示压测数据的环境 (以 TB202609 前缀判定)
                sample = conn.execute("SELECT order_id FROM orders LIMIT 20").fetchall()
                is_demo_data = bool(sample and any(r["order_id"].startswith("TB202609") for r in sample))

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
                    "is_demo_data": is_demo_data,
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

        # 3. 核心功能：单笔交易实时录入并触发即时对账 (安全金额校验与防静默覆盖)
        if path == "/api/submit-single":
            try:
                order_id = str(body.get("order_id", f"TB_LIVE_{int(datetime.now().timestamp())}")).strip()
                if not order_id:
                    return self._send_json({"code": 400, "msg": "业务订单号不能为空"}, 400)

                allow_overwrite = bool(body.get("allow_overwrite", False))
                existing_order = self.db.get_order(order_id)
                if existing_order and not allow_overwrite:
                    return self._send_json({
                        "code": 409,
                        "msg": f"业务订单号【{order_id}】已存在于数据库中（原商品: {existing_order['product_name']}，原买家实付: ¥{existing_order['actual_payable']}）。请确认是否要覆盖已有单据？",
                        "data": {
                            "exists": True,
                            "existing_order": {
                                "order_id": existing_order["order_id"],
                                "product_name": existing_order["product_name"],
                                "actual_payable": existing_order["actual_payable"],
                                "order_status": existing_order["order_status"],
                                "created_at": existing_order["created_at"]
                            }
                        }
                    }, 409)

                user_id = str(body.get("user_id", "USR_LIVE")).strip() or "USR_LIVE"
                prod_name = str(body.get("product_name", "小微电商实测商品")).strip() or "小微电商实测商品"

                # 严格安全金额校验（支持千分位，杜绝负数干崩借贷平衡）
                goods_amt = _parse_amount(body.get("goods_amount", "99.00"), "商品标价总额")
                shop_disc = _parse_amount(body.get("shop_discount", "0.00"), "店铺自主优惠")
                plat_coupon = _parse_amount(body.get("platform_coupon", "0.00"), "平台跨店满减补贴")
                freight = _parse_amount(body.get("freight_fee", "0.00"), "运费收取")
                goods_cost = _parse_amount(body.get("goods_cost", "32.00"), "商品采购成本")

                actual_payable = money(max(Decimal("0.00"), goods_amt - shop_disc - plat_coupon + freight))
                order_status = str(body.get("order_status", "PAID")).strip() or "PAID"
                refund_amt = _parse_amount(body.get("refund_amount", "0.00"), "退款金额", max_val=actual_payable)
                refund_time = str(body.get("refund_time", "")).strip() or None
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 构建业务订单
                order = OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=actual_payable, order_status=order_status,
                    goods_cost=goods_cost, refund_amount=refund_amt, refund_time=refund_time,
                    created_at=now_str
                )
                self.db.insert_order(order, overwrite=True)

                # 构建支付流水
                pay_amt = _parse_amount(body.get("pay_amount", actual_payable), "渠道实际入账扣款金额")
                pay_fee = _parse_amount(body.get("channel_fee", pay_amt * Decimal("0.006")), "渠道手续费/平台佣金")
                trans_time = str(body.get("trans_time", "")).strip() or now_str
                pay = PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id,
                    channel=str(body.get("channel", "ALIPAY")).strip() or "ALIPAY", trans_type="PAY",
                    trans_amount=pay_amt, channel_fee=pay_fee,
                    channel_status="SUCCESS", trans_time=trans_time
                )
                self.db.insert_payment(pay)

                # 构建物流履约
                ship_fee = _parse_amount(body.get("shipping_fee", "4.50"), "基础运费")
                overweight = _parse_amount(body.get("overweight_fee", "0.00"), "超重补扣费")
                ins_fee = _parse_amount(body.get("freight_insurance_fee", "0.80"), "运费险保费代扣")
                ins_claim = _parse_amount(body.get("insurance_claim_amount", "0.00"), "运费险理赔到账金额")
                delivery_status = str(body.get("delivery_status", "DELIVERED")).strip() or "DELIVERED"
                log = LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id,
                    carrier=str(body.get("carrier", "SF")).strip() or "SF", shipping_fee=ship_fee,
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

                if existing_order:
                    self.db.record_audit_log(user["username"], "OVERWRITE_ORDER", f"覆盖已存在订单: {order_id} (原买家实付 ¥{existing_order['actual_payable']} -> 新买家实付 ¥{actual_payable})")
                else:
                    self.db.record_audit_log(user["username"], "REALTIME_SUBMIT", f"实时录入新订单: {order_id} (金额: ¥{actual_payable})")

                return self._send_json({
                    "code": 0,
                    "msg": "单据已成功入库并完成三方对账判定" + ("（已覆盖旧单）" if existing_order else ""),
                    "data": {
                        "order_id": order_id,
                        "is_overwritten": bool(existing_order),
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
            except ValueError as ve:
                return self._send_json({"code": 400, "msg": str(ve)}, 400)
            except Exception as e:
                return self._send_json({"code": 500, "msg": f"录入失败: {str(e)}"}, 500)

        # 4. 批量生成脏数据压测流水 (RBAC: 仅超级管理员 Admin 可操作，店长与审计员严密拦截)
        if path == "/api/generate-batch":
            if user["role"] != "admin":
                return self._send_json({
                    "code": 403,
                    "msg": "高危操作拦截：批量注入压测数据将覆盖清空全库真实单据，仅超级管理员 (Admin) 允许操作。店长运营 (Operator) 与审计员无权执行此操作。"
                }, 403)

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
            if file_type not in ["orders", "payments", "logistics"]:
                return self._send_json({"code": 400, "msg": f"不支持的单据类型: '{file_type}'。系统仅支持 orders, payments, logistics"}, 400)

            content = body.get("content", "")
            if not content:
                return self._send_json({"code": 400, "msg": "CSV 账单文件内容不能为空"}, 400)

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
    print(f"🚀 企业级全栈对账交互工作台 v1.0 已在本地启动!")
    print(f"👉 访问地址: http://localhost:{port}")
    print(f"🔑 默认账号密码:")
    print(f"   - 审计员 (Auditor)  : auditor  / auditor123")
    print(f"   - 运营主管 (Operator): operator / operator123")
    print(f"   - 超级管理员 (Admin): admin    / admin123")
    print(f"=======================================================\n")
    httpd.serve_forever()

# /* StarOfCAU-END */
