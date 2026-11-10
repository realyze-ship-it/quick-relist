"""Offline durable creation/recovery tests. No Hub connectivity is qualified."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from quick_relist.creation import CreationStore, DraftCreator, CreationBlocked
from quick_relist.core import OperationBusy
from quick_relist.mutations import MutationRequest, canonical, digest, validate_receipt
from test_core import SyntheticHub
from creation_fixture import SyntheticCreationProvider, Crash


def creation_request(folder, operation="parent-create", rxcu="RXCU200001"):
    media = folder / "photo.bin"
    media.write_bytes(b"synthetic original bytes")
    snapshot = SyntheticHub(media).source(rxcu)
    snapshot["fields"]["unknown_extension"] = {"flag": True, "number": 1, "nested": [None, {"literal": ""}]}
    snapshot["media_manifest"][0].update(cover=True, tags=["first", "unknown"], extension={"flag": True})
    address = deepcopy(snapshot["fields"]["address"])
    address["street_number"] = "99"
    snapshot_hash = digest(snapshot)
    return MutationRequest(action="create", operation_id=operation, account_id="synthetic-account",
        agency_id="synthetic-agency", source_id=snapshot["source_id"], source_rxcu=rxcu,
        expected_source_revision="r3", portal_targets=tuple(snapshot["portal_targets"]),
        snapshot_json=canonical(snapshot), snapshot_hash=snapshot_hash,
        approved_address_json=canonical(address), approval_hash=digest({"snapshot_hash": snapshot_hash,
            "address": address, "archive_reason": "mandate_expired"}))


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / "private" / "creation.sqlite3"
        self.request = creation_request(self.folder)
        self.provider = SyntheticCreationProvider(self.request)
        self.store = CreationStore(self.path)
        self.creator = DraftCreator(self.store, self.provider)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = CreationStore(self.path)
        self.creator = DraftCreator(self.store, self.provider)

    def finish(self):
        for _ in range(100):
            result = self.creator.advance_for_testing(self.request)
            if result["status"] == "complete":
                return result
        self.fail("Did not complete")

    def test_default_entrypoint_has_no_effect(self):
        with self.assertRaises(CreationBlocked):
            self.creator.execute(self.request)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM creation_bindings").fetchone()[0], 0)
        self.assertFalse(self.creator.is_live_ready)
        self.assertFalse(self.creator.raw_only_coverage_verified)

    def test_full_fidelity_exact_fields_metadata_actual_bytes_and_restart(self):
        allocated = self.creator.advance_for_testing(self.request)
        self.assertEqual(allocated["ordinal"], 1)
        self.assertIsNotNone(allocated["draft_id"])
        self.reopen()
        result = self.finish()
        draft = self.provider.drafts[result["draft_id"]]
        self.assertEqual(canonical(draft["fields"]), canonical(self.request.replacement_fields))
        self.assertEqual(draft["fields"]["mandate"], self.request.snapshot["fields"]["mandate"])
        self.assertEqual(self.provider.uploads[0][1], b"synthetic original bytes")
        self.assertNotIn("local_path", self.provider.uploads[0][2])
        receipt = self.creator.complete_receipt_for_testing(self.request)
        validate_receipt(self.request, receipt)
        before = len(self.provider.calls)
        self.assertEqual(self.creator.advance_for_testing(self.request), result)
        self.assertEqual(len(self.provider.calls), before)
        self.assertEqual(self.creator.progress_for_testing(self.request)["status"], "applied")

    def test_crash_after_every_remote_write_recovers_without_duplicates(self):
        write_count = len(self.creator._plan(self.request)) - 1
        for ordinal in range(write_count):
            with self.subTest(ordinal=ordinal):
                self.provider.crash_after = ordinal
                with self.assertRaises(Crash):
                    self.creator.advance_for_testing(self.request)
                self.reopen()
                self.assertEqual(self.creator.status(self.request)["status"], "needs_reconciliation")
                before = len(self.provider.calls)
                with self.assertRaises(CreationBlocked):
                    self.creator.advance_for_testing(self.request)
                recovered = self.creator.reconcile_for_testing(self.request)
                self.assertEqual(recovered["ordinal"], ordinal + 1)
                self.assertEqual(len(self.provider.calls), before)
                self.reopen()
        self.finish()
        self.assertEqual(len(self.provider.drafts), 1)
        self.assertEqual(len(self.provider.uploads), 1)
        self.assertEqual(len(self.provider.calls), write_count)

    def test_loss_after_remote_receipt_before_local_commit(self):
        original = self.creator._append
        def crash(request, event):
            if event["phase"] == "applied":
                raise Crash()
            original(request, event)
        with patch.object(self.creator, "_append", crash):
            with self.assertRaises(Crash):
                self.creator.advance_for_testing(self.request)
        self.reopen()
        self.creator.reconcile_for_testing(self.request)
        self.assertEqual(len(self.provider.drafts), 1)
        self.finish()
        self.assertEqual(len(self.provider.drafts), 1)

    def test_terminal_absence_only_allows_explicit_same_key_retry(self):
        self.provider.fail_before = 0
        with self.assertRaisesRegex(CreationBlocked, "uncertain") as caught:
            self.creator.advance_for_testing(self.request)
        self.assertNotIn("SECRET", str(caught.exception))
        self.creator.reconcile_for_testing(self.request)
        self.assertEqual(self.creator.progress_for_testing(self.request)["status"], "not_applied")
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.creator.advance_for_testing(self.request, retry_not_applied=True)
        self.assertEqual(self.provider.calls[0], self.provider.calls[1])
        self.assertEqual(len(self.provider.drafts), 1)

    def test_partial_terminal_upload_absence_resumes_same_child(self):
        upload = next(i for i, (stage, _) in enumerate(self.creator._plan(self.request)) if stage == "upload")
        for _ in range(upload):
            self.creator.advance_for_testing(self.request)
        self.provider.fail_before = upload
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.creator.reconcile_for_testing(self.request)
        checkpoint = self.creator.checkpoint_for_testing(self.request)
        self.assertTrue(checkpoint["retry_not_applied"])
        self.assertEqual(self.creator.progress_for_testing(self.request)["status"], "partial")
        self.creator.advance_for_testing(self.request, expected_checkpoint=checkpoint)
        self.assertEqual(self.provider.calls[-1], self.provider.calls[-2])
        self.finish()
        self.assertEqual(len(self.provider.uploads), 1)

    def test_unknown_nonterminal_and_cross_bound_reconcile_never_retry(self):
        self.provider.fail_after = 0
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        for changes in ({"status": "unknown"}, {"status": "not_applied", "terminal": False},
                        {"status": "not_applied", "terminal": True, "request_hash": "other"},
                        {"status": "not_applied", "terminal": 1}):
            self.provider.reconciliation = lambda cmd: {**DraftCreator._binding(cmd), **changes}
            with self.assertRaises(CreationBlocked):
                self.creator.reconcile_for_testing(self.request)
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request, retry_not_applied=True)
        self.provider.reconciliation = None
        self.creator.reconcile_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), 1)

    def test_changed_media_blocks_before_allocate_and_before_upload(self):
        media = Path(self.request.snapshot["media_manifest"][0]["local_path"])
        media.write_bytes(b"changed")
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(self.provider.calls, [])
        media.write_bytes(b"synthetic original bytes")
        self.creator.advance_for_testing(self.request)
        media.write_bytes(b"changed")
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertIsNotNone(self.creator.status(self.request)["draft_id"])

    def test_false_integer_substitution_and_extra_media_fail_fidelity(self):
        while self.creator.advance_for_testing(self.request)["ordinal"] < len(self.creator._plan(self.request)) - 1:
            pass
        draft = next(iter(self.provider.drafts.values()))
        draft["fields"]["unknown_extension"]["flag"] = 1
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        draft["fields"] = self.request.replacement_fields
        draft["media_manifest"].append(deepcopy(draft["media_manifest"][0]))
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        with self.assertRaises(CreationBlocked):
            self.creator.complete_receipt_for_testing(self.request)

    def test_source_prerequisites_and_stale_draft_block_without_write(self):
        for name, bad in (("source_revision", "r4"), ("archived", False), ("portals_removed", False)):
            good = getattr(self.provider, name)
            setattr(self.provider, name, bad)
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request)
            setattr(self.provider, name, good)
        self.assertEqual(self.provider.calls, [])
        self.creator.advance_for_testing(self.request)
        next(iter(self.provider.drafts.values()))["revision"] = "external-edit"
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), 1)

    def test_stale_checkpoint_and_changed_parent_are_blocked(self):
        self.creator.advance_for_testing(self.request)
        checkpoint = self.creator.checkpoint_for_testing(self.request)
        self.creator.advance_for_testing(self.request, expected_checkpoint=checkpoint)
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request, expected_checkpoint=checkpoint)
        with self.assertRaises(CreationBlocked):
            self.creator.status(replace(self.request, expected_source_revision="r4"))
        self.assertEqual(len(self.provider.calls), 2)

    def test_journal_immutable_and_corruption_rejected(self):
        self.creator.advance_for_testing(self.request)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute("DELETE FROM creation_events")
        self.store.conn.rollback()
        with self.store._tx() as db:
            db.execute("DROP TRIGGER immutable_creation_event_update")
            db.execute("UPDATE creation_events SET event_json=? WHERE sequence=1", (canonical({"phase": "intent", "ordinal": True, "command": {}}),))
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), 1)

    def test_independent_store_lock_blocks_overlap(self):
        second = CreationStore(self.path)
        try:
            with self.store.operation_lock():
                with self.assertRaises(OperationBusy):
                    DraftCreator(second, self.provider).advance_for_testing(self.request)
        finally:
            second.close()
        self.assertEqual(self.provider.calls, [])

    def test_duplicate_source_parent_is_rejected(self):
        self.creator.advance_for_testing(self.request)
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(replace(self.request, operation_id="another-parent"))
        self.assertEqual(len(self.provider.calls), 1)

    def test_cross_parent_draft_substitution_is_rejected(self):
        self.creator.advance_for_testing(self.request)
        other = creation_request(self.folder, "another-parent", "RXCU200002")
        provider = SyntheticCreationProvider(other)
        creator = DraftCreator(self.store, provider)
        # Both synthetic servers allocate synthetic-draft-1: collision must block.
        with self.assertRaises(CreationBlocked):
            creator.advance_for_testing(other)
        self.assertEqual(creator.status(other)["status"], "needs_reconciliation")
        with self.assertRaises(CreationBlocked):
            creator.reconcile_for_testing(other)
        self.assertEqual(len(provider.calls), 1)

    def test_private_database_and_link_rejection(self):
        if os.name == "nt":
            self.skipTest("POSIX mode check")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        broad = self.folder / "broad"
        broad.mkdir(mode=0o755)
        with self.assertRaises(CreationBlocked):
            CreationStore(broad / "database")
        link = self.folder / "linked"
        link.symlink_to(self.path)
        with self.assertRaises(CreationBlocked):
            CreationStore(link)

    def test_multi_asset_order_metadata_and_bytes_are_exact(self):
        snapshot = self.request.snapshot
        plan = self.folder / "plan.bin"
        plan.write_bytes(b"synthetic plan")
        import hashlib
        snapshot["media_manifest"].insert(0, {"id": "plan-2", "kind": "floorplan", "position": 7,
            "caption": "Plan", "cover": False, "extra": [False, 0], "size": len(plan.read_bytes()),
            "sha256": hashlib.sha256(plan.read_bytes()).hexdigest(), "local_path": str(plan)})
        snapshot["media_count"] = 2
        snap_hash = digest(snapshot)
        self.request = replace(self.request, snapshot_json=canonical(snapshot), snapshot_hash=snap_hash,
            approval_hash=digest({"snapshot_hash": snap_hash, "address": self.request.approved_address,
                                  "archive_reason": "mandate_expired"}))
        self.provider = SyntheticCreationProvider(self.request)
        self.creator = DraftCreator(self.store, self.provider)
        self.finish()
        self.assertEqual([u[2]["id"] for u in self.provider.uploads], ["plan-2", "asset-original"])
        self.assertEqual(self.provider.uploads[0][1], b"synthetic plan")
        self.assertEqual(self.provider.uploads[0][2]["extra"], [False, 0])

    def test_atomic_guard_rejects_race_after_readonly_preflight(self):
        preflight = self.provider.preflight
        def race(command):
            response = preflight(command)
            self.provider.source_revision = "changed-after-read"
            return response
        self.provider.preflight = race
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(self.provider.drafts, {})
        self.assertEqual(self.creator.status(self.request)["status"], "needs_reconciliation")
        self.creator.reconcile_for_testing(self.request)
        self.assertEqual(self.creator.status(self.request)["status"], "retry_available")

    def test_cross_process_lock_blocks_without_provider_work(self):
        code = """import sys
from quick_relist.creation import CreationStore
from quick_relist.core import OperationBusy
store = CreationStore(sys.argv[1])
try:
    with store.operation_lock():
        print('UNEXPECTED')
except OperationBusy:
    print('BLOCKED')
finally:
    store.close()
"""
        with self.store.operation_lock():
            result = subprocess.run([sys.executable, "-c", code, str(self.path)],
                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(result.stdout.strip(), "BLOCKED")

    @unittest.skipUnless(os.name == "nt", "Windows ACL runtime qualification requires Windows")
    def test_windows_private_directory_survives_reopen(self):
        from quick_relist.creation import _windows_private_acl
        self.store.close()
        # A new independent directory gets its protected owner-only ACL at creation.
        self.path = self.folder / "private-child" / "creation.sqlite3"
        self.store = CreationStore(self.path)
        _windows_private_acl(self.path.parent)
        _windows_private_acl(self.path, protected=False)
        self.reopen()

    def test_completed_receipt_rechecks_current_fidelity(self):
        self.finish()
        next(iter(self.provider.drafts.values()))["state"] = "published"
        with self.assertRaises(CreationBlocked):
            self.creator.complete_receipt_for_testing(self.request)


if __name__ == "__main__":
    unittest.main()
