"""麦当劳套餐推荐引擎。

把 ``query-meals``（菜单与实时价格）、``list-nutrition-foods``（营养成分）、
以及优惠券数据三份异构信息对齐后，在预算与营养约束下搜索最优套餐组合。

典型用法::

    from combo import RecommendationPipeline

    pipeline = RecommendationPipeline.from_files("menu.json", "nutrition.txt")
    plans = pipeline.recommend(budget=35, kcal_max=900, people=1, strategy="balanced")
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .catalog import Item, load_menu, load_menu_file
from .expander import (
    DerivedNutrition,
    attach_combo_nutrition,
    compact_detail,
    derive_combo_nutrition,
    load_details_file,
)
from .matcher import NutritionMatcher
from .nutrition import NutritionFact, load_nutrition_file, parse_nutrition_text
from .planner import Constraints, Plan, SCORERS, generate_plans

__version__ = "1.0.0"

__all__ = [
    "RecommendationPipeline",
    "Constraints",
    "Plan",
    "Item",
    "NutritionFact",
    "NutritionMatcher",
    "load_menu",
    "load_menu_file",
    "load_nutrition_file",
    "parse_nutrition_text",
    "generate_plans",
    "SCORERS",
    "attach_combo_nutrition",
    "compact_detail",
    "derive_combo_nutrition",
    "load_details_file",
    "DerivedNutrition",
]

DEFAULT_ALIAS_PATH = "combo/data/aliases.json"


class RecommendationPipeline:
    """串联营养解析、名称匹配与组合搜索的入口类。"""

    def __init__(self, items: List[Item], matcher: NutritionMatcher):
        self.items = items
        self.matcher = matcher
        #: 通过套餐拆解补齐营养的商品数量
        self.expanded_combos: int = 0

    @classmethod
    def from_payload(
        cls,
        menu_payload: Dict[str, Any],
        nutrition_text: str,
        aliases: Optional[Dict[str, str]] = None,
    ) -> "RecommendationPipeline":
        """直接用 MCP 返回的内存数据构建。"""
        facts = parse_nutrition_text(nutrition_text)
        matcher = NutritionMatcher(facts, aliases)
        if isinstance(menu_payload, dict) and "data" in menu_payload:
            menu_payload = menu_payload["data"]
        items = load_menu(menu_payload, matcher)
        return cls(items, matcher)

    @classmethod
    def from_files(
        cls,
        menu_path: str,
        nutrition_path: str,
        alias_path: Optional[str] = None,
        combo_details_path: Optional[str] = None,
    ) -> "RecommendationPipeline":
        """从磁盘上的菜单 JSON 与营养文本构建，便于离线测试。

        ``combo_details_path`` 指向 ``query-meal-detail`` 的批量裁剪结果，
        提供后打包套餐也能算出热量，覆盖率会明显提升。
        """
        facts = load_nutrition_file(nutrition_path)
        matcher = NutritionMatcher.from_files(facts, alias_path)
        items = load_menu_file(menu_path, matcher)
        expanded = 0
        if combo_details_path:
            details = load_details_file(combo_details_path)
            expanded = attach_combo_nutrition(items, details, matcher)
        pipeline = cls(items, matcher)
        pipeline.expanded_combos = expanded
        return pipeline

    def coverage_report(self) -> Dict:
        """统计菜单的营养覆盖率，用于判断热量结论的可信度。

        这里统计的是「最终是否拿得到热量」，直接单品命中与套餐拆解补齐都算数。
        """
        foods = [item for item in self.items if item.is_food]
        missed = [item.name for item in foods if not item.nutrition_known]
        total = len(foods)
        hit = total - len(missed)
        return {
            "total_food_items": total,
            "matched": hit,
            "coverage": round(hit / total, 4) if total else 0.0,
            "resolved_via_combo_expansion": self.expanded_combos,
            "missed_examples": missed[:12],
        }

    def recommend(
        self,
        budget: float,
        people: int = 1,
        kcal_min: Optional[float] = None,
        kcal_max: Optional[float] = None,
        protein_min: Optional[float] = None,
        sodium_max: Optional[float] = None,
        require_drink: bool = False,
        exclude_keywords: Sequence[str] = (),
        strategy: str = "balanced",
        top_n: int = 5,
    ) -> List[Plan]:
        constraints = Constraints(
            budget=budget,
            people=people,
            kcal_min=kcal_min,
            kcal_max=kcal_max,
            protein_min=protein_min,
            sodium_max=sodium_max,
            require_drink=require_drink,
            exclude_keywords=exclude_keywords,
        )
        return generate_plans(self.items, constraints, strategy=strategy, top_n=top_n)
