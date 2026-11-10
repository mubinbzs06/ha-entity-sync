import contextlib
import io
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

    def test_spook_style_suggestion_uses_same_domain_and_0_8_cutoff(self):
        suggestion = app.suggest_missing_entity_replacement(
            "sensor.living_room_temperatur",
            {"sensor.living_room_temperature", "binary_sensor.living_room_temperature"},
        )
        self.assertEqual(suggestion["entity_id"], "sensor.living_room_temperature")
        self.assertEqual(suggestion["confidence"], "name_similarity_only")
        self.assertEqual(suggestion["similarity_cutoff"], 0.8)

    def test_spook_style_suggestion_does_not_cross_domains(self):
        suggestion = app.suggest_missing_entity_replacement(
            "sensor.living_room_motion",
            {"binary_sensor.living_room_motion"},
        )
        self.assertIsNone(suggestion["entity_id"])
        self.assertEqual(suggestion["confidence"], "none")

    def test_deleted_entity_history_takes_precedence_over_fuzzy_match(self):
        suggestion = app.suggest_missing_entity_replacement(
            "sensor.living_room_temperatur",
            {"sensor.living_room_temperature"},
            {"sensor.living_room_temperatur": {"platform": "mqtt", "modified_at": "2026-11-01T00:00:00+00:00"}},
        )
        self.assertEqual(suggestion["method"], "deleted_entity_history")
        self.assertIsNone(suggestion["entity_id"])
        self.assertEqual(suggestion["deleted_platform"], "mqtt")
        self.assertEqual(suggestion["candidates"][0]["entity_id"], "sensor.living_room_temperature")
        self.assertTrue(suggestion["deleted_history_found"])

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

    def test_naming_suggestions_do_not_strip_numeric_suffixes(self):
        suggestions = app.suggest_entity_names(
            "light.bathroom_2", {"light.bathroom_2"}
        )
        self.assertEqual(suggestions, [])

    def test_naming_suggestions_collapse_only_adjacent_repeated_words(self):
        suggestions = app.suggest_entity_names(
            "light.kitchen_kitchen_ceiling_2",
            {"light.kitchen_kitchen_ceiling_2"},
        )
        self.assertEqual(suggestions, ["light.kitchen_ceiling_2"])
        non_adjacent = app.suggest_entity_names(
            "sensor.bathroom_temperature_bathroom",
            {"sensor.bathroom_temperature_bathroom"},
        )
        self.assertEqual(non_adjacent, [])
        collision = app.suggest_entity_names(
            "sensor.mommy_mommy_heater_heating",
            {"sensor.mommy_mommy_heater_heating", "sensor.mommy_heater_heating"},
        )
        self.assertEqual(collision, [])

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


    def test_clean_name_plan_can_flag_ha_numeric_suffix_collision(self):
        registry = [
            {"entity_id": "sensor.laundry_temperature_2", "platform": "mqtt"},
            {"entity_id": "sensor.laundry_temperature"},
        ]
        plans = app.build_clean_name_suggestions(registry, [])
        plan = next(item for item in plans if item["entity_id"] == "sensor.laundry_temperature_2")
        self.assertEqual(plan["suggested_entity_id"], "sensor.laundry_temperature")
        self.assertTrue(plan["collision"])
        self.assertEqual(plan["confidence"], "low")


    def test_active_reference_evidence_keeps_every_line_for_repair_plans(self):
        lines = [f"entity_id: sensor.known # reference {index}" for index in range(45)]
        (self.root / "automations.yaml").write_text("\n".join(lines), encoding="utf-8")
        report = app.scan()
        reference = next(item for item in report["results"] if item["entity_id"] == "sensor.known")
        self.assertEqual(reference["count"], 45)
        self.assertEqual(len(reference["matches"]), 45)
        self.assertEqual(reference["lines"], list(range(1, 46)))


    def test_readded_pair_rejects_same_names_without_shared_lineage(self):
        registry = [
            {"entity_id": "light.bathroom", "device_id": "bathroom-1", "platform": "zha",
             "config_entry_id": "entry-a", "original_name": "Bathroom"},
            {"entity_id": "light.bathroom_2", "device_id": "bathroom-2", "platform": "zha",
             "config_entry_id": "entry-b", "original_name": "Bathroom"},
        ]
        live = {
            "light.bathroom": {"state": "unavailable"},
            "light.bathroom_2": {"state": "on"},
        }
        plans, _ = app.detect_readded_pairs(registry, [], live, {})
        self.assertEqual(plans, [])

    def test_readded_pair_accepts_same_unique_id_lineage(self):
        registry = [
            {"entity_id": "sensor.device_temp", "platform": "mqtt", "unique_id": "same-unique"},
            {"entity_id": "sensor.device_temp_2", "platform": "mqtt", "unique_id": "same-unique"},
        ]
        live = {
            "sensor.device_temp": {"state": "unavailable"},
            "sensor.device_temp_2": {"state": "22"},
        }
        plans, _ = app.detect_readded_pairs(registry, [], live, {})
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["identity_reason"], "Same integration unique_id")

    def test_prefix_removal_is_not_suggested_and_repeated_words_remain_primary(self):
        registry = [
            {"entity_id": "sensor.sun_next_dawn", "platform": "sun"},
            {"entity_id": "sensor.wiz_rgbw_tunable_light", "platform": "wiz"},
            {"entity_id": "sensor.mommy_mommy_heater_heating"},
        ]
        plans = app.build_clean_name_suggestions(registry, [])
        by_id = {item["entity_id"]: item for item in plans}
        self.assertNotIn("sensor.sun_next_dawn", by_id)
        self.assertNotIn("sensor.wiz_rgbw_tunable_light", by_id)
        self.assertEqual(
            by_id["sensor.mommy_mommy_heater_heating"]["suggested_entity_id"],
            "sensor.mommy_heater_heating",
        )
        self.assertEqual(by_id["sensor.mommy_mommy_heater_heating"]["confidence"], "high")

    def test_duplicate_suggested_targets_in_same_batch_are_flagged(self):
        registry = [
            {"entity_id": "sensor.mommy_mommy_heater_heating"},
            {"entity_id": "sensor.mommy_mommy_heater_heating_2"},
        ]
        plans = app.build_clean_name_suggestions(registry, [])
        self.assertEqual(len(plans), 2)
        self.assertEqual(
            {item["suggested_entity_id"] for item in plans},
            {"sensor.mommy_heater_heating"},
        )
        self.assertEqual(sum(item["collision"] for item in plans), 1)
        self.assertEqual(
            sum("same target ID" in item["reason"] for item in plans),
            1,
        )

    def test_live_state_api_logs_http_status_and_uses_supervisor_bearer_token(self):
        class FakeResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return b'[{"entity_id":"sensor.test","state":"on"}]'
            def getcode(self):
                return self.status

        output = io.StringIO()
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "test-token"}), \
             patch.object(app.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen, \
             contextlib.redirect_stdout(output):
            states = app.fetch_live_states()
        self.assertEqual(states["sensor.test"]["state"], "on")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://supervisor/core/api/states")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertIn("HTTP 200", output.getvalue())

    def test_live_state_api_logs_http_error_status(self):
        error = app.urllib.error.HTTPError(
            "http://supervisor/core/api/states", 403, "Forbidden", {}, None
        )
        output = io.StringIO()
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "test-token"}), \
             patch.object(app.urllib.request, "urlopen", side_effect=error), \
             contextlib.redirect_stdout(output):
            states = app.fetch_live_states()
        self.assertIsNone(states)
        self.assertIn("HTTP 403", output.getvalue())


    def test_candidate_files_excludes_symlinks_and_symlink_directories(self):
        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside.yaml")
        outside.write_text("entity_id: sensor.known\\n", encoding="utf-8")
        linked_file = self.root / "linked.yaml"
        linked_file.symlink_to(outside)
        outside_dir = self.root / "linked_dir"
        outside_dir.symlink_to(outside.parent, target_is_directory=True)
        try:
            included = {path.relative_to(self.root).as_posix() for path in app.candidate_files()}
            self.assertNotIn("linked.yaml", included)
            self.assertNotIn("linked_dir", included)
        finally:
            outside.unlink()

    def test_scan_stops_at_configured_file_limit_and_reports_warning(self):
        (self.root / "z_second.yaml").write_text("entity_id: sensor.known\\n", encoding="utf-8")
        with patch.object(app, "MAX_SCAN_FILES", 1):
            report = app.scan()
        self.assertTrue(report["ok"])
        self.assertEqual(report["files_scanned"], 1)
        self.assertTrue(any("safety limit" in warning for warning in report["scan_warnings"]))

    def test_live_state_api_reports_missing_supervisor_token(self):
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output):
            states = app.fetch_live_states()
        self.assertIsNone(states)
        self.assertIn("SUPERVISOR_TOKEN is missing", output.getvalue())


    def test_addon_startup_uses_s6_environment_wrapper(self):
        startup = Path(__file__).resolve().parents[1] / "ha_entity_sync" / "run.sh"
        self.assertEqual(
            startup.read_text(encoding="utf-8").splitlines()[0],
            "#!/command/with-contenv bash",
        )


if __name__ == "__main__":
    unittest.main()

    def test_dashboard_health_classifies_available_unavailable_unknown_and_missing(self):
        references = [
            {"entity_id": "sensor.ready", "file": ".storage/lovelace", "category": "Dashboard", "count": 1, "lines": [1], "matches": []},
            {"entity_id": "sensor.down", "file": ".storage/lovelace", "category": "Dashboard", "count": 1, "lines": [2], "matches": []},
            {"entity_id": "sensor.uncertain", "file": ".storage/lovelace", "category": "Dashboard", "count": 1, "lines": [3], "matches": []},
            {"entity_id": "sensor.retired_dashboard_id", "file": ".storage/lovelace", "category": "Dashboard", "count": 1, "lines": [4], "matches": []},
        ]
        registry = [{"entity_id": "sensor.ready"}, {"entity_id": "sensor.down"}, {"entity_id": "sensor.uncertain"}]
        live = {"sensor.ready": {"state": "on"}, "sensor.down": {"state": "unavailable"}, "sensor.uncertain": {"state": "unknown"}}
        result = app.build_dashboard_health(references, registry, live)
        statuses = {item["entity_id"]: item["health_status"] for item in result}
        self.assertEqual(statuses["sensor.ready"], "available")
        self.assertEqual(statuses["sensor.down"], "unavailable")
        self.assertEqual(statuses["sensor.uncertain"], "unknown")
        self.assertEqual(statuses["sensor.retired_dashboard_id"], "missing")

    def test_dashboard_health_does_not_claim_missing_when_live_api_unavailable(self):
        result = app.build_dashboard_health([{"entity_id": "sensor.old", "file": ".storage/lovelace", "category": "Dashboard"}], [], None)
        self.assertEqual(result[0]["health_status"], "not_checked")

    def test_dashboard_scan_surfaces_unregistered_id_even_without_entity_context_key(self):
        dashboard = self.root / ".storage" / "lovelace.dashboard_test"
        dashboard.write_text('{"data":{"config":{"custom_label":"sensor.dashboard_missing"}}}', encoding="utf-8")
        report = app.scan()
        stale = [item for item in report["potentially_stale"] if item["entity_id"] == "sensor.dashboard_missing"]
        self.assertTrue(stale)
        health = [item for item in report["dashboard_health"] if item["entity_id"] == "sensor.dashboard_missing"]
        self.assertTrue(health)
        self.assertEqual(health[0]["registry_status"], "not_registered")
    def test_dashboard_health_identifies_nested_lovelace_card(self):
        dashboard = self.root / ".storage" / "lovelace.dashboard_test"
        dashboard.write_text(json.dumps({"data": {"config": {"views": [{"cards": [{"type": "sensor", "entity": "sensor.dashboard_missing", "name": "Pi-hole Ads"}]}]}}}), encoding="utf-8")
        context = app.find_dashboard_card_context(".storage/lovelace.dashboard_test", "sensor.dashboard_missing")
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["card_type"], "sensor")
        self.assertEqual(context[0]["card_title"], "Pi-hole Ads")
        self.assertIn("cards[0]", context[0]["card_path"])

class ReplacementIdentityEvidenceTests(unittest.TestCase):
    def test_evaluator_exposes_strong_match_and_missing_metadata(self):
        result = app.evaluate_replacement_identity(
            {"unique_id": "abc", "platform": "mqtt"},
            {"unique_id": "abc", "platform": "mqtt"},
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["confidence"], "high")
        self.assertTrue(any(item["status"] == "match" and item["strength"] == "strong"
                            for item in result["identity_evidence"]))
        self.assertTrue(any(item["status"] == "not_available"
                            for item in result["identity_evidence"]))

    def test_conflicting_identity_blocks_otherwise_matching_candidate(self):
        result = app.evaluate_replacement_identity(
            {"unique_id": "same", "device_id": "device-a", "platform": "mqtt"},
            {"unique_id": "same", "device_id": "device-b", "platform": "mqtt"},
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["confidence"], "none")
        self.assertTrue(result["identity_conflicts"])

    def test_names_and_matching_platform_alone_are_not_identity(self):
        result = app.evaluate_replacement_identity(
            {"platform": "mqtt", "original_name": "Laundry temperature"},
            {"platform": "mqtt", "original_name": "Laundry temperature"},
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["confidence"], "none")

    def test_readded_pair_includes_explainable_identity_evidence(self):
        registry = [
            {"entity_id": "sensor.laundry_temperature", "device_id": "device-a", "platform": "mqtt"},
            {"entity_id": "sensor.laundry_temperature_2", "device_id": "device-a", "platform": "mqtt"},
        ]
        live = {
            "sensor.laundry_temperature": {"state": "unavailable"},
            "sensor.laundry_temperature_2": {"state": "21.5"},
        }
        plans, _ = app.detect_readded_pairs(registry, [], live, {})
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0]["identity_evidence"])
        self.assertEqual(plans[0]["identity_conflicts"], [])


class DashboardRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".storage").mkdir()
        self.config_patch = patch.object(app, "CONFIG", self.root)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.temp.cleanup()

    def test_default_dashboard_route_is_available(self):
        self.assertEqual(app.dashboard_route_index()[".storage/lovelace"], "/lovelace")

    def test_custom_dashboard_route_uses_registered_url_path(self):
        payload = {"data": {"dashboards": [
            {"id": "dash-123", "url_path": "energy-overview"},
            {"id": "dash-456", "url_path": "security"}
        ]}}
        (self.root / ".storage" / "lovelace_dashboards").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        routes = app.dashboard_route_index()
        self.assertEqual(routes[".storage/lovelace.dash-123"], "/energy-overview")
        self.assertEqual(routes[".storage/lovelace.dash-456"], "/security")

    def test_invalid_dashboard_url_path_is_not_exposed(self):
        payload = {"data": {"dashboards": [
            {"id": "unsafe", "url_path": "../config"}
        ]}}
        (self.root / ".storage" / "lovelace_dashboards").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        self.assertNotIn(".storage/lovelace.unsafe", app.dashboard_route_index())

    def test_missing_dashboard_registry_keeps_default_route(self):
        self.assertEqual(app.dashboard_route_index(), {".storage/lovelace": "/lovelace"})


    def test_unique_id_without_matching_integration_is_not_sufficient(self):
        result = app.evaluate_replacement_identity(
            {"unique_id": "same-id", "platform": "mqtt"},
            {"unique_id": "same-id", "platform": "zwave"},
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(result["identity_conflicts"])

    def test_scanned_dashboard_references_include_live_preview_route(self):
        payload = {"data": {"dashboards": [
            {"id": "dash-123", "url_path": "energy-overview"}
        ]}}
        (self.root / ".storage" / "lovelace_dashboards").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        references = app.enrich_dashboard_references([
            {"entity_id": "sensor.example", "file": ".storage/lovelace.dash-123",
             "category": "Dashboard", "count": 1}
        ])
        self.assertEqual(references[0]["dashboard_url"], "/energy-overview")
        self.assertTrue(references[0]["dashboard_preview_available"])
