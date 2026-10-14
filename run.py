#!/usr/bin/env python3
# /* StarOfCAU-BEGIN: Main Execution Entrypoint */
"""
小微电商多渠道资金对账与年审异常核算系统 - 一键启动入口
作者: StarOfCAU (李锐)
特性: 
1. 支持 CLI 极速对账流水分析与 A4 报告导出
2. 支持 --web 或 --serve 启动企业级交互式全栈 Web 工作台 (SQLite + Auth + 实时录入)
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    DirtyDataGenerator,
    ReconciliationEngine,
    AccountingBalancer,
    AuditReporter
)
from core.db import DatabaseManager
from web.server import run_web_server


def run_pipeline(total_count: int = 1000, data_dir: str = "data") -> str:
    print("=" * 70)
    print("🚀 启动小微电商多渠道资金对账与年审核算系统 (StarOfCAU)")
    print("=" * 70)

    # 1. 业务脏数据注入与账单导出
    print(f"\n[Step 1/4] 注入电商 12 种真实业务异常，生成 {total_count} 笔三方交易流水...")
    generator = DirtyDataGenerator()
    orders, payments, logistics, ground_truth = generator.generate_batch(total_count=total_count)
    
    # 同时持久化到 CSV 和 SQLite 数据库
    generator.export_csv(orders, payments, logistics, output_dir=data_dir)
    db_path = os.path.join(data_dir, "reconcile.db")
    db = DatabaseManager(db_path)
    from core.auth import AuthManager
    AuthManager(db)
    db.clear_business_data()
    for o in orders:
        db.insert_order(o)
    for p in payments:
        db.insert_payment(p)
    for l in logistics:
        db.insert_logistics(l)

    print(f"  ✓ 业务订单表 (orders.csv & SQLite)    : {len(orders)} 笔")
    print(f"  ✓ 支付渠道表 (payments.csv & SQLite)  : {len(payments)} 笔 (含退款流水)")
    print(f"  ✓ 履约物流表 (logistics.csv & SQLite) : {len(logistics)} 笔 (含多次补发)")

    # 2. 三方核对状态机执行
    print("\n[Step 2/4] 执行三方复合对撞（业务系统 vs 收单渠道 vs 仓储物流）...")
    engine = ReconciliationEngine()
    reconcile_result = engine.reconcile(orders, payments, logistics)
    summary = reconcile_result["summary"]
    
    # 差错明细持久化到 SQLite
    db.save_discrepancies(reconcile_result["discrepancies"])

    print(f"  ✓ 对账总规模     : {summary['total_orders']} 笔")
    print(f"  ✓ 自动平账笔数   : {summary['matched_count']} 笔 (平账率: {summary['matched_rate']}%)")
    print(f"  ✓ 检出异常笔数   : {summary['discrepancy_count']} 笔")

    # 3. 财会试算平衡校验与毛利侵蚀分析
    print("\n[Step 3/4] 财会老法师复式记账试算平衡自检与真实毛利测算...")
    balancer = AccountingBalancer(initial_cash_balance=50000.00)
    balance_result = balancer.verify_trial_balance(reconcile_result)
    
    status_text = "【通过】借贷平衡已闭合" if balance_result["is_balanced"] else "【异常】存在未对齐尾差"
    print(f"  ✓ 试算平衡状态   : {status_text}")
    print(f"  ✓ 业务应收总额   : ¥{summary['total_biz_turnover']}")
    print(f"  ✓ 渠道实收净额   : ¥{summary['total_channel_turnover']}")
    print(f"  ✓ 真实经营毛利   : ¥{balance_result['gross_profit']} (真实毛利率: {balance_result['actual_profit_margin']}%)")
    print(f"  ✓ 隐藏损益侵蚀   : ¥{balance_result['total_hidden_loss']} (侵蚀率: {balance_result['erosion_rate']}%)")

    # 4. 渲染导出审计报告
    print("\n[Step 4/4] 自动生成像素级《资金全链路对账与年审异常审计底稿》...")
    report_path = os.path.join(data_dir, "reports", "audit_reconciliation_report.html")
    AuditReporter.generate_html_report(reconcile_result, balance_result, report_path)
    abs_report_path = os.path.abspath(report_path)
    print(f"  ✓ 审计报告生成完毕: {abs_report_path}")
    print("=" * 70)

    return abs_report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电商资金对账与异常核算系统")
    parser.add_argument("--count", type=int, default=1000, help="压测流水笔数 (默认 1000)")
    parser.add_argument("--data-dir", type=str, default="data", help="数据与报告输出目录")
    parser.add_argument("--web", "--serve", dest="serve", action="store_true", help="启动交互式 Web 工作台 (登录/实时录入/SQLite/看板)")
    parser.add_argument("--port", type=int, default=8088, help="Web 看板端口 (默认 8088)")

    args = parser.parse_args()

    if args.serve:
        db_file = os.path.join(args.data_dir, "reconcile.db")
        run_web_server(port=args.port, db_path=db_file)
    else:
        report_file = run_pipeline(total_count=args.count, data_dir=args.data_dir)
        print(f"\n💡 提示: 若要启动带【登录鉴权、SQLite 数据库、实时单据录入、交互式大盘】的完整 Web 工作台，请运行:")
        print(f"   python run.py --web --port {args.port}\n")

# /* StarOfCAU-END */
