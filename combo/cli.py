"""命令行入口：把 JSON 化的 MCP 数据喂进来，输出可读的推荐方案。

::

    python -m combo.cli \\
        --menu examples/menu-shanghai.json \\
        --nutrition examples/nutrition.txt \\
        --budget 35 --kcal-max 900 --protein-min 25 --strategy balanced
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .planner import Plan, SCORERS
from .catalog import ROLE_LABELS
from . import RecommendationPipeline, __version__

STRATEGY_HELP = ", ".join(f"{name}" for name in SCORERS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="combo",
        description="麦当劳套餐推荐引擎：在预算与营养约束下搜索最优搭配",
    )
    parser.add_argument("--version", action="version", version=f"combo {__version__}")
    parser.add_argument("--menu", required=True, help="query-meals 保存下来的 JSON 文件")
    parser.add_argument("--nutrition", required=True, help="list-nutrition-foods 保存下来的文本文件")
    parser.add_argument("--aliases", help="餐品别名表 JSON，用于修正两套数据源的命名差异")
    parser.add_argument(
        "--combo-details",
        help="套餐拆解数据 JSON（query-meal-detail 的批量结果），传入后套餐也能算热量",
    )
    parser.add_argument("--budget", type=float, required=True, help="预算上限（元）")
    parser.add_argument("--people", type=int, default=1, help="用餐人数，默认 1")
    parser.add_argument("--kcal-min", type=float, help="每人热量下限（大卡）")
    parser.add_argument("--kcal-max", type=float, help="每人热量上限（大卡）")
    parser.add_argument("--protein-min", type=float, help="每人蛋白质下限（克）")
    parser.add_argument("--sodium-max", type=float, help="每人钠上限（毫克）")
    parser.add_argument("--drink", action="store_true", help="是否必须搭配饮品")
    parser.add_argument("--exclude", default="", help="忌口关键字，逗号分隔，例如 辣,牛肉")
    parser.add_argument("--strategy", default="balanced", help=f"打分策略：{STRATEGY_HELP}")
    parser.add_argument("--top", type=int, default=3, help="返回方案数量")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON 而非表格")
    parser.add_argument("--coverage", action="store_true", help="只输出营养覆盖率诊断")
    return parser


def render_text(plans: List[Plan], budget: float, people: int) -> str:
    if not plans:
        return "没有找到满足条件的组合。可以试着：放宽预算上下限、去掉热量约束、或减少人数。"

    lines = [f"预算 ¥{budget:.1f} / {people} 人，为你找到 {len(plans)} 个方案：", ""]
    for index, plan in enumerate(plans, start=1):
        lines.append(f"方案 {index}　合计 ¥{plan.total_price:.1f}　评分 {plan.score:.2f}")
        for item in plan.items:
            kcal_text = f"{item.kcal:.0f} 大卡" if item.kcal is not None else "热量未知"
            lines.append(
                f"  · [{ROLE_LABELS.get(item.role, item.role)}] {item.name}"
                f"　¥{item.price:.1f}　{kcal_text}"
            )
        if plan.total_kcal is not None:
            lines.append(
                f"  小计：{plan.total_kcal:.0f} 大卡"
                f"（人均 {plan.total_kcal / people:.0f}）"
                f"　蛋白质 {plan.total_protein:.0f} 克"
            )
        if plan.total_saved > 0:
            lines.append(f"  优惠：省 ¥{plan.total_saved:.1f}")
        lines.append("  理由：" + "；".join(plan.reasons))
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        pipeline = RecommendationPipeline.from_files(
            args.menu, args.nutrition, args.aliases, args.combo_details
        )
    except FileNotFoundError as error:
        print(f"读取失败：{error}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"菜单文件不是合法 JSON：{error}", file=sys.stderr)
        return 2

    if args.coverage:
        print(json.dumps(pipeline.coverage_report(), ensure_ascii=False, indent=2))
        return 0

    exclude = [word.strip() for word in args.exclude.split(",") if word.strip()]
    try:
        plans = pipeline.recommend(
            budget=args.budget,
            people=args.people,
            kcal_min=args.kcal_min,
            kcal_max=args.kcal_max,
            protein_min=args.protein_min,
            sodium_max=args.sodium_max,
            require_drink=args.drink,
            exclude_keywords=exclude,
            strategy=args.strategy,
            top_n=args.top,
        )
    except ValueError as error:
        print(f"参数有误：{error}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False, indent=2))
    else:
        print(render_text(plans, args.budget, args.people))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
