# /* StarOfCAU-BEGIN: Printable HTML & Markdown Audit Report Generator */
"""
审计底稿与电商资金对账报告渲染器
作者: StarOfCAU (李锐)
特点: 
1. 纯原生 Python 字符串模板渲染（零外部模板引擎依赖）
2. 内置像素级响应式 CSS 与 @media print 打印样式（一键另存为 A4 审计底稿 PDF）
3. 严格对标事务所年审底稿标准与大厂 Sales Ops 经营分析报表
"""

import os
from typing import Dict, Any, List
from .models import DiscrepancyItem


class AuditReporter:
    @staticmethod
    def generate_html_report(
        reconcile_result: Dict[str, Any],
        balance_result: Dict[str, Any],
        output_file: str
    ) -> str:
        summary = reconcile_result["summary"]
        discrepancies: List[DiscrepancyItem] = reconcile_result["discrepancies"]

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

        rows_html = []
        for idx, d in enumerate(discrepancies, 1):
            badge_class = "badge-danger" if "UNILATERAL" in d.reconcile_status.value else "badge-warning"
            rows_html.append(f"""
            <tr>
                <td style="text-align: center; color: #64748b;">{idx}</td>
                <td><code>{d.order_id}</code></td>
                <td><span class="badge {badge_class}">{d.discrepancy_type.value}</span></td>
                <td style="text-align: right; font-weight: 600; color: #e11d48;">¥{d.diff_amount}</td>
                <td style="font-size: 13px; color: #334155;">{d.suspect_reason}</td>
                <td style="font-size: 12px; font-family: monospace; background: #f8fafc; color: #0369a1; padding: 6px 10px; border-radius: 4px;">{d.accounting_advice}</td>
            </tr>
            """)

        table_rows_str = "\n".join(rows_html)

        type_breakdown_html = []
        for k, v in balance_result["type_summary"].items():
            type_breakdown_html.append(f"""
            <div class="stat-card">
                <div class="stat-title">{k}</div>
                <div class="stat-val" style="color: #e11d48;">¥{v}</div>
            </div>
            """)
        type_breakdown_str = "\n".join(type_breakdown_html)

        balance_badge = '<span class="badge badge-success">✓ 试算平衡已达成 (借贷相符)</span>' if balance_result["is_balanced"] else '<span class="badge badge-danger">✗ 试算未平 (存在未知差异)</span>'

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小微电商多渠道资金对账与年审异常审计报告</title>
    <style>
        :root {{
            --primary: #0284c7;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --text-main: #0f172a;
            --bg-gray: #f8fafc;
            --border: #e2e8f0;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background: #f1f5f9;
            color: var(--text-main);
            padding: 24px;
            font-size: 14px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
            padding: 32px;
        }}
        .header {{
            border-bottom: 2px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
        }}
        .header-title h1 {{ font-size: 24px; color: var(--text-main); font-weight: 700; margin-bottom: 6px; }}
        .header-title p {{ font-size: 13px; color: #64748b; }}
        .header-meta {{ text-align: right; font-size: 13px; color: #475569; }}
        
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }}
        .kpi-card {{
            background: var(--bg-gray);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}
        .kpi-label {{ font-size: 13px; color: #64748b; margin-bottom: 4px; }}
        .kpi-value {{ font-size: 24px; font-weight: 700; color: var(--primary); }}
        .kpi-sub {{ font-size: 12px; color: #94a3b8; margin-top: 4px; }}
        
        .grid-2 {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }}
        .panel {{
            background: #ffffff;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
        }}
        .panel h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 14px; color: var(--text-main); }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: #f8fafc; font-weight: 600; color: #475569; font-size: 13px; }}
        tr:hover {{ background: #fdfefe; }}
        
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            font-size: 12px;
            font-weight: 500;
            border-radius: 4px;
        }}
        .badge-success {{ background: #ecfdf5; color: #047857; }}
        .badge-warning {{ background: #fffbeb; color: #b45309; }}
        .badge-danger {{ background: #fef2f2; color: #b91c1c; }}

        .stat-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
        .stat-card {{ background: #f8fafc; padding: 12px; border-radius: 6px; border-left: 3px solid var(--primary); }}
        .stat-title {{ font-size: 12px; color: #64748b; }}
        .stat-val {{ font-size: 16px; font-weight: 700; margin-top: 2px; }}

        @media print {{
            body {{ background: #fff; padding: 0; }}
            .container {{ box-shadow: none; padding: 0; }}
            tr {{ page-break-inside: avoid; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">
                <h1>小微电商资金全链路对账与年审异常审计报告</h1>
                <p>涵盖业务订单中心 (Orders) · 支付渠道收单流水 (Payments) · 仓储物流与运费险履约 (Logistics)</p>
            </div>
            <div class="header-meta">
                <div>编制团队: <strong>StarOfCAU (李锐)</strong></div>
                <div>核算基准: <strong>《企业会计准则》复式记账法</strong></div>
                <div>试算状态: {balance_badge}</div>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">全量对账流水笔数</div>
                <div class="kpi-value">{summary["total_orders"]} 笔</div>
                <div class="kpi-sub">三方对撞闭环</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">对账自动平账率</div>
                <div class="kpi-value" style="color: #10b981;">{summary["matched_rate"]}%</div>
                <div class="kpi-sub">成功对齐 {summary["matched_count"]} 笔</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">检出异常风险流水</div>
                <div class="kpi-value" style="color: #e11d48;">{summary["discrepancy_count"]} 笔</div>
                <div class="kpi-sub">触发审计预警与挂账</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">隐藏利润侵蚀总额</div>
                <div class="kpi-value" style="color: #f59e0b;">¥{balance_result["total_hidden_loss"]}</div>
                <div class="kpi-sub">侵蚀毛利率 {balance_result["erosion_rate"]}%</div>
            </div>
        </div>

        <div class="grid-2">
            <div class="panel">
                <h3>🏛️ 《资金对账调节平衡表》（期末试算平衡）</h3>
                <table>
                    <thead>
                        <tr>
                            <th>核算项目</th>
                            <th style="text-align: right;">借方 / 贷方发生额</th>
                            <th>会计解释与准则依据</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>业务订单系统应收净额</td>
                            <td style="text-align: right; font-weight: 600;">¥{summary["total_biz_turnover"]}</td>
                            <td>主营业务收入扣减退货应收基线</td>
                        </tr>
                        <tr>
                            <td>支付渠道实际入账净额</td>
                            <td style="text-align: right; font-weight: 600; color: #0284c7;">¥{summary["total_channel_turnover"]}</td>
                            <td>银行存款及支付宝实际清算流水</td>
                        </tr>
                        <tr>
                            <td>支付渠道手续费及暗扣佣金</td>
                            <td style="text-align: right; color: #e11d48;">¥{summary["total_channel_fees"]}</td>
                            <td>财务费用/销售费用（含千六基线与跳档提点）</td>
                        </tr>
                        <tr>
                            <td>仓储物流与超重扣款</td>
                            <td style="text-align: right; color: #e11d48;">¥{summary["total_shipping_costs"]}</td>
                            <td>主营业务成本（含超重罚款与运费险）</td>
                        </tr>
                        <tr style="background: #f8fafc; font-weight: 700;">
                            <td>期末理论经营毛利 (Gross Profit)</td>
                            <td style="text-align: right; color: #10b981;">¥{balance_result["gross_profit"]}</td>
                            <td>真实经营毛利率: <strong>{balance_result["actual_profit_margin"]}%</strong></td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div class="panel">
                <h3>🔍 异常科目损益归集</h3>
                <div class="stat-grid">
                    {type_breakdown_str}
                </div>
            </div>
        </div>

        <div class="panel">
            <h3>📑 异常差错明细与调账建议清单（审计底稿）</h3>
            <table>
                <thead>
                    <tr>
                        <th style="width: 50px; text-align: center;">序号</th>
                        <th style="width: 140px;">业务单号</th>
                        <th style="width: 160px;">差错分类</th>
                        <th style="width: 90px; text-align: right;">差异金额</th>
                        <th>异常原因穿透排查</th>
                        <th style="width: 320px;">建议会计分录（财会老法师批注）</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_str}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
        """

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        return output_file

# /* StarOfCAU-END */
