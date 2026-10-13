"""Where the compiler reads menus: the live MCP server, or the offline fixture."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcd_spec.client import McpClient, McpError
from mcd_spec.spec import Spec


SCENE_CODES = {
    "pickup": (1, 1),
    "drive": (1, 5),
    "delivery": (2, 2),
}


@dataclass(frozen=True)
class Store:
    store_code: str
    be_code: str
    name: str
    address: str = ""
    distance: str = ""
    open: bool | None = None


class Catalog(Protocol):
    notices: tuple[str, ...]

    def resolve_store(self, spec: Spec) -> Store: ...

    def context(self, store: Store, spec: Spec) -> dict: ...

    def meals(self, ctx: dict) -> dict: ...

    def meal_detail(self, ctx: dict, code: str) -> dict: ...

    def nutrition(self): ...

    def store_coupons(self, ctx: dict) -> list: ...

    def calculate_price(self, ctx: dict, item: dict) -> dict: ...


class McpCatalog:
    notices: tuple[str, ...] = ()

    def __init__(self, client: McpClient):
        self.client = client

    def resolve_store(self, spec: Spec) -> Store:
        if spec.store_code:
            return Store(spec.store_code, spec.be_code or "", name="指定门店")
        if spec.scene == "delivery":
            payload = self.client.call_tool("delivery-query-addresses", {})
            addresses = []
            if isinstance(payload, dict):
                addresses = payload.get("addresses") or []
            chosen = _select_address(addresses, spec.address_id)
            rows = self.client.call_tool(
                "delivery-query-stores",
                {"addressId": str(chosen.get("addressId")), "beType": 2},
            )
            return pick_store(_as_rows(rows))
        _order_type, be_type = SCENE_CODES[spec.scene]
        rows = self.client.call_tool(
            "query-nearby-stores",
            {
                "searchType": 2,
                "beType": be_type,
                "city": spec.city,
                "keyword": spec.place,
            },
        )
        return pick_store(_as_rows(rows))

    def context(self, store: Store, spec: Spec) -> dict:
        order_type, be_type = SCENE_CODES[spec.scene]
        ctx = {
            "storeCode": store.store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if store.be_code:
            ctx["beCode"] = store.be_code
        return ctx

    def meals(self, ctx: dict) -> dict:
        payload = self.client.call_tool("query-meals", ctx)
        if not isinstance(payload, dict):
            raise McpError("菜单返回不是对象")
        return payload

    def meal_detail(self, ctx: dict, code: str) -> dict:
        payload = self.client.call_tool("query-meal-detail", {**ctx, "code": code})
        if not isinstance(payload, dict):
            raise McpError(f"{code} 的餐品详情不是对象")
        return payload

    def nutrition(self):
        return self.client.call_tool("list-nutrition-foods", {})

    def store_coupons(self, ctx: dict) -> list:
        payload = self.client.call_tool("query-store-coupons", ctx)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            rows = payload.get("list") or payload.get("coupons") or []
            return rows if isinstance(rows, list) else []
        return []

    def calculate_price(self, ctx: dict, item: dict) -> dict:
        payload = self.client.call_tool("calculate-price", {**ctx, "items": [item]})
        if not isinstance(payload, dict):
            raise McpError("核价返回不是对象")
        return payload


class DemoCatalog:
    notices = ("当前是离线示例数据，不是门店实时菜单、价格或优惠。",)

    def __init__(self, world: dict | None = None):
        self.world = world if world is not None else load_demo_world()

    def resolve_store(self, spec: Spec) -> Store:
        raw = self.world["store"]
        return Store(
            store_code=str(raw["storeCode"]),
            be_code=str(raw.get("beCode") or ""),
            name=str(raw.get("storeName") or "示例餐厅"),
            address=str(raw.get("address") or ""),
            distance=str(raw.get("distance") or ""),
            open=raw.get("businessStatus"),
        )

    def context(self, store: Store, spec: Spec) -> dict:
        order_type, be_type = SCENE_CODES[spec.scene]
        ctx = {
            "storeCode": store.store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if store.be_code:
            ctx["beCode"] = store.be_code
        return ctx

    def meals(self, ctx: dict) -> dict:
        return self.world["menu"]

    def meal_detail(self, ctx: dict, code: str) -> dict:
        try:
            return self.world["details"][str(code)]
        except KeyError as exc:
            raise McpError(f"示例数据里没有餐品 {code}") from exc

    def nutrition(self):
        return self.world["nutrition"]

    def store_coupons(self, ctx: dict) -> list:
        return list(self.world.get("coupons") or [])

    def calculate_price(self, ctx: dict, item: dict) -> dict:
        code = str(item["productCode"])
        quantity = int(item["quantity"])
        unit = int(self.world["pricesFen"][code])
        coupon_id = item.get("couponId")
        cut = 0
        if coupon_id:
            cut = int(self.world.get("couponCutsFen", {}).get(coupon_id, {}).get(code, 0))
        line = unit * quantity
        discount = cut * quantity
        payable = line - discount
        return {
            "productOriginalPrice": line,
            "productPrice": payable,
            "deliveryOriginalPrice": 0,
            "deliveryPrice": 0,
            "originalPrice": line,
            "discount": discount,
            "price": payable,
            "productList": [
                {
                    "productCode": code,
                    "quantity": quantity,
                    "originalSubtotal": line,
                    "subtotal": payable,
                }
            ],
        }


def load_demo_world() -> dict:
    path = Path(__file__).with_name("demo_world.json")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_store(rows: list[dict]) -> Store:
    if not rows:
        raise McpError("没有找到可用门店")
    chosen = next((row for row in rows if row.get("businessStatus") is True), rows[0])
    status = chosen.get("businessStatus")
    return Store(
        store_code=str(chosen.get("storeCode") or ""),
        be_code=str(chosen.get("beCode") or ""),
        name=str(chosen.get("storeName") or "麦当劳餐厅"),
        address=str(chosen.get("address") or ""),
        distance=_distance_text(chosen.get("distance")),
        open=None if status is None else bool(status),
    )


def _distance_text(value) -> str:
    if isinstance(value, bool) or value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        number = int(value) if float(value).is_integer() else value
        return f"{number}米"
    return str(value)


def _select_address(addresses: list, address_id: str | None) -> dict:
    rows = [row for row in addresses if isinstance(row, dict)]
    if address_id:
        for row in rows:
            if str(row.get("addressId")) == str(address_id):
                return row
        raise McpError(f"没有找到地址 {address_id}")
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise McpError("账号下没有可配送地址")
    lines = [f"{row.get('addressId')}  {row.get('fullAddress') or ''}".rstrip() for row in rows]
    raise McpError("有多个配送地址，请用 --address-id 指定其中一个：\n" + "\n".join(lines))


def _as_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("stores", "list", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    raise McpError("门店列表无法读取")
