# 评价标准版本与口径血缘

景区质量评价不再只计硬件。主管部门更新评价项目定义后，字段名可能仍然相同，
但分数含义未必可比。本服务因此把评价标准版本作为来源目录、产品编排和交付回执
的共同依据。

## 版本状态

| 状态 | 含义 | 可登记字段 | 可登记原件 | 可出具结论 |
| --- | --- | --- | --- | --- |
| `draft` | 草案，仅供准备 | 是 | 是 | 否 |
| `published` | 已发布并生效 | 否，须另立版本 | 是 | 是 |
| `interrupted` | 发布中断，暂停执行 | 否 | 否 | 否 |
| `withdrawn` | 已撤回 | 否 | 否 | 否 |

`standards.register` 建立草案，`publish`、`interrupt`、`withdraw` 推进状态并写入
`standard.*` 审计记录。发布后字段定义冻结，调整必须登记新版本。

## 三处口径

- **来源字段目录**：`catalog.register(..., standard_id, standard_version)` 把来源
  字段挂在具体版本下；`catalog.add_version_fields` 为同一来源补登新口径字段。
  未给出编号和版本的记录以 `unversioned/0` 保存，只作历史材料，不参与新口径比较。
- **产品引用**：`products.assemble(..., standard_id, standard_version)` 记录产品
  修订采用的版本，`basis` 保存每个输入的字段沿用明细。未显式给出时沿用首个输入
  原件的口径。
- **结论交付依据**：交付回执的 `field_basis` 固化字段级依据，`basis` 汇总标准
  编号、版本、可比性及沿用/重取字段。回执另带 `current_standard_status` 与
  `current_conclusion_state`，供复核人员识别标准后续变化。

## 字段沿用与重新取证

`standards.align` 在草案版本上登记跨版本的字段沿用关系（`reuse` / `recollect`）。
`standards.review(asset_id, standard_id, standard_version)` 输出待审材料逐字段状态：

| 状态 | 含义 |
| --- | --- |
| `reuse` | 字段定义未变或已登记沿用，可直接使用 |
| `recollect` | 字段名相同但定义变化，须按新口径重新取证 |
| `retired` | 新口径不再采用该字段 |
| `collect` | 新口径要求但材料未提供 |
| `unverified` | 来源尚未登记目标口径字段 |

只要存在 `recollect`、`retired`、`collect` 或 `unverified`，材料即标记为不可比。
产品编排允许保留这种草案供准备，但交付会以 `IncomparableInputs` 拒绝，字段不会
被默默重解释成新分数。

## 中止与撤回

`standards.impact(standard_id, version)` 区分：

- `issued`：已经出具的结论。标准中断或撤回后 `current_conclusion_state` 为
  `retained-original-interpretation`，历史回执内容不改写。
- `drafts`：尚未交付的产品修订。按标准状态标记为 `unpublished-draft`、
  `draft-awaiting-delivery`、`suspended-draft` 或 `withdrawn-draft`，不能作为结论。

原有数据库在打开时会自动补充版本列；旧记录一律标为 `unversioned/0`，不会被
推断到新口径。
