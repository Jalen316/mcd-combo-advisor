"""在预算与营养双重约束下搜索最优套餐组合。

组合这件事看起来是背包问题，但真实点餐有几个约束让它比纯背包简单得多：

* 每个角色最多点一两件（没人会点三个汉堡当午餐）；
* 单价超过预算的餐品可以直接伐掉；
* 用户关心的是「给我三五个能解释为什么好的方案」，不是全局唯一最优解。

所以这里用「按角色选候选 + 有限笛卡尔积 + 实时剪枝」的组合搜索，
既能在毫秒级跑完，又能保证结果可解释。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .catalog import ROLE_COMBO, ROLE_DRINK, ROLE_MAIN, ROLE_SIDE, ROLE_SHARE, Item

# 中国居民膳食指南常用的一餐参考量（单位：毫克）
RECOMMENDED_SODIUM_PER_MEAL = 2000.0

#: 一顿正餐的能量下限（每人，大卡）。低于这个数基本等于没吃，
#: 用来兜住 cheapest 这类只追求低价的策略——否则它会推出「一瓶水加一份蘸酱」。
MIN_MEAL_KCAL_PER_PERSON = 250.0


@dataclass
class Constraints:
    """用户的点餐约束。"""

    budget: float                       # 整单总预算（元），**不随人数翻倍**
    people: int = 1                     # 人数
    kcal_min: Optional[float] = None    # 热量下限（大卡，按每人计）
    kcal_max: Optional[float] = None    # 热量上限（大卡，按每人计）
    protein_min: Optional[float] = None # 每人的蛋白质下限（克）
    sodium_max: Optional[float] = None  # 每人的钠上限（毫克）
    require_main: bool = True           # 是否必须含主餐
    require_drink: bool = False         # 是否必须含饮品
    max_sides: int = 2                  # 小食最多几件
    exclude_keywords: Sequence[str] = ()# 忌口关键字
    candidate_limit: int = 24           # 每个角色最多考虑多少候选，控制搜索规模

    def scaled(self) -> "Constraints":
        """把「每人」口径的营养目标乘以人数，得到整单目标。

        预算是整单口径，不参与换算——用户说「两个人 60 块」指的是总共 60 块。
        """
        return Constraints(
            budget=self.budget,
            people=self.people,
            kcal_min=self.kcal_min * self.people if self.kcal_min else None,
            kcal_max=self.kcal_max * self.people if self.kcal_max else None,
            protein_min=self.protein_min * self.people if self.protein_min else None,
            sodium_max=self.sodium_max * self.people if self.sodium_max else None,
            require_main=self.require_main,
            require_drink=self.require_drink,
            max_sides=self.max_sides,
            exclude_keywords=self.exclude_keywords,
            candidate_limit=self.candidate_limit,
        )


@dataclass
class Plan:
    """一个推荐方案。"""

    items: List[Item]
    total_price: float = 0.0
    total_kcal: Optional[float] = None
    total_protein: Optional[float] = None
    total_sodium: Optional[float] = None
    total_saved: float = 0.0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def nutrition_known(self) -> bool:
        return all(item.nutrition_known for item in self.items)

    def to_dict(self) -> Dict:
        return {
            "items": [
                {
                    "code": item.code,
                    "name": item.name,
                    "role": item.role,
                    "price": item.price,
                    "original_price": item.original_price,
                    "kcal": item.kcal,
                    "protein": item.protein,
                    "nutrition_source": item.match.label if item.match else "未匹配",
                    "nutrition_confidence": item.match.confidence if item.match else 0.0,
                }
                for item in self.items
            ],
            "total_price": self.total_price,
            "total_kcal": self.total_kcal,
            "total_protein": self.total_protein,
            "total_sodium": self.total_sodium,
            "total_saved": self.total_saved,
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


SCORERS: Dict[str, Callable[[Plan, Constraints], float]] = {}


def register(name: str):
    def wrapper(func: Callable[[Plan, Constraints], float]):
        SCORERS[name] = func
        return func

    return wrapper


@register("balanced")
def score_balanced(plan: Plan, c: Constraints) -> float:
    """默认策略：吃好吃饱、别超支、别太咸。"""
    score = 0.0
    # 预算利用率：接近但不超预算最好，剩太多说明没喂饱
    usage = plan.total_price / c.budget if c.budget else 0.0
    score += max(0.0, 1.0 - abs(1.0 - usage)) * 3.0
    # 省下的钱
    score += min(plan.total_saved / 20.0, 1.0) * 1.5
    # 蛋白质给足加分
    if plan.total_protein is not None and c.protein_min:
        score += min(plan.total_protein / c.protein_min, 1.5) * 1.2
    elif plan.total_protein is not None:
        score += min(plan.total_protein / 30.0, 1.0) * 0.8
    # 钠超标扣分
    if plan.total_sodium is not None and plan.total_sodium > RECOMMENDED_SODIUM_PER_MEAL * c.people:
        score -= min((plan.total_sodium - RECOMMENDED_SODIUM_PER_MEAL * c.people) / 1000.0, 2.0)
    return score


@register("protein")
def score_protein(plan: Plan, c: Constraints) -> float:
    """增肌取向：单位价格的蛋白质最大化。"""
    if not plan.total_protein:
        return -99.0
    density = plan.total_protein / plan.total_price if plan.total_price else 0.0
    return density * 10.0 + min(plan.total_protein / 50.0, 1.0) * 2.0


@register("light")
def score_light(plan: Plan, c: Constraints) -> float:
    """减脂取向：热量越低越好，同时保底蛋白质不至于饿。"""
    if plan.total_kcal is None:
        return -99.0
    score = 1000.0 / max(plan.total_kcal, 1.0)
    if plan.total_protein is not None and c.protein_min:
        score += min(plan.total_protein / c.protein_min, 1.2)
    return score


@register("cheapest")
def score_cheapest(plan: Plan, c: Constraints) -> float:
    """省钱取向：摁着预算花最少。"""
    if plan.total_price <= 0:
        return -99.0
    return (c.budget / plan.total_price) * 2.0 + min(plan.total_saved / 15.0, 1.0)


def explain(plan: Plan, c: Constraints, strategy: str) -> List[str]:
    """把推荐理由写成用户能读懂的短句。"""
    reasons: List[str] = []
    leftover = round(c.budget - plan.total_price, 2)
    if leftover >= 0:
        reasons.append(f"合计 ¥{plan.total_price:.1f}，预算还剩 ¥{leftover:.1f}")
    else:
        reasons.append(f"合计 ¥{plan.total_price:.1f}，超出预算 ¥{abs(leftover):.1f}")

    if plan.total_saved > 0:
        reasons.append(f"相比原价省 ¥{plan.total_saved:.1f}")

    if plan.total_kcal is not None:
        per_person = plan.total_kcal / c.people
        reasons.append(f"人均约 {per_person:.0f} 大卡")
        if c.kcal_max and plan.total_kcal <= c.kcal_max:
            reasons.append("热量控制在目标以内")
        if c.kcal_min and plan.total_kcal >= c.kcal_min:
            reasons.append("热量达到你设定的下限")

    if plan.total_protein is not None:
        reasons.append(f"蛋白质 {plan.total_protein:.0f} 克")

    if plan.total_sodium is not None and plan.total_sodium > RECOMMENDED_SODIUM_PER_MEAL * c.people:
        reasons.append(f"注意钠含量较高（{plan.total_sodium:.0f} 毫克），建议搭配无糖饮品")

    low_confidence = [i.name for i in plan.items if i.match and i.match.strategy == "size_default"]
    if low_confidence:
        reasons.append(f"其中「{'、'.join(low_confidence)}」的营养按默认规格估算")
    return reasons


def _candidates(items: List[Item], role: str, c: Constraints) -> List[Item]:
    pool = [
        item
        for item in items
        if item.role == role and item.is_food and item.price <= c.budget
    ]
    for keyword in c.exclude_keywords:
        pool = [item for item in pool if keyword not in item.name]

    if c.kcal_max or c.kcal_min or c.protein_min:
        # 有营养约束时，剔掉查不到营养数据的餐品，否则算出来的热量是假的
        known = [item for item in pool if item.nutrition_known]
        pool = known or pool
    elif role == ROLE_MAIN:
        # 即便没有显式营养约束，主餐也必须知道热量——否则「一份蘸酱」也能当作正餐凑进去
        pool = [item for item in pool if item.nutrition_known]

    pool.sort(key=lambda item: (-item.discount_amount, item.price))
    return pool[: c.candidate_limit]


def _build_plan(combination: Sequence[Item], c: Constraints, strategy: str) -> Optional[Plan]:
    items = list(combination)
    if not items:
        return None

    price = round(sum(item.price for item in items), 2)
    if price > c.budget:
        return None

    saved = round(sum(item.discount_amount for item in items), 2)
    kcal = round(sum(item.kcal for item in items), 1) if all(i.nutrition_known for i in items) else None
    protein = round(sum(item.protein for item in items), 1) if all(i.nutrition_known for i in items) else None
    sodium = round(sum(item.sodium for item in items), 1) if all(i.nutrition_known for i in items) else None

    # 兜底：热量已知却低到不像一顿饭的方案直接淘汰
    if kcal is not None and kcal < MIN_MEAL_KCAL_PER_PERSON * c.people:
        return None

    if c.kcal_max is not None and kcal is not None and kcal > c.kcal_max:
        return None
    if c.kcal_min is not None and kcal is not None and kcal < c.kcal_min:
        return None
    if c.protein_min is not None and protein is not None and protein < c.protein_min:
        return None
    if c.sodium_max is not None and sodium is not None and sodium > c.sodium_max:
        return None

    roles = {item.role for item in items}
    if c.require_main and not (roles & {ROLE_MAIN, ROLE_COMBO, ROLE_SHARE}):
        return None
    if c.require_drink and ROLE_DRINK not in roles:
        return None

    plan = Plan(
        items=items,
        total_price=price,
        total_kcal=kcal,
        total_protein=protein,
        total_sodium=sodium,
        total_saved=saved,
    )
    plan.score = SCORERS.get(strategy, score_balanced)(plan, c)
    plan.reasons = explain(plan, c, strategy)
    return plan


def diversify(plans: List[Plan], top_n: int, max_repeat: int = 1) -> List[Plan]:
    """让返回结果的搭配不要长得太像。

    打分函数天然偏爱折扣最大的那几件（例如全场折扣最狠的某款饮品会被每个方案都选中），
    连看三条推荐里三杯同款饮料，对用户毫无参考价值。
    这里限制同一杯饮品最多出现 ``max_repeat`` 次，凑不够数量时再退回原排序。
    """
    if max_repeat <= 0 or top_n <= 1:
        return plans[:top_n]

    counts: Dict[tuple, int] = {}
    seen: set = set()
    result: List[Plan] = []
    for plan in plans:
        signature = tuple(sorted(item.code for item in plan.items))
        if signature in seen:
            continue
        drinks = tuple(sorted(item.code for item in plan.items if item.role == ROLE_DRINK))
        if drinks and counts.get(drinks, 0) >= max_repeat:
            continue
        seen.add(signature)
        if drinks:
            counts[drinks] = counts.get(drinks, 0) + 1
        result.append(plan)
        if len(result) >= top_n:
            break
    return result if len(result) >= min(top_n, len(plans)) else plans[:top_n]


def generate_plans(
    items: List[Item],
    constraints: Constraints,
    strategy: str = "balanced",
    top_n: int = 5,
    diversify_drinks: bool = True,
) -> List[Plan]:
    """枚举所有可行组合并返回评分最高的若干方案。"""
    c = constraints.scaled()
    mains = _candidates(items, ROLE_MAIN, c)
    sides = _candidates(items, ROLE_SIDE, c)
    drinks = _candidates(items, ROLE_DRINK, c)
    combos = [
        item for item in items
        if item.role in (ROLE_COMBO, ROLE_SHARE)
        and item.is_food
        and item.price <= c.budget
    ]
    # 套餐要用它顶替正餐，就也得知道热量，否则 10 块钱的「饮品随心选」也能冒充一顿饭。
    # 但如果一个套餐都没有营养数据（例如没做拆解），降级保留全部，至少还有东西可推荐。
    combos_with_nutrition = [item for item in combos if item.nutrition_known]
    if combos_with_nutrition:
        combos = combos_with_nutrition

    plans: List[Plan] = []

    # 一) 单点搭配：主餐 + 0~N 小食 + 0~1 饮品
    side_options: List[tuple] = [()]
    limit = min(c.max_sides, len(sides))
    for count in range(1, limit + 1):
        side_options.extend(itertools.combinations(sides, count))

    for main, side_pick, drink in itertools.product(mains, side_options, [None] + drinks):
        pieces = [main, *side_pick]
        if drink is not None:
            pieces.append(drink)
        plan = _build_plan(pieces, c, strategy)
        if plan is not None:
            plans.append(plan)

    # 二) 现成套餐，可再补一杯饮品
    for combo in combos:
        for drink in [None] + drinks:
            pieces = [combo] if drink is None else [combo, drink]
            plan = _build_plan(pieces, c, strategy)
            if plan is not None:
                plans.append(plan)

    plans.sort(key=lambda plan: (-plan.score, plan.total_price))
    ranked = plans[:top_n]
    if diversify_drinks:
        ranked = diversify(plans, top_n)
    return ranked
