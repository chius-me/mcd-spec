import unittest
from pathlib import Path

from mcd_spec.spec import Spec, SpecError, parse_file, parse_text


ROOT = Path(__file__).resolve().parents[1]


class SpecTest(unittest.TestCase):
    def test_text_and_example_file(self):
        text = parse_text("预算 40\n热量 700\n不要 可乐、酸黄瓜\n饮料 无糖\n想吃 鸡腿堡\n")
        file_spec = parse_file(ROOT / "examples" / "spec.json")
        self.assertEqual(text.budget_yuan, 40)
        self.assertEqual(text.avoid, ("可乐", "酸黄瓜"))
        self.assertEqual(file_spec.max_kcal, 700)
        self.assertEqual(file_spec.city, "上海")
        self.assertEqual(file_spec.place, "人民广场")
        file_spec.validate(demo=False)

    def test_live_pickup_requires_a_place(self):
        spec = Spec(scene="pickup")
        with self.assertRaises(SpecError):
            spec.validate(demo=False)
        spec.validate(demo=True)

    def test_pinned_store_skips_place(self):
        Spec(store_code="S1", be_code="B1").validate(demo=False)
        Spec(store_code="S1").validate(demo=False)


if __name__ == "__main__":
    unittest.main()
