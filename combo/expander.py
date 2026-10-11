"""把打包套餐拆解成标准子餐品，从而算出它真实的热量与蛋白质。

``list-nutrition-foods`` 里只有单品，没有任何打包套餐。官方菜单却又以三件套、四件套为主，
直接沿用单品匹配会导致最常被点的商品全部「营养未知」——这正是套餐推荐做不准的根因。

解法是调用 ``query-meal-detail``，它会把套餐拆成若干轮选配::

    rounds[].choices[].name   —— 子餐品名
    rounds[].choices[].isDefault —— 是否为默认搭配

而这些子餐品名（``中薯条``、``可乐中杯``）恰好就是营养库里的标准写法，
因此拆完再逐项累加，就能得到套餐的真实营养。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .matcher import NutritionMatcher


@dataclass
class DerivedNutrition:
    """由子餐品累加得到的套餐营养。"""

    kcal: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbohydrate: Optional[float] = None
    sodium: Optional[float] = None
    calcium: Optional[float] = None
    components: List[str] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        """是否所有成分都查到了营养数据，差一项就只能算部分可信。"""
        return not self.unresolved

    @property
    def confidence(self) -> float:
        total = len(self.components) + len(self.unresolved)
        return round(len(self.components) / total, 2) if total else 0.0


def iter_default_components(detail: Dict[str, Any]) -> Iterable[str]:
    """取出一个套餐详情里所有默认选中的子餐品名。"""
    if not isinstance(detail, dict):
        return []
    data = detail.get("data", detail)
    for round_info in data.get("rounds") or []:
        for choice in round_info.get("choices") or []:
            if choice.get("isDefault") == 1 and int(choice.get("quantity") or 0) >= 1:
                name = (choice.get("name") or "").strip()
                if name:
                    yield name


def compact_detail(detail: Dict[str, Any]) -> Dict:
    """把冗长的套餐详情裁剪成只有 `{code, name, components}` 的紧凑结构。

    原始返回里混着大量 ``modification`` 特调选项，与营养计算无关却占据绝大部分体积，
    落盘前先裁掉。
    """
    data = detail.get("data", detail) if isinstance(detail, dict) else {}
    return {
        "code": data.get("code"),
        "name": data.get("name"),
        "components": list(iter_default_components(detail)),
    }


def derive_combo_nutrition(detail: Dict[str, Any], matcher: NutritionMatcher) -> DerivedNutrition:
    """累加套餐各默认成分的营养。"""
    result = DerivedNutrition()
    if isinstance(detail, dict) and "components" in detail:
        components = list(detail.get("components") or [])
    else:
        components = list(iter_default_components(detail))

    for name in components:
        match = matcher.match_with_size_fallback(name)
        fact = match.fact
        if fact is None:
            result.unresolved.append(name)
            continue
        result.components.append(name)
        for attr in ("kcal", "protein", "fat", "carbohydrate", "sodium", "calcium"):
            value = getattr(fact, f"energy_{attr}" if attr == "kcal" else attr)
            if value is None:
                continue
            setattr(result, attr, round((getattr(result, attr) or 0.0) + value, 2))
    if not result.components:
        result.kcal = None
    return result


def attach_combo_nutrition(
    items, details: Dict[str, Any], matcher: NutritionMatcher
) -> int:
    """为套餐类商品补上拆解所得的营养，返回成功补齐的数量。

    ``details`` 支持两种形态：

    * ``query-meal-detail`` 的原始返回；
    * :func:`compact_detail` 裁剪后的 ``{code: {name, components}}``。
    """
    attached = 0
    for item in items:
        if item.code not in details:
            continue
        derived = derive_combo_nutrition(details[item.code], matcher)
        if not derived.components:
            continue
        # 拆解结果是准确值，直接顶掉任何来自单品名称的猜测
        item.derived = derived
        item.match = None
        item.nutrition_code = None
        attached += 1
    return attached


def load_details_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "details" in payload:
        payload = payload["details"]
    return payload
