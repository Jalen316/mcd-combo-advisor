---
name: mcd-combo-advisor
description: 麦当劳套餐推荐助手。当用户说「帮我配个套餐」「预算 30 吃什么」「想吃点低卡的」「健身吃什么」「两个人点什么」「XXX 热量多少」「怎么点最划算」等涉及麦当劳点餐、搭配、热量、预算的请求时使用。基于官方 MCP 的实时菜单、营养库和优惠券，在预算与营养双重约束下搜索可行搭配并给出可解释理由。
---

# 麦当劳套餐推荐助手（mcd-combo-advisor）

把「今天中午吃点什么」这个决策，变成一道有约束、有依据、可解释的优化题。

## 核心能力

- **双约束搜索**：预算上限 + 热量上下限 + 蛋白质下限 + 钠上限，同时满足
- **四种打分策略**：`balanced` 均衡 / `protein` 增肌 / `light` 减脂 / `cheapest` 省钱
- **套餐真实核算**：三件套这类打包套餐走 `query-meal-detail` 拆成子餐品后逐项累加热量
- **多样性去重**：不会连着三条推荐都配同一杯饮料

## 前置条件

1. 已在 WorkBuddy 配置 `mcd-mcp` 连接器（麦当劳 MCP Token，见 https://github.com/M-China/mcd-mcp-server）
2. 本机有 Python 3.9+（本 Skill 只用标准库，无需安装依赖）
3. 记住仓库根目录路径，下文记为 `<REPO>`

## 执行流程

### 第 1 步：搞清约束

向用户确认最少的信息，**不要一上来就抛一堆问题**。用户随口说「随便吃点」时，直接按预算 35 元、1 人、`balanced` 出结果，再问要不要调整。

需要但用户没说的，用这些默认值：`people=1`、`budget=35`、`strategy=balanced`、`require_drink=False`。

用户说「减脂」「不想胖」→ `strategy=light`；说「增肌」「练完」→ `strategy=protein`；说「最便宜」「省钱」→ `strategy=cheapest`。

### 第 2 步：定位门店

调用 `mcp__mcd-mcp__query-nearby-stores`（到店：`beType=1`，得来速：`beType=5`）。用户没给位置就用 `searchType=1` 查收藏餐厅。

把门店列表给用户选，记住 `storeCode`（得来速还要 `beCode`）。

### 第 3 步：拉取三份数据并存盘

创建临时目录 `<TMP>`（建议 `<REPO>/.tmp/`），依次落盘：

| 调用 | 存为 | 说明 |
| --- | --- | --- |
| `mcp__mcd-mcp__query-meals` | `<TMP>/menu.json` | 存 MCP 返回的**完整 JSON**（含 `data` 层） |
| `mcp__mcd-mcp__list-nutrition-foods` | `<TMP>/nutrition.txt` | 存 `data` 字段的原文，保持 `\n` 换行 |
| `mcp__mcd-mcp__query-store-coupons` | 对话中参考 | 该门店可用优惠券 |

> `nutrition.txt` 只要 MCP 返回的 `data` 字符串部分，**不要**把整个 JSON 响应体存进去。

### 第 4 步：拆解候选套餐（关键步骤）

打包套餐在三份数据里都没有直接营养。用 `query-meal-detail` 拆：

1. 从 `menu.json` 里挑出名称含「件套 / 套餐 / 随心配 / 双人餐」的条目
2. 按价格贴近预算的程度排序，**最多取 6 个**逐个调用 `mcp__mcd-mcp__query-meal-detail`（`<storeCode>`, `orderType`, `beType`, `code`）
3. 原始返回里 90% 是 `modification` 特调选项，与营养无关。用脚本裁剪后存盘：

```bash
cd <REPO> && python3 -c "
import json,sys
sys.path.insert(0,'.')
from combo.expander import compact_detail
raw = json.load(open('<TMP>/detail_raw.json'))   # {code: 原始返回}
out = {c: compact_detail(d) for c,d in raw.items()}
json.dump({'details': out}, open('<TMP>/combo-details.json','w'), ensure_ascii=False, indent=2)
"
```

跑 `--coverage` 看一眼 `resolved_via_combo_expansion`，为 0 说明这步没生效，回头检查。

### 第 5 步：运行推荐引擎

```bash
cd <REPO> && PYTHONPATH=. python3 -m combo.cli \
  --menu <TMP>/menu.json \
  --nutrition <TMP>/nutrition.txt \
  --aliases <REPO>/combo/data/aliases.json \
  --combo-details <TMP>/combo-details.json \
  --budget 35 --people 1 --kcal-max 900 --protein-min 25 \
  --strategy balanced --top 3
```

想让 Agent 后续处理结构化结果就加 `--json`。

常用参数：`--kcal-min` `--kcal-max` `--protein-min` `--sodium-max` `--drink`（必须含饮品）`--exclude 辣,牛肉`（忌口）。

### 第 6 步：给用户解释

脚本输出的「理由」可以直接用，但要补两句人话：

- 说明套餐的热量是**拆解后累加**的，不是单品热量
- 若某方案里有 `--nutrition-source` 标为「按默认规格推断」的项，主动提一句是估算值
- 钠超 2000 毫克时提醒配无糖饮品

### 第 7 步：下单

用户选定后：

1. `mcp__mcd-mcp__query-my-coupons` 或 `query-store-coupons` 找出可用券，告诉用户能再省多少
2. `mcp__mcd-mcp__calculate-price` 用真实单价复核总价
3. **`mcp__mcd-mcp__create-order` 之前必须先得到用户明确确认**，并复述门店、餐品、总价。这是真实付款操作，绝不自动执行。

## 好不好用的几条经验

- **预算太紧会没有结果**时，先放宽到 `--kcal-max` 取消，或提高预算再问用户
- `--coverage` 低于 30% 说明数据没拉全，通常是 `nutrition.txt` 存错了内容
- 套餐的 `- ¥13.1` 优惠通常比单点拼要大，省钱场景优先看套餐方案
- Token 限流 600 次/分钟，批量拉套餐详情时注意间隔

## 边界

- 引擎是**推荐**工具，最终下单必须经用户确认
- 热量数据源于麦当劳官方营养库，仅供参考，不构成医疗或营养建议
- 不要把 MCP Token 写进任何提交给 GitHub 的文件
