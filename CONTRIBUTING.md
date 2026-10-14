# 贡献指南 (Contributing Guide)

感谢你关注 **小微电商多渠道资金对账与年审异常核算系统 (Ecommerce Reconcile & Audit Engine)**！  
本项目秉持**严谨的财会准则校验、高确定性工程实现与零外部依赖**原则。我们欢迎任何改进代码质量、补充电商真实业务异常场景或优化审计底稿的贡献。

---

## 🛠️ 开发与测试规范

### 1. 本地环境准备
本项目核心模块**零第三方外部依赖**，你只需要具备标准 Python 3.9+ 环境即可：
```bash
# 克隆仓库
git clone https://github.com/<your-username>/ecommerce-reconcile.git
cd ecommerce-reconcile

# 运行全量单元测试
python -m unittest discover tests -v
```

### 2. 代码与精度规范
- **财务金额绝对严禁使用 `float`**：所有涉及货币、费率、佣金、物流费用的计算，必须使用 `decimal.Decimal` 并通过 `core.models.money()` 格式化为两位定点数。
- **原创业务标记**：对于新增的电商垂类核算规则或状态机逻辑，建议使用 `/* StarOfCAU-BEGIN */` 与 `/* StarOfCAU-END */` 注释进行范围标记。
- **不可破坏借贷试算平衡**：任何对核算状态机的修改，必须确保 `AccountingBalancer` 的 `unexplained_diff == Decimal("0.00")`。

---

## 🌿 Git 分支与 Commit 规范

提交信息请遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/) 规范：

- `feat`: 新增对账异常场景、新数据源支持或前端功能
- `fix`: 修复平账逻辑差错、精度舍入溢出或界面渲染 Bug
- `docs`: 文档、教育指南或 README 补充
- `test`: 新增或重构测试用例
- `refactor`: 代码重构（不改变现有外部接口）

---

## 📬 提交 Pull Request

1. Fork 本仓库并基于 `main` 分支创建特性分支（例如 `feat/pdd-refund-rules`）；
2. 提交代码并确保新增逻辑有对应的 `tests/` 单元测试覆盖；
3. 确保本地所有单元测试通过（`python -m unittest discover tests`）；
4. 提交 PR 并详细描述业务背景与会计准则依据。
