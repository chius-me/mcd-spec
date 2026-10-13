import unittest

from mcd_spec.nutrition import lookup_nutrition, parse_toon


OFFICIAL = (
    "[1]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n"
    "  猪柳麦满分,null,1288,308,16,16,24,781,213\n "
)


class NutritionTest(unittest.TestCase):
    def test_official_toon_sample(self):
        rows = parse_toon(OFFICIAL)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["productName"], "猪柳麦满分")
        self.assertIsNone(row["nutritionDescription"])
        self.assertEqual(row["energyKcal"], 308)
        self.assertEqual(row["calcium"], 213)

    def test_lookup_prefers_exact_name(self):
        rows = parse_toon(
            "[2]{productName,energyKcal}:\n"
            "  可口可乐中杯,142\n"
            "  无糖可口可乐中杯,1\n"
        )
        found = lookup_nutrition(rows, "可口可乐中杯")
        self.assertEqual(found["energyKcal"], 142)


if __name__ == "__main__":
    unittest.main()
