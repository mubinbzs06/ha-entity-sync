import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_entity_sync"))
import app


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".storage").mkdir()
        registry = {
            "data": {
                "entities": [
                    {"entity_id": "sensor.known"},
                    {"entity_id": "sensor.fan_speed"},
                    {"entity_id": "sensor.fan_speed_2"},
                    {"entity_id": "sensor.room_room_temperature"},
                ]
            }
        }
        (self.root / ".storage" / "core.entity_registry").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        (self.root / "automations.yaml").write_text(
            "automation:\n"
            "  - action:\n"
            "      - service: light.turn_on\n"
            "        target:\n"
            "          entity_id: sensor.missing_entity\n"
            "      - service: homeassistant.update_entity\n"
            "        data:\n"
            "          entity_id: sensor.known\n"
            "      - service: custom_domain.do_thing\n"
            "        data:\n"
            "          entity_id: custom_domain.unknown\n",
            encoding="utf-8",
        )
        self.config_patch = patch.object(app, "CONFIG", self.root)
        self.registry_patch = patch.object(
            app, "REGISTRY", self.root / ".storage" / "core.entity_registry"
        )
        self.config_patch.start()
        self.registry_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.registry_patch.stop()
        self.temp.cleanup()

    def test_registered_reference_contains_line_evidence(self):
        report = app.scan()
        self.assertTrue(report["ok"])
        known = [x for x in report["results"] if x["entity_id"] == "sensor.known"]
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["matches"][0]["line"], 8)
        self.assertIn("entity_id: sensor.known", known[0]["matches"][0]["text"])

    def test_unregistered_common_domain_is_only_a_candidate(self):
        report = app.scan()
        ids = {x["entity_id"] for x in report["potentially_stale"]}
        self.assertIn("sensor.missing_entity", ids)
        self.assertNotIn("custom_domain.unknown", ids)
        self.assertIn("candidate", {x["confidence"] for x in report["potentially_stale"]})

    def test_naming_patterns_are_informational_not_rename_recommendations(self):
        report = app.scan()
        findings = {x["entity_id"]: x for x in report["naming_findings"]}
        self.assertIn("sensor.fan_speed_2", findings)
        self.assertIn("informational", findings["sensor.fan_speed_2"]["issues"][0]["type"])
        self.assertEqual(
            findings["sensor.fan_speed_2"]["issues"][0]["matching_base"],
            "sensor.fan_speed",
        )
        self.assertIn("sensor.room_room_temperature", findings)
        self.assertIn("informational", findings["sensor.room_room_temperature"]["issues"][0]["type"])
        self.assertNotIn("suggested_entity_id", findings["sensor.room_room_temperature"])



    def test_reference_evidence_keeps_full_line_for_expansion(self):
        full_line = "  entity_id: sensor.known # " + ("extra-context " * 60)
        (self.root / "automations.yaml").write_text(full_line + "\n", encoding="utf-8")
        report = app.scan()
        reference = next(
            item for item in report["results"]
            if item["entity_id"] == "sensor.known"
        )
        evidence = reference["matches"][0]
        self.assertEqual(evidence["full_text"], full_line)
        self.assertFalse(evidence["full_text_truncated"])
        self.assertLessEqual(len(evidence["text"]), 500)

    def test_naming_finding_exposes_entity_source_from_registry(self):
        registry = {
            "data": {
                "entities": [
                    {"entity_id": "sensor.known", "platform": "mqtt"},
                    {"entity_id": "sensor.fan_speed_2", "platform": "mqtt"},
                ]
            }
        }
        (self.root / ".storage" / "core.entity_registry").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        report = app.scan()
        finding = next(
            item for item in report["naming_findings"]
            if item["entity_id"] == "sensor.fan_speed_2"
        )
        self.assertEqual(finding["source"], "mqtt")

    def test_naming_findings_include_active_and_historical_usage_locations(self):
        (self.root / "automations.yaml").write_text(
            "action:\n"
            "  entity_id: sensor.fan_speed_2\n", encoding="utf-8"
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.fan_speed_2"}', encoding="utf-8"
        )
        report = app.scan()
        finding = next(
            item for item in report["naming_findings"]
            if item["entity_id"] == "sensor.fan_speed_2"
        )
        usage = finding["usage"]
        active = [item for item in usage if item["category"] != "Historical"]
        historical = [item for item in usage if item["category"] == "Historical"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["file"], "automations.yaml")
        self.assertEqual(active[0]["matches"][0]["line"], 2)
        self.assertEqual(len(historical), 1)
        self.assertEqual(historical[0]["file"], ".storage/core.restore_state")

    def test_scan_scope_includes_user_config_and_dashboard_but_excludes_internal_and_backup(self):
        (self.root / "packages").mkdir()
        (self.root / "packages" / "lights.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        (self.root / ".storage" / "lovelace.dashboard_test").write_text(
            '{"entity":"sensor.known"}', encoding="utf-8"
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.known"}', encoding="utf-8"
        )
        (self.root / ".storage" / "trace.saved_traces").write_text(
            '{"entity_id":"sensor.known"}', encoding="utf-8"
        )
        custom = self.root / "custom_components" / "demo"
        custom.mkdir(parents=True)
        (custom / "sensor.py").write_text(
            'entity_id: "sensor.known"\\n', encoding="utf-8"
        )
        backup = self.root / "backups" / "old"
        backup.mkdir(parents=True)
        (backup / "automations.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        (self.root / "automations_backup.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        included = {
            path.relative_to(self.root).as_posix()
            for path in app.candidate_files()
        }
        self.assertIn("automations.yaml", included)
        self.assertIn("packages/lights.yaml", included)
        self.assertIn(".storage/lovelace.dashboard_test", included)
        self.assertIn(".storage/core.restore_state", included)
        self.assertIn(".storage/trace.saved_traces", included)
        self.assertNotIn("custom_components/demo/sensor.py", included)
        self.assertNotIn("backups/old/automations.yaml", included)
        self.assertNotIn("automations_backup.yaml", included)


    def test_historical_references_are_separated_from_active_dependencies(self):
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.known","old":"sensor.retired"}', encoding="utf-8"
        )
        report = app.scan()
        historical = [x for x in report["results"] if x["file"] == ".storage/core.restore_state"]
        self.assertTrue(historical)
        self.assertTrue(all(x["category"] == "Historical" for x in historical))
        retired = [x for x in report["potentially_stale"] if x["entity_id"] == "sensor.retired"]
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0]["category"], "Historical")
        self.assertIn("sensor.retired", retired[0]["matches"][0]["text"])

    def test_file_viewer_reads_only_scanner_approved_files(self):
        (self.root / "automations.yaml").write_text(
            "first line\nentity_id: sensor.known\n", encoding="utf-8"
        )
        (self.root / "backups").mkdir()
        (self.root / "backups" / "old.yaml").write_text(
            "secret backup", encoding="utf-8"
        )
        result = app.read_scoped_file("automations.yaml")
        self.assertEqual(result["path"], "automations.yaml")
        self.assertIn("entity_id: sensor.known", result["content"])
        with self.assertRaises(ValueError):
            app.read_scoped_file("../outside.yaml")
        with self.assertRaises(FileNotFoundError):
            app.read_scoped_file("backups/old.yaml")



if __name__ == "__main__":
    unittest.main()
