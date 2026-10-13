"""Parse the compact nutrition table returned by list-nutrition-foods."""

from __future__ import annotations

import re


def load_nutrition(data) -> list[dict]:
    if isinstance(data, str):
        return parse_toon(data)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("data"), str):
        return parse_toon(data["data"])
    return []


def parse_toon(data: str) -> list[dict]:
    """Parse a TOON table such as ``[1]{productName,energyKcal}:`` plus rows."""
    header = re.search(r"\{([^{}]+)\}", data)
    if not header:
        return []
    columns = [column.strip() for column in header.group(1).split(",") if column.strip()]
    if not columns:
        return []
    rest = data[header.end() :]
    if ":" in rest[:8]:
        rest = rest.split(":", 1)[1]
    rows = []
    for line in rest.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < len(columns):
            continue
        row = {}
        for column, part in zip(columns, parts):
            row[column] = _cell(part)
        if row.get("productName"):
            rows.append(row)
    return rows


def lookup_nutrition(rows: list[dict], name: str) -> dict | None:
    target = _norm(name)
    if not target:
        return None
    exact = [row for row in rows if _norm(str(row.get("productName") or "")) == target]
    if exact:
        return exact[0]
    contained = []
    for row in rows:
        product = _norm(str(row.get("productName") or ""))
        if product and (product in target or target in product):
            contained.append(row)
    if not contained:
        return None
    contained.sort(key=lambda row: abs(len(_norm(str(row.get("productName") or ""))) - len(target)))
    return contained[0]


def _cell(value: str):
    if value == "" or value.lower() == "null":
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()
