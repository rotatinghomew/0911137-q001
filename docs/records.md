# 记录样式

以下为当前业务文件中的实际记录，字段口径见 `data-contract.md`；字段标识保持业务接口写法。

### `products`

字段：`inactive_from`、`launched_at`、`name`、`product`。

```json
{
  "product": "p1",
  "name": "款式1",
  "inactive_from": null,
  "launched_at": 1
}
```

### `membership`

字段：`category`、`end`、`known_at`、`product`、`start`。

```json
{
  "product": "p1",
  "category": "c1",
  "start": 1,
  "end": null,
  "known_at": 1
}
```

### `sales`

字段：`known_at`、`observed`、`product`、`quantity`、`region`、`week`。

```json
{
  "product": "p1",
  "region": "华东",
  "week": 1,
  "quantity": 13,
  "observed": true,
  "known_at": 1
}
```
