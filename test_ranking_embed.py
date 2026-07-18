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

        ranking_text = "\n".join(field.value for field in result.fields)
        self.assertNotIn("█", ranking_text)
        self.assertNotIn("░", ranking_text)
        self.assertEqual([field.name for field in result.fields], ["Podio", "Clasificación"])
        self.assertIn("**Prio desde 90 pts** · 4 scouts", result.description)
        self.assertIn("🥇 **ChinoJVS** · 168 pts", ranking_text)
        self.assertIn("🥈 **Sherlock22** · 146 pts", ranking_text)
        self.assertIn("**4.** **Jesus** · 84 pts · faltan **6 pts**", ranking_text)
        self.assertNotIn("**Prio**", ranking_text)
        self.assertEqual(result.footer.text, "Página 1/1")


if __name__ == "__main__":
    unittest.main()
