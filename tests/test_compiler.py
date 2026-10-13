import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from mcd_spec.catalog import DemoCatalog, Store
from mcd_spec.cli import main
from mcd_spec.client import McpError
from mcd_spec.compiler import _Product, _build_cart, _has_term, avoid_hit, compile_spec
from mcd_spec.render import render_text
from mcd_spec.spec import Spec, parse_file


ROOT = Path(__file__).resolve().parents[1]


class AvoidTest(unittest.TestCase):
    def test_sugar_free_cola_stays_available_when_drink_is_requested(self):
        self.assertIsNone(avoid_hit("无糖可口可乐中杯", ("可乐",), "无糖"))
        self.assertEqual(avoid_hit("可口可乐中杯", ("可乐",), "无糖"), "可乐")
        self.assertEqual(avoid_hit("无糖可口可乐中杯", ("可乐",), None), "可乐")

    def test_burger_word_matches_the_longer_menu_name(self):
        self.assertTrue(_has_term("麦辣鸡腿汉堡", "鸡腿堡"))
        self.assertFalse(_has_term("麦乐鸡", "鸡腿堡"))


class CompileDemoTest(unittest.TestCase):
    def setUp(self):
        self.spec = parse_file(ROOT / "examples" / "spec.json")
        self.catalog = DemoCatalog()
        self.catalog.priced = []
        original = self.catalog.calculate_price

        def spy(ctx, item):
            self.catalog.priced.append(item["productCode"])
            return original(ctx, item)

        self.catalog.calculate_price = spy
        self.result = compile_spec(self.spec, self.catalog)

    def test_winner_is_customized_and_coupon_priced(self):
        winner = self.result.plans[0]
        self.assertEqual(winner.code, "1002")
        self.assertTrue(winner.satisfies)
        self.assertEqual(winner.price_fen, 2300)
        self.assertEqual(winner.discount_fen, 500)
        self.assertEqual(winner.coupon_title, "板烧立减5元")
        self.assertEqual(int(winner.kcal), 540)
        self.assertEqual(winner.notes, [
            "去掉酸黄瓜",
            "热量未计入配料增减",
            "没有名称包含「无糖」的可选项",
        ])
        item = winner.quote["items"][0]
        self.assertEqual(item["modification"]["values"], [
            {"code": "100201", "quantity": 0, "key": "0-0"},
        ])
        self.assertEqual(item["couponId"], "demo-coupon-ban")
        self.assertNotIn("roundList", item)
        self.assertEqual(winner.quote["orderType"], 1)
        self.assertEqual(winner.quote["beType"], 1)

    def test_combo_over_calorie_is_swapped_but_not_chosen(self):
        combo = self.result.plans[2]
        self.assertEqual(combo.code, "1001")
        self.assertFalse(combo.satisfies)
        self.assertEqual(combo.reason, "热量超出")
        self.assertEqual(int(combo.kcal), 771)
        self.assertIn("换成无糖可口可乐中杯", combo.notes)
        codes = [
            choice["code"]
            for rnd in combo.quote["items"][0]["roundList"]
            for choice in rnd["comboItemList"]
        ]
        self.assertIn("3071", codes)
        self.assertNotIn("3070", codes)

    def test_impossible_avoidance_is_not_priced(self):
        self.assertEqual(self.result.skipped, 1)
        self.assertEqual(self.result.detailed, 4)
        self.assertNotIn("1003", [plan.code for plan in self.result.plans])
        self.assertEqual(self.result.omitted[0].code, "1004")
        self.assertNotIn("1004", self.catalog.priced)
        self.assertEqual(self.catalog.priced.count("1002"), 2)

    def test_unseen_avoidance_is_not_reported_as_removed(self):
        spec = replace(self.spec, avoid=("洋葱",))
        result = compile_spec(spec, DemoCatalog())
        self.assertTrue(any("洋葱" in warning for warning in result.warnings))

    def test_text_mentions_sample_data_and_price(self):
        text = render_text(self.result)
        self.assertIn("离线示例数据", text)
        self.assertIn("核价 23.00 元，优惠 5.00 元，券「板烧立减5元」", text)
        self.assertIn("另有 1 个方案未展示：无法避开「可乐」。", text)
        self.assertIn("还没有下单", text)

    def test_several_omitted_reasons_stay_readable(self):
        from mcd_spec.compiler import CompileResult, Plan

        def plan(reason):
            return Plan(
                name="餐",
                code="1",
                quantity=1,
                satisfies=False,
                status="",
                reason=reason,
                notes=[],
                menu_price_fen=None,
                price_fen=100,
                discount_fen=0,
                coupon_title=None,
                kcal=1,
                protein=1,
                quote=None,
                want_hits=0,
                drink_met=False,
            )

        shown = plan("满足")
        shown.satisfies = True
        result = CompileResult(
            store=None,
            spec=self.spec,
            plans=[shown],
            omitted=[plan("热量未知"), plan("热量超出")],
        )
        text = render_text(result)
        self.assertIn("另有 1 个热量未知、1 个热量超出，未展示。", text)


class ChoiceModificationTest(unittest.TestCase):
    def test_removal_on_a_combo_choice_is_sent_with_that_choice(self):
        product = _Product("1", "板烧鸡腿堡三件套", 3450)
        detail = {
            "name": "板烧鸡腿堡三件套",
            "modification": {"items": []},
            "rounds": [
                {
                    "id": 1,
                    "choices": [
                        {
                            "code": "1406",
                            "name": "板烧鸡腿堡",
                            "quantity": 1,
                            "isDefault": 1,
                            "modification": {
                                "items": [
                                    {
                                        "values": [
                                            {
                                                "code": "102201",
                                                "name": "酸黄瓜",
                                                "selectedQuantity": 1,
                                                "minQuantity": 0,
                                                "unselectedKey": "0-0",
                                            }
                                        ]
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        built = _build_cart(product, detail, Spec(avoid=("酸黄瓜",), quantity=1), [])
        choice = built.item["roundList"][0]["comboItemList"][0]
        self.assertEqual(choice["code"], "1406")
        self.assertEqual(
            choice["modification"]["values"],
            [{"code": "102201", "quantity": 0, "key": "0-0"}],
        )
        self.assertNotIn("modification", {key: built.item[key] for key in built.item if key != "roundList"})


class CouponSkuTest(unittest.TestCase):
    def test_coupon_product_missing_from_the_menu_is_still_priced(self):
        catalog = _CouponCatalog()
        spec = Spec(budget_yuan=40, max_kcal=700, avoid=("洋葱",), quantity=1, use_coupon=True)
        result = compile_spec(spec, catalog)
        self.assertEqual(result.detailed, 1)
        self.assertEqual(catalog.detailed_codes, ["1"])
        coupon_plan = next(plan for plan in result.plans if plan.code == "900")
        self.assertEqual(coupon_plan.price_fen, 500)
        self.assertEqual(coupon_plan.coupon_title, "券")
        self.assertIn("这张券对应的商品不在菜单详情里，按券商品直接核价", coupon_plan.notes)
        self.assertNotIn("800", [plan.code for plan in result.plans])
        self.assertTrue(any("洋葱" in warning for warning in result.warnings))


class _CouponCatalog:
    notices = ()

    def __init__(self):
        self.detailed_codes = []

    def resolve_store(self, spec):
        return Store("S", "", "店")

    def context(self, store, spec):
        return {"storeCode": "S", "orderType": 1, "beType": 1}

    def meals(self, ctx):
        return {
            "categories": [{"meals": [{"code": "1"}, {"code": "800"}]}],
            "meals": {
                "1": {"name": "苹果派", "currentPrice": "8"},
                "800": {
                    "name": "会员堡",
                    "currentPrice": "10",
                    "originalPrice": "90",
                    "withOrder": {"membershipCode": "106"},
                },
            },
        }

    def meal_detail(self, ctx, code):
        self.detailed_codes.append(code)
        if code == "900":
            raise McpError("没有这个商品")
        return {"name": "苹果派", "rounds": [], "modification": {"items": []}}

    def nutrition(self):
        return "[2]{productName,energyKcal,protein}:\n苹果派,200,2\n券商品,100,1\n"

    def store_coupons(self, ctx):
        return [{
            "title": "券",
            "couponId": "c1",
            "couponCode": "k1",
            "products": [{"productCode": "900", "productName": "券商品"}],
        }]

    def calculate_price(self, ctx, item):
        if item.get("couponId"):
            return {"price": 500, "discount": 500}
        return {"price": 800, "discount": 0}


class CliTest(unittest.TestCase):
    def test_demo_json(self):
        buf = StringIO()
        with redirect_stdout(buf):
            code = main([
                "compile",
                "--demo",
                "--json",
                str(ROOT / "examples" / "spec.json"),
            ])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["plans"][0]["priceYuan"], 23)
        self.assertEqual(payload["plans"][0]["kcal"], 540)
        self.assertTrue(payload["warnings"])

    def test_live_compile_without_token_fails(self):
        buf = StringIO()
        previous = os.environ.pop("MCD_MCP_TOKEN", None)
        try:
            with redirect_stdout(buf), redirect_stderr(StringIO()):
                code = main(["compile", "--city", "上海", "--place", "人民广场", "--budget", "40"])
        finally:
            if previous is not None:
                os.environ["MCD_MCP_TOKEN"] = previous
        self.assertEqual(code, 1)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
