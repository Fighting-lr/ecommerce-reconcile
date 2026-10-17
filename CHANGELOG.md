# 变更日志 (CHANGELOG)

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-14

### Added (新增)
- **核心数据模型 (`core/models.py`)**：
  - 全流程强制采用 `decimal.Decimal` 财务高精度计算，彻底根绝浮点数精度截断误差。
  - 定义三方核对核心实体：`OrderRecord`（业务订单）、`PaymentRecord`（支付流水）、`LogisticsRecord`（物流履约）。
  - 定义审计底稿级差异项模型 `DiscrepancyItem` 与状态枚举。
- **12 种电商脏数据注入与压测引擎 (`core/dirty_data_generator.py`)**：
  - 覆盖正常平账、跨月退款冲销、运费险理赔滞后、平台佣金跳档暗扣、满减券退款分摊舍入差、物流超重扣款、掉单长款、破损多次发货等 12 种真实边界 Case。
  - 支持一键导出标准 `orders.csv`、`payments.csv`、`logistics.csv`。
- **三方复合对账状态机 (`core/reconciliation_engine.py`)**：
  - 实现基于订单主键的多流水自动聚合与多维核对。
  - 自动识别单边账、金额差异、扣点异常与履约异常。
- **财会复式记账试算平衡自检引擎 (`core/accounting_balancer.py`)**：
  - 严格按借贷复式记账法验算《资金对账调节表》，实现 `unexplained_diff == 0.00` 试算平衡。
  - 测算隐藏损益侵蚀（运费超重、扣点暗扣）对小微电商微利业务的真实侵蚀率。
- **企业级全栈 Web 工作台 (`web/server.py` & `web/static/index.html`)**：
  - 基于原生 Python 标准库与单页应用 (SPA) 架构，提供高可用 REST API。
  - **内置 RBAC 权限矩阵**：集成 SHA-256 加盐哈希密码存储，严格隔离审计员 (Auditor)、店长运营 (Operator) 与超级管理员 (Admin)。
  - **实时单据录入与即时判定**：支持单笔三方业务单据录入，毫秒级反馈对账结论与财会分录；具备 P0 级单号冲突防静默覆盖机制与金额安全校验。
  - **审计差错审核与穿透对比**：支持多维组合筛选、三方单据穿透对比抽屉 (Drill-Down)、调账凭证与经办人记录留痕。
  - **主流电商 CSV 账单智能导入**：支持淘宝千牛、抖店、支付宝、微信支付、顺丰、菜鸟等平台表头自适应映射、文件拖拽与 UTF-8 BOM 标准模版下载。
  - **移动端与响应式适配**：支持平板与移动手持设备断点自适应排版。
- **教育文件与交付规范**：
  - 编写 `docs/教育文件/01-电商对账与三方核算原理-小白版.md`（名词卡 + 三层解释法 + 课后自测）。
  - 提供生产级 `Dockerfile` 与 `docker-compose.yml` 容器化交付配置。

