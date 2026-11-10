"""Exercise the real gated adapter with an offline, atomic synthetic provider."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quick_relist.core import AdapterUnavailable, Engine, SafetyBlocked, Store
from quick_relist.mutations import GatedHubAdapter, MutationContractError, MutationRequest, canonical, digest
from test_core import SyntheticHub


class MutationAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.media = self.folder / "synthetic-photo.bin"
        self.media.write_bytes(b"synthetic full size photo")
        self.store = Store(self.folder / "jobs.sqlite3")
        self.provider = SyntheticHub(self.media)
        self.adapter = GatedHubAdapter(self.provider)
        self.engine = Engine(self.store, self.adapter)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prepared(self, rxcu="RXCU200001", transaction="sale"):
        source = self.provider.source(rxcu)
        source["fields"]["transaction_type"] = transaction
        source["fields"]["mandate"] = {"type": "sole", "signed_date": "2024-01-01",
            "expiry_date": "2024-06-30", "notes": "Synthetic dates retained literally"}
        source["fields"]["rooms"] = {
            "bedrooms": [{"description": "Bedroom one", "options": ["built_in_cupboards", "balcony"]},
                         {"description": "Bedroom two", "options": []}],
            "bathrooms": [{"description": "Bathroom", "full": True, "en_suite": False}],
            "kitchens": [{"description": "Kitchen", "options": {"gas": True, "island": False}}],
            "parking": [{"kind": "garage", "automated": True}], "lounges": [{"description": "Lounge"}],
        }
        source["fields"]["commissions"] = {"vat_inclusive": False, "percentage": "4.5"}
        source["fields"]["rental"] = {"deposit": "25000", "available_date": "2024-02-01"} if transaction == "rental" else None
        source["media_manifest"][0].update(cover=True, tags=["exterior"], position=0)
        plan = self.folder / (rxcu + "-floorplan.bin")
        plan.write_bytes(b"synthetic original floorplan")
        source["media_manifest"].append({"id": rxcu + "-plan", "kind": "floorplan", "position": 1,
            "caption": "Original floor plan", "cover": False, "tags": ["plan"], "sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
            "size": plan.stat().st_size, "local_path": str(plan)})
        source["media_count"] = 2
        batch = self.engine.new_batch([rxcu])
        job_id = self.store.list_jobs(batch)[0]["id"]
        capture = self.engine.snapshot(job_id)
        address = deepcopy(capture["original_address"])
        address["street_number"] = "99"
        self.engine.approve_address(job_id, address, "mandate_expired")
        return job_id

    def reach(self, job_id, target):
        for _ in range(8):
            if self.store.get_job(job_id)["state"] == target:
                return self.store.get_job(job_id)
            self.engine.advance(job_id)
        self.fail("Target stage was not reached")

    def request(self, job_id, action):
        row = self.store.conn.execute("SELECT payload_json FROM operations WHERE job_id=? AND action=?", (job_id, action)).fetchone()
        return MutationRequest.from_dict(json.loads(row[0]))

    def test_sale_and_rental_full_copy_through_production_adapter(self):
        for index, transaction in enumerate(("sale", "rental")):
            with self.subTest(transaction=transaction):
                job_id = self.prepared("RXCU" + str(200001 + index), transaction)
                job = self.reach(job_id, "publication_pending")
                replacement = self.provider.listings[job["new_listing_id"]]
                expected = deepcopy(job["snapshot"]["fields"])
                expected["address"] = job["corrected_address"]
                self.assertEqual(replacement["fields"], expected)
                self.assertEqual(replacement["raw_source"], job["snapshot"]["raw"])
                self.assertEqual(replacement["media_manifest"], job["snapshot"]["media_manifest"])
                self.assertEqual(replacement["portal_targets"], job["snapshot"]["portal_targets"])
                uploads = self.provider.uploads[job["new_listing_id"]]
                for upload, asset in zip(uploads, job["snapshot"]["media_manifest"]):
                    self.assertNotIn("local_path", upload["metadata"])
                    self.assertEqual(upload["metadata"], {key: value for key, value in asset.items() if key != "local_path"})
                    self.assertEqual(upload["content"], Path(asset["local_path"]).read_bytes())
                create = self.request(job_id, "create")
                self.assertEqual(create.snapshot, job["snapshot"])
                self.assertEqual(create.replacement_fields["mandate"], job["snapshot"]["fields"]["mandate"])
                self.assertEqual(create.expected_source_revision, "r3")
                self.assertEqual(create.mandate_policy, "preserve_exact_source")
                self.assertEqual(self.request(job_id, "archive").archive_status, "cancelled")
                self.assertEqual(self.request(job_id, "archive").archive_reason, "mandate_expired")
                publish = self.request(job_id, "publish")
                self.assertEqual(publish.expected_replacement_revision, "draft1")
                self.assertEqual(self.store.get_job(job_id)["replacement_revision"], "published1")
                self.assertEqual(self.engine.advance(job_id)["state"], "publication_pending")
                self.provider.live = True
                self.assertEqual(self.engine.reconcile(job_id)["state"], "live")
                self.provider.live = False
        self.assertEqual([action for action, _ in self.provider.calls], ["remove_portals", "archive", "create", "publish"] * 2)

    def test_default_adapter_lists_concrete_missing_capabilities(self):
        default = GatedHubAdapter()
        self.assertFalse(default.is_live_ready)
        for capability in ("atomic_revision_preconditions", "bound_operation_receipts", "non_mutating_replacement_validation", "provider.execute"):
            self.assertIn(capability, default.missing_capabilities)
        batch = self.engine.new_batch(["RXCU999999"])
        job_id = self.store.list_jobs(batch)[0]["id"]
        with self.assertRaisesRegex(AdapterUnavailable, "atomic_revision_preconditions"):
            Engine(self.store).snapshot(job_id)
        self.assertEqual(self.provider.calls, [])

    def test_missing_provider_method_cannot_be_enabled_by_flags(self):
        self.provider.execute = None
        self.assertFalse(self.adapter.is_live_ready)
        self.assertIn("provider.execute", self.adapter.missing_capabilities)

    def test_request_is_deeply_immutable_and_canonically_round_trips(self):
        job_id = self.prepared()
        self.engine.advance(job_id)
        request = self.request(job_id, "remove_portals")
        with self.assertRaises(FrozenInstanceError):
            request.source_id = "different"
        with self.assertRaises(TypeError):
            request.portal_targets[0] = "different"
        snapshot = request.snapshot
        snapshot["fields"]["mandate"]["expiry_date"] = "2099-01-01"
        address = request.approved_address
        address["street_number"] = "changed"
        self.assertEqual(request.snapshot["fields"]["mandate"]["expiry_date"], "2024-06-30")
        self.assertEqual(request.approved_address["street_number"], "99")
        self.assertEqual(request, MutationRequest.from_dict(request.to_dict()))
        self.assertEqual(request.fingerprint, MutationRequest.from_dict(request.to_dict()).fingerprint)
        with self.assertRaises(MutationContractError):
            replace(request, archive_status="sold")
        with self.assertRaises(MutationContractError):
            replace(request, mandate_policy="renew")

    def test_standalone_command_requires_complete_original_capture_and_media_schema(self):
        job_id = self.prepared()
        job = self.store.get_job(job_id)
        command = self.engine._build_request(job, "remove_portals", "synthetic-standalone-command")
        corruptions = [lambda snapshot, key=key: snapshot.pop(key) for key in ("revision", "raw", "media_manifest", "media_count")]
        corruptions += [
            lambda snapshot: snapshot.update(raw={}),
            lambda snapshot: snapshot.update(media_count=True),
            lambda snapshot: snapshot.update(required_address_fields=["street_number", "street_number"]),
            lambda snapshot: snapshot["media_manifest"][0].update(size=True),
            lambda snapshot: snapshot["media_manifest"][0].update(sha256="unverified"),
            lambda snapshot: snapshot["media_manifest"][0].pop("local_path"),
            lambda snapshot: snapshot["media_manifest"][1].update(id=snapshot["media_manifest"][0]["id"]),
        ]
        for index, corrupt in enumerate(corruptions):
            with self.subTest(index=index):
                snapshot = command.snapshot
                corrupt(snapshot)
                snapshot_hash = digest(snapshot)
                approval_hash = digest({"snapshot_hash": snapshot_hash, "address": command.approved_address,
                                        "archive_reason": command.archive_reason})
                with self.assertRaises(MutationContractError):
                    replace(command, snapshot_json=canonical(snapshot), snapshot_hash=snapshot_hash, approval_hash=approval_hash)
        self.assertEqual(self.provider.calls, [])

    def test_every_dispatch_revalidates_a_forged_or_tampered_command(self):
        job_id = self.prepared()
        self.engine.advance(job_id)
        command = self.request(job_id, "remove_portals")
        object.__setattr__(command, "archive_reason", "sold")
        count = len(self.provider.calls)
        for method in (self.adapter.remove_from_portals, self.adapter.validate_replacement, self.adapter.reconcile_action):
            with self.assertRaises(MutationContractError):
                method(command)
        self.assertEqual(len(self.provider.calls), count)

    def test_replacement_validation_precedes_any_intention_and_uses_exact_command(self):
        job_id = self.prepared()
        original = self.provider.validate_replacement
        def validate(request):
            self.assertEqual(self.store.conn.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
            self.assertEqual(self.provider.calls, [])
            return original(request)
        self.provider.validate_replacement = validate
        self.engine.advance(job_id)
        self.assertEqual(self.provider.validation_requests[0], self.request(job_id, "remove_portals"))

    def test_invalid_or_renewal_required_plan_blocks_before_removal(self):
        for valid, decision in ((False, False), (False, True), (True, True)):
            with self.subTest(valid=valid, decision=decision):
                job_id = self.prepared("RXCU" + str(210000 + len(self.store.list_jobs())))
                self.provider.validation_valid = valid
                self.provider.validation_decision_required = decision
                with self.assertRaises(SafetyBlocked) as caught:
                    self.engine.advance(job_id)
                self.assertIn("business decision" if decision else "not valid", str(caught.exception))
                self.assertEqual(self.provider.calls, [])
                self.assertEqual(self.store.conn.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
                self.assertEqual(self.store.get_job(job_id)["snapshot"]["fields"]["mandate"]["expiry_date"], "2024-06-30")
                self.engine.cancel(job_id)

    def test_unverified_or_unbound_replacement_validation_blocks_before_write(self):
        original = self.provider.validate_replacement
        changes = ({"non_mutating": False}, {"verified": False}, {"valid": None},
                   {"mandate_policy": "renew"}, {"request_hash": "wrong"}, {"authoritative": False})
        for change in changes:
            with self.subTest(change=change):
                job_id = self.prepared("RXCU" + str(220000 + len(self.store.list_jobs())))
                self.provider.validate_replacement = lambda request: {**original(request), **change}
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(self.provider.calls, [])
                self.assertEqual(self.store.conn.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
                self.engine.cancel(job_id)

    def test_media_change_during_preflight_blocks_before_intention(self):
        job_id = self.prepared()
        original = self.provider.validate_replacement
        def validate(request):
            receipt = original(request)
            self.media.write_bytes(b"changed after initial media verification")
            return receipt
        self.provider.validate_replacement = validate
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM operations").fetchone()[0], 0)

    def test_media_change_at_upload_consumption_never_creates_replacement(self):
        job_id = self.prepared()
        self.reach(job_id, "archived")
        self.provider.before_execute = lambda request: self.media.write_bytes(b"changed immediately before upload")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.provider.listings, {})
        self.assertEqual(self.provider.uploads, {})

    def test_atomic_source_revision_conflict_is_not_replaced_with_fresh_revision(self):
        job_id = self.prepared()
        self.provider.before_execute = lambda request: self.provider.source(request.source_rxcu).update(revision="concurrent-change")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        request = self.request(job_id, "remove_portals")
        self.assertEqual(request.expected_source_revision, "r1")
        self.assertEqual(self.provider.removed_sources, set())
        self.assertEqual(self.provider.receipts, {})
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertEqual(len(self.provider.calls), 1)

    def test_atomic_replacement_revision_conflict_never_publishes(self):
        job_id = self.prepared()
        job = self.reach(job_id, "fidelity_verified")
        self.provider.before_execute = lambda request: self.provider.listings[request.replacement_id].update(revision="concurrent-change")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        request = self.request(job_id, "publish")
        self.assertEqual(request.expected_replacement_revision, "draft1")
        self.assertEqual(self.provider.listings[job["new_listing_id"]]["state"], "draft")
        self.assertNotIn(request.operation_id, self.provider.receipts)
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(job_id)

    def test_account_drift_between_preflight_and_mutation_is_atomic_conflict(self):
        job_id = self.prepared()
        self.provider.before_execute = lambda request: self.provider.account_identity.update(agency_id="different")
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.provider.removed_sources, set())
        self.assertEqual(self.provider.receipts, {})

    def test_post_write_read_revision_must_equal_receipt_not_arbitrary_fresh_revision(self):
        job_id = self.prepared()
        original = self.provider.execute
        def execute(request):
            receipt = original(request)
            self.provider.source(request.source_rxcu)["revision"] = "outside-write-after-success"
            return receipt
        self.provider.execute = execute
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["expected_revision"], "r1")
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(job_id)
        self.assertEqual(len(self.provider.calls), 1)

    def test_loss_after_every_write_recovers_without_repeating_request(self):
        job_id = self.prepared()
        stages = (("remove_portals", "portals_removed"), ("archive", "archived"), ("create", "created"), ("publish", "publication_pending"))
        for action, next_state in stages:
            with self.subTest(action=action):
                if action == "publish":
                    self.engine.advance(job_id)  # independent fidelity comparison
                self.provider.fail_after = action
                count = len(self.provider.calls)
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(len(self.provider.calls), count + 1)
                self.assertEqual(self.engine.reconcile(job_id)["state"], next_state)
                self.assertEqual(len(self.provider.calls), count + 1)

    def test_loss_before_every_write_requires_terminal_reconciliation_and_same_exact_key(self):
        job_id = self.prepared()
        stages = (("remove_portals", "ready", "portals_removed"), ("archive", "portals_removed", "archived"),
                  ("create", "archived", "created"), ("publish", "fidelity_verified", "publication_pending"))
        for action, previous, after in stages:
            with self.subTest(action=action):
                if action == "publish":
                    self.engine.advance(job_id)
                self.provider.fail_before = action
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                command = self.request(job_id, action)
                count = len(self.provider.calls)
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(len(self.provider.calls), count)
                self.assertEqual(self.engine.reconcile(job_id)["state"], previous)
                self.assertEqual(self.engine.advance(job_id)["state"], after)
                self.assertEqual(self.request(job_id, action), command)
                self.assertEqual(self.provider.calls[-1], self.provider.calls[-2])
        self.assertEqual(len(self.provider.listings), 1)

    def test_not_applied_without_terminal_or_matching_binding_never_unlocks_retry(self):
        job_id = self.prepared()
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        request = self.request(job_id, "remove_portals")
        valid = self.provider.reconcile(request)
        for changes in ({"terminal": False}, {"request_hash": "different"}, {"operation_id": "other"}, {"authoritative": False}):
            with self.subTest(changes=changes):
                self.provider.reconcile_override = {**valid, **changes}
                with self.assertRaises(SafetyBlocked):
                    self.engine.reconcile(job_id)
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(len(self.provider.calls), 1)

    def test_unbound_success_receipt_stays_uncertain_until_authoritative_reconciliation(self):
        job_id = self.prepared()
        execute = self.provider.execute
        self.provider.execute = lambda request: {**execute(request), "request_hash": "other"}
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")
        self.assertEqual(len(self.provider.calls), 1)

    def test_wrong_source_or_account_receipt_cannot_confirm_mutation(self):
        job_id = self.prepared()
        original = self.provider.execute
        self.provider.execute = lambda request: {**original(request), "account_identity": {"account_id": "other", "agency_id": "other"}}
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")

    def assert_no_provider_marker(self, job_id, marker):
        self.assertNotIn(marker, str(self.store.journal(job_id)))
        rows = self.store.conn.execute("SELECT result_json FROM operations WHERE job_id=?", (job_id,)).fetchall()
        self.assertNotIn(marker, str([tuple(row) for row in rows]))
        self.assertNotIn(marker.encode(), (self.folder / "jobs.sqlite3").read_bytes())

    def test_all_mutation_receipts_project_only_validated_action_fields(self):
        job_id = self.prepared()
        original = self.provider.execute
        marker = "UNTRUSTED_RECEIPT_CONTENT_MUST_NOT_REACH_AUDIT"
        def execute(request):
            result = original(request)
            # These used to pass a broad audit allowlist without being checked
            # for the action. Required receipt fields remain valid and bound.
            extras = {key: marker for key in ("reason", "state", "rxcu", "position", "approval_hash", "snapshot_hash", "media_count", "archive_reason", "all_removed", "archived", "verified")}
            extras["evidence"] = marker
            if request.action == "archive":
                extras.pop("archive_reason")
            if request.action != "publish":
                extras["accepted"] = marker
            return {**result, **extras}
        self.provider.execute = execute
        self.reach(job_id, "publication_pending")
        self.assert_no_provider_marker(job_id, marker)
        confirmed = [event["details"] for event in self.store.journal(job_id) if event["phase"] == "confirmed"]
        self.assertEqual([item["state"] for item in confirmed], ["portals_removed", "archived", "created", "publication_pending"])
        for details in confirmed:
            self.assertTrue(details["evidence_present"])
            self.assertEqual(len(details["evidence_hash"]), 64)

    def test_publication_status_extras_and_arbitrary_state_are_never_logged(self):
        job_id = self.prepared()
        self.reach(job_id, "publication_pending")
        marker = "UNTRUSTED_PUBLICATION_CONTENT_MUST_NOT_REACH_AUDIT"
        original = self.provider.publication_status
        self.provider.publication_status = lambda listing_id: {**original(listing_id),
            "state": marker, "reason": marker, "status": marker, "source_id": marker, "accepted": marker,
            "evidence": marker, "verified": marker, "portal_targets": [marker]}
        self.assertEqual(self.engine.advance(job_id)["state"], "publication_pending")
        self.assert_no_provider_marker(job_id, marker)
        details = self.store.journal(job_id)[-1]["details"]
        self.assertEqual(details["state"], "pending")
        self.assertIs(details["verified"], False)
        self.assertEqual(details["portal_count"], 2)

    def test_reconciliation_extras_never_enter_not_applied_or_applied_audit(self):
        job_id = self.prepared()
        marker = "UNTRUSTED_RECONCILIATION_CONTENT_MUST_NOT_REACH_AUDIT"
        original = self.provider.reconcile
        def reconcile(request):
            response = original(request)
            return {**response, "reason": marker, "state": marker, "source_id": marker,
                    "accepted": marker, "evidence": marker, "verified": marker, "snapshot_hash": marker}
        self.provider.reconcile = reconcile
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.engine.reconcile(job_id)["state"], "ready")
        self.assert_no_provider_marker(job_id, marker)
        self.provider.fail_after = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.engine.reconcile(job_id)["state"], "portals_removed")
        self.assert_no_provider_marker(job_id, marker)

    def test_immutable_persisted_operation_prevents_payload_changes_and_deletion(self):
        job_id = self.prepared()
        self.engine.advance(job_id)
        for statement in ("UPDATE operations SET payload_json='{}'", "UPDATE operations SET action='publish'", "DELETE FROM operations"):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.conn:
                    self.store.conn.execute(statement)
        self.assertEqual(self.request(job_id, "remove_portals").expected_source_revision, "r1")

    def test_legacy_unbound_uncertain_and_not_applied_operations_are_never_rebuilt(self):
        for status, state in (("uncertain", "needs_reconciliation"), ("not_applied", "ready")):
            with self.subTest(status=status):
                # A separate store models an older application installation.
                with tempfile.TemporaryDirectory() as folder:
                    store = Store(Path(folder) / "old.sqlite3")
                    try:
                        engine = Engine(store, self.adapter)
                        batch = engine.new_batch(["RXCU300001"])
                        job_id = store.list_jobs(batch)[0]["id"]
                        capture = engine.snapshot(job_id)
                        engine.approve_address(job_id, capture["original_address"], "mandate_expired")
                        with store.conn:
                            store.conn.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?)", ("legacy-id", job_id, "remove_portals", '{"source_id":"source-RXCU300001"}', "ready", status, None))
                            store.conn.execute("UPDATE jobs SET state=? WHERE id=?", (state, job_id))
                        with self.assertRaisesRegex(SafetyBlocked, "legacy"):
                            (engine.reconcile if status == "uncertain" else engine.advance)(job_id)
                        self.assertEqual(self.provider.calls, [])
                    finally:
                        store.close()

    def test_cross_job_valid_command_cannot_be_used_for_reconciliation(self):
        first = self.prepared()
        second = self.prepared("RXCU200002")
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(first)
        operation = self.request(first, "remove_portals")
        wrong = self.engine._build_request(self.store.get_job(second), "remove_portals", operation.operation_id)
        # Model accidental corruption in an older/external database writer. The
        # normal application trigger independently prohibits this update.
        with self.store.conn:
            self.store.conn.execute("DROP TRIGGER immutable_operation_request")
            self.store.conn.execute("UPDATE operations SET payload_json=? WHERE id=?", (json.dumps(wrong.to_dict()), operation.operation_id))
        reconciliations = []
        self.provider.reconcile = lambda request: reconciliations.append(request)
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(first)
        self.assertEqual(reconciliations, [])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.store.get_job(first)["state"], "needs_reconciliation")

    def test_changed_expected_revision_in_stored_request_blocks_reconciliation(self):
        job_id = self.prepared()
        self.provider.fail_before = "remove_portals"
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        operation = self.request(job_id, "remove_portals")
        wrong = replace(operation, expected_source_revision="fresh-but-not-approved")
        with self.store.conn:
            self.store.conn.execute("DROP TRIGGER immutable_operation_request")
            self.store.conn.execute("UPDATE operations SET payload_json=? WHERE id=?", (json.dumps(wrong.to_dict()), operation.operation_id))
        reconciliations = []
        self.provider.reconcile = lambda request: reconciliations.append(request)
        with self.assertRaises(SafetyBlocked):
            self.engine.reconcile(job_id)
        self.assertEqual(reconciliations, [])
        self.assertEqual(len(self.provider.calls), 1)

    def test_replacement_revision_lineage_draft_rooms_and_mandate_changes_block_publication(self):
        changes = (
            lambda listing: listing.update(revision="unapproved-revision"),
            lambda listing: listing.update(source_id="another-source"),
            lambda listing: listing.update(source_snapshot_hash="another-capture"),
            lambda listing: listing.update(creation_operation_id="another-operation"),
            lambda listing: listing.update(state="live"),
            lambda listing: listing["fields"]["rooms"]["bedrooms"][0].update(description="changed room"),
            lambda listing: listing["fields"]["mandate"].update(expiry_date="2099-12-31"),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                # Release earlier synthetic queue heads only for independent cases.
                with self.store.conn:
                    self.store.conn.execute("UPDATE jobs SET state='live'")
                job_id = self.prepared("RXCU" + str(240000 + index))
                job = self.reach(job_id, "created")
                change(self.provider.listings[job["new_listing_id"]])
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertNotIn("publish", [action for action, _ in self.provider.calls])

    def test_json_boolean_number_coercion_is_not_exact_fidelity(self):
        job_id = self.prepared()
        job = self.reach(job_id, "created")
        self.provider.listings[job["new_listing_id"]]["fields"]["rooms"]["bathrooms"][0]["full"] = 1
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertNotIn("publish", [action for action, _ in self.provider.calls])

    def test_source_boolean_number_coercion_is_not_unchanged(self):
        job_id = self.prepared()
        self.provider.source("RXCU200001")["fields"]["rooms"]["bathrooms"][0]["full"] = 1
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(self.provider.calls, [])

    def test_process_death_and_restart_at_every_write_preserves_bound_request(self):
        job_id = self.prepared()
        stages = (("remove_portals", "portals_removed"), ("archive", "archived"), ("create", "created"), ("publish", "publication_pending"))
        for action, next_state in stages:
            with self.subTest(action=action):
                if action == "publish":
                    self.engine.advance(job_id)
                self.provider.crash_after = action
                with self.assertRaises(SystemExit):
                    self.engine.advance(job_id)
                command = self.request(job_id, action)
                count = len(self.provider.calls)
                self.store.close()
                self.store = Store(self.folder / "jobs.sqlite3")
                self.adapter = GatedHubAdapter(self.provider)
                self.engine = Engine(self.store, self.adapter)
                self.assertEqual(self.request(job_id, action), command)
                with self.assertRaises(SafetyBlocked):
                    self.engine.advance(job_id)
                self.assertEqual(self.engine.reconcile(job_id)["state"], next_state)
                self.assertEqual(len(self.provider.calls), count)

    def test_duplicate_batch_and_replacement_source_alias_are_rejected(self):
        job_id = self.prepared()
        job = self.reach(job_id, "created")
        with self.assertRaises(SafetyBlocked):
            self.engine.new_batch(["RXCU200001"])
        source = self.provider.source("RXCU300001")
        source["source_id"] = job["new_listing_id"]
        batch = self.engine.new_batch(["RXCU300001"])
        other = self.store.list_jobs(batch)[0]["id"]
        with self.assertRaises(SafetyBlocked):
            self.engine.snapshot(other)

    def test_old_positional_mutation_signature_is_not_silently_used(self):
        job_id = self.prepared()
        old_calls = []
        def legacy(source_id, operation_id):
            old_calls.append((source_id, operation_id))
        self.provider.remove_from_portals = legacy
        with self.assertRaises(SafetyBlocked):
            self.engine.advance(job_id)
        self.assertEqual(old_calls, [])
        self.assertEqual(self.provider.receipts, {})
        self.assertEqual(self.store.get_job(job_id)["state"], "needs_reconciliation")


if __name__ == "__main__":
    unittest.main()
