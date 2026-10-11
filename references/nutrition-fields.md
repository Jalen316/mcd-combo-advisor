# 数据字段参考

记录三个官方 MCP Tool 返回中本项目实际依赖的字段，便于排查数据问题时对照。

## list-nutrition-foods

返回不是 JSON，而是结构化文本。首行是表头 `[160]{...}:`，其后每行一条记录。

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  巨无霸,null,2146,513,27,26,42,961,171
  纯悦,能量约清水1杯,0,0,0,0,0,0,0
```

| 字段 | 含义 | 单位（按份计） |
| --- | --- | --- |
| `productName` | 餐品名（标准名，与菜单名不完全一致） | — |
| `nutritionDescription` | 可选的通俗类比，如「能量约清水1杯」 | — |
| `energyKj` / `energyKcal` | 能量 | 千焦 / 大卡 |
| `protein` | 蛋白质 | 克 |
| `fat` | 脂肪 | 克 |
| `carbohydrate` | 碳水化合物 | 克 |
| `sodium` | 钠 | 毫克 |
| `calcium` | 钙 | 毫克 |

**解析要点**：`nutritionDescription` 是自由文本，可能含逗号，因此不能用固定下标切分。
本项目改为「从左取名字、从右取七个数值、中间全部归为描述」的解析方式。

## query-meals

```jsonc
{
  "data": {
    "categories": [
      { "name": "巨无霸\n牛鱼肉堡",          // 分类名常含换行符
        "meals": [ { "code": "1100", "tags": ["100%纯牛肉"] } ] }
    ],
    "meals": {
      "1100": {
        "name": "巨无霸",
        "currentPrice": "25.5",        // 字符串，需转 float
        "originalPrice": "25.5",       // 划线价，与现价之差即优惠金额
        "discountType": null,          // null / 促销优惠 / 早餐卡优惠 / 麦金卡优惠 / 随单购麦金卡优惠
        "image": "https://...",
        "canWithOrder": false
      }
    }
  }
}
```

同一餐品会出现在多个分类中，本项目按 `code` 去重并保留首次出现的分类。

## query-meal-detail

```jsonc
{
  "data": {
    "code": "9900005466",
    "name": "巨无霸三件套",
    "rounds": [
      { "id": 1, "name": "新升级巨无霸",
        "choices": [
          { "code": "1100", "name": "巨无霸", "isDefault": 1, "quantity": 1,
            "diffPrice": "+ ¥0", "modification": { ... } }   // 特调选项，占绝大部分体积
        ] },
      { "id": 2, "name": "选择套餐内小食", "choices": [ ... ] },
      { "id": 3, "name": "选择套餐内饮料", "choices": [ ... ] }
    ]
  }
}
```

**取出默认搭配**：遍历 `rounds[].choices[]`，取 `isDefault == 1` 且 `quantity >= 1` 的 `name`。
这些名字与营养库标准名一致，可直接查表累加。

`modification` 字段与营养计算无关，建议用 `combo.expander.compact_detail()` 先裁掉再落盘。

---

## 营养参考口径

| 指标 | 取值 | 出处 |
| --- | --- | --- |
| 一餐钠参考上限 | 2000 毫克 | 中国居民膳食指南（成人每日 2000 毫克钠 ≈ 5 克盐，按三餐分摊后取单餐口径并留冗余） |
| 一餐能量下限 | 250 大卡 | 用于兜底，防止推荐出「一瓶水加一份蘸酱」这种不合格的正餐 |

> 两者均为工程上的启发式阈值，用于生成建议，不构成医疗或营养建议。
