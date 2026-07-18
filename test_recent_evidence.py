import gc
import importlib
import os
import tempfile
import unittest
import warnings


class RecentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.temp_dir.name
        import database

        self.database = importlib.reload(database)
        self.database.init_db()

    def tearDown(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()
        self.temp_dir.cleanup()

    def test_returns_latest_evidence_with_compact_totals(self):
        self.database.create_evidence_review(
            "100",
            "1",
            "Scout Uno",
            "kill_pelea",
            [("1", "Scout Uno", 2), ("2", "Scout Dos", 1)],
        )
        self.database.create_evidence_review(
            "101",
            "3",
            "Scout Tres",
            "scouteo",
            [("3", "Scout Tres", 4, 17)],
        )

        rows = self.database.get_recent_evidence(2)

        self.assertEqual([row["message_id"] for row in rows], ["101", "100"])
        self.assertEqual(rows[0]["points"], 17)
        self.assertEqual(rows[1]["participants"], 2)
        self.assertEqual(rows[1]["points"], 9)


if __name__ == "__main__":
    unittest.main()
