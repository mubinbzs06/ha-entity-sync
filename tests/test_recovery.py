import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_entity_sync"))
import recovery


def make_backup_archive(entity_ids, registry_entries=None):
    registry_entries = registry_entries or {}
    registry = {
        "data": {
            "entities": [
                registry_entries.get(entity_id, {"entity_id": entity_id})
                for entity_id in entity_ids
            ]
        }
    }
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w:gz") as archive:
        raw = json.dumps(registry).encode()
        info = tarfile.TarInfo("./.storage/core.entity_registry")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    outer = io.BytesIO()
    with tarfile.open(fileobj=outer, mode="w") as archive:
        raw = inner.getvalue()
        info = tarfile.TarInfo("homeassistant.tar.gz")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return outer.getvalue()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.old = "sensor.pi_cpu"
        self.new = "sensor.pi_cpu_2"
        self.old_entry = {
            "entity_id": self.old, "unique_id": "old-unique",
            "device_id": "device-a", "platform": "mqtt",
            "config_entry_id": "entry-a",
        }
        self.new_entry = {
            "entity_id": self.new, "unique_id": "new-unique",
            "device_id": "device-a", "platform": "mqtt",
            "config_entry_id": "entry-a",
        }

    def test_backup_archive_verifies_nested_entity_registry(self):
        archive = make_backup_archive([self.old, self.new])
        self.assertTrue(recovery.verify_backup_archive(archive, [self.old, self.new]))

    def test_backup_archive_rejects_missing_registry_entry(self):
        archive = make_backup_archive([self.old])
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_backup_archive(archive, [self.old, self.new])

    def test_backup_archive_accepts_matching_registry_identity(self):
        archive = make_backup_archive(
            [self.old, self.new],
            {self.old: self.old_entry, self.new: self.new_entry},
        )
        self.assertTrue(recovery.verify_backup_archive(
            archive, [self.old, self.new],
            {self.old: self.old_entry, self.new: self.new_entry},
        ))

    def test_backup_archive_rejects_changed_registry_identity(self):
        archive = make_backup_archive(
            [self.old, self.new],
            {self.old: self.old_entry, self.new: self.new_entry},
        )
        with self.assertRaisesRegex(recovery.RecoveryError, "identity"):
            recovery.verify_backup_archive(
                archive, [self.old, self.new],
                {self.old: self.old_entry, self.new: {**self.new_entry, "device_id": "other"}},
            )

    def test_identity_requires_strong_registry_lineage(self):
        self.assertEqual(
            recovery._identity_reason(self.old_entry, self.new_entry),
            "Same registered device ID and integration",
        )
        cross_integration = {**self.new_entry, "platform": "other"}
        self.assertIsNone(recovery._identity_reason(self.old_entry, cross_integration))
        unrelated = {
            "entity_id": self.new, "unique_id": "different",
            "device_id": "other-device", "platform": "other",
            "config_entry_id": "other-entry",
        }
        self.assertIsNone(recovery._identity_reason(self.old_entry, unrelated))

    def test_pair_preflight_requires_old_unavailable_and_new_live(self):
        with patch.object(recovery, "_fresh", return_value=(
            {self.old: self.old_entry, self.new: self.new_entry},
            {
                self.old: {"state": "unavailable"},
                self.new: {"state": "17.5"},
            },
        )):
            old_entry, new_entry, reason = recovery._validate_pair(
                self.old, self.new, lambda: {}
            )
        self.assertEqual(old_entry, self.old_entry)
        self.assertEqual(new_entry, self.new_entry)
        self.assertEqual(reason, "Same registered device ID and integration")

    def test_pair_preflight_blocks_disabled_entities(self):
        disabled = {**self.old_entry, "disabled_by": "user"}
        with patch.object(recovery, "_fresh", return_value=(
            {self.old: disabled, self.new: self.new_entry},
            {self.old: {"state": "unavailable"}, self.new: {"state": "17.5"}},
        )):
            with self.assertRaisesRegex(recovery.RecoveryError, "disabled"):
                recovery._validate_pair(self.old, self.new, lambda: {})

    def test_pair_preflight_blocks_name_only_matches(self):
        unrelated = {**self.new_entry, "device_id": "different", "config_entry_id": "different"}
        with patch.object(recovery, "_fresh", return_value=(
            {self.old: self.old_entry, self.new: unrelated},
            {self.old: {"state": "unavailable"}, self.new: {"state": "17.5"}},
        )):
            with self.assertRaisesRegex(recovery.RecoveryError, "identity"):
                recovery._validate_pair(self.old, self.new, lambda: {})

    def test_recovery_moves_old_entry_to_quarantine_and_live_replacement_to_original(self):
        temp_id = "sensor.pi_cpu_hes_old_deadbeef"
        preflight = (self.old_entry, self.new_entry, "Same registered device ID")
        final_entries = {
            self.old: self.new_entry,
            self.new: None,
            temp_id: self.old_entry,
        }
        final_states = {self.old: {"state": "17.5"}}
        with (
            patch.object(recovery, "_validate_pair", side_effect=[preflight, preflight]),
            patch.object(recovery, "create_verified_backup", return_value={"slug": "backup123", "name": "test"}),
            patch.object(recovery.secrets, "token_hex", side_effect=["record123", "deadbeef"]),
            patch.object(recovery, "_append_audit"),
            patch.object(recovery, "_update_audit"),
            patch.object(recovery, "_fresh", side_effect=[
                ({temp_id: None}, {}),
                (final_entries, final_states),
            ]),
            patch.object(recovery, "_entries", side_effect=[
                {self.old: None, self.new: self.new_entry, temp_id: self.old_entry},
                final_entries,
            ]),
            patch.object(recovery, "_rename") as rename,
            patch.object(recovery.time, "sleep"),
        ):
            live_fetch = unittest.mock.Mock(side_effect=[{}, final_states])
            result = recovery.recover_entity_pair(self.old, self.new, live_fetch)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["temporary_entity_id"], temp_id)
        self.assertEqual(result["backup_slug"], "backup123")
        self.assertEqual(rename.call_args_list[0].args, (self.old, temp_id))
        self.assertEqual(rename.call_args_list[1].args, (self.new, self.old))

    def test_completed_recovery_can_be_rolled_back_with_a_second_backup(self):
        temp_id = "sensor.pi_cpu_hes_old_deadbeef"
        record = {
            "id": "record123",
            "status": "recovered",
            "rollback_available": True,
            "old_entity_id": self.old,
            "replacement_entity_id": self.new,
            "temporary_entity_id": temp_id,
            "old_identity": recovery._identity_snapshot(self.old_entry),
            "replacement_identity": recovery._identity_snapshot(self.new_entry),
        }
        recovered_entries = {self.old: self.new_entry, self.new: None, temp_id: self.old_entry}
        recovered_states = {self.old: {"state": "17.5"}}
        rolled_back_entries = {self.old: self.old_entry, self.new: self.new_entry, temp_id: None}
        rolled_back_states = {self.new: {"state": "17.5"}}
        with (
            patch.object(recovery, "load_audit", return_value=[record]),
            patch.object(recovery, "create_verified_backup", return_value={"slug": "backup456", "name": "undo"}),
            patch.object(recovery, "_fresh", side_effect=[
                (recovered_entries, recovered_states),
                (recovered_entries, recovered_states),
                (rolled_back_entries, rolled_back_states),
            ]),
            patch.object(recovery, "_entries", return_value={self.old: None, self.new: self.new_entry, temp_id: self.old_entry}),
            patch.object(recovery, "_rename") as rename,
            patch.object(recovery, "_update_audit") as update_audit,
            patch.object(recovery.time, "sleep"),
        ):
            result = recovery.rollback_entity_recovery("record123", lambda: recovered_states)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["backup_slug"], "backup456")
        self.assertEqual(rename.call_args_list[0].args, (self.old, self.new))
        self.assertEqual(rename.call_args_list[1].args, (temp_id, self.old))
        update_audit.assert_called_once()

    def test_failed_second_rename_attempts_compensating_rollback(self):
        temp_id = "sensor.pi_cpu_hes_old_deadbeef"
        preflight = (self.old_entry, self.new_entry, "Same registered device ID")
        entries_after_first = {self.old: None, self.new: self.new_entry, temp_id: self.old_entry}
        original_entries = {self.old: self.old_entry, self.new: self.new_entry}
        with (
            patch.object(recovery, "_validate_pair", side_effect=[preflight, preflight]),
            patch.object(recovery, "create_verified_backup", return_value={"slug": "backup123", "name": "test"}),
            patch.object(recovery.secrets, "token_hex", side_effect=["record123", "deadbeef"]),
            patch.object(recovery, "_append_audit"),
            patch.object(recovery, "_update_audit"),
            patch.object(recovery, "_fresh", return_value=({temp_id: None}, {})),
            patch.object(recovery, "_entries", side_effect=[
                entries_after_first,
                entries_after_first,
                entries_after_first,
                original_entries,
            ]),
            patch.object(recovery, "_rename", side_effect=[None, recovery.RecoveryError("rename failed"), None, None]) as rename,
        ):
            result = recovery.recover_entity_pair(self.old, self.new, lambda: {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rolled_back_after_failure")
        self.assertGreaterEqual(rename.call_count, 3)


if __name__ == "__main__":
    unittest.main()
