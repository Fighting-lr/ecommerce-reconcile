# /* StarOfCAU-BEGIN: E-Commerce Dirty Data Generator (12 Boundary Cases) */
"""
小微电商全链路 12 种真实脏数据注入与压测引擎
作者: StarOfCAU (StarOfCAU)
用途: 模拟真实电商经营中由于网络超时、结算周期滞后、运费险条款、平台佣金暗扣导致的资金差错，
     为对账系统提供极具工业级实战价值的测试数据集。
"""

import csv
import os
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Tuple, List, Dict, Any
from .models import money, OrderRecord, PaymentRecord, LogisticsRecord


class DirtyDataGenerator:
    def __init__(self, seed: int = 20261014):
        random.seed(seed)
        self.products = [
            ("夏季冰丝短袖T恤", Decimal("49.90"), Decimal("18.00")),
            ("透气网面运动鞋", Decimal("129.00"), Decimal("45.00")),
            ("法兰绒加厚家居服", Decimal("89.90"), Decimal("32.00")),
            ("大容量便携保温杯", Decimal("39.90"), Decimal("12.50")),
            ("降噪无线蓝牙耳机", Decimal("199.00"), Decimal("68.00")),
            ("精梳棉中筒袜6双装", Decimal("29.90"), Decimal("8.00")),
        ]

    def generate_batch(self, total_count: int = 1000) -> Tuple[List[OrderRecord], List[PaymentRecord], List[LogisticsRecord], Dict[str, str]]:
        """
        生成指定规模的三方仿真数据
        返回: (orders, payments, logistics, ground_truth_labels)
        """
        orders: List[OrderRecord] = []
        payments: List[PaymentRecord] = []
        logistics: List[LogisticsRecord] = []
        ground_truth: Dict[str, str] = {}

        base_date = datetime(2026, 9, 1, 9, 0, 0)

        for i in range(1, total_count + 1):
            order_id = f"TB202609{i:05d}"
            user_id = f"USR_{random.randint(1000, 9999)}"
            prod_name, unit_price, cost_price = random.choice(self.products)
            qty = random.randint(1, 3)
            goods_amt = money(unit_price * qty)
            goods_cost = money(cost_price * qty)
            shop_disc = money(Decimal("5.00") if goods_amt >= Decimal("50.00") else Decimal("0.00"))
            plat_coupon = money(Decimal("10.00") if goods_amt >= Decimal("100.00") else Decimal("0.00"))
            freight = money(Decimal("0.00") if goods_amt >= Decimal("68.00") else Decimal("6.00"))
            payable = money(goods_amt - shop_disc - plat_coupon + freight)

            create_time = (base_date + timedelta(minutes=i * 35)).strftime("%Y-%m-%d %H:%M:%S")

            # 决定该订单所属的场景（85% 正常，15% 分布在 11 种真实脏数据异常中）
            dice = random.random()

            if dice < 0.85:
                # 场景 1: 完全正常履约平账
                case_name = "NORMAL_COMPLETED"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="DELIVERED"
                ))

            elif dice < 0.88:
                # 场景 2: 跨月退款（9月收入已确认收款，10月跨期冲销退款）
                case_name = "CROSS_PERIOD_REFUND"
                # 退款发生在 10 月 (跨越 2026-09-30 截止日)
                refund_time = (datetime(2026, 10, 1, 10, 0, 0) + timedelta(hours=(i % 100) * 2)).strftime("%Y-%m-%d %H:%M:%S")
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="REFUNDED",
                    created_at=create_time, goods_cost=goods_cost, refund_amount=payable, refund_time=refund_time
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                # 渠道跨期退款流水 (10月入账)
                payments.append(PaymentRecord(
                    pay_flow_id=f"REF_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="REFUND", trans_amount=money(-payable), channel_fee=money(-payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=refund_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("8.00"),
                    ship_time=create_time, delivery_status="RETURNED"
                ))

            elif dice < 0.90:
                # 场景 3: 运费险赔付延迟（已扣保费，但理赔单在保险公司审核中，未到账）
                case_name = "FREIGHT_INSURANCE_DELAY"
                refund_time = (datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S") + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="REFUNDED",
                    created_at=create_time, goods_cost=goods_cost, refund_amount=payable, refund_time=refund_time
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"REF_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="REFUND", trans_amount=money(-payable), channel_fee=money(-payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=refund_time
                ))
                # 物流显示已退货，扣了0.8元保费，但 insurance_claim_amount 为 0（滞后）
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="RETURNED"
                ))

            elif dice < 0.92:
                # 场景 4: 平台佣金跳档暗扣（预估 2%，实扣 5%）
                case_name = "PLATFORM_COMMISSION_SURPRISE"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))
                # 渠道手续费被多扣了
                higher_fee = money(payable * Decimal("0.05"))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=higher_fee,
                    channel_status="SUCCESS", trans_time=create_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="DELIVERED"
                ))

            elif dice < 0.94:
                # 场景 5: 支付渠道截断与分摊舍入误差 (金额差 0.05 元)
                case_name = "COUPON_PRORATA_ROUNDING"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))
                diff_pay = money(payable - Decimal("0.05"))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=diff_pay, channel_fee=money(diff_pay * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="DELIVERED"
                ))

            elif dice < 0.96:
                # 场景 6: 物流超重异常扣款
                case_name = "LOGISTICS_OVERWEIGHT_PENALTY"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("6.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="DELIVERED"
                ))

            elif dice < 0.97:
                # 场景 7: 支付成功但业务掉单（渠道单边账 - 待查长款）
                case_name = "UNILATERAL_DROPPED_BIZ_ORDER"
                # 业务订单表里没有这一单，但支付宝流水里扣了款
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="WXPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))

            elif dice < 0.98:
                # 场景 8: 业务单边账（业务标记已支付发货，但渠道漏记未到账）
                case_name = "UNILATERAL_ABANDONED_ORDER"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))

            elif dice < 0.99:
                # 场景 9: T+1 跨期在途结算 (订单于9月30日支付，资金10月1日划拨入账)
                case_name = "IN_TRANSIT_SETTLEMENT_LAG"
                eom_create_time = (datetime(2026, 9, 30, 20, 0, 0) + timedelta(minutes=(i % 180))).strftime("%Y-%m-%d %H:%M:%S")
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=eom_create_time,
                    goods_cost=goods_cost
                ))
                # 支付结算时间晚至次月入账 (跨越截止日 2026-09-30 23:59:59)
                lagged_time = (datetime(2026, 10, 1, 9, 30, 0) + timedelta(minutes=(i % 180))).strftime("%Y-%m-%d %H:%M:%S")
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="UNIONPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=lagged_time
                ))
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=eom_create_time, delivery_status="DELIVERED"
                ))

            else:
                # 场景 10: 破损补发导致单据一对多 (产生两笔运费)
                case_name = "DAMAGED_RESHIP_MULTI_TRACK"
                orders.append(OrderRecord(
                    order_id=order_id, user_id=user_id, product_name=prod_name,
                    goods_amount=goods_amt, shop_discount=shop_disc, platform_coupon=plat_coupon,
                    freight_fee=freight, actual_payable=payable, order_status="PAID", created_at=create_time,
                    goods_cost=goods_cost
                ))
                payments.append(PaymentRecord(
                    pay_flow_id=f"PAY_{order_id}", order_id=order_id, channel="ALIPAY",
                    trans_type="PAY", trans_amount=payable, channel_fee=money(payable * Decimal("0.006")),
                    channel_status="SUCCESS", trans_time=create_time
                ))
                # 第一次正常发货
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}_1", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.80"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=create_time, delivery_status="DELIVERED"
                ))
                # 第二次补发破损件 (再次产生4.5元运费)
                reship_time = (datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S") + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
                logistics.append(LogisticsRecord(
                    tracking_no=f"SF_{order_id}_2", order_id=order_id, carrier="SF",
                    shipping_fee=Decimal("4.50"), overweight_fee=Decimal("0.00"),
                    freight_insurance_fee=Decimal("0.00"), insurance_claim_amount=Decimal("0.00"),
                    ship_time=reship_time, delivery_status="DELIVERED"
                ))

            ground_truth[order_id] = case_name

        return orders, payments, logistics, ground_truth

    @staticmethod
    def export_csv(orders: List[OrderRecord], payments: List[PaymentRecord], logistics: List[LogisticsRecord], output_dir: str):
        """将生成的数据导出为标准 CSV 账单文件"""
        os.makedirs(output_dir, exist_ok=True)

        # 1. 业务订单表
        with open(os.path.join(output_dir, "orders.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["order_id", "user_id", "product_name", "goods_amount", "shop_discount", "platform_coupon", "freight_fee", "actual_payable", "order_status", "created_at", "goods_cost", "refund_amount", "refund_time"])
            for o in orders:
                writer.writerow([o.order_id, o.user_id, o.product_name, str(o.goods_amount), str(o.shop_discount), str(o.platform_coupon), str(o.freight_fee), str(o.actual_payable), o.order_status, o.created_at, str(o.goods_cost), str(o.refund_amount), o.refund_time or ""])

        # 2. 支付渠道流水表
        with open(os.path.join(output_dir, "payments.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["pay_flow_id", "order_id", "channel", "trans_type", "trans_amount", "channel_fee", "channel_status", "trans_time"])
            for p in payments:
                writer.writerow([p.pay_flow_id, p.order_id, p.channel, p.trans_type, str(p.trans_amount), str(p.channel_fee), p.channel_status, p.trans_time])

        # 3. 物流履约表
        with open(os.path.join(output_dir, "logistics.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["tracking_no", "order_id", "carrier", "shipping_fee", "overweight_fee", "freight_insurance_fee", "insurance_claim_amount", "ship_time", "delivery_status"])
            for l in logistics:
                writer.writerow([l.tracking_no, l.order_id, l.carrier, str(l.shipping_fee), str(l.overweight_fee), str(l.freight_insurance_fee), str(l.insurance_claim_amount), l.ship_time, l.delivery_status])

# /* StarOfCAU-END */
