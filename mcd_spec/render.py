"""Text and JSON renderings of a compile result."""

from __future__ import annotations

import json
from collections import Counter

from mcd_spec.compiler import CompileResult, Plan
from mcd_spec.spec import Spec, spec_to_mapping


SCENE_LABELS = {
    "pickup": "到店取餐",
    "drive": "得来速",
    "delivery": "麦乐送",
}


def render_text(result: CompileResult) -> str:
    lines: list[str] = []
    for warning in result.warnings:
        lines.append(f"注意  {warning}")
    if result.warnings:
        lines.append("")
    scene = SCENE_LABELS.get(result.spec.scene, result.spec.scene)
    lines.append(f"mcd-spec  {scene}")
    if result.store is not None:
        store = result.store
        code = store.store_code
        if store.be_code:
            code = f"{code} / {store.be_code}"
        lines.append(f"门店  {store.name}（{code}）")
        if store.address:
            lines.append(f"地址  {store.address}")
        if store.distance:
            lines.append(f"距离  {store.distance}")
    place = " ".join(part for part in (result.spec.city, result.spec.place) if part)
    if place:
        lines.append(f"地点  {place}")
    lines.append("")
    lines.append("规格")
    lines.extend(f"  {line}" for line in _spec_lines(result.spec))
    lines.append("")
    lines.append(
        f"菜单 {result.menu_count} 个餐品，标价或名称排除 {result.skipped} 个，核对详情 {result.detailed} 个。"
    )
    if not result.plans:
        lines.append("")
        lines.append("没有可展示的方案。")
        lines.append("")
        lines.append("以上是核价结果，还没有下单。")
        return "\n".join(lines) + "\n"
    for index, plan in enumerate(result.plans, start=1):
        lines.append("")
        lines.extend(_plan_lines(index, plan))
    if result.omitted:
        lines.append("")
        lines.append(_omitted_line(result.omitted))
    lines.append("")
    lines.append("以上是核价结果，还没有下单。")
    return "\n".join(lines) + "\n"


def render_json(result: CompileResult) -> str:
    payload = {
        "scene": result.spec.scene,
        "warnings": result.warnings,
        "store": None
        if result.store is None
        else {
            "storeCode": result.store.store_code,
            "beCode": result.store.be_code,
            "name": result.store.name,
            "address": result.store.address,
            "distance": result.store.distance,
            "open": result.store.open,
        },
        "spec": spec_to_mapping(result.spec),
        "trace": {
            "menuCount": result.menu_count,
            "skipped": result.skipped,
            "detailed": result.detailed,
        },
        "plans": [_plan_dict(plan) for plan in result.plans],
        "omitted": [_plan_dict(plan) for plan in result.omitted],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _spec_lines(spec: Spec) -> list[str]:
    lines = []
    if spec.budget_yuan is not None:
        lines.append(f"预算 ≤ {spec.budget_yuan:.2f} 元")
    if spec.max_kcal is not None:
        lines.append(f"热量 ≤ {spec.max_kcal} kcal")
    if spec.avoid:
        lines.append("不要 " + "、".join(spec.avoid))
    if spec.drink:
        lines.append(f"饮料偏好 {spec.drink}")
    if spec.want:
        lines.append("想吃 " + "、".join(spec.want))
    lines.append(f"数量 {spec.quantity}")
    lines.append("用券 是" if spec.use_coupon else "用券 否")
    return lines


def _plan_lines(index: int, plan: Plan) -> list[str]:
    lines = [f"方案 {index}  {plan.status}", f"  {plan.name} × {plan.quantity}"]
    for note in plan.notes:
        lines.append(f"  调整  {note}")
    if plan.menu_price_fen is not None:
        lines.append(f"  菜单价 {_yuan(plan.menu_price_fen)} 元")
    if plan.price_fen is None:
        lines.append("  核价  未完成")
    else:
        price = f"核价 {_yuan(plan.price_fen)} 元"
        if plan.discount_fen:
            price += f"，优惠 {_yuan(plan.discount_fen)} 元"
        if plan.coupon_title:
            price += f"，券「{plan.coupon_title}」"
        lines.append(f"  {price}")
    nutrition = []
    if plan.kcal is not None:
        nutrition.append(f"热量 {_amount(plan.kcal)} kcal")
    if plan.protein is not None:
        nutrition.append(f"蛋白质 {_amount(plan.protein)} g")
    if nutrition:
        lines.append("  " + "，".join(nutrition))
    else:
        lines.append("  热量  营养表未收录")
    if plan.quote is not None:
        lines.append("  购物车")
        dumped = json.dumps(plan.quote, ensure_ascii=False, indent=2)
        lines.extend(f"  {line}" for line in dumped.splitlines())
    return lines


def _omitted_line(plans: list[Plan]) -> str:
    reasons = Counter(plan.reason for plan in plans)
    if len(reasons) == 1:
        reason, count = next(iter(reasons.items()))
        return f"另有 {count} 个方案未展示：{reason}。"
    parts = [f"{count} 个{reason}" for reason, count in reasons.items()]
    return "另有 " + "、".join(parts) + "，未展示。"


def _plan_dict(plan: Plan) -> dict:
    return {
        "name": plan.name,
        "code": plan.code,
        "quantity": plan.quantity,
        "satisfies": plan.satisfies,
        "status": plan.status,
        "reason": plan.reason,
        "notes": plan.notes,
        "menuPriceYuan": None if plan.menu_price_fen is None else plan.menu_price_fen / 100,
        "priceYuan": None if plan.price_fen is None else plan.price_fen / 100,
        "discountYuan": None if plan.discount_fen is None else plan.discount_fen / 100,
        "couponTitle": plan.coupon_title,
        "kcal": _json_number(plan.kcal),
        "protein": _json_number(plan.protein),
        "wantHits": plan.want_hits,
        "drinkMet": plan.drink_met,
        "quote": plan.quote,
    }


def _json_number(value):
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


def _yuan(fen: int) -> str:
    return f"{fen / 100:.2f}"


def _amount(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"
