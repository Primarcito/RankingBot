import unittest

import mapping_analysis


def road(player: str, discord_id: str, message_id: str):
    return mapping_analysis.MappingEvent(
        event_type="road",
        player=player,
        discord_id=discord_id,
        created_at="2026-07-18 10:00 UTC",
        source_message_id=message_id,
        source_url=f"https://discord.test/{message_id}",
        from_map="Wetgrave Swale",
        to_map="Deathwisp Sink",
    )


class MappingDuplicateTests(unittest.TestCase):
    def test_duplicate_route_is_audited_but_scores_zero(self):
        analysis = mapping_analysis.analyze_mapping_events(
            [
                road("Primero", "100", "1"),
                road("Duplicado", "200", "2"),
            ]
        )
        rows = {row["player"]: row for row in analysis["ranking"]}

        self.assertEqual(rows["Primero"]["road_unique"], 1)
        self.assertEqual(rows["Primero"]["score"], 1.0)
        self.assertEqual(rows["Duplicado"]["road_duplicates"], 1)
        self.assertEqual(rows["Duplicado"]["score"], 0.0)
        self.assertEqual(
            mapping_analysis.final_units_for_row(
                rows["Duplicado"],
                top_weight=rows["Primero"]["score"],
                max_units=25,
            ),
            0,
        )
        self.assertEqual(len(analysis["duplicates"]), 1)


if __name__ == "__main__":
    unittest.main()
