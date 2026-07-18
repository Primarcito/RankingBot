import gc
import importlib
import os
import tempfile
import unittest
import warnings


class PriorityThresholdTests(unittest.TestCase):
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

    def test_single_priority_cutoff_is_persistent(self):
        self.assertEqual(self.database.get_priority_min_points(), 50)
        self.database.set_priority_min_points(75)
        self.assertEqual(self.database.get_priority_min_points(), 75)
        self.assertFalse(self.database.get_prio_status(74)["qualifies"])
        self.assertEqual(self.database.get_prio_status(74)["missing"], 1)
        self.assertTrue(self.database.get_prio_status(75)["qualifies"])

    def test_legacy_level_helper_no_longer_returns_letter_tiers(self):
        self.database.set_priority_min_points(50)
        self.assertEqual(self.database.get_nivel(49)[0], "Sin prio")
        self.assertEqual(self.database.get_nivel(50)[0], "Con prio")


if __name__ == "__main__":
    unittest.main()
