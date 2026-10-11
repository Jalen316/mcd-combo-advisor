"""把菜单里的餐品名对齐到官方营养库的条目名。

这是整个 Skill 最容易出错、也最有价值的一环。

麦当劳 MCP 的两份数据源彼此独立维护，命名并不统一：

=============================== ==============================
``query-meals`` 返回的名称       ``list-nutrition-foods`` 的名称
=============================== ==============================
薯条                            中薯条
可乐                            可乐中杯
纯牛奶(盒装)                    纯牛奶（盒装）
100% 苹果汁(盒装)               100%苹果汁
麦乐鸡                          麦乐鸡5块
=============================== ==============================

直接用等号匹配会漏掉大量餐品，这里采用分层策略逐级放宽，并保留「用哪一招匹配上的」记录，
以便在推荐结果里对低置信度的搭配给出提示。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .nutrition import NutritionFact

# 全角符号 → 半角，以及需要剔除的装饰性符号
_TRANSLATION = {
    ord("（"): "(",
    ord("）"): ")",
    ord("，"): ",",
    ord("、"): ",",
    ord("："): ":",
    ord("　"): " ",
}
_STRIP_CHARS = "【】「」“”‘’…· "


#: 匹配策略的可读说明，用于向用户解释推荐依据
#: 出现这些字的餐品大概率是打包套餐，绝不能用包含关系去猜它的营养。
#:
#: 这是本项目踩过的最大的一个坑：``巨无霸三件套`` 会被 ``contains`` 策略匹配到单品
#: ``巨无霸``，于是热量报成 513 大卡，而真实值（汉堡+中薯条+可乐中杯）是 949 大卡，
#: 几乎低估一半。套餐必须走 :mod:`combo.expander` 拆解，宁可报「未知」也不能猜。
COMBO_HINTS = (
    "件套", "套餐", "双人餐", "三人餐", "多人餐", "乐园餐", "亲子餐",
    "随心配", "随心选", "随心拼", "分享餐", "小食盘", "全盒", "双拼",
)

STRATEGY_LABELS = {
    "exact": "名称完全一致",
    "alias": "命中人工核对的别名表",
    "contains": "名称包含关系推断",
    "size_default": "按默认规格推断",
    "skipped_combo": "打包套餐，需拆解后单独核算",
}


def normalize(name: str) -> str:
    """统一餐品名的书写差异：全角转半角、去空格与装饰符号、NFKC 归一。"""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name).translate(_TRANSLATION)
    text = text.strip().strip(_STRIP_CHARS)
    return re.sub(r"\s+", "", text)


@dataclass
class MatchResult:
    """一次名称匹配的结果。"""

    fact: Optional[NutritionFact]
    strategy: str
    confidence: float

    @property
    def matched(self) -> bool:
        return self.fact is not None

    @property
    def label(self) -> str:
        return STRATEGY_LABELS.get(self.strategy, "未匹配")


class NutritionMatcher:
    """在营养库中为菜单餐品查找对应条目。"""

    # 当营养库里同一餐品有多个规格、而菜单未标明规格时，按此顺序挑选默认规格
    SIZE_PREFERENCE = ("中杯", "中", "小杯", "大杯")

    def __init__(self, facts: Dict[str, NutritionFact], aliases: Optional[Dict[str, str]] = None):
        self.facts = facts
        self.aliases = aliases or {}
        self._normalized: Dict[str, NutritionFact] = {normalize(k): v for k, v in facts.items()}
        self._cache: Dict[str, MatchResult] = {}

    @classmethod
    def from_files(cls, facts, alias_path: Optional[str] = None) -> "NutritionMatcher":
        aliases: Dict[str, str] = {}
        if alias_path and os.path.exists(alias_path):
            with open(alias_path, "r", encoding="utf-8") as handle:
                aliases = json.load(handle)
        return cls(facts, aliases)

    def match(self, menu_name: str) -> MatchResult:
        if menu_name in self._cache:
            return self._cache[menu_name]

        result = self._match_uncached(menu_name)
        self._cache[menu_name] = result
        return result

    def _match_uncached(self, menu_name: str) -> MatchResult:
        key = normalize(menu_name)

        # 1) 完全一致
        fact = self._normalized.get(key)
        if fact is not None:
            return MatchResult(fact, "exact", 1.0)

        # 2) 别名表：人工核对过的高频差异，精度最高
        alias_target = self.aliases.get(key) or self.aliases.get(menu_name)
        if alias_target:
            fact = self.facts.get(alias_target) or self._normalized.get(normalize(alias_target))
            if fact is not None:
                return MatchResult(fact, "alias", 0.95)

        # 2.5) 打包套餐不许用包含关系猜营养，见 COMBO_HINTS 的说明
        if any(hint in key for hint in COMBO_HINTS):
            return MatchResult(None, "skipped_combo", 0.0)

        # 3) 包含关系：取长度差最小的一条，避免「麦乐鸡」错配到「麦乐鸡翅」
        candidates = [
            (abs(len(name) - len(key)), name, value)
            for name, value in self._normalized.items()
            if len(key) >= 2 and (key in name or name in key)
        ]
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            _, _, fact = candidates[0]
            return MatchResult(fact, "contains", 0.8)

        return MatchResult(None, "none", 0.0)

    def match_with_size_fallback(self, menu_name: str) -> MatchResult:
        """在常规匹配失败后，尝试按默认规格推断。

        例如菜单只写「可乐」，而营养库只有「可乐中杯 / 可乐大杯 / 可乐小杯」，
        此时按门店实际默认售卖的中杯处理，并给出较低置信度供下游提示。
        """
        result = self.match(menu_name)
        if result.matched:
            return result

        key = normalize(menu_name)
        for size in self.SIZE_PREFERENCE:
            fact = self._normalized.get(f"{key}{size}")
            if fact is not None:
                return MatchResult(fact, "size_default", 0.7)
        for size in self.SIZE_PREFERENCE:
            fact = self._normalized.get(f"{size}{key}")
            if fact is not None:
                return MatchResult(fact, "size_default", 0.65)
        return result

    def coverage(self, menu_names) -> Tuple[int, int, list]:
        """统计一批菜单名的营养覆盖情况，返回 ``(命中数, 总数, 未命中名单)``。"""
        missed = []
        hit = 0
        total = 0
        for name in menu_names:
            total += 1
            if self.match_with_size_fallback(name).matched:
                hit += 1
            else:
                missed.append(name)
        return hit, total, missed
