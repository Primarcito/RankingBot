import unittest
from unittest import mock

import embeds


class RankingEmbedTests(unittest.TestCase):
    def test_ranking_is_compact_without_progress_bars(self):
        ranked = [
            (("1", "ChinoJVS"), 168),
            (("2", "Sherlock22"), 146),
            (("3", "Monito"), 125),
            (("4", "Jesus"), 84),
        ]

        def prio_status(points, cutoff):
            return {
                "qualifies": points >= cutoff,
                "missing": max(0, cutoff - points),
            }

        with (
            mock.patch.object(embeds, "get_all_scouts", return_value=[row for row, _ in ranked]),
            mock.patch.object(embeds, "get_ranked_scouts", return_value=ranked),
            mock.patch.object(embeds, "get_priority_min_points", return_value=90),
            mock.patch.object(embeds, "get_prio_status", side_effect=prio_status),
        ):
            result = embeds.build_ranking_embed(page=0, per_page=10)

        self.assertNotIn("█", result.description)
        self.assertNotIn("░", result.description)
        self.assertIn("🥇 **1.** **ChinoJVS** · **168 pts**", result.description)
        self.assertIn("**Prio**", result.description)
        self.assertIn("**4.** **Jesus** · **84 pts** · Faltan **6 pts**", result.description)
        self.assertEqual(result.footer.text, "Página 1/1 · Prio: 90 pts · 4 scouts")


if __name__ == "__main__":
    unittest.main()
