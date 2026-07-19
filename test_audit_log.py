import gc
import importlib
import os
import tempfile
import unittest
import warnings
from datetime import datetime, timezone

from audit_log import audit_event_risk, build_audit_markdown, format_audit_dm_line


class AuditLogTests(unittest.TestCase):
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

    def test_events_are_persistent_and_returned_newest_first(self):
        first_id = self.database.record_audit_event(
            "multiplicadores",
            "actualizar",
            "10",
            "Officer",
            "evidencia",
            "100",
            "Cambio x1.00 a x0.95",
            {"antes": "1.00", "despues": "0.95"},
            "2026-07-18T10:00:00",
        )
        second_id = self.database.record_audit_event(
            "evidencias",
            "aprobar",
            "11",
            "Admin",
            "evidencia",
            "100",
            "Evidencia aprobada",
            created_at="2026-07-18T10:05:00",
        )
        events = self.database.get_audit_events()
        self.assertEqual([event["id"] for event in events], [second_id, first_id])
        self.assertEqual(events[1]["details"]["despues"], "0.95")

    def test_markdown_contains_actor_target_and_details(self):
        events = [{
            "id": 7,
            "created_at": "2026-07-18T10:00:00",
            "category": "multiplicadores",
            "action": "actualizar",
            "actor_id": "10",
            "actor_name": "Officer",
            "target_type": "evidencia",
            "target_id": "100",
            "summary": "Cambio x1.00 a x0.95",
            "details": {"antes": "1.00", "despues": "0.95"},
        }]
        content = build_audit_markdown(
            events,
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIn("# Historial de RankingBot", content)
        self.assertIn("Ajustes de multiplicador", content)
        self.assertIn("Officer (`10`)", content)
        self.assertIn("evidencia `100`", content)
        self.assertIn("**Despues:** `0.95`", content)

    def test_new_events_queue_for_dm_and_are_marked_once(self):
        event_id = self.database.record_audit_event(
            "puntos",
            "restar",
            "10",
            "Officer",
            "scout",
            "20",
            "Resto 5 puntos.",
        )
        queued = self.database.get_pending_audit_dm_events()
        self.assertEqual([event["id"] for event in queued], [event_id])
        self.assertTrue(self.database.mark_audit_dm_notified(event_id))
        self.assertEqual(self.database.get_pending_audit_dm_events(), [])

    def test_dm_line_is_single_line_and_risk_colored(self):
        event = {
            "category": "puntos",
            "action": "restar",
            "actor_id": "10",
            "actor_name": "Officer",
            "target_type": "scout",
            "target_id": "20",
            "summary": "Resto puntos\npor auditoria.",
        }
        self.assertEqual(audit_event_risk(event), "high")
        line = format_audit_dm_line(event)
        self.assertTrue(line.startswith("🔴"))
        self.assertIn("Officer <@10>", line)
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
