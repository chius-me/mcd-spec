"""Turn a meal spec into a few priced carts.

The compiler reads the menu, applies avoidances to customization options,
and asks calculate-price for the payable total. It does not create an order.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from mcd_spec.catalog import Catalog, Store
from mcd_spec.client import McpError
from mcd_spec.nutrition import load_nutrition, lookup_nutrition
from mcd_spec.spec import Spec, SpecError


MAX_DETAILS = 8


@dataclass
class Plan:
    name: str
    code: str
    quantity: int
    satisfies: bool
    status: str
    reason: str
    notes: list[str]
    menu_price_fen: int | None
    price_fen: int | None
    discount_fen: int | None
    coupon_title: str | None
    kcal: float | None
    protein: float | None
    quote: dict | None
    want_hits: int
    drink_met: bool


@dataclass
class CompileResult:
    store: Store | None
    spec: Spec
    plans: list[Plan] = field(default_factory=list)
    omitted: list[Plan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    menu_count: int = 0
    skipped: int = 0
    detailed: int = 0


def compile_spec(spec: Spec, catalog: Catalog, *, limit: int = 3) -> CompileResult:
    if limit < 1:
        raise SpecError("limit 至少为 1")
    result = CompileResult(store=None, spec=spec, warnings=list(getattr(catalog, "notices", ())))
    store = catalog.resolve_store(spec)
    result.store = store
    if store.open is False:
        result.warnings.append("门店当前可能未营业，菜单和价格仍按接口结果核对。")
    ctx = catalog.context(store, spec)
    menu = catalog.meals(ctx)
    try:
        rows = load_nutrition(catalog.nutrition())
    except McpError as exc:
        rows = []
        result.warnings.append(f"营养表没有取到：{exc}")
    coupons: list = []
    if spec.use_coupon:
        try:
            coupons = catalog.store_coupons(ctx)
        except McpError as exc:
            result.warnings.append(f"门店优惠券没有取到，这次按无券核价：{exc}")
    products = list(iter_menu(menu))
    result.menu_count = len(products)
    coupon_codes = _coupon_product_codes(coupons)
    known_codes = {product.code for product in products}
    synthetic_codes: set[str] = set()
    for coupon in coupons:
        for raw in coupon.get("products") or []:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("productCode") or "")
            name = str(raw.get("productName") or coupon.get("title") or "")
            if not code or not name or code in known_codes:
                continue
            products.append(_Product(code, name, None))
            known_codes.add(code)
            synthetic_codes.add(code)
    budget_fen = None if spec.budget_yuan is None else int(round(spec.budget_yuan * 100))
    seen_terms: set[str] = set()
    kept = []
    for product in products:
        for term in spec.avoid:
            if _has_term(product.name, term):
                seen_terms.add(term)
        if avoid_hit(product.name, spec.avoid, spec.drink):
            result.skipped += 1
            continue
        if (
            budget_fen is not None
            and product.menu_price_fen is not None
            and product.menu_price_fen * spec.quantity > budget_fen
        ):
            result.skipped += 1
            continue
        product.want_hits = sum(1 for term in spec.want if _has_term(product.name, term))
        kept.append(product)
    menu_kept = [product for product in kept if product.code not in synthetic_codes]
    chosen = _select_for_detail(menu_kept, spec, coupon_codes - synthetic_codes)
    extras = [product for product in kept if product.code in synthetic_codes][:2]
    plans = []
    for product in extras + chosen:
        if product.code in synthetic_codes:
            built = _build_bare(product, spec, rows)
            built.notes.insert(0, "这张券对应的商品不在菜单详情里，按券商品直接核价")
            _price_cart(built, catalog, ctx, coupons, spec)
            plans.append(_to_plan(built, spec, ctx, budget_fen))
            continue
        try:
            detail = catalog.meal_detail(ctx, product.code)
        except McpError as exc:
            if product.code not in coupon_codes:
                result.warnings.append(f"{product.name} 的详情没有取到：{exc}")
                continue
            built = _build_bare(product, spec, rows)
            built.notes.insert(0, "这张券对应的商品不在菜单详情里，按券商品直接核价")
            _price_cart(built, catalog, ctx, coupons, spec)
            plans.append(_to_plan(built, spec, ctx, budget_fen))
            continue
        result.detailed += 1
        _mark_detail_terms(detail, spec, seen_terms)
        built = _build_cart(product, detail, spec, rows)
        if not built.blockers:
            _price_cart(built, catalog, ctx, coupons, spec)
        plans.append(_to_plan(built, spec, ctx, budget_fen))
    missing = [term for term in spec.avoid if term not in seen_terms]
    if missing:
        joined = "、".join(f"「{term}」" for term in missing)
        result.warnings.append(f"这次看到的餐品可选项里没有{joined}，没法确认已经去掉。")
    plans.sort(key=lambda plan: _sort_key(plan, spec))
    result.plans = plans[:limit]
    result.omitted = plans[limit:]
    return result


def avoid_hit(name: str, avoid: tuple[str, ...], drink: str | None) -> str | None:
    """Return the avoidance term found in name.

    A drink the user asked for is kept even when its name also contains an
    avoided word, so「不要可乐、饮料无糖」仍可选「无糖可口可乐」.
    """
    for term in avoid:
        if not _has_term(name, term):
            continue
        if drink and _has_term(name, drink):
            continue
        return term
    return None


def _has_term(name: str, term: str) -> bool:
    """Match 鸡腿堡 against 鸡腿汉堡 without treating every short word as equal."""
    if not term:
        return False
    return _fold(term) in _fold(name)


def _fold(value: str) -> str:
    return value.replace("汉堡", "堡")


def _coupon_product_codes(coupons) -> set[str]:
    codes = set()
    for coupon in coupons:
        if not isinstance(coupon, dict):
            continue
        for raw in coupon.get("products") or []:
            if isinstance(raw, dict) and raw.get("productCode"):
                codes.add(str(raw["productCode"]))
    return codes


def _select_for_detail(kept, spec: Spec, coupon_codes: set[str]):
    """Pick a few menus items whose details are worth a request.

    Coupon meals stay in front. When a drink was requested, combos come
    before single items, because a single burger usually cannot swap drinks.
    """

    def price_key(product: _Product):
        return product.menu_price_fen if product.menu_price_fen is not None else 10**12

    coupons = sorted((product for product in kept if product.code in coupon_codes), key=price_key)
    wanted = sorted(
        (product for product in kept if product.want_hits and product.code not in coupon_codes),
        key=price_key,
    )
    others = sorted(
        (product for product in kept if not product.want_hits and product.code not in coupon_codes),
        key=price_key,
    )
    if spec.drink:
        want_combos = [product for product in wanted if "套" in product.name]
        want_other = [product for product in wanted if "套" not in product.name]
        other_combos = [product for product in others if "套" in product.name]
        other_rest = [product for product in others if "套" not in product.name]
        groups = (
            coupons[:2],
            want_combos[:4],
            want_other,
            want_combos[4:],
            other_combos,
            other_rest,
            coupons[2:],
        )
    else:
        groups = (coupons[:2], wanted, others, coupons[2:])
    chosen = []
    seen: set[str] = set()
    for group in groups:
        for product in group:
            if product.code in seen:
                continue
            chosen.append(product)
            seen.add(product.code)
            if len(chosen) >= MAX_DETAILS:
                return chosen
    return chosen


def _removed_modifications(source: dict, spec: Spec):
    values = []
    notes = []
    blockers = []
    changed = False
    for option in _modification_values(source):
        option_name = _option_name(option)
        if not avoid_hit(option_name, spec.avoid, spec.drink):
            continue
        selected = _as_int(option.get("selectedQuantity"), 1)
        minimum = _as_int(option.get("minQuantity"), 0)
        if selected <= 0:
            continue
        if minimum > 0:
            blockers.append(f"无法去掉「{option_name}」")
            continue
        code = option.get("code")
        if code in (None, ""):
            blockers.append(f"「{option_name}」缺少特调编码")
            continue
        entry = {"code": str(code), "quantity": 0}
        if option.get("unselectedKey") not in (None, ""):
            entry["key"] = option.get("unselectedKey")
        values.append(entry)
        notes.append(f"去掉{option_name}")
        changed = True
    return values, notes, blockers, changed


def _build_bare(product: _Product, spec: Spec, rows: list[dict]) -> _Built:
    kcal, protein = _sum_nutrition(rows, [product.name])
    notes = []
    if spec.drink:
        notes.append(f"没有名称包含「{spec.drink}」的可选项")
    return _Built(
        product=product,
        item={"productCode": product.code, "quantity": spec.quantity},
        notes=notes,
        blockers=[],
        component_names=[],
        kcal=kcal,
        protein=protein,
        drink_met=False,
        modification_changed=False,
    )


def _mark_detail_terms(detail: dict, spec: Spec, found: set[str]) -> None:
    sources = [_modification_values(detail)]
    for rnd in _rounds(detail):
        for choice in _round_choices(rnd):
            for term in spec.avoid:
                if _has_term(_option_name(choice), term):
                    found.add(term)
            sources.append(_modification_values({"modification": choice.get("modification")}))
    for values in sources:
        for value in values:
            for term in spec.avoid:
                if _has_term(_option_name(value), term):
                    found.add(term)


@dataclass
class _Product:
    code: str
    name: str
    menu_price_fen: int | None
    want_hits: int = 0


@dataclass
class _Built:
    product: _Product
    item: dict
    notes: list[str]
    blockers: list[str]
    component_names: list[str]
    kcal: float | None
    protein: float | None
    drink_met: bool
    modification_changed: bool
    price_fen: int | None = None
    discount_fen: int | None = None
    coupon_title: str | None = None


def iter_menu(menu: dict):
    meals = menu.get("meals") or {}
    seen: set[str] = set()
    for category in menu.get("categories") or []:
        if not isinstance(category, dict):
            continue
        for meal in category.get("meals") or []:
            if not isinstance(meal, dict):
                continue
            code = str(meal.get("code") or "")
            if not code or code in seen:
                continue
            info = meals.get(code) or meals.get(meal.get("code")) or {}
            if not isinstance(info, dict):
                info = {}
            seen.add(code)
            name = str(info.get("name") or "")
            if not name:
                continue
            yield _Product(code, name, _menu_price_fen(info))
    for code, info in meals.items():
        text = str(code)
        if text in seen or not isinstance(info, dict):
            continue
        name = str(info.get("name") or "")
        if not name:
            continue
        yield _Product(text, name, _menu_price_fen(info))


def _build_cart(product: _Product, detail: dict, spec: Spec, rows: list[dict]) -> _Built:
    name = str(detail.get("name") or product.name)
    notes: list[str] = []
    blockers: list[str] = []
    item: dict = {"productCode": product.code, "quantity": spec.quantity}
    component_names: list[str] = []
    modification_changed = False

    values, removal_notes, removal_blockers, modification_changed = _removed_modifications(detail, spec)
    notes.extend(removal_notes)
    blockers.extend(removal_blockers)
    if values:
        item["modification"] = {"values": values}

    round_list = []
    for rnd in _rounds(detail):
        choices = _round_choices(rnd)
        if not choices:
            continue
        chosen, blocked = _choose_option(choices, spec.avoid, spec.drink)
        if chosen is None:
            blockers.append(f"无法避开「{blocked}」" if blocked else "无法满足忌口")
            continue
        default = next((choice for choice in choices if _is_default(choice)), choices[0])
        if _option_code(chosen) != _option_code(default):
            notes.append(f"换成{_option_name(chosen)}")
        identifier = _round_id(rnd)
        if identifier is None:
            if _option_code(chosen) != _option_code(default):
                blockers.append("套餐子项缺少编号，无法表达替换")
            continue
        quantity = _as_int(chosen.get("quantity"), 1) or 1
        choice_item = {"code": _option_code(chosen), "quantity": quantity}
        choice_values, choice_notes, choice_blockers, choice_changed = _removed_modifications(
            {"modification": chosen.get("modification")},
            spec,
        )
        notes.extend(choice_notes)
        blockers.extend(choice_blockers)
        modification_changed = modification_changed or choice_changed
        if choice_values:
            choice_item["modification"] = {"values": choice_values}
        round_list.append({"round": identifier, "comboItemList": [choice_item]})
        component_names.append(_option_name(chosen))
    if round_list:
        item["roundList"] = round_list

    if component_names:
        kcal, protein = _sum_nutrition(rows, component_names)
    else:
        kcal, protein = _sum_nutrition(rows, [name])
    if modification_changed and kcal is not None:
        notes.append("热量未计入配料增减")
    drink_met = not spec.drink or any(_has_term(component, spec.drink) for component in component_names)
    if spec.drink and not drink_met and not blockers:
        notes.append(f"没有名称包含「{spec.drink}」的可选项")
    return _Built(
        product=_Product(product.code, name, product.menu_price_fen, product.want_hits),
        item=item,
        notes=notes,
        blockers=blockers,
        component_names=component_names,
        kcal=kcal,
        protein=protein,
        drink_met=drink_met,
        modification_changed=modification_changed,
    )


def _price_cart(built: _Built, catalog: Catalog, ctx: dict, coupons: list, spec: Spec) -> None:
    options: list[tuple[dict, dict | None]] = [(built.item, None)]
    if spec.use_coupon:
        for coupon in _matching_coupons(coupons, built.product.code, built.product.name)[:2]:
            cloned = copy.deepcopy(built.item)
            coupon_id = coupon.get("couponId")
            coupon_code = coupon.get("couponCode")
            if coupon_id not in (None, ""):
                cloned["couponId"] = str(coupon_id)
            if coupon_code not in (None, ""):
                cloned["couponCode"] = str(coupon_code)
            options.append((cloned, coupon))
    best_item = None
    best_price = None
    best_discount = None
    best_coupon = None
    best_rank = None
    last_error = None
    for cart_item, coupon in options:
        try:
            payload = catalog.calculate_price(ctx, cart_item)
        except McpError as exc:
            last_error = str(exc)
            continue
        price = _as_fen(payload.get("price"))
        if price is None:
            continue
        discount = _as_fen(payload.get("discount")) or 0
        rank = (price, coupon is not None)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_item = cart_item
            best_price = price
            best_discount = discount
            best_coupon = coupon
    if best_item is not None:
        built.item = best_item
        built.price_fen = best_price
        built.discount_fen = best_discount
        if best_coupon is not None:
            built.coupon_title = str(best_coupon.get("title") or "优惠券")
    elif last_error:
        built.notes.append(last_error)


def _to_plan(built: _Built, spec: Spec, ctx: dict, budget_fen: int | None) -> Plan:
    price = built.price_fen
    kcal = built.kcal
    if built.blockers:
        satisfies = False
        reason = built.blockers[0]
    elif price is None:
        satisfies = False
        reason = "核价失败"
    elif budget_fen is not None and price > budget_fen:
        satisfies = False
        reason = "超出预算"
    elif spec.max_kcal is not None and kcal is None:
        satisfies = False
        reason = "热量未知"
    elif spec.max_kcal is not None and kcal > spec.max_kcal:
        satisfies = False
        reason = "热量超出"
    else:
        satisfies = True
        reason = "未命中想吃" if spec.want and built.product.want_hits == 0 else "满足"
    if satisfies and reason == "未命中想吃":
        status = "满足，未命中想吃"
    elif satisfies:
        status = "满足"
    else:
        status = f"未满足：{reason}"
    quote = {**ctx, "items": [built.item]} if not built.blockers else None
    return Plan(
        name=built.product.name,
        code=built.product.code,
        quantity=spec.quantity,
        satisfies=satisfies,
        status=status,
        reason=reason,
        notes=list(built.notes),
        menu_price_fen=built.product.menu_price_fen,
        price_fen=price,
        discount_fen=built.discount_fen,
        coupon_title=built.coupon_title,
        kcal=kcal,
        protein=built.protein,
        quote=quote,
        want_hits=built.product.want_hits,
        drink_met=built.drink_met,
    )


def _sort_key(plan: Plan, spec: Spec):
    drink_rank = 0 if (not spec.drink or plan.drink_met) else 1
    return (
        not plan.satisfies,
        drink_rank,
        -plan.want_hits,
        plan.price_fen if plan.price_fen is not None else 10**12,
        plan.kcal if plan.kcal is not None else 10**9,
    )


def _matching_coupons(coupons: list, code: str, name: str) -> list[dict]:
    specific = []
    general = []
    for coupon in coupons:
        if not isinstance(coupon, dict):
            continue
        products = coupon.get("products") or []
        if not products:
            general.append(coupon)
            continue
        if _coupon_covers(products, code, name):
            specific.append(coupon)
    picked = specific[:2]
    if len(picked) < 2 and general:
        picked.append(general[0])
    return picked


def _coupon_covers(products, code: str, name: str) -> bool:
    for product in products:
        if not isinstance(product, dict):
            continue
        if str(product.get("productCode") or "") == code:
            return True
        title = str(product.get("productName") or "")
        if title and _fold(title) == _fold(name):
            return True
    return False


def _choose_option(choices: list[dict], avoid: tuple[str, ...], drink: str | None):
    viable = []
    blocked = None
    for choice in choices:
        hit = avoid_hit(_option_name(choice), avoid, drink)
        if hit:
            blocked = hit
            continue
        viable.append(choice)
    if not viable:
        return None, blocked
    if drink:
        preferred = [choice for choice in viable if _has_term(_option_name(choice), drink)]
        if preferred:
            return preferred[0], None
    defaults = [choice for choice in viable if _is_default(choice)]
    return (defaults[0] if defaults else viable[0]), None


def _sum_nutrition(rows: list[dict], names: list[str]):
    kcals = []
    proteins = []
    for name in names:
        row = lookup_nutrition(rows, name)
        if row is None:
            return None, None
        kcal = _as_number(row.get("energyKcal"))
        if kcal is None:
            return None, None
        kcals.append(kcal)
        proteins.append(_as_number(row.get("protein")))
    protein = None if any(value is None for value in proteins) else sum(proteins)
    return sum(kcals), protein


def _modification_values(detail: dict) -> list[dict]:
    raw = detail.get("modification") or {}
    groups = raw if isinstance(raw, list) else raw.get("items") or []
    values = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        nested = group.get("values")
        if isinstance(nested, list):
            values.extend(item for item in nested if isinstance(item, dict))
        elif group.get("name") and group.get("code"):
            values.append(group)
    return values


def _rounds(detail: dict):
    raw = detail.get("rounds") or detail.get("comboItems") or []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _round_choices(rnd: dict) -> list[dict]:
    for key in ("choices", "comboProducts", "items", "comboItemList", "products"):
        value = rnd.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _round_id(rnd: dict):
    for key in ("round", "id", "idx", "roundId"):
        value = rnd.get(key)
        if value not in (None, ""):
            return value
    return None


def _option_name(option: dict) -> str:
    return str(option.get("name") or option.get("productName") or "")


def _option_code(option: dict) -> str:
    value = option.get("code", option.get("productCode"))
    return "" if value is None else str(value)


def _is_default(option: dict) -> bool:
    for key in ("isDefault", "default", "selected", "isSelected"):
        if option.get(key) in (True, 1, "1", "true", "True"):
            return True
    return False


def _menu_price_fen(info: dict) -> int | None:
    # A with-order price only applies together with a membership card.
    price = info.get("originalPrice") if info.get("withOrder") else info.get("currentPrice")
    return _yuan_to_fen(price)


def _yuan_to_fen(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _as_fen(value) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


def _as_int(value, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_number(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
