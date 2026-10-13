import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from mcd_spec.catalog import DemoCatalog
from mcd_spec.cli import main
from mcd_spec.compiler import avoid_hit, compile_spec
from mcd_spec.render import render_text
from mcd_spec.spec import parse_file


ROOT = Path(__file__).resolve().parents[1]


class AvoidTest(unittest.TestCase):
    def test_sugar_free_cola_stays_available_when_drink_is_requested(self):
        self.assertIsNone(avoid_hit("无糖可口可乐中杯", ("可乐",), "无糖"))
        self.assertEqual(avoid_hit("可口可乐中杯", ("可乐",), "无糖"), "可乐")
        self.assertEqual(avoid_hit("无糖可口可乐中杯", ("可乐",), None), "可乐")


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

    def test_text_mentions_sample_data_and_price(self):
        text = render_text(self.result)
        self.assertIn("离线示例数据", text)
        self.assertIn("核价 23.00 元，优惠 5.00 元，券「板烧立减5元」", text)
        self.assertIn("另有 1 个方案未展示：无法避开「可乐」。", text)
        self.assertIn("还没有下单", text)


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
