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
- **像素级审计底稿生成器与可视化看板 (`core/audit_reporter.py` & `run.py`)**：
  - 生成自洽的响应式 HTML 审计底稿报告，支持 `@media print` 一键打印或另存为 PDF。
  - 内置基于 Python 标准库 `http.server` 的零依赖本地 Web 看板服务。
- **教育文件与交付规范**：
  - 编写 `docs/教育文件/01-电商对账与三方核算原理-小白版.md`（名词卡 + 三层解释法 + 课后自测）。
  - 提供生产级 `Dockerfile` 与 `docker-compose.yml` 容器化交付配置。
