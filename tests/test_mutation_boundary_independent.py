"""Independent offline regressions for the executable mutation boundary.

These use synthetic records only and do not qualify any live Hub provider.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from quick_relist.core import Engine, SafetyBlocked, Store
from quick_relist.mutations import GatedHubAdapter, MutationContractError, canonical, digest
from test_core import SyntheticHub


class IndependentMutationBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.media = self.folder / "original.bin"
        self.media.write_bytes(b"Independent synthetic original media")
        self.database = self.folder / "jobs.sqlite3"
        self.store = Store(self.database)
        self.provider = SyntheticHub(self.media)
        self.engine = Engine(self.store, GatedHubAdapter(self.provider))
        batch = self.engine.new_batch(["RXCU786001"])
        self.job_id = self.store.list_jobs(batch)[0]["id"]
        job = self.engine.snapshot(self.job_id)
        address = deepcopy(job["original_address"])
        address["street_number"] = "80"
        self.engine.approve_address(self.job_id, address, "mandate_expired")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reach(self, target):
        for _ in range(8):
            job = self.store.get_job(self.job_id)
            if job["state"] == target:
                return job
            self.engine.advance(self.job_id)
        self.fail("Synthetic workflow did not reach requested state")

    def assert_not_persisted(self, marker):
        self.assertNotIn(marker, json.dumps(self.store.journal(self.job_id)))
        results = self.store.conn.execute("SELECT result_json FROM operations").fetchall()
        self.assertNotIn(marker, str([tuple(row) for row in results]))
        self.assertNotIn(marker.encode(), self.database.read_bytes())

    def test_valid_receipt_extra_allowlisted_fields_are_not_copied_to_journal(self):
        marker = "INDEPENDENT_SYNTHETIC_PRIVATE_RECEIPT"
        execute = self.provider.execute

        def with_untrusted_extras(request):
            return {**execute(request), "reason": marker, "state": marker,
                    "accepted": marker, "listing_id": marker, "rxcu": marker,
                    "archive_reason": marker, "listing_revision": marker}

        self.provider.execute = with_untrusted_extras
        self.assertEqual(self.engine.advance(self.job_id)["state"], "portals_removed")
        self.assert_not_persisted(marker)

    def test_valid_reconciliation_extras_are_not_copied_to_journal(self):
        marker = "INDEPENDENT_SYNTHETIC_PRIVATE_RECONCILIATION"
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(self.job_id)
        reconcile = self.provider.reconcile
        self.provider.reconcile = lambda request: {
            **reconcile(request), "reason": marker, "state": marker,
            "source_revision": marker, "source_id": marker,
        }
        self.assertEqual(self.engine.reconcile(self.job_id)["state"], "ready")
        self.assert_not_persisted(marker)

    def test_unrecognized_publication_status_is_not_logged_verbatim(self):
        self.reach("publication_pending")
        marker = "INDEPENDENT_SYNTHETIC_PRIVATE_PUBLICATION"
        publication = self.provider.publication_status
        self.provider.publication_status = lambda listing_id: {
            **publication(listing_id), "state": marker, "reason": marker,
            "status": marker, "source_id": marker,
        }
        self.assertEqual(self.engine.reconcile(self.job_id)["state"], "publication_pending")
        self.assert_not_persisted(marker)

    def test_corrupt_previous_stage_blocks_before_reconciliation_provider_call(self):
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(self.job_id)
        with self.store.conn:
            self.store.conn.execute("DROP TRIGGER immutable_operation_request")
            self.store.conn.execute("UPDATE operations SET previous_state='archived'")
        reconciliations = []
        self.provider.reconcile = lambda request: reconciliations.append(request)
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(self.job_id)
        self.assertEqual(reconciliations, [])
        self.assertEqual(self.store.get_job(self.job_id)["state"], "needs_reconciliation")

    def test_standalone_command_requires_every_complete_capture_component(self):
        command = self.engine._request(self.store.get_job(self.job_id), "remove_portals")
        for missing in ("revision", "raw", "media_manifest", "media_count"):
            with self.subTest(missing=missing):
                snapshot = command.snapshot
                del snapshot[missing]
                snapshot_hash = digest(snapshot)
                changes = {
                    "snapshot_json": canonical(snapshot),
                    "snapshot_hash": snapshot_hash,
                    "approval_hash": digest({"snapshot_hash": snapshot_hash,
                        "address": command.approved_address,
                        "archive_reason": command.archive_reason}),
                }
                with self.assertRaises(MutationContractError):
                    replace(command, **changes)
                # Dispatch must independently revalidate a forged object too.
                forged = replace(command)
                for key, value in changes.items():
                    object.__setattr__(forged, key, value)
                with self.assertRaises(MutationContractError):
                    self.engine.adapter.remove_from_portals(forged)
        self.assertEqual(self.provider.calls, [])

    def test_standalone_command_rejects_inconsistent_media_manifest(self):
        command = self.engine._request(self.store.get_job(self.job_id), "remove_portals")
        changes = (
            lambda snapshot: snapshot.update(media_count=0),
            lambda snapshot: snapshot.update(media_count=True),
            lambda snapshot: snapshot.update(raw={}),
            lambda snapshot: snapshot["media_manifest"][0].update(size=True),
            lambda snapshot: snapshot["media_manifest"][0].update(sha256="unverified"),
            lambda snapshot: snapshot["media_manifest"][0].pop("local_path"),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                snapshot = command.snapshot
                change(snapshot)
                snapshot_hash = digest(snapshot)
                with self.assertRaises(MutationContractError):
                    replace(command, snapshot_json=canonical(snapshot), snapshot_hash=snapshot_hash,
                        approval_hash=digest({"snapshot_hash": snapshot_hash,
                            "address": command.approved_address,
                            "archive_reason": command.archive_reason}))
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
