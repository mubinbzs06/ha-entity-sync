import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_entity_sync"))
import config_repair as repair


class ConfigRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.target = self.root / "automations.yaml"
        self.target.write_text(
            "trigger:\n  - platform: state\n    entity_id: sensor.old_name\n",
            encoding="utf-8",
        )
        self.backup_patch = patch.object(repair, "BACKUP_DIR", self.data / "backups")
        self.audit_patch = patch.object(repair, "AUDIT_FILE", self.data / "audit.json")
        self.backup_patch.start()
        self.audit_patch.start()

    def tearDown(self):
        self.backup_patch.stop()
        self.audit_patch.stop()
        self.temp.cleanup()

    def test_preview_is_read_only_and_reports_exact_change(self):
        before = self.target.read_text(encoding="utf-8")
        plan = repair.preview_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name"
        )
        self.assertEqual(plan["occurrences"], 1)
        self.assertIn("sensor.new_name", plan["after"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), before)

    def test_apply_creates_verified_backup_and_can_rollback(self):
        plan = repair.preview_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name"
        )
        result = repair.apply_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name",
            plan["before_sha256"],
        )
        self.assertTrue(result["ok"])
        self.assertIn("sensor.new_name", self.target.read_text(encoding="utf-8"))
        rolled = repair.rollback_config_repair(self.root, result["record"]["id"])
        self.assertTrue(rolled["ok"])
        self.assertIn("sensor.old_name", self.target.read_text(encoding="utf-8"))

    def test_stale_preview_is_rejected_without_write(self):
        plan = repair.preview_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name"
        )
        self.target.write_text("entity_id: sensor.other\n", encoding="utf-8")
        with self.assertRaises(repair.ConfigRepairError):
            repair.apply_config_repair(
                self.root, "automations.yaml", "sensor.old_name", "sensor.new_name",
                plan["before_sha256"],
            )
        self.assertEqual(self.target.read_text(encoding="utf-8"), "entity_id: sensor.other\n")

    def test_rollback_refuses_to_overwrite_later_edits(self):
        plan = repair.preview_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name"
        )
        result = repair.apply_config_repair(
            self.root, "automations.yaml", "sensor.old_name", "sensor.new_name",
            plan["before_sha256"],
        )
        self.target.write_text("a later manual edit\n", encoding="utf-8")
        with self.assertRaises(repair.ConfigRepairError):
            repair.rollback_config_repair(self.root, result["record"]["id"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "a later manual edit\n")

    def test_path_traversal_and_historical_storage_are_rejected(self):
        with self.assertRaises(repair.ConfigRepairError):
            repair.preview_config_repair(
                self.root, "../outside.yaml", "sensor.old_name", "sensor.new_name"
            )
        with self.assertRaises(repair.ConfigRepairError):
            repair.preview_config_repair(
                self.root, ".storage/core.restore_state", "sensor.old_name", "sensor.new_name"
            )


if __name__ == "__main__":
    unittest.main()
