# MCP 接入说明

## 1. 使用的 MCP Server

| 项目 | 值 |
| --- | --- |
| MCP Server | 麦当劳中国官方 MCP（`https://mcp.mcd.cn`） |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | `Authorization: Bearer <MCP_TOKEN>` |
| 官方文档 | https://github.com/M-China/mcd-mcp-server |
| 开放平台 | https://open.mcd.cn/mcp |
| 接入配置 | WorkBuddy 左侧「连接器」→ 自定义连接器 → 填入官方 JSON 模板 |
| 限流 | 600 次 / 分钟（超限返回 429） |

项目中不保存任何 Token，配置示例一律使用占位符。

---

## 2. 实际调用的 Tool

本项目共使用 **6 个** Tool，分为「必需」与「可选」两类。

### 2.1 构成推荐引擎核心的 4 个

| Tool | 用途 | 返回中被本项目使用的字段 |
| --- | --- | --- |
| `list-nutrition-foods` | 官方营养库（160 条） | `data` 文本中的 `productName` / `energyKcal` / `protein` / `fat` / `carbohydrate` / `sodium` / `calcium` |
| `query-meals` | 门店实时菜单与价格 | `data.categories[].meals[].code`、`data.meals.{code}` 中的 `name` / `currentPrice` / `originalPrice` / `discountType` / `image` |
| `query-meal-detail` | **拆解套餐**（本项目技术核心） | `data.rounds[].choices[].name`、`choices[].isDefault`、`choices[].quantity` |
| `query-nearby-stores` | 定位门店 | `data[].storeCode`、`data[].beCode`、`data[].storeName`、`data[].address`、`data[].distance` |

### 2.2 下单与省钱环节的 2 个

| Tool | 用途 |
| --- | --- |
| `query-store-coupons` / `query-my-coupons` | 在推荐结果上叠加用户真实可用的优惠券 |
| `calculate-price` | 用真实单价复核最终总价后再下单 |

> `create-order` 虽在 Skill 流程中定义，但**必须得到用户明确确认后才会调用**，引擎本身不会自动下单。

---

## 3. 调用流程

```
┌─ 输入：预算 / 人数 / 热量目标 / 忌口 / 策略

① query-nearby-stores  → 用户选门店 → 得到 storeCode（得来速还需 beCode）
② query-meals          → 实时菜单 + 价格           → 落盘 menu.json
③ list-nutrition-foods → 160 条官方营养数据         → 落盘 nutrition.txt
④ query-meal-detail    → 对候选套餐（≤6 个）逐个拆解 → 落盘 combo-details.json
                          （用 compact_detail() 裁掉占 90% 体积的特调选项）
⑤ 本地引擎跑优化        → nutrition.py / matcher.py / expander.py / planner.py
⑥ query-store-coupons  → 叠加可用券，给出最终到手价
⑦ calculate-price      → 真实单价复核
⑧ create-order         → ⚠️ 需用户确认后才执行

└─ 输出：Top N 方案（明细 / 总价 / 总热量 / 蛋白质 / 优惠 / 推荐理由）
```

### `query-meal-detail` 为什么是关键一环

`list-nutrition-foods` 中**不存在任何打包套餐**。而麦当劳菜单以三件套、四件套为主，
若不做拆解，最常被点的商品会全部变成「营养未知」，推荐就失去意义。

该 Tool 返回的 `rounds[].choices[]` 把套餐还原成若干子餐品，`isDefault=1` 标出默认搭配：

```
巨无霸三件套 (9900005466)
├─ round 1  新升级巨无霸      → 巨无霸      (isDefault=1)
├─ round 2  选择套餐内小食    → 中薯条      (isDefault=1)
└─ round 3  选择套餐内饮料    → 可乐中杯    (isDefault=1)
```

而这三项的名字正是营养库中的标准条目名，可直接查表累加：
**513 + 289 + 147 = 949 大卡**。

> 若不做这一步、仅凭名称猜，会得到 `巨无霸` 的 513 大卡，**低估 45%**。

---

## 4. 业务价值

| 维度 | 说明 |
| --- | --- |
| **对用户** | 把「预算 + 热量 + 蛋白质」三个同时成立的条件一次算清，而不是逐个翻 App 比对 |
| **对麦当劳** | 优惠券核销率提升 —— 推荐结果会主动用掉可用券；套餐等高毛利组合的转化率提高 |
| **数据充分利用** | 官方开放了菜单、营养、券三类数据，但此前没有被组合计算过，本项目是一次打通 |
| **可解释性** | 每个方案都给出中文理由（为什么选它、热量从哪来、哪些是估算值），而非黑盒打分 |

---

## 5. 安全与合规

- 仓库中**不含任何 Token、密钥、账号凭证**，全部使用占位符与环境变量
- 不采集、不上传任何用户个人信息；`examples/` 中的门店数据仅为公开菜单信息
- 遵循官方《麦当劳 MCP 服务规则》，仅用于非商业性质的 connection 与计算
- 下单动作（`create-order`）强制人工确认，不存在自动扣款路径
