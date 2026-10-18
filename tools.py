import ast
import difflib
import json
import math
import operator
import re
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "data" / "corpus.jsonl"

# Three writing styles appear in the 2025 district profiles.
PATTERNS = {
    "population_2020": [
        r"2020 census had recorded\s+([\d,]+)",
        r"Census population:\s+([\d,]+)\s+in 2020",
        r"up from\s+([\d,]+)\s+at the 2020 census",
    ],
    "population_2025": [
        r"2025 census recorded\s+([\d,]+)",
        r"Census population:\s+[\d,]+\s+in 2020 and\s+([\d,]+)\s+in 2025",
        r"2025 census counted\s+([\d,]+)",
    ],
    "area_km2": [r"([\d,]+(?:\.\d+)?)\s+square kilometres"],
    "founded_year": [r"founded in\s+(\d{4})"],
    "budget_2025_million_tenge": [
        r"(?:budget for 2025 was set at|2025 budget amounted to|2025 budget was)\s+([\d,]+(?:\.\d+)?)\s+million tenge"
    ],
    "household_water_2025_megalitres": [
        r"(?:came to|used)\s+([\d,]+(?:\.\d+)?)\s+(megalitres|cubic metres)"
    ],
    "schools_2025_26": [r"(?:There are|has|runs)\s+([\d,]+)\s+schools"],
    "pupils_2025_26": [r"(?:attended by|enrolled|schools with)\s+([\d,]+)\s+pupils"],
    "paved_road_km": [
        r"(?:paved road network is|keeps|maintains)\s+([\d,]+(?:\.\d+)?)\s+km"
    ],
    "water_tariff_2025_tenge_per_m3": [
        r"2025 the regional household water tariff is\s+([\d,]+(?:\.\d+)?)\s+tenge per cubic metre"
    ],
}

UNITS = {
    "population_2020": "residents",
    "population_2025": "residents",
    "area_km2": "km2",
    "founded_year": "year",
    "budget_2025_million_tenge": "million tenge",
    "household_water_2025_megalitres": "megalitres",
    "schools_2025_26": "schools",
    "pupils_2025_26": "pupils",
    "paved_road_km": "km",
    "water_tariff_2025_tenge_per_m3": "tenge per m3",
}
INTEGER_FIELDS = {
    "population_2020", "population_2025", "founded_year",
    "schools_2025_26", "pupils_2025_26",
}


def _find_value(text, field):
    """Extract a field from one profile. None means no matching pattern."""
    for pattern in PATTERNS[field]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1).replace(",", ""))
            if not math.isfinite(value):
                raise ValueError("number is not finite")
            if field in INTEGER_FIELDS:
                if not value.is_integer():
                    raise ValueError("expected an integer")
                value = int(value)
            unit = match.group(2).lower() if field == "household_water_2025_megalitres" else None
            return value, unit
    return None


def lookup(entity, field):
    """Return one current documented fact, or an explanatory error dict."""
    try:
        if not isinstance(entity, str) or not 0 < len(entity.strip()) <= 60:
            return {"error": "entity must be a non-empty string (max 60 characters)"}
        if not isinstance(field, str) or field not in PATTERNS:
            return {"error": f"unknown field: {field!r}; allowed: {list(PATTERNS)}"}

        with CORPUS.open("r", encoding="utf-8") as file:
            docs = [json.loads(line) for line in file if line.strip()]

        wanted = entity.strip().casefold()
        if wanted == "region":
            if field != "water_tariff_2025_tenge_per_m3":
                return {"error": "'region' only supports water_tariff_2025_tenge_per_m3"}
            matches = [d for d in docs if d["id"] == "NR-15"]
            display_name = "region"
        else:
            if field == "water_tariff_2025_tenge_per_m3":
                return {"error": "for the common regional tariff, use entity='region'"}
            profiles = {}
            for d in docs:
                title = d.get("title", "")
                if title.endswith(" district profile (2025)") and "ARCHIVED" not in title.upper():
                    name = title.split(" district profile", 1)[0]
                    profiles.setdefault(name, []).append(d)
            names = sorted(profiles)
            exact = [name for name in names if name.casefold() == wanted]
            if not exact:
                partial = [name for name in names if wanted in name.casefold()]
                if len(partial) > 1:
                    return {"error": f"ambiguous district {entity!r}; choose: {', '.join(partial)}"}
                suggestions = difflib.get_close_matches(entity.strip(), names, n=3, cutoff=0.55)
                return {"error": f"no district named {entity!r}; suggestions: {suggestions}"}
            display_name = exact[0]
            matches = profiles[display_name]

        if len(matches) != 1:
            return {"error": "expected exactly one current source document"}
        doc = matches[0]
        found = _find_value(doc["text"], field)
        if found is None:
            return {"error": f"could not read {field} from {doc['id']}"}
        value, original_unit = found
        sources = [doc["id"]]

        # A correction notice overrides the originally printed area.
        if field == "area_km2":
            for notice in docs:
                if notice.get("title", "").casefold() == f"correction notice - {display_name}".casefold():
                    corrected = re.search(
                        r"correct area is\s+([\d,]+(?:\.\d+)?)\s+square kilometres",
                        notice["text"], flags=re.IGNORECASE,
                    )
                    if not corrected:
                        return {"error": f"cannot parse correction in {notice['id']}"}
                    value = float(corrected.group(1).replace(",", ""))
                    sources.append(notice["id"])

        # Lindenmoor reports cubic metres; other profiles report megalitres.
        if field == "household_water_2025_megalitres":
            if original_unit == "cubic metres":
                value /= 1000
                sources.append("NR-15")  # This document defines the conversion.
            elif original_unit != "megalitres":
                return {"error": f"unknown water unit: {original_unit!r}"}

        return {
            "entity": display_name,
            "field": field,
            "value": value,
            "unit": UNITS[field],
            "sources": sources,
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"error": f"lookup failed: {type(exc).__name__}: {exc}"}


OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
MAX_ABS = 1e15


def calculate(expression):
    """Evaluate only +, -, *, /, parentheses and numeric literals via AST."""
    if not isinstance(expression, str) or not 0 < len(expression.strip()) <= 120:
        return {"error": "expression must be a non-empty string (max 120 characters)"}
    try:
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 40:
            return {"error": "expression has too many parts"}

        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                result = float(node.value)
            elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
                number = evaluate(node.operand)
                result = number if isinstance(node.op, ast.UAdd) else -number
            elif isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
                left, right = evaluate(node.left), evaluate(node.right)
                result = OPERATORS[type(node.op)](left, right)
            else:
                raise ValueError("unsupported operation: only +, -, *, / and numbers are allowed")
            if not math.isfinite(result) or abs(result) > MAX_ABS:
                raise ValueError("numeric result is too large or not finite")
            return result

        return {"value": evaluate(tree.body)}
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return {"error": f"calculation failed: {type(exc).__name__}: {exc}"}
