import importlib
import gc
import os
import tempfile
import unittest
import warnings


class ScouteoPointsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.temp_dir.name
        import database

        self.database = importlib.reload(database)
        self.database.init_db()
        self.database.set_puntos("scouteo", 5)

    def tearDown(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()
        self.temp_dir.cleanup()

    def test_approval_persists_proportional_rounded_points(self):
        unit_value = self.database.create_evidence_review(
            "summary-1",
            "1",
            "Scout",
            "scouteo",
            [("1", "Scout", 6, 29)],
        )
        self.assertEqual(unit_value, 5)
        self.assertIsNotNone(self.database.approve_evidence("summary-1"))

        scout = self.database.get_scout("1")
        self.assertEqual(scout[5], 6)
        self.assertEqual(self.database.get_points_adjustment("1"), -1)
        self.assertEqual(self.database.calc_puntos_totales(scout), 29)

        snapshot_id = self.database.create_ranking_snapshot(reason="test")
        snapshot_row = self.database.get_ranking_snapshot_rows(snapshot_id)[0]
        self.assertEqual(snapshot_row[5], 6)
        self.assertEqual(snapshot_row[7], 29)

    def test_moving_approved_evidence_preserves_exact_points(self):
        self.database.add_activity("seed", "Seed", "mapeo", 1)
        snapshot_id = self.database.create_ranking_snapshot(reason="previous_week")
        self.database.reset_all()

        self.database.create_evidence_review(
            "summary-2",
            "1",
            "Scout",
            "scouteo",
            [("1", "Scout", 6, 29)],
        )
        self.database.approve_evidence("summary-2")
        result = self.database.move_evidence_to_snapshot("summary-2", snapshot_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["points"], 29)
        self.assertEqual(self.database.calc_puntos_totales(self.database.get_scout("1")), 0)
        snapshot_scout = next(row for row in self.database.get_ranking_snapshot_rows(snapshot_id) if row[0] == "1")
        self.assertEqual(snapshot_scout[5], 6)
        self.assertEqual(snapshot_scout[7], 29)


if __name__ == "__main__":
    unittest.main()
