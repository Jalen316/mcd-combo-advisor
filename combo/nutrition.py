"""解析麦当劳 MCP ``list-nutrition-foods`` 工具返回的结构化文本。

该工具的 ``data`` 字段是一段形如 ``[160]{表头}:`` 后跟若干数据行的文本，而不是 JSON：
每行由逗号分隔，首字段是餐品名，末七个字段是数值，中间可能夹着一个自由文本描述。

::

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      巨无霸,null,2146,513,27,26,42,961,171
      山芋派,能量约清水1杯,971,232,2,12,28,159,7

之所以不直接按 ``split(",")`` 取固定下标，是因为 ``nutritionDescription`` 是自由文本，
一旦其中出现逗号，整行解析就会错位。这里改成「从左取名字、从右取七个数值、中间全部归描述」，
即使描述里含有逗号也不会串列。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

#: 数值字段的顺序，与官方表头尾部保持一致（单位见各字段注释）。
NUMERIC_FIELDS = (
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
)

#: 官方表头是 camelCase，数据类字段用 snake_case，这里建立两者的对应关系。
FIELD_ATTRS = {
    field: re.sub(r"(?<!^)(?=[A-Z])", "_", field).lower() for field in NUMERIC_FIELDS
}

_HEADER_RE = re.compile(r"^\s*\[\s*(\d+)\s*\]")
_NULL_TOKENS = {"", "null", "NULL", "None", "-", "--"}


@dataclass(frozen=True)
class NutritionFact:
    """单个餐品的营养成分。"""

    name: str
    note: Optional[str] = None
    energy_kj: Optional[float] = None
    energy_kcal: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbohydrate: Optional[float] = None
    sodium: Optional[float] = None
    calcium: Optional[float] = None

    @property
    def is_complete(self) -> bool:
        """七大营养素是否齐全，缺失任一项都无法参与热量/蛋白质核算。"""
        return all(
            getattr(self, field) is not None
            for field in ("energy_kcal", "protein", "fat", "carbohydrate", "sodium", "calcium")
        )


def _to_number(raw: str) -> Optional[float]:
    raw = raw.strip()
    if raw in _NULL_TOKENS:
        return None
    try:
        return float(raw)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", raw)
        return float(match.group()) if match else None


def _split_row(line: str) -> Optional[tuple]:
    """把一行数据拆成 ``(name, note, numbers...)``。无法识别时返回 ``None``。"""
    parts = [part.strip() for part in line.split(",")]
    # 至少要能凑齐：名字 + 七个数值
    if len(parts) < len(NUMERIC_FIELDS) + 1:
        return None

    numbers: list[Optional[float]] = []
    consumed = 0
    for raw in reversed(parts):
        if consumed == len(NUMERIC_FIELDS):
            break
        consumed += 1
        numbers.append(_to_number(raw))
    numbers.reverse()

    head = parts[: len(parts) - len(NUMERIC_FIELDS)]
    if not head:
        return None

    name = head[0]
    note = ",".join(head[1:]).strip() if len(head) > 1 else ""
    note = None if note in _NULL_TOKENS else note
    if not name:
        return None
    return (name, note, *numbers)


def parse_nutrition_text(text: str) -> Dict[str, NutritionFact]:
    """把 MCP 返回的营养文本解析成 ``{餐品名: NutritionFact}``。

    官方数据中存在完全重复的行（例如 ``纯牛奶（盒装）``），这里保留首次出现的记录，
    并对内容互相矛盾的重复项保留营养信息更完整的一条。
    """
    facts: Dict[str, NutritionFact] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 形如 "[160]{...}:" 的表头直接跳过
        if _HEADER_RE.match(line) or line.endswith("}:") or line.startswith("#"):
            continue

        row = _split_row(line)
        if row is None:
            continue

        name, note = row[0], row[1]
        values = row[2:]
        fact = NutritionFact(
            name=name,
            note=note,
            **{FIELD_ATTRS[field]: value for field, value in zip(NUMERIC_FIELDS, values)},
        )
        existing = facts.get(name)
        if existing is None or (fact.is_complete and not existing.is_complete):
            facts[name] = fact
    return facts


def load_nutrition_file(path: str) -> Dict[str, NutritionFact]:
    """从磁盘读取营养文本。文件应为 UTF-8 编码的 MCP 原始返回内容。"""
    with open(path, "r", encoding="utf-8") as handle:
        return parse_nutrition_text(handle.read())


def iter_facts(facts: Dict[str, NutritionFact]) -> Iterable[NutritionFact]:
    return facts.values()
