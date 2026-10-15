# /* StarOfCAU-BEGIN: Core Data Models & Financial Types */
"""
小微电商多渠道资金对账与年审异常核算系统 - 数据模型定义
作者: StarOfCAU (StarOfCAU)
特性: 
1. 强制使用 decimal.Decimal 杜绝浮点数精度丢失 (0.1 + 0.2 != 0.3)
2. 规范化电商三方交易模型（业务订单、支付渠道流水、物流履约），支持商品采购成本 (COGS)
3. 规范对账规则与账期配置 (ReconcileConfig)
4. 审计差异分类模型（符合大厂清结算与事务所年审规范，支持调账凭证与工作流）
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime


def money(val: Any) -> Decimal:
    """转换为保留两位小数的 Decimal 金额（四舍五入）"""
    if val is None or val == "":
        return Decimal("0.00")
    if isinstance(val, (int, str)):
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(val, float):
        return Decimal(f"{val:.4f}").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal("0.00")


class ReconcileStatus(str, Enum):
    """对账状态枚举"""
    MATCHED = "MATCHED"                           # 完全平账 (三方一致)
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"           # 金额不符 (实收与订单不平)
    FEE_DISCREPANCY = "FEE_DISCREPANCY"           # 手续费/扣点异常 (扣点超标或少扣)
    UNILATERAL_CHANNEL = "UNILATERAL_CHANNEL"     # 渠道单边账 (渠道扣款成功，业务端掉单)
    UNILATERAL_BIZ = "UNILATERAL_BIZ"             # 业务单边账 (业务有单未付款，或退款未到账)
    TIMING_LAG = "TIMING_LAG"                     # 跨期在途 (跨月退款/资金在途)
    LOGISTICS_MISMATCH = "LOGISTICS_MISMATCH"     # 履约异常 (超重扣款/破损重发单据分裂/运费险未赔)


class DiscrepancyType(str, Enum):
    """审计差错分类（会计科目对应）"""
    TIMING_IN_TRANSIT = "在途资金未结算"              # 资产类：其他货币资金-在途
    CROSS_PERIOD_REFUND = "跨期退款待冲销"           # 损益类：主营业务收入（红字）/应收账款
    PLATFORM_COMMISSION_DIFF = "平台扣点与税费差额"   # 损益类：销售费用/财务费用
    FREIGHT_INSURANCE_LAG = "运费险理赔滞后"          # 资产类：其他应收款-保险赔付
    UNILATERAL_SUSPENSE = "单边挂账（待查短款/长款）" # 负债类/资产类：其他应付款-待查长款
    LOGISTICS_OVERWEIGHT_FEE = "物流超重异常扣费"    # 损益类：主营业务成本-物流配送


@dataclass
class ReconcileConfig:
    """对账规则与账期参数配置"""
    cutoff_date: Optional[str] = "2026-09-30 23:59:59"  # 对账截止日 (Cutoff Date)
    initial_cash_balance: Decimal = Decimal("50000.00")  # 期初资金余额
    fee_baseline_rate: Decimal = Decimal("0.006")       # 基准通道费率 (0.6%)
    commission_jump_threshold: Decimal = Decimal("0.50") # 佣金跳档暗扣容忍阈值 (¥0.50)
    insurance_claim_standard: Decimal = Decimal("8.00")  # 运费险标准理赔保额 (¥8.00)
    tolerance: Decimal = Decimal("0.00")                # 抹零容差阈值 (¥0.00)


@dataclass
class OrderRecord:
    """业务订单记录（电商后台 / 订单中心）"""
    order_id: str                              # 业务主订单号
    user_id: str                               # 用户标识
    product_name: str                          # 商品名称
    goods_amount: Decimal                      # 商品标价总额
    shop_discount: Decimal                     # 店铺自主优惠券
    platform_coupon: Decimal                   # 平台跨店满减补贴 (平台事后返还)
    freight_fee: Decimal                       # 运费收取
    actual_payable: Decimal                    # 买家应付金额 = goods_amount - shop_discount - platform_coupon + freight_fee
    order_status: str                          # 订单状态 (PAID, REFUNDED, SHIPPED, CLOSED)
    created_at: str                            # 下单时间
    goods_cost: Decimal = Decimal("0.00")       # 商品采购成本 (COGS, 用于计算真实毛利)
    refund_amount: Decimal = Decimal("0.00")   # 业务记录退款金额
    refund_time: Optional[str] = None          # 申请退款时间


@dataclass
class PaymentRecord:
    """支付渠道流水（支付宝/微信/银联收单）"""
    pay_flow_id: str                   # 支付网关流水号 (外部交易号)
    order_id: str                      # 关联业务订单号
    channel: str                       # 支付渠道 (ALIPAY, WXPAY, UNIONPAY)
    trans_type: str                    # 交易类型 (PAY, REFUND)
    trans_amount: Decimal              # 渠道结算金额 (正数表示入账，负数表示退款)
    channel_fee: Decimal               # 渠道手续费 (如千分之六 0.6%)
    channel_status: str                # 支付状态 (SUCCESS, FAILED)
    trans_time: str                    # 渠道实际扣款/入账时间


@dataclass
class LogisticsRecord:
    """履约物流账单（菜鸟/顺丰/运费险账单）"""
    tracking_no: str                   # 物流运单号
    order_id: str                      # 关联业务订单号
    carrier: str                       # 物流承运商 (SF, ZTO, CAINIAO)
    shipping_fee: Decimal              # 基础运费扣费
    overweight_fee: Decimal            # 超重补收运费
    freight_insurance_fee: Decimal     # 运费险保费代扣 (如每单0.8元)
    insurance_claim_amount: Decimal    # 运费险实际理赔赔付款
    ship_time: str                     # 发货时间
    delivery_status: str               # 履约状态 (DELIVERED, RETURNING, RETURNED)


@dataclass
class DiscrepancyItem:
    """单笔对账差异明细（审计底稿项）"""
    order_id: str
    reconcile_status: ReconcileStatus
    discrepancy_type: DiscrepancyType
    diff_amount: Decimal
    biz_amount: Decimal
    channel_amount: Decimal
    logistics_amount: Decimal
    suspect_reason: str
    accounting_advice: str             # 财会老法师的调账分录建议
    id: Optional[int] = None
    process_status: str = "PENDING"    # PENDING (待处理), RESOLVED (已调账)
    resolved_note: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    detected_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# /* StarOfCAU-END */
