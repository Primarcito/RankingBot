import unittest

from scouteo_scoring import (
    calculate_scouteo_records,
    format_scouteo_summary,
    parse_multiplier_hundredths,
)


class ScouteoScoringTests(unittest.TestCase):
    def test_multiplier_absent_defaults_to_one(self):
        self.assertEqual(parse_multiplier_hundredths("Scout - 5h 0m - 3 mapas"), 100)

    def test_multiplier_accepts_dot_and_comma(self):
        self.assertEqual(parse_multiplier_hundredths("x0.95"), 95)
        self.assertEqual(parse_multiplier_hundredths("x0,70"), 70)

    def test_multiplier_rejects_values_that_increase_or_exceed_penalty_floor(self):
        with self.assertRaises(ValueError):
            parse_multiplier_hundredths("x1.05")
        with self.assertRaises(ValueError):
            parse_multiplier_hundredths("x0.65")

    def test_penalty_is_applied_once_to_integer_units(self):
        records = [{"name": "Scout", "hours": 10, "minutes": 0, "maps": 12, "multiplier_hundredths": 95}]
        result = calculate_scouteo_records(records, hours_per_point=5, maps_per_point=3)[0]
        self.assertEqual(result["base_total"], 6)
        self.assertEqual(result["total"], 5)

    def test_multiplier_never_increases_units(self):
        records = [{"name": "Scout", "hours": 10, "minutes": 0, "maps": 12, "multiplier_hundredths": 100}]
        result = calculate_scouteo_records(records, 5, 3)[0]
        self.assertEqual(result["base_total"], result["total"])

    def test_compact_summary_hides_neutral_multiplier(self):
        record = {"hours": 12, "minutes": 46, "maps": 13, "base_total": 7}
        self.assertEqual(
            format_scouteo_summary(record, units=7, unit_points=5),
            "35 pts · 7u · 12h46 · 13 mapas",
        )

    def test_compact_summary_shows_penalty_and_lost_units(self):
        record = {
            "hours": 10,
            "minutes": 0,
            "maps": 12,
            "base_total": 6,
            "multiplier_hundredths": 95,
        }
        self.assertEqual(
            format_scouteo_summary(record, units=5, unit_points=5),
            "25 pts · 5/6u · x0.95 · 10h · 12 mapas",
        )


if __name__ == "__main__":
    unittest.main()
