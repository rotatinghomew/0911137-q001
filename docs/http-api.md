# 本地接口

- `GET /api/catalog`：资料名称、可选业务范围、业务时点范围、表计数、商品状态、品类与资料指纹，并给出预测步长上限。
- `GET /api/view`：当前历史摘要，可选 `cutoff` 整数和 `group` 业务范围；未知筛选项返回 400。
- `GET /api/forecast`：分层补货预测，可选 `cutoff`、`group`、`horizon`（1–8，默认 4），以及替代情景 `from`、`to`、`share`。返回逐序列预测、品类/区域/总层级结果、回测目录与提示。未知或冲突筛选项、超过步长上限、情景商品不存在返回 400。
- `GET /api/source?table=sales`：按实际资料表名称查询原始记录；不存在的表返回 404。
- `POST /api/analyses`：JSON 对象包含 `title` 与 `options`，按 `options` 重新计算并保存，成功返回 201。`options` 与当前摘要或预测的筛选结构相同，`options.mode` 为 `forecast` 时保存预测结果。
- `GET /api/analyses`：只返回版本目录及资料指纹。
- `GET /api/analyses/{id}`：返回保存的完整结果，不重新计算。

历史与预测的 `rows` 都是领域行，`summary` 是当前汇总，`notes` 是业务口径提示，`options` 是实际采用的筛选值。预测另含 `levels`（各层点值与区间）、`scenario`（替代情景）与 `validation`（回测起点与模型计数）。无记录使用空列表或 `null` 点值，不能把无观测偷偷表示为有效的零值。错误 JSON 的 `error` 可直接在页面反馈。


这是本地单用户应用，不包含认证与外网部署设置。默认只监听回环地址，容器中可显式监听所有接口，但宿主机端口建议只绑定回环地址。
