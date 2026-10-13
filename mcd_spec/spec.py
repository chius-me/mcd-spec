"""Meal spec: budget, calories, avoidances, and where to order."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


class SpecError(ValueError):
    """The spec is missing a required field or has a bad value."""


SCENES = ("pickup", "drive", "delivery")

_SCENE_ALIASES = {
    "pickup": "pickup",
    "drive": "drive",
    "delivery": "delivery",
    "到店": "pickup",
    "到店取餐": "pickup",
    "自取": "pickup",
    "得来速": "drive",
    "外送": "delivery",
    "麦乐送": "delivery",
}


@dataclass
class Spec:
    budget_yuan: float | None = None
    max_kcal: int | None = None
    avoid: tuple[str, ...] = ()
    drink: str | None = None
    want: tuple[str, ...] = ()
    quantity: int = 1
    scene: str = "pickup"
    city: str | None = None
    place: str | None = None
    store_code: str | None = None
    be_code: str | None = None
    address_id: str | None = None
    use_coupon: bool = True

    def validate(self, *, demo: bool = False) -> None:
        if self.quantity < 1:
            raise SpecError("数量至少为 1")
        if self.budget_yuan is not None and self.budget_yuan <= 0:
            raise SpecError("预算必须大于 0")
        if self.max_kcal is not None and self.max_kcal <= 0:
            raise SpecError("热量上限必须大于 0")
        if self.scene not in SCENES:
            raise SpecError("场景只能是 pickup、drive 或 delivery")
        if demo:
            return
        pinned = bool(self.store_code and self.be_code)
        if self.scene in ("pickup", "drive") and not pinned:
            if not self.city or not self.place:
                raise SpecError(
                    "到店或得来速需要 --city 和 --place，或同时提供 --store-code 与 --be-code"
                )
        if self.scene == "delivery" and not pinned and not self.address_id:
            # A single saved address can still be resolved later. Multiple
            # addresses fail in the catalog with ids the user can copy.
            return


def split_terms(value: str) -> tuple[str, ...]:
    parts = re.split(r"[,，、]+", value.strip())
    return tuple(part.strip() for part in parts if part.strip())


def parse_mapping(data: dict) -> Spec:
    if not isinstance(data, dict):
        raise SpecError("规格必须是一个对象")

    def pick(*keys):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        return None

    scene_raw = pick("scene", "场景") or "pickup"
    scene = _SCENE_ALIASES.get(str(scene_raw).strip(), None)
    if scene is None:
        raise SpecError(f"无法识别的场景：{scene_raw}")

    avoid = pick("avoid", "不要") or []
    want = pick("want", "想吃") or []
    if isinstance(avoid, str):
        avoid = split_terms(avoid)
    if isinstance(want, str):
        want = split_terms(want)

    use_coupon = pick("useCoupon", "use_coupon", "用券")
    if use_coupon is None:
        coupon_flag = True
    else:
        coupon_flag = _as_bool(use_coupon)

    budget = pick("budget", "预算")
    kcal = pick("maxKcal", "max_kcal", "热量")
    quantity = pick("quantity", "数量")
    return Spec(
        budget_yuan=None if budget is None else float(budget),
        max_kcal=None if kcal is None else int(kcal),
        avoid=tuple(str(term) for term in avoid),
        drink=_clean(pick("drink", "饮料")),
        want=tuple(str(term) for term in want),
        quantity=1 if quantity is None else int(quantity),
        scene=scene,
        city=_clean(pick("city", "城市")),
        place=_clean(pick("place", "地点")),
        store_code=_clean(pick("storeCode", "store_code", "门店")),
        be_code=_clean(pick("beCode", "be_code")),
        address_id=_clean(pick("addressId", "address_id")),
        use_coupon=coupon_flag,
    )


def parse_text(text: str) -> Spec:
    mapping: dict = {}
    list_keys = {"不要": "avoid", "想吃": "want"}
    scalar = {
        "预算": "budget",
        "热量": "maxKcal",
        "饮料": "drink",
        "数量": "quantity",
        "场景": "scene",
        "城市": "city",
        "地点": "place",
        "门店": "storeCode",
        "用券": "useCoupon",
    }
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise SpecError(f"无法解析这一行：{line}")
        key, value = parts
        if key in list_keys:
            mapping[list_keys[key]] = split_terms(value)
        elif key in scalar:
            mapping[scalar[key]] = value
        else:
            raise SpecError(f"无法识别的规格字段：{key}")
    return parse_mapping(mapping)


def parse_file(path: Path) -> Spec:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SpecError(f"规格 JSON 无法解析：{exc}") from exc
        return parse_mapping(data)
    return parse_text(text)


def apply_overrides(spec: Spec, overrides: dict) -> Spec:
    if not overrides:
        return spec
    merged = spec_to_mapping(spec)
    merged.update(overrides)
    return parse_mapping(merged)


def spec_to_mapping(spec: Spec) -> dict:
    data = {
        "quantity": spec.quantity,
        "scene": spec.scene,
        "useCoupon": spec.use_coupon,
        "avoid": list(spec.avoid),
        "want": list(spec.want),
    }
    if spec.budget_yuan is not None:
        data["budget"] = spec.budget_yuan
    if spec.max_kcal is not None:
        data["maxKcal"] = spec.max_kcal
    if spec.drink:
        data["drink"] = spec.drink
    if spec.city:
        data["city"] = spec.city
    if spec.place:
        data["place"] = spec.place
    if spec.store_code:
        data["storeCode"] = spec.store_code
    if spec.be_code:
        data["beCode"] = spec.be_code
    if spec.address_id:
        data["addressId"] = spec.address_id
    return data


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "用", "要"}:
        return True
    if text in {"0", "false", "no", "n", "否", "不用", "不要"}:
        return False
    raise SpecError(f"无法识别的用券取值：{value}")
