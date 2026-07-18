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

    def test_pending_evidence_is_not_counted_and_alerts_only_once(self):
        self.database.create_evidence_review(
            "overdue",
            "1",
            "Scout Uno",
            "kill_scout",
            [("1", "Scout Uno", 2)],
        )
        with self.database.get_conn() as conn:
            conn.execute(
                """
                UPDATE evidence_messages
                SET fecha='2020-01-01T00:00:00', review_message_id='900'
                WHERE message_id='overdue'
                """
            )
            conn.commit()

        scout = self.database.get_scout("1")
        self.assertEqual(self.database.calc_puntos_totales(scout), 0)
        overdue = self.database.get_overdue_pending_evidence(hours=5)
        self.assertEqual([item["message_id"] for item in overdue], ["overdue"])

        self.assertTrue(self.database.mark_evidence_review_alerted("overdue"))
        self.assertEqual(self.database.get_overdue_pending_evidence(hours=5), [])


if __name__ == "__main__":
    unittest.main()
