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

    def test_report_counts_distinguish_records_entities_and_unreferenced(self):
        (self.root / "automations.yaml").write_text(
            "entity_id: sensor.known\nentity_id: sensor.known\n",
            encoding="utf-8",
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.fan_speed_2"}', encoding="utf-8"
        )
        report = app.scan()
        self.assertEqual(report["registered_entities"], 4)
        self.assertEqual(report["entities_with_active_references"], 1)
        self.assertEqual(report["entities_with_historical_only_references"], 1)
        self.assertEqual(report["unreferenced_entities"], 2)
        self.assertEqual(report["reference_records"], len(report["results"]))
        self.assertEqual(report["reference_hits"], sum(x["count"] for x in report["results"]))

    def test_conversation_domain_is_ignored_for_stale_candidates(self):
        (self.root / "automations.yaml").write_text(
            "entity_id: conversation.home_assistant\n", encoding="utf-8"
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"conversation.home_assistant"}', encoding="utf-8"
        )
        report = app.scan()
        self.assertNotIn(
            "conversation.home_assistant",
            {item["entity_id"] for item in report["potentially_stale"]},
        )

    def test_stale_candidates_rank_active_evidence_before_historical_only(self):
        (self.root / "automations.yaml").write_text(
            "entity_id: sensor.active_missing\n", encoding="utf-8"
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.history_missing"}', encoding="utf-8"
        )
        report = app.scan()
        candidates = report["potentially_stale"]
        self.assertEqual(candidates[0]["entity_id"], "sensor.active_missing")
        history = next(x for x in candidates if x["entity_id"] == "sensor.history_missing")
        self.assertEqual(history["priority"], "Historical-only evidence")

    def test_scan_cache_reuses_recent_result_and_force_rescans(self):
        app._scan_cache.update({"report": None, "created": 0.0})
        with patch.object(app, "scan", return_value={"ok": True, "marker": 1}) as scan_mock:
            first = app.get_scan_report()
            second = app.get_scan_report()
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(scan_mock.call_count, 1)
            app.get_scan_report(force=True)
            self.assertEqual(scan_mock.call_count, 2)
        app._scan_cache.update({"report": None, "created": 0.0})

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

    def test_naming_suggestions_are_unique_advisory_and_keep_domain(self):
        occupied = {
            "sensor.bathroom_temperature_2",
            "sensor.bathroom_temperature",
        }
        suggestions = app.suggest_entity_names(
            "sensor.bathroom_temperature_sensor_2", occupied
        )
        self.assertTrue(suggestions)
        self.assertTrue(all(item.startswith("sensor.") for item in suggestions))
        self.assertEqual(len(suggestions), len(set(suggestions)))
        self.assertNotIn("sensor.bathroom_temperature", suggestions)
        self.assertNotIn("sensor.bathroom_temperature_sensor_2", suggestions)

    def test_naming_suggestions_collapse_repeated_words(self):
        suggestions = app.suggest_entity_names(
            "light.kitchen_kitchen_ceiling_2",
            {"light.kitchen_kitchen_ceiling_2"},
        )
        self.assertIn("light.kitchen_ceiling", suggestions)

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



    def test_readded_pair_requires_live_replacement_and_identity_match(self):
        registry = [
            {"entity_id": "sensor.laundry_temperature", "device_id": "device-a", "platform": "mqtt"},
            {"entity_id": "sensor.laundry_temperature_2", "device_id": "device-a", "platform": "mqtt"},
        ]
        references = [{
            "entity_id": "sensor.laundry_temperature", "file": "automations.yaml",
            "category": "Active configuration", "count": 2, "lines": [4],
            "matches": [{"line": 4, "full_text": "entity_id: sensor.laundry_temperature"}],
        }]
        live = {
            "sensor.laundry_temperature": {"entity_id": "sensor.laundry_temperature", "state": "unavailable"},
            "sensor.laundry_temperature_2": {"entity_id": "sensor.laundry_temperature_2", "state": "21.5"},
        }
        plans, note = app.detect_readded_pairs(registry, references, live, {})
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["old_entity_id"], "sensor.laundry_temperature")
        self.assertEqual(plans[0]["replacement_entity_id"], "sensor.laundry_temperature_2")
        self.assertEqual(plans[0]["confidence"], "high")
        self.assertEqual(plans[0]["reference_count"], 2)
        self.assertEqual(plans[0]["active_references"][0]["file"], "automations.yaml")
        self.assertTrue(plans[0]["read_only_plan"])

    def test_readded_pair_does_not_guess_without_live_state_or_identity(self):
        registry = [
            {"entity_id": "sensor.laundry_temperature", "device_id": "device-a", "platform": "mqtt"},
            {"entity_id": "sensor.laundry_temperature_2", "device_id": "device-b", "platform": "zha"},
        ]
        plans, note = app.detect_readded_pairs(registry, [], None, {})
        self.assertEqual(plans, [])
        plans, note = app.detect_readded_pairs(
            registry, [], {
                "sensor.laundry_temperature": {"state": "unavailable"},
                "sensor.laundry_temperature_2": {"state": "21.5"},
            }, {}
        )
        self.assertEqual(plans, [])

    def test_readded_pair_detects_orphaned_old_state(self):
        registry = [
            {"entity_id": "sensor.laundry_temperature", "config_entry_id": "entry-a", "platform": "mqtt"},
            {"entity_id": "sensor.laundry_temperature_3", "config_entry_id": "entry-a", "platform": "mqtt"},
        ]
        plans, _ = app.detect_readded_pairs(
            registry, [], {"sensor.laundry_temperature_3": {"state": "22"}}, {}
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["old_state"], "orphaned")
        self.assertEqual(plans[0]["confidence"], "high")

    def test_clean_name_plan_shows_collision_and_exact_line_diff(self):
        registry = [
            {"entity_id": "sensor.bathroom_1_bathroom_1_temperature"},
            {"entity_id": "sensor.bathroom_1_temperature"},
        ]
        references = [{
            "entity_id": "sensor.bathroom_1_bathroom_1_temperature",
            "file": "packages/bathroom.yaml", "category": "Active configuration",
            "count": 1, "matches": [{
                "line": 12,
                "full_text": "entity_id: sensor.bathroom_1_bathroom_1_temperature",
            }],
        }]
        suggestions = app.build_clean_name_suggestions(registry, references)
        plan = next(item for item in suggestions if item["entity_id"] == "sensor.bathroom_1_bathroom_1_temperature")
        self.assertEqual(plan["suggested_entity_id"], "sensor.bathroom_1_temperature")
        self.assertTrue(plan["collision"])
        self.assertEqual(plan["affected_lines"][0]["file"], "packages/bathroom.yaml")
        self.assertEqual(plan["affected_lines"][0]["line"], 12)
        self.assertIn("sensor.bathroom_1_temperature", plan["affected_lines"][0]["after"])


if __name__ == "__main__":
    unittest.main()
