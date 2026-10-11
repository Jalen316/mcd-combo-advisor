"""把 ``query-meals`` 的原始返回整理成可供推荐的商品目录。

``query-meals`` 返回两块相互引用的数据：

* ``categories`` —— 分类维度，只给一串餐品编码；
* ``meals`` —— 编码到详情的映射，含名称、现价、原价、优惠类型。

这里把两份数据合并成一个扁平的商品列表，并补齐三件下游必须用到的信息：

1. **角色**：这道菜是主餐、小食、饮品还是打包好的套餐——组合搜索靠它决定「一个汉堡配一杯饮料」这类搭配；
2. **是否食物**：菜单里混着 ``麦当劳联名复古棒球帽`` 这类周边商品，必须剔除，否则会出现荒谬的搭配结果；
3. **营养：** 通过 :class:`~combo.matcher.NutritionMatcher` 对齐到官方营养库。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .matcher import MatchResult, NutritionMatcher

ROLE_MAIN = "main"        # 汉堡 / 卷 / 炸鸡等主餐
ROLE_SIDE = "side"        # 薯条、鸡块、玉米杯等小食
ROLE_DRINK = "drink"      # 饮品
ROLE_DESSERT = "dessert"  # 甜品
ROLE_COMBO = "combo"      # 已打包好的套餐
ROLE_SHARE = "share"      # 多人分享餐
ROLE_CONDIMENT = "condiment"  # 蘸酱、调味料，不是一道菜
ROLE_NONFOOD = "nonfood"  # 周边等非食用商品

ROLE_LABELS = {
    ROLE_MAIN: "主餐",
    ROLE_SIDE: "小食",
    ROLE_DRINK: "饮品",
    ROLE_DESSERT: "甜品",
    ROLE_COMBO: "套餐",
    ROLE_SHARE: "分享餐",
    ROLE_CONDIMENT: "蘸酱",
    ROLE_NONFOOD: "非餐品",
}

#: 分类名 → 默认角色。分类名里常带换行符（如 ``鸡肉汉堡\n/卷``），匹配前会先行清洗。
CATEGORY_ROLES: Dict[str, str] = {
    "鸡肉汉堡/卷": ROLE_MAIN,
    "巨无霸牛鱼肉堡": ROLE_MAIN,
    "炸鸡": ROLE_MAIN,
    "蘸酱炸鸡": ROLE_MAIN,
    "饮品": ROLE_DRINK,
    "麦咖啡™": ROLE_DRINK,
    "麦咖啡": ROLE_DRINK,
    "小食甜品/其他": ROLE_SIDE,
    "人气热卖": ROLE_MAIN,
    "精选单人餐": ROLE_COMBO,
    "大堡口福单人餐": ROLE_COMBO,
    "500大卡套餐": ROLE_COMBO,
    "开心乐园": ROLE_COMBO,
    "随心配1+1": ROLE_COMBO,
    "小食拼盘/多人餐": ROLE_SHARE,
}

#: 名称关键字 → 角色的二次修正。分类只能给默认值，真正可靠的是菜名本身。
#: 从上到下依次匹配，命中即返回，所以蘸酱这类规则必须排在前面。
NAME_RULES: list[tuple[str, str]] = [
    (r"棒球帽|周边|玩具|公仔|礼品卡|收纳", ROLE_NONFOOD),
    # 蘸酱和调味料不是一道菜。这一条不加的话，「韩式烟熏芝士风味酱」会被当成正餐配给顾客。
    (r"酱$|酱料|风味酱", ROLE_CONDIMENT),
    (r"薯条|薯饼|扭扭薯|玉米杯|鸡块|鸡翅|鸡排|苹果片|薯格|洋葱|派$|薯角", ROLE_SIDE),
    (r"新地|麦旋风|冰淇淋|雪冰|奶冻|拉明顿|阿芙佳朵", ROLE_DESSERT),
    (r"可乐|雪碧|红茶|咖啡|牛奶|豆浆|果汁|柠柠|黄金橙橙|怡泉|气泡|美式|拿铁|卡布|奶铁|¥水|纯悦|饮品", ROLE_DRINK),
    (r"套餐|三件套|四件套|五件套|件套|随心配|乐园餐|双人餐|拼$|盘$|随心选", ROLE_COMBO),
    (r"堡$|堡[^贝]|汉堡|卷|鸡$|肉$|鱼$", ROLE_MAIN),
]

_PRICEKEYS = ("currentPrice", "originalPrice")


def clean_category(name: str) -> str:
    """抹平分类名里的换行与空格，例如 ``"鸡肉汉堡\\n/卷"`` → ``"鸡肉汉堡/卷"``。"""
    return re.sub(r"\s+", "", name or "")


def _to_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return round(float(str(raw).strip()), 2)
    except (TypeError, ValueError):
        return None


def guess_role(name: str, category: str) -> str:
    """先按菜名关键字判定，判定不了再退回分类默认值。"""
    cleaned_name = re.sub(r"\s+", "", name or "")
    for pattern, role in NAME_RULES:
        if re.search(pattern, cleaned_name):
            return role
    return CATEGORY_ROLES.get(clean_category(category), ROLE_SIDE)


@dataclass
class Item:
    """一个可直接下单的商品。"""

    code: str
    name: str
    price: float
    original_price: Optional[float]
    category: str
    role: str
    discount_type: Optional[str] = None
    tags: list = field(default_factory=list)
    nutrition_code: Optional[str] = None
    match: Optional[MatchResult] = None
    #: 套餐拆解所得的营养（见 :mod:`combo.expander`），仅打包套餐会有值
    derived: Optional[Any] = None

    @property
    def is_food(self) -> bool:
        """是否算得上「一道吃的」。蘸酱和周边都不算。"""
        return self.role not in (ROLE_NONFOOD, ROLE_CONDIMENT)

    def nutrition_value(self, attr: str) -> Optional[float]:
        """先取套餐拆解值，再退回单品匹配结果。

        拆解值必须优先：它记录的是套餐里真实有哪些东西，而单品匹配对套餐而言
        只能是猜测（见 :data:`combo.matcher.COMBO_HINTS`）。
        """
        if self.derived is not None:
            value = getattr(self.derived, attr, None)
            if value is not None:
                return value
        if self.match and self.match.fact:
            field_name = f"energy_{attr}" if attr == "kcal" else attr
            value = getattr(self.match.fact, field_name, None)
            if value is not None:
                return value
        if self.derived is not None:
            return getattr(self.derived, attr, None)
        return None

    @property
    def kcal(self) -> Optional[float]:
        return self.nutrition_value("kcal")

    @property
    def protein(self) -> Optional[float]:
        return self.nutrition_value("protein")

    @property
    def sodium(self) -> Optional[float]:
        return self.nutrition_value("sodium")

    @property
    def nutrition_known(self) -> bool:
        return self.kcal is not None

    @property
    def nutrition_origin(self) -> str:
        """这条营养数据是怎么来的，用于向用户标注可信度。"""
        if self.derived is not None:
            parts = "、".join(self.derived.components)
            unresolved = f"，缺：{'、'.join(self.derived.unresolved)}" if self.derived.unresolved else ""
            return f"套餐拆解为 {parts}{unresolved}"
        if self.match and self.match.fact:
            return self.match.label
        return "未匹配"

    @property
    def discount_amount(self) -> float:
        """相对原价省了多少钱，为 0 表示没有折扣。"""
        if self.original_price is None:
            return 0.0
        return round(max(self.original_price - self.price, 0.0), 2)


def load_menu(data: Dict[str, Any], matcher: Optional[NutritionMatcher] = None) -> List[Item]:
    """把 ``query-meals`` 的 ``data`` 字段转换成商品列表。

    同一个餐品可能出现在多个分类里，这里按编码去重，保留最先出现的分类。
    """
    raw_meals: Dict[str, Any] = data.get("meals") or {}
    categories = data.get("categories") or []

    first_category: Dict[str, str] = {}
    tags_by_code: Dict[str, list] = {}
    for category in categories:
        cat_name = clean_category(category.get("name", ""))
        for entry in category.get("meals") or []:
            code = str(entry.get("code", "")).strip()
            if not code:
                continue
            first_category.setdefault(code, cat_name)
            tags_by_code.setdefault(code, list(entry.get("tags") or []))

    items: List[Item] = []
    for code, detail in raw_meals.items():
        if not isinstance(detail, dict):
            continue
        name = (detail.get("name") or "").strip()
        price = _to_price(detail.get("currentPrice"))
        if not name or price is None:
            continue

        category = first_category.get(code, "")
        item = Item(
            code=str(code),
            name=name,
            price=price,
            original_price=_to_price(detail.get("originalPrice")),
            category=category,
            role=guess_role(name, category),
            discount_type=detail.get("discountType"),
            tags=tags_by_code.get(code, []),
        )
        if matcher is not None:
            result = matcher.match_with_size_fallback(name)
            item.match = result
            item.nutrition_code = result.fact.name if result.fact else None
        items.append(item)
    return items


def load_menu_file(path: str, matcher: Optional[NutritionMatcher] = None) -> List[Item]:
    """从 JSON 文件加载菜单。文件里可以是完整的 MCP 返回，也可以只有 ``data`` 部分。"""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    return load_menu(payload, matcher)
