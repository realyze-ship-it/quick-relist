"""Synthetic, offline tests. No real Hub account, private data or portal calls."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from quick_relist.mutations import MutationRequest

from quick_relist.core import (
    AdapterUnavailable, Engine, OperationBusy, SafetyBlocked, Store,
    UnavailableHubAdapter, ValidationError, VerificationUnavailable,
    REQUIRED_ADAPTER_QUALIFICATIONS,
)


class SyntheticHub:
    is_live_ready = True
    # Test-only assertions. This in-memory fixture is never a real connector.
    qualification = {key: True for key in REQUIRED_ADAPTER_QUALIFICATIONS}

    def __init__(self, media_path):
        self.media_path = media_path
        self.sources = {}
        self.listings = {}
        self.calls = []
        self.receipts = {}
        self.requests = {}
        self.not_applied = set()
        self.removed_sources = set()
        self.archived_sources = set()
        self.validation_requests = []
        self.uploads = {}
        self.validation_valid = True
        self.validation_decision_required = False
        self.before_execute = None
        self._lock = threading.RLock()
        self.portal_clear = True
        self.archive_verified = True
        self.live = False
        self.fail_after = None
        self.crash_after = None
        self.fail_before = None
        self.reconcile_override = None
        self.create_id = None
        self.account_identity = {"account_id": "synthetic-account", "agency_id": "synthetic-agency"}

    def authenticated_identity(self):
        return {**self.account_identity, "verified": True}

    def source(self, rxcu):
        if rxcu not in self.sources:
            self.sources[rxcu] = {
                "rxcu": rxcu, "source_id": "source-" + rxcu, "revision": "r1", "complete": True,
                "account_identity": deepcopy(self.account_identity),
                "required_address_fields": ["street_number", "street_name", "suburb", "city", "province", "postal_code"],
                "raw": {"vendorRecord": {"synthetic": True, "full": "original"}},
                "fields": {
                    "address": {"street_number": "10", "street_name": "Example Road", "suburb": "Test Suburb", "city": "Test City", "province": "Test Province", "postal_code": "0000"},
                    "price": 123456, "description": "Synthetic description", "beds": 3,
                    "features": ["fixture A", "fixture B"], "agent": {"name": "Example Agent"},
                    "mandate": {"type": "sole", "notes": "Synthetic only"},
                    "contacts": [{"name": "Example Person", "role": "owner"}],
                    "unknown_extension": {"must_preserve": [1, 2, 3]},
                },
                "media_manifest": [{"id": "asset-original", "kind": "photo", "position": 0,
                    "caption": "Example original", "sha256": hashlib.sha256(self.media_path.read_bytes()).hexdigest(),
                    "size": self.media_path.stat().st_size, "local_path": str(self.media_path)}],
                "media_count": 1, "portal_targets": ["example-portal-a", "example-portal-b"],
            }
        return self.sources[rxcu]

    def read_source(self, rxcu):
        return deepcopy(self.source(rxcu))

    def _bound(self, request):
        return {"operation_id": request.operation_id, "request_hash": request.fingerprint,
                "action": request.action, "authoritative": True, "evidence": "Synthetic authoritative ledger"}

    def validate_replacement(self, request):
        self.validation_requests.append(request)
        return {**self._bound(request), "non_mutating": True, "verified": True,
                "valid": self.validation_valid, "decision_required": self.validation_decision_required,
                "mandate_policy": "preserve_exact_source"}

    def _record(self, request, apply):
        # This lock models a server transaction. No real Hub call exists here.
        with self._lock:
            action, operation_id = request.action, request.operation_id
            self.calls.append((action, operation_id))
            if operation_id in self.requests and self.requests[operation_id] != request:
                raise AssertionError("Operation key changed its immutable request")
            self.requests[operation_id] = request
            if operation_id in self.receipts:
                raise AssertionError("A successful operation was blindly repeated")
            if self.fail_before == action:
                self.fail_before = None
                self.not_applied.add(operation_id)
                raise TimeoutError("Synthetic request was not applied")
            if self.before_execute:
                hook, self.before_execute = self.before_execute, None
                hook(request)
            source = self.source(request.source_rxcu)
            if (self.account_identity != request.account_identity
                    or source["account_identity"] != request.account_identity
                    or source["source_id"] != request.source_id
                    or source["revision"] != request.expected_source_revision):
                self.not_applied.add(operation_id)
                raise RuntimeError("Synthetic atomic source/account/revision conflict")
            if action != "remove_portals" and request.source_id not in self.removed_sources:
                self.not_applied.add(operation_id)
                raise RuntimeError("Synthetic atomic portal prerequisite conflict")
            if action in {"create", "publish"} and request.source_id not in self.archived_sources:
                self.not_applied.add(operation_id)
                raise RuntimeError("Synthetic atomic archive prerequisite conflict")
            if action == "publish":
                listing = self.listings[request.replacement_id]
                if (listing["revision"] != request.expected_replacement_revision or listing["state"] != "draft"
                        or listing["account_identity"] != request.account_identity
                        or listing["source_id"] != request.source_id
                        or listing["source_snapshot_hash"] != request.snapshot_hash):
                    self.not_applied.add(operation_id)
                    raise RuntimeError("Synthetic atomic replacement conflict")
            result = apply()
            result = {**self._bound(request), "status": "applied", "account_identity": request.account_identity,
                      "source_id": request.source_id, "previous_source_revision": request.expected_source_revision,
                      "source_revision": source["revision"], **result}
            self.receipts[operation_id] = deepcopy(result)
            self.not_applied.discard(operation_id)
            if self.fail_after == action:
                self.fail_after = None
                raise TimeoutError("Synthetic response lost after apply")
            if self.crash_after == action:
                self.crash_after = None
                raise SystemExit("Synthetic process death after apply")
            return result

    def remove_from_portals(self, request):
        def apply():
            self.removed_sources.add(request.source_id)
            self.source(request.source_rxcu)["revision"] = "r2"
            return {"accepted": True}
        return self._record(request, apply)

    def portals_removed(self, source_id):
        clear = self.portal_clear and source_id in self.removed_sources
        return {"source_id": source_id, "verified": clear, "all_removed": clear,
                "portals": {"example-portal-a": "removed", "example-portal-b": "removed"}, "evidence": "Synthetic verified portal reads"}

    def archive(self, request):
        def apply():
            self.reason = request.archive_reason
            self.archived_sources.add(request.source_id)
            self.source(request.source_rxcu)["revision"] = "r3"
            return {"archived": True, "archive_status": request.archive_status, "archive_reason": request.archive_reason}
        return self._record(request, apply)

    def archive_status(self, source_id):
        archived = self.archive_verified and source_id in self.archived_sources
        return {"source_id": source_id, "verified": archived, "archived": archived,
                "status": "cancelled", "reason": getattr(self, "reason", None), "evidence": "Synthetic archive record"}

    def create_listing(self, request):
        def apply():
            snapshot = request.snapshot
            uploads = []
            for asset in snapshot["media_manifest"]:
                content = Path(asset["local_path"]).read_bytes()
                if len(content) != asset["size"] or hashlib.sha256(content).hexdigest() != asset["sha256"]:
                    self.not_applied.add(request.operation_id)
                    raise RuntimeError("Synthetic media changed at consumption")
                uploads.append({"metadata": {key: value for key, value in asset.items() if key != "local_path"}, "content": content})
            new_id = self.create_id or "new-" + snapshot["rxcu"]
            self.uploads[new_id] = uploads
            self.listings[new_id] = {"listing_id": new_id, "fields": request.replacement_fields,
                "state": "draft", "revision": "draft1", "complete": True,
                "source_id": request.source_id, "source_snapshot_hash": request.snapshot_hash,
                "creation_operation_id": request.operation_id, "raw_source": snapshot["raw"],
                "account_identity": request.account_identity,
                "media_manifest": snapshot["media_manifest"], "portal_targets": list(request.portal_targets)}
            return {"listing_id": new_id, "listing_revision": "draft1", "listing_state": "draft"}
        return self._record(request, apply)

    def read_listing(self, listing_id):
        return deepcopy(self.listings[listing_id])

    def publish(self, request):
        def apply():
            self.listings[request.replacement_id]["revision"] = "published1"
            self.listings[request.replacement_id]["state"] = "pending"
            return {"listing_id": request.replacement_id, "listing_revision": "published1",
                    "previous_listing_revision": request.expected_replacement_revision, "accepted": True}
        return self._record(request, apply)

    def publication_status(self, listing_id):
        state = "live" if self.live else "pending"
        return {"listing_id": listing_id, "state": state, "verified": self.live,
                "portals": {"example-portal-a": state, "example-portal-b": state}, "evidence": "Synthetic publication observations"}

    def reconcile_action(self, request):
        if self.reconcile_override is not None:
            return deepcopy(self.reconcile_override)
        status = "applied" if request.operation_id in self.receipts else "not_applied" if request.operation_id in self.not_applied else "unknown"
        return {**self._bound(request), "status": status, "terminal": status == "not_applied",
                "result": deepcopy(self.receipts.get(request.operation_id, {}))}

    # Provider API used through the production GatedHubAdapter in adapter tests.
    def execute(self, request):
        methods = {"remove_portals": self.remove_from_portals, "archive": self.archive,
                   "create": self.create_listing, "publish": self.publish}
        return methods[request.action](request)

    def reconcile(self, request):
        return self.reconcile_action(request)


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.media = self.folder / "synthetic-original.bin"
        self.media.write_bytes(b"synthetic original media bytes")
        self.path = self.folder / "jobs.sqlite3"
        self.store = Store(self.path)
        self.hub = SyntheticHub(self.media)
        self.engine = Engine(self.store, self.hub)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def job(self, references=None):
        batch = self.engine.new_batch(references or ["RXCU100001"])
        return self.store.list_jobs(batch)[0]["id"]

    def prepared(self):
        job_id = self.job()
        job = self.engine.snapshot(job_id)
        address = deepcopy(job["original_address"])
        address["street_number"] = "12"
        self.engine.approve_address(job_id, address, "mandate_expired")
        return job_id

    def reach(self, target):
        job_id = self.prepared()
        for _ in range(8):
            if self.store.get_job(job_id)["state"] == target:
                return job_id
            self.engine.advance(job_id)
        self.fail("Target stage was not reached")

    def test_offline_default_is_fail_closed(self):
        job_id = self.job()
        offline = Engine(self.store)
        self.assertIsInstance(offline.adapter, UnavailableHubAdapter)
        with self.assertRaises(AdapterUnavailable):
            offline.snapshot(job_id)
        job = self.store.get_job(job_id)
        self.assertEqual(job["state"], "queued")
        self.assertIsNone(job["snapshot"])
        self.assertEqual(self.hub.calls, [])

    def test_normalization_duplicates_and_global_duplicate_history(self):
        job_id = self.job([" rxcu100001 "])
        self.assertEqual(self.store.get_job(job_id)["rxcu"], "RXCU100001")
        with self.assertRaises(ValidationError):
            self.engine.new_batch(["RXCU100002", "rxcu100002"])
        with self.assertRaises(SafetyBlocked):
            self.engine.new_batch(["RXCU100001"])
        with self.assertRaises(ValidationError):
            self.engine.new_batch(["bad"])

    def test_original_snapshot_and_journal_are_immutable(self):
        job_id = self.prepared()
        job = self.store.get_job(job_id)
        self.assertEqual(job["snapshot"]["fields"]["address"]["street_number"], "10")
        self.assertEqual(job["corrected_address"]["street_number"], "12")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.conn:
                self.store.conn.execute("UPDATE jobs SET snapshot_json='{}' WHERE id=?", (job_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.conn:
                self.store.conn.execute("DELETE FROM journal WHERE job_id=?", (job_id,))
        self.assertIn("raw", self.store.get_job(job_id)["snapshot"])

    def test_review_requires_capture_and_complete_address_reason(self):
        job_id = self.job()
        with self.assertRaises(SafetyBlocked):
            self.engine.approve_address(job_id, {"street_number": "12"}, "mandate_expired")
        job = self.engine.snapshot(job_id)
        with self.assertRaises(ValidationError):
            self.engine.approve_address(job_id, {"street_number": "12"}, "mandate_expired")
        with self.assertRaises(ValidationError):
            self.engine.approve_address(job_id, job["original_address"], "sold")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.hub.calls, [])

    def test_incomplete_capture_is_never_approved(self):
        job_id = self.job()
        self.hub.source("RXCU100001")["complete"] = False
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(job_id)
        self.assertIsNone(self.store.get_job(job_id)["snapshot"])

    def test_missing_or_corrupt_media_prevents_destructive_work(self):
        job_id = self.prepared()
        self.media.write_bytes(b"replaced")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.hub.calls, [])
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")

    def test_source_identity_content_and_revision_changes_stop(self):
        for key, changed in (("source_id", "different"), ("revision", "r2"), ("fields", {"address": {"street": "changed"}})):
            with self.subTest(key=key):
                rxcu = "RXCU" + str(200000 + len(self.store.list_jobs()))
                batch = self.engine.new_batch([rxcu])
                job_id = self.store.list_jobs(batch)[0]["id"]
                original = self.engine.snapshot(job_id)
                self.engine.approve_address(job_id, original["original_address"], "mandate_expired")
                self.hub.source(rxcu)[key] = changed
                # Separate synthetic stores ensure each is queue head.
                if len(self.store.list_jobs()) > 1:
                    with self.store.conn:
                        self.store.conn.execute("UPDATE jobs SET state='live' WHERE id<>?", (job_id,))
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(self.hub.calls, [])

    def test_portal_removal_verified_before_archive(self):
        job_id = self.prepared()
        self.hub.portal_clear = False
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual([x[0] for x in self.hub.calls], ["remove_portals"])
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.hub.portal_clear = True
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")
        self.assertEqual(self.engine.advance(job_id)["state"], "archived")

    def test_portals_are_rechecked_before_archiving(self):
        job_id = self.reach("portals_removed")
        self.hub.portal_clear = False
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual([x[0] for x in self.hub.calls], ["remove_portals"])

    def test_archive_must_be_independently_verified(self):
        job_id = self.reach("portals_removed")
        self.hub.archive_verified = False
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertNotIn("create", [x[0] for x in self.hub.calls])

    def test_address_approval_cannot_change_after_destructive_step(self):
        job_id = self.reach("portals_removed")
        job = self.store.get_job(job_id)
        with self.assertRaises(SafetyBlocked):
            self.engine.approve_address(job_id, job["original_address"], "mandate_expired")

    def test_fidelity_includes_unknown_fields_media_and_portals(self):
        job_id = self.reach("created")
        job = self.store.get_job(job_id)
        self.assertTrue(job["new_listing_id"])
        self.hub.listings[job["new_listing_id"]]["fields"]["unknown_extension"] = {}
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [x[0] for x in self.hub.calls])

    def test_fidelity_rechecked_immediately_before_publication(self):
        job_id = self.reach("fidelity_verified")
        listing_id = self.store.get_job(job_id)["new_listing_id"]
        self.hub.listings[listing_id]["media_manifest"][0]["caption"] = "drift"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [x[0] for x in self.hub.calls])

    def test_new_listing_must_be_draft_not_auto_published(self):
        job_id = self.reach("created")
        self.hub.listings[self.store.get_job(job_id)["new_listing_id"]]["state"] = "live"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)

    def test_publication_pending_is_not_live(self):
        job_id = self.reach("publication_pending")
        self.assertEqual(self.engine.advance(job_id)["state"], "publication_pending")
        self.assertEqual(len([x for x in self.hub.calls if x[0] == "publish"]), 1)
        self.hub.live = True
        self.assertEqual(self.engine.reconcile(job_id)["state"], "live")
        self.engine.advance(job_id)
        self.assertEqual(len([x for x in self.hub.calls if x[0] == "publish"]), 1)

    def test_multiple_rxcus_are_sequential_even_between_batches(self):
        first = self.prepared()
        batch = self.engine.new_batch(["RXCU100002", "RXCU100003"])
        second = self.store.list_jobs(batch)[0]["id"]
        capture = self.engine.snapshot(second)
        self.engine.approve_address(second, capture["original_address"], "mandate_expired")
        with self.assertRaises(OperationBusy):
            self.engine.advance(second)
        self.assertEqual(self.store.get_job(second)["state"], "ready")
        self.hub.live = True
        while self.store.get_job(first)["state"] != "live":
            self.engine.advance(first)
        self.assertEqual(self.engine.advance(second)["state"], "portals_removed")

    def test_response_lost_after_create_is_reconciled_without_duplicate(self):
        job_id = self.reach("archived")
        self.hub.fail_after = "create"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertIsNone(self.store.get_job(job_id)["new_listing_id"])
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        job = self.engine.reconcile(job_id)
        self.assertEqual(job["state"], "created")
        self.assertEqual(job["new_listing_id"], "new-RXCU100001")
        self.assertEqual(len([x for x in self.hub.calls if x[0] == "create"]), 1)

    def test_process_death_preserves_intention_and_requires_reconciliation(self):
        job_id = self.prepared()
        self.hub.crash_after = "remove_portals"
        with self.assertRaises(SystemExit):
            self.engine.advance(job_id)
        self.store.close()
        self.store = Store(self.path)
        self.engine = Engine(self.store, self.hub)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")
        self.assertEqual(len(self.hub.calls), 1)
        events = self.store.journal(job_id)
        self.assertIn("intended", [e["phase"] for e in events])
        self.assertIn("reconciled_applied", [e["phase"] for e in events])

    def test_unknown_reconciliation_never_retries(self):
        job_id = self.prepared()
        self.hub.fail_after = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.hub.reconcile_override = {"status": "unknown", "evidence": "Synthetic uncertain result"}
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertEqual(len(self.hub.calls), 1)

    def test_confirmed_not_applied_retry_reuses_operation_id(self):
        job_id = self.prepared()
        self.hub.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.engine.reconcile(job_id)["state"], "ready")
        self.engine.advance(job_id)
        self.assertEqual(self.hub.calls[0], self.hub.calls[1])

    def test_original_id_cannot_be_used_as_recreated_identity(self):
        job_id = self.reach("archived")
        self.hub.create_id = self.store.get_job(job_id)["source_id"]
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertIsNone(self.store.get_job(job_id)["new_listing_id"])
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")

    def test_storage_can_be_used_from_worker_thread(self):
        job_id = self.job()
        results = []
        def worker():
            try:
                results.append(self.engine.snapshot(job_id)["state"])
            except Exception as exc:
                results.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(results, ["awaiting_address"])

    def test_parallel_instances_cannot_run_external_actions(self):
        second_store = Store(self.path)
        try:
            with self.store.operation_lock():
                with self.assertRaises(OperationBusy):
                    with second_store.operation_lock():
                        self.fail("Second instance obtained the external-action lock")
        finally:
            second_store.close()

    def test_only_cancelled_with_mandate_expired_is_supported(self):
        job_id = self.job()
        job = self.engine.snapshot(job_id)
        with self.assertRaises(ValidationError):
            self.engine.approve_address(job_id, job["original_address"], "cancelled")
        self.assertFalse(self.store.get_job(job_id)["approved"])

    def test_authenticated_account_drift_blocks_before_any_write(self):
        job_id = self.prepared()
        self.hub.account_identity["account_id"] = "other-synthetic-account"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.hub.calls, [])
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")

    def test_capture_account_must_match_authenticated_account(self):
        job_id = self.job()
        self.hub.source("RXCU100001")["account_identity"]["agency_id"] = "different-agency"
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(job_id)
        self.assertIsNone(self.store.get_job(job_id)["snapshot"])

    def test_archive_reason_alone_does_not_prove_cancelled_status(self):
        job_id = self.reach("portals_removed")
        original = self.hub.archive_status
        self.hub.archive_status = lambda source_id: {**original(source_id), "status": "active"}
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")

    def test_adapter_exception_text_is_never_exposed_or_persisted(self):
        job_id = self.prepared()
        marker = "SYNTHETIC_PRIVATE_TOKEN_DO_NOT_LOG"
        def fail(request):
            raise RuntimeError(marker)
        self.hub.remove_from_portals = fail
        with self.assertRaises(SafetyBlocked) as caught:
            self.engine.advance(job_id)
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, str(self.store.get_job(job_id)))
        self.assertNotIn(marker, str(self.store.journal(job_id)))
        self.assertNotIn(marker.encode(), self.path.read_bytes())

    def test_arbitrary_adapter_evidence_is_hashed_not_logged(self):
        job_id = self.reach("fidelity_verified")
        marker = "SYNTHETIC_PRIVATE_PROVIDER_PAYLOAD"
        old_publish = self.hub.publish
        def publish(request):
            receipt = old_publish(request)
            return {**receipt, "evidence": marker, "error": marker, "unknown_vendor_payload": marker}
        self.hub.publish = publish
        self.engine.advance(job_id)
        self.assertNotIn(marker, str(self.store.journal(job_id)))
        self.assertNotIn(marker.encode(), self.path.read_bytes())
        event = self.store.journal(job_id)[-1]
        self.assertTrue(event["details"]["evidence_present"])
        self.assertEqual(len(event["details"]["evidence_hash"]), 64)

    def test_intention_is_durable_before_external_action(self):
        job_id = self.prepared()
        original = self.hub.remove_from_portals
        def remove(request):
            operation_id = request.operation_id
            other = sqlite3.connect(self.path)
            try:
                self.assertEqual(other.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0], "needs_reconciliation")
                self.assertEqual(other.execute("SELECT phase FROM journal WHERE operation_id=?", (operation_id,)).fetchone()[0], "intended")
            finally:
                other.close()
            return original(request)
        self.hub.remove_from_portals = remove
        self.engine.advance(job_id)

    def test_replacement_account_identity_is_required_for_fidelity(self):
        job_id = self.reach("created")
        listing = self.hub.listings[self.store.get_job(job_id)["new_listing_id"]]
        del listing["account_identity"]
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [action for action, _ in self.hub.calls])
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")

    def test_replacement_account_and_agency_must_match_before_publish(self):
        job_id = self.reach("fidelity_verified")
        listing = self.hub.listings[self.store.get_job(job_id)["new_listing_id"]]
        listing["account_identity"] = {"account_id": "other-account", "agency_id": "other-agency"}
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [action for action, _ in self.hub.calls])
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")

    def test_replacement_agency_drift_alone_blocks_publication(self):
        job_id = self.reach("fidelity_verified")
        listing = self.hub.listings[self.store.get_job(job_id)["new_listing_id"]]
        listing["account_identity"]["agency_id"] = "other-agency"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [action for action, _ in self.hub.calls])

    def test_existing_replacement_cannot_be_captured_as_another_source(self):
        first_id = self.reach("created")
        replacement_id = self.store.get_job(first_id)["new_listing_id"]
        second_id = self.job(["RXCU100002"])
        self.hub.source("RXCU100002")["source_id"] = replacement_id
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(second_id)
        self.assertIsNone(self.store.get_job(second_id)["snapshot"])
        self.assertIsNone(self.store.get_job(second_id)["source_id"])

    def test_existing_original_cannot_be_captured_under_another_rxcu(self):
        first_id = self.prepared()
        original_id = self.store.get_job(first_id)["source_id"]
        second_id = self.job(["RXCU100002"])
        self.hub.source("RXCU100002")["source_id"] = original_id
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(second_id)
        self.assertIsNone(self.store.get_job(second_id)["snapshot"])

    def test_prewrite_source_read_timeouts_remain_retryable_at_every_stage(self):
        job_id = self.prepared()
        stages = ["ready", "portals_removed", "archived", "created", "fidelity_verified", "publication_pending"]
        original = self.hub.read_source
        def timeout(rxcu):
            raise TimeoutError("Synthetic source read timeout")
        for state in stages:
            with self.subTest(state=state):
                self.assertEqual(self.store.get_job(job_id)["state"], state)
                count = len(self.hub.calls)
                self.hub.read_source = timeout
                with self.assertRaises(VerificationUnavailable):
                    self.engine.advance(job_id)
                self.assertEqual(self.store.get_job(job_id)["state"], state)
                self.assertEqual(len(self.hub.calls), count)
                self.hub.read_source = original
                self.engine.advance(job_id)
        self.hub.live = True
        self.assertEqual(self.engine.advance(job_id)["state"], "live")
        self.assertEqual([action for action, _ in self.hub.calls], ["remove_portals", "archive", "create", "publish"])

    def test_fidelity_read_timeout_can_be_retried_without_write(self):
        job_id = self.reach("created")
        original = self.hub.read_listing
        def timeout(listing_id):
            raise TimeoutError("Synthetic draft read timeout")
        self.hub.read_listing = timeout
        count = len(self.hub.calls)
        with self.assertRaises(VerificationUnavailable):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "created")
        self.assertEqual(len(self.hub.calls), count)
        self.hub.read_listing = original
        self.assertEqual(self.engine.advance(job_id)["state"], "fidelity_verified")

    def test_read_timeout_after_a_write_still_requires_reconciliation(self):
        job_id = self.prepared()
        original = self.hub.portals_removed
        def timeout(source_id):
            raise TimeoutError("Synthetic post-write verification timeout")
        self.hub.portals_removed = timeout
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(len(self.hub.calls), 1)
        self.hub.portals_removed = original
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")
        self.assertEqual(len(self.hub.calls), 1)

    def test_backward_clock_cannot_reorder_or_preempt_archived_job(self):
        first = self.reach("archived")
        first_batch = self.store.get_job(first)["batch_id"]
        with patch("quick_relist.core._now", return_value="2000-01-01T00:00:00+00:00"):
            second = self.job(["RXCU100002"])
        second_batch = self.store.get_job(second)["batch_id"]
        capture = self.engine.snapshot(second)
        self.engine.approve_address(second, capture["original_address"], "mandate_expired")
        self.assertEqual([item["id"] for item in self.store.list_jobs()], [first, second])
        self.assertEqual([item["id"] for item in self.store.list_batches()], [first_batch, second_batch])
        with self.assertRaises(OperationBusy):
            self.engine.advance(second)
        self.assertEqual(self.store.get_job(second)["state"], "ready")
        self.store.close()
        self.store = Store(self.path)
        self.engine = Engine(self.store, self.hub)
        self.assertEqual([item["id"] for item in self.store.list_jobs()], [first, second])
        self.assertEqual(self.engine.advance(first)["state"], "created")

    def test_equal_timestamps_keep_durable_insertion_order(self):
        with patch("quick_relist.core._now", return_value="2000-01-01T00:00:00+00:00"):
            jobs = [self.job([f"RXCU{100000 + index}"]) for index in range(5)]
        self.assertEqual([item["id"] for item in self.store.list_jobs()], jobs)

    def test_required_address_values_cannot_be_null_empty_or_blank(self):
        job_id = self.job()
        job = self.engine.snapshot(job_id)
        for value in (None, "", "  ", 12):
            with self.subTest(value=value):
                address = deepcopy(job["original_address"])
                address["street_number"] = value
                with self.assertRaises(ValidationError):
                    self.engine.approve_address(job_id, address, "mandate_expired")
                self.assertFalse(self.store.get_job(job_id)["approved"])

    def test_connector_without_atomic_revision_qualification_stays_offline(self):
        job_id = self.job()
        self.hub.qualification = dict(self.hub.qualification)
        self.hub.qualification["atomic_revision_preconditions"] = False
        with self.assertRaises(AdapterUnavailable):
            self.engine.snapshot(job_id)
        self.assertIsNone(self.store.get_job(job_id)["snapshot"])
        self.assertEqual(self.hub.calls, [])

    def test_cancel_queued_job_unblocks_queue_and_preserves_history(self):
        first = self.job()
        second = self.job(["RXCU100002"])
        self.assertEqual(Engine(self.store).cancel(first)["state"], "cancelled")
        events = self.store.journal(first)
        self.assertEqual(events[-1]["phase"], "cancelled")
        self.assertEqual(len(events), 2)
        captured = self.engine.snapshot(second)
        self.engine.approve_address(second, captured["original_address"], "mandate_expired")
        self.assertEqual(self.engine.advance(second)["state"], "portals_removed")
        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_cancelled_job_cannot_be_restarted_but_reference_can_be_reentered(self):
        first = self.prepared()
        original_snapshot = self.store.get_job(first)["snapshot"]
        self.assertEqual(self.engine.cancel_job(first)["state"], "cancelled")
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(first)
        with self.assertRaises(SafetyBlocked):
            self.engine.approve_address(first, original_snapshot["fields"]["address"], "mandate_expired")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(first)
        second = self.job()
        self.assertNotEqual(first, second)
        self.assertEqual(self.engine.snapshot(second)["state"], "awaiting_address")
        self.assertEqual(self.store.get_job(first)["snapshot"], original_snapshot)

    def test_cancel_after_capture_keeps_immutable_original(self):
        first = self.job()
        original = self.engine.snapshot(first)["snapshot"]
        self.assertEqual(self.engine.cancel(first)["snapshot"], original)
        self.assertEqual(self.store.get_job(first)["state"], "cancelled")

    def test_cancellation_is_rejected_after_any_write_intention(self):
        job_id = self.prepared()
        self.hub.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        with self.assertRaises(SafetyBlocked):
            self.engine.cancel(job_id)
        self.assertEqual(self.engine.reconcile(job_id)["state"], "ready")
        with self.assertRaises(SafetyBlocked):
            self.engine.cancel(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "ready")
        with self.assertRaises(SafetyBlocked):
            self.engine.new_batch(["RXCU100001"])

    def test_cancellation_of_an_already_cancelled_job_is_idempotent(self):
        job_id = self.job()
        self.engine.cancel(job_id)
        count = len(self.store.journal(job_id))
        self.engine.cancel(job_id)
        self.assertEqual(len(self.store.journal(job_id)), count)

    def test_prewrite_blocked_job_can_be_cancelled_without_losing_history(self):
        first = self.prepared()
        original = self.store.get_job(first)["snapshot"]
        self.hub.source("RXCU100001")["fields"]["price"] += 1
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(first)
        self.assertEqual(self.store.get_job(first)["state"], "blocked")
        self.assertEqual(self.hub.calls, [])
        self.assertEqual(self.engine.cancel_job(first)["state"], "cancelled")
        self.assertEqual(self.store.get_job(first)["snapshot"], original)
        replacement = self.job()
        capture = self.engine.snapshot(replacement)
        self.engine.approve_address(replacement, capture["original_address"], "mandate_expired")
        self.assertEqual(self.engine.advance(replacement)["state"], "portals_removed")
        self.assertEqual(self.store.journal(first)[-1]["phase"], "cancelled")

    def test_postwrite_blocked_job_still_cannot_be_cancelled(self):
        job_id = self.reach("portals_removed")
        self.hub.source("RXCU100001")["fields"]["price"] += 1
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")
        with self.assertRaises(SafetyBlocked):
            self.engine.cancel_job(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "blocked")
        self.assertNotIn("cancelled", [event["phase"] for event in self.store.journal(job_id)])


if __name__ == "__main__":
    unittest.main()


def test_hyphenated_references_normalize_consistently():
    from quick_relist.core import normalize_rxcu
    assert normalize_rxcu(" rxcu-12345 ") == "RXCU12345"
    assert normalize_rxcu("RXCU12345") == "RXCU12345"


def test_rxcu_rejects_non_ascii_digits():
    from quick_relist.core import normalize_rxcu, ValidationError
    import pytest
    with pytest.raises(ValidationError): normalize_rxcu("RXCU-١٢٣")
