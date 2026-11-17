"""Full offline workflow with the real durable staged coordinator, never Hub."""
from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import unittest
import tempfile

from quick_relist.core import Engine, SafetyBlocked, Store, AdapterUnavailable
from quick_relist.creation import CreationStore, DraftCreator
from quick_relist.creation_engine import CreationEngine, StagedHubAdapter, validate_creation_progress
from quick_relist.mutations import MutationRequest, MutationContractError, digest
from creation_fixture import SyntheticCreationProvider, Crash
from test_core import SyntheticHub


class CoupledChildren(SyntheticCreationProvider):
    """Share source/account/lifecycle state with the outer synthetic server."""
    def __init__(self, request, hub):
        super().__init__(request)
        self.hub = hub

    def _sync(self):
        self.source_revision = self.hub.source(self.request.source_rxcu)["revision"]
        self.account = self.hub.account_identity
        self.archived = self.request.source_id in self.hub.archived_sources
        self.portals_removed = self.request.source_id in self.hub.removed_sources

    def preflight(self, command):
        self._sync()
        return super().preflight(command)

    def execute(self, command, content):
        self._sync()
        return super().execute(command, content)


class CoordinatorHub(SyntheticHub):
    qualification = {**SyntheticHub.qualification, "bound_creation_checkpoints": True, "atomic_checkpoint_resume": True}
    """Test-only optional provider. Never imported by application code."""
    def __init__(self, media, child_path):
        super().__init__(media)
        self.child_path = child_path
        self.child_store = CreationStore(child_path)
        self.creator = None
        self.child = None
        self.fail_child_before = None
        self.fail_child_after = None
        self.crash_child_after = None
        self.before_resume = None
        self.before_resume_guard = None
        self.resume_checkpoints = []
        self.progress_override = None
        self.crash_on_resume_entry = False

    def _export(self, request):
        receipt = self.creator.complete_receipt_for_testing(request)
        draft = self.child.drafts[receipt["listing_id"]]
        self.listings[receipt["listing_id"]] = {
            "listing_id": receipt["listing_id"], "revision": draft["revision"],
            "state": draft["state"], "complete": True,
            "account_identity": request.account_identity, "source_id": request.source_id,
            "source_snapshot_hash": request.snapshot_hash, "creation_operation_id": request.operation_id,
            "fields": deepcopy(draft["fields"]), "media_manifest": deepcopy(draft["media_manifest"]),
            "portal_targets": deepcopy(draft["portal_targets"]),
        }
        self.receipts[request.operation_id] = deepcopy(receipt)
        return receipt

    def _run(self, request, checkpoint=None, retry=False):
        state = self.creator.advance_for_testing(request, expected_checkpoint=checkpoint, retry_not_applied=retry)
        while state["status"] != "complete":
            state = self.creator.advance_for_testing(request)
        return self._export(request)

    def create_listing(self, request):
        self.calls.append(("create", request.operation_id))
        if request.operation_id in self.requests:
            assert self.requests[request.operation_id] == request
        self.requests[request.operation_id] = request
        if self.creator is None:
            self.child = CoupledChildren(request, self)
            self.child.fail_before = self.fail_child_before
            self.child.fail_after = self.fail_child_after
            self.child.crash_after = self.crash_child_after
            self.creator = DraftCreator(self.child_store, self.child)
            return self._run(request)
        return self._run(request, retry=self.creator.status(request)["status"] == "retry_available")

    def reconcile_creation(self, request):
        if self.progress_override:
            return self.progress_override(request)
        state = self.creator.status(request)
        if state["status"] == "needs_reconciliation":
            self.creator.reconcile_for_testing(request)
        response = self.creator.progress_for_testing(request)
        if response["status"] == "applied":
            self._export(request)
        return response

    def resume_creation(self, request, checkpoint):
        self.resume_checkpoints.append(deepcopy(checkpoint))
        if self.crash_on_resume_entry:
            self.crash_on_resume_entry = False
            raise Crash()
        if self.before_resume:
            hook, self.before_resume = self.before_resume, None
            hook(request, checkpoint)
        return self._run(request, checkpoint)


def workflow(tmp_path):
    media = tmp_path / "synthetic-original.bin"
    media.write_bytes(b"original synthetic media bytes")
    store = Store(tmp_path / "outer.sqlite3")
    hub = CoordinatorHub(media, tmp_path / "creation-private" / "children.sqlite3")
    engine = CreationEngine(store, StagedHubAdapter(hub))
    batch = engine.new_batch(["RXCU918271"])
    job_id = store.list_jobs(batch)[0]["id"]
    job = engine.snapshot(job_id)
    address = deepcopy(job["original_address"])
    address["street_number"] = "45"
    engine.approve_address(job_id, address, "mandate_expired")
    engine.advance(job_id)
    engine.advance(job_id)
    yield engine, store, hub, job_id
    store.close()
    hub.child_store.close()


def parent_request(store, job_id):
    row = store.conn.execute("SELECT payload_json FROM operations WHERE job_id=? AND action='create'", (job_id,)).fetchone()
    return MutationRequest.from_dict(json.loads(row[0]))


def interrupt(workflow, ordinal=2, before=False):
    engine, store, hub, job_id = workflow
    if before:
        hub.fail_child_before = ordinal
    else:
        hub.fail_child_after = ordinal
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.advance(job_id)
    assert store.get_job(job_id)["state"] == "needs_reconciliation"
    return parent_request(store, job_id)


def finish_live(workflow):
    engine, store, hub, job_id = workflow
    assert engine.advance(job_id)["state"] == "fidelity_verified"
    assert engine.advance(job_id)["state"] == "publication_pending"
    hub.live = True
    assert engine.reconcile(job_id)["state"] == "live"
    assert len(hub.child.drafts) == 1
    assert len([call for call in hub.calls if call[0] == "create"]) == 1


def _scenario_full_staged_workflow_and_fidelity(workflow):
    engine, store, hub, job_id = workflow
    assert engine.advance(job_id)["state"] == "created"
    request = parent_request(store, job_id)
    listing = hub.listings[store.get_job(job_id)["new_listing_id"]]
    assert listing["fields"] == request.replacement_fields
    assert listing["media_manifest"] == [{k: v for k, v in a.items() if k != "local_path"} for a in request.snapshot["media_manifest"]]
    assert [content for _, content, _ in hub.child.uploads] == [Path(a["local_path"]).read_bytes() for a in request.snapshot["media_manifest"]]
    assert engine.creation_progress(job_id)["status"] == "complete"
    finish_live(workflow)


def _scenario_partial_reconcile_is_read_only_explicit_resume_same_parent(workflow, ordinal):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow, ordinal)
    before = len(hub.child.calls)
    assert engine.reconcile(job_id)["state"] == "needs_reconciliation"
    assert len(hub.child.calls) == before
    progress = engine.creation_progress(job_id)
    assert progress["status"] == "partial" and progress["draft_id"]
    assert progress["remaining_stages"] > 0 and not progress["uncertain"]
    assert store.get_job(job_id)["new_listing_id"] is None
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.advance(job_id)
    assert len(hub.child.calls) == before
    assert engine.resume_creation(job_id)["state"] == "created"
    assert parent_request(store, job_id) == request
    assert len({c.operation_id for c in hub.child.calls}) == len(hub.child.calls)
    row = store.conn.execute("SELECT * FROM creation_resume_events WHERE kind='resume_intended'").fetchone()
    checkpoint = json.loads(row["checkpoint_json"])
    assert row["request_hash"] == request.fingerprint
    assert row["checkpoint_hash"] == digest(checkpoint)
    assert checkpoint == hub.resume_checkpoints[0]
    finish_live(workflow)


def _scenario_terminal_child_absence_retries_exact_child_not_parent(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow, before=True)
    failed = hub.child.calls[-1]
    engine.reconcile(job_id)
    assert engine.creation_progress(job_id)["status"] == "partial"
    engine.resume_creation(job_id)
    matching = [command for command in hub.child.calls if command.operation_id == failed.operation_id]
    assert matching == [failed, failed]
    assert parent_request(store, job_id) == request
    finish_live(workflow)


def _scenario_terminal_allocation_absence_uses_normal_same_parent_retry(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow, 0, before=True)
    assert not hub.child.drafts
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert engine.reconcile(job_id)["state"] == "archived"
    assert engine.advance(job_id)["state"] == "created"
    assert parent_request(store, job_id) == request
    assert len(hub.child.drafts) == 1
    assert hub.child.calls[0] == hub.child.calls[1]


def _scenario_unknown_child_never_unblocks_resume(workflow):
    engine, store, hub, job_id = workflow
    interrupt(workflow)
    hub.child.reconciliation = lambda cmd: {**DraftCreator._binding(cmd), "status": "unknown"}
    before = len(hub.child.calls)
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.reconcile(job_id)
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert len(hub.child.calls) == before
    assert not hub.resume_checkpoints


def _scenario_stale_checkpoint_rejected_under_coordinator_lock(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    seen = []
    def other_progress(req, checkpoint):
        seen.append(len(hub.child.calls))
        hub.creator.advance_for_testing(req)  # simulate a competing holder progressing
    hub.before_resume = other_progress
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert len(hub.child.calls) == seen[0] + 1  # only the competing operation
    assert len(hub.resume_checkpoints) == 1
    assert store.get_job(job_id)["state"] == "needs_reconciliation"
    engine.reconcile(job_id)
    assert engine.resume_creation(job_id)["state"] == "created"
    assert parent_request(store, job_id) == request


def _scenario_resume_crash_preserves_checkpoint_then_recovers_after_reopen(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    hub.crash_on_resume_entry = True
    with unittest.TestCase().assertRaises(Crash):
        engine.resume_creation(job_id)
    assert engine.creation_progress(job_id)["uncertain"]
    before = len(hub.child.calls)
    path = store.path
    store.close()
    store.__init__(path)
    engine = CreationEngine(store, StagedHubAdapter(hub))
    hub.child_store.close()
    hub.child_store = CreationStore(hub.child_path)
    hub.creator = DraftCreator(hub.child_store, hub.child)
    assert engine.reconcile(job_id)["state"] == "needs_reconciliation"
    assert len(hub.child.calls) == before
    assert engine.resume_creation(job_id)["state"] == "created"
    assert parent_request(store, job_id) == request
    assert store.conn.execute("SELECT count(*) FROM creation_resume_events WHERE kind='resume_intended'").fetchone()[0] == 2


def _scenario_resume_rechecks_every_original_gate(workflow, change):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    before = len(hub.child.calls)
    if change == "source":
        hub.source(request.source_rxcu)["revision"] = "external"
    elif change == "account":
        hub.account_identity = {"account_id": "other", "agency_id": "other"}
    elif change == "archive":
        hub.archived_sources.clear()
    elif change == "portals":
        hub.removed_sources.clear()
    elif change == "validation":
        hub.validation_decision_required = True
    else:
        hub.media_path.write_bytes(b"changed")
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert len(hub.child.calls) == before
    assert not hub.resume_checkpoints


def _scenario_external_draft_revision_change_rejected_at_child_cas(workflow):
    engine, store, hub, job_id = workflow
    interrupt(workflow)
    engine.reconcile(job_id)
    def drift(req, checkpoint):
        hub.child.drafts[checkpoint["draft_id"]]["revision"] = "external"
    hub.before_resume = drift
    before = len(hub.child.calls)
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert len(hub.child.calls) == before  # strict preflight also blocks it
    assert store.get_job(job_id)["new_listing_id"] is None


def _scenario_forged_checkpoint_blocks_without_resume(workflow, field, value):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    response = hub.creator.progress_for_testing(request)
    response["checkpoint"][field] = value
    response["checkpoint_hash"] = digest(response["checkpoint"])
    hub.progress_override = lambda req: response
    with unittest.TestCase().assertRaises(SafetyBlocked):
        engine.resume_creation(job_id)
    assert not hub.resume_checkpoints


def _scenario_original_adapter_and_engine_remain_strict(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    partial = hub.creator.progress_for_testing(request)
    hub.reconcile_override = partial
    with unittest.TestCase().assertRaises(SafetyBlocked):
        Engine(store, StagedHubAdapter(hub)).reconcile(job_id)
    with unittest.TestCase().assertRaises(AdapterUnavailable):
        CreationEngine(store).resume_creation(job_id)
    assert not hub.resume_checkpoints


def _scenario_resume_history_is_immutable_and_journal_has_no_checkpoint(workflow):
    engine, store, hub, job_id = workflow
    request = interrupt(workflow)
    engine.reconcile(job_id)
    engine.resume_creation(job_id)
    with unittest.TestCase().assertRaises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE creation_resume_events SET checkpoint_json='{}'")
    with unittest.TestCase().assertRaises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM creation_resume_events")
    journal = json.dumps(store.journal(job_id))
    assert "completed_chain_hash" not in journal and "checkpoint_json" not in journal
    assert str(hub.media_path) not in journal and "SECRET" not in journal


class CreationEngineTests(unittest.TestCase):
    """Each case uses fresh private outer and child databases."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = workflow(Path(self.temp.name))
        self.workflow = next(self.context)

    def tearDown(self):
        try:
            next(self.context)
        except StopIteration:
            pass
        self.temp.cleanup()

    def test_full_staged_workflow_and_fidelity(self):
        _scenario_full_staged_workflow_and_fidelity(self.workflow)

    def test_partial_reconcile_is_read_only_explicit_resume_same_parent_0(self):
        _scenario_partial_reconcile_is_read_only_explicit_resume_same_parent(self.workflow, 0)

    def test_partial_reconcile_is_read_only_explicit_resume_same_parent_1(self):
        _scenario_partial_reconcile_is_read_only_explicit_resume_same_parent(self.workflow, 2)

    def test_partial_reconcile_is_read_only_explicit_resume_same_parent_2(self):
        _scenario_partial_reconcile_is_read_only_explicit_resume_same_parent(self.workflow, 10)

    def test_terminal_child_absence_retries_exact_child_not_parent(self):
        _scenario_terminal_child_absence_retries_exact_child_not_parent(self.workflow)

    def test_terminal_allocation_absence_uses_normal_same_parent_retry(self):
        _scenario_terminal_allocation_absence_uses_normal_same_parent_retry(self.workflow)

    def test_unknown_child_never_unblocks_resume(self):
        _scenario_unknown_child_never_unblocks_resume(self.workflow)

    def test_stale_checkpoint_rejected_under_coordinator_lock(self):
        _scenario_stale_checkpoint_rejected_under_coordinator_lock(self.workflow)

    def test_resume_crash_preserves_checkpoint_then_recovers_after_reopen(self):
        _scenario_resume_crash_preserves_checkpoint_then_recovers_after_reopen(self.workflow)

    def test_resume_rechecks_every_original_gate_0(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'source')

    def test_resume_rechecks_every_original_gate_1(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'account')

    def test_resume_rechecks_every_original_gate_2(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'archive')

    def test_resume_rechecks_every_original_gate_3(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'portals')

    def test_resume_rechecks_every_original_gate_4(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'validation')

    def test_resume_rechecks_every_original_gate_5(self):
        _scenario_resume_rechecks_every_original_gate(self.workflow, 'media')

    def test_external_draft_revision_change_rejected_at_child_cas(self):
        _scenario_external_draft_revision_change_rejected_at_child_cas(self.workflow)

    def test_forged_checkpoint_blocks_without_resume_0(self):
        _scenario_forged_checkpoint_blocks_without_resume(self.workflow, 'parent_operation_id', 'other')

    def test_forged_checkpoint_blocks_without_resume_1(self):
        _scenario_forged_checkpoint_blocks_without_resume(self.workflow, 'draft_id', '')

    def test_forged_checkpoint_blocks_without_resume_2(self):
        _scenario_forged_checkpoint_blocks_without_resume(self.workflow, 'no_pending', False)

    def test_forged_checkpoint_blocks_without_resume_3(self):
        _scenario_forged_checkpoint_blocks_without_resume(self.workflow, 'next_stage', True)

    def test_forged_checkpoint_blocks_without_resume_4(self):
        _scenario_forged_checkpoint_blocks_without_resume(self.workflow, 'next_child_hash', 'bogus')

    def test_original_adapter_and_engine_remain_strict(self):
        _scenario_original_adapter_and_engine_remain_strict(self.workflow)

    def test_resume_history_is_immutable_and_journal_has_no_checkpoint(self):
        _scenario_resume_history_is_immutable_and_journal_has_no_checkpoint(self.workflow)


    def test_missing_child_ledger_never_reallocates(self):
        engine, store, hub, job_id = self.workflow
        interrupt(self.workflow)
        engine.reconcile(job_id)
        old_store = hub.child_store
        old_store.close()
        hub.child_store = CreationStore(Path(self.temp.name) / "missing-private" / "missing-ledger.sqlite3")
        hub.creator = DraftCreator(hub.child_store, hub.child)
        before = len(hub.child.calls)
        with self.assertRaises(SafetyBlocked):
            engine.resume_creation(job_id)
        with self.assertRaises(SafetyBlocked):
            engine.reconcile(job_id)
        self.assertEqual(len(hub.child.calls), before)
        self.assertEqual(len(hub.child.drafts), 1)
        self.assertEqual(store.get_job(job_id)["state"], "needs_reconciliation")

    def test_observed_partial_cannot_become_not_applied(self):
        engine, store, hub, job_id = self.workflow
        request = interrupt(self.workflow)
        engine.reconcile(job_id)
        hub.progress_override = lambda req: {**DraftCreator._outer_binding(req), "status": "not_applied", "terminal": True}
        with self.assertRaises(SafetyBlocked):
            engine.reconcile(job_id)
        with self.assertRaises(SafetyBlocked):
            engine.resume_creation(job_id)
        self.assertEqual(store.get_job(job_id)["state"], "needs_reconciliation")
        self.assertEqual(parent_request(store, job_id), request)

    def test_observed_draft_cannot_be_substituted(self):
        engine, store, hub, job_id = self.workflow
        request = interrupt(self.workflow)
        engine.reconcile(job_id)
        response = hub.creator.progress_for_testing(request)
        response["checkpoint"]["draft_id"] = "another-draft"
        response["checkpoint_hash"] = digest(response["checkpoint"])
        hub.progress_override = lambda req: response
        with self.assertRaises(SafetyBlocked):
            engine.resume_creation(job_id)
        self.assertFalse(hub.resume_checkpoints)

    def test_process_crash_during_resumed_child_recovers_without_duplicate(self):
        engine, store, hub, job_id = self.workflow
        request = interrupt(self.workflow)
        engine.reconcile(job_id)
        next_ordinal = hub.creator.status(request)["ordinal"]
        hub.child.crash_after = next_ordinal
        with self.assertRaises(Crash):
            engine.resume_creation(job_id)
        before = len(hub.child.calls)
        self.assertTrue(engine.creation_progress(job_id)["uncertain"])
        engine.reconcile(job_id)
        self.assertEqual(len(hub.child.calls), before)
        self.assertEqual(engine.resume_creation(job_id)["state"], "created")
        self.assertEqual(len({c.operation_id for c in hub.child.calls}), len(hub.child.calls))

    def test_atomic_child_rechecks_source_after_preflight(self):
        engine, store, hub, job_id = self.workflow
        request = interrupt(self.workflow)
        engine.reconcile(job_id)
        original = hub.child.execute
        def raced(command, content):
            hub.source(request.source_rxcu)["revision"] = "raced"
            return original(command, content)
        hub.child.execute = raced
        drafts = deepcopy(hub.child.drafts)
        with self.assertRaises(SafetyBlocked):
            engine.resume_creation(job_id)
        self.assertEqual(hub.child.drafts, drafts)
        self.assertEqual(store.get_job(job_id)["state"], "needs_reconciliation")

    def test_progress_rejects_corrupt_parent_context(self):
        engine, store, hub, job_id = self.workflow
        interrupt(self.workflow)
        engine.reconcile(job_id)
        store.conn.execute("DROP TRIGGER immutable_operation_request")
        store.conn.execute("UPDATE operations SET previous_state='ready' WHERE action='create'")
        with self.assertRaises(SafetyBlocked):
            engine.creation_progress(job_id)

    def test_progress_rejects_cross_job_checkpoint_binding(self):
        engine, store, hub, job_id = self.workflow
        interrupt(self.workflow)
        engine.reconcile(job_id)
        store.conn.execute("DROP TRIGGER immutable_resume_event_update")
        store.conn.execute("UPDATE creation_resume_events SET request_hash='another-parent'")
        with self.assertRaises(SafetyBlocked):
            engine.creation_progress(job_id)

    def test_progress_rejects_forged_completed_status(self):
        engine, store, hub, job_id = self.workflow
        interrupt(self.workflow)
        engine.reconcile(job_id)
        store.conn.execute("UPDATE operations SET status='applied' WHERE action='create'")
        with self.assertRaises(SafetyBlocked):
            engine.creation_progress(job_id)

    def test_progress_rejects_changed_completed_identity_or_revision(self):
        engine, store, hub, job_id = self.workflow
        engine.advance(job_id)
        job = store.get_job(job_id)
        for field, value in (("new_listing_id", "other-draft"), ("replacement_revision", "external")):
            with self.subTest(field=field):
                store.conn.execute("UPDATE jobs SET " + field + "=? WHERE id=?", (value, job_id))
                with self.assertRaises(SafetyBlocked):
                    engine.creation_progress(job_id)
                store.conn.execute("UPDATE jobs SET " + field + "=? WHERE id=?", (job[field], job_id))
        finish_live(self.workflow)
        self.assertEqual(engine.creation_progress(job_id)["status"], "complete")

    def test_optional_staged_capability_requires_methods_and_evidence(self):
        engine, store, hub, job_id = self.workflow
        for capability in ("bound_creation_checkpoints", "atomic_checkpoint_resume"):
            with self.subTest(capability=capability):
                hub.qualification = {**type(hub).qualification, capability: False}
                adapter = StagedHubAdapter(hub)
                self.assertFalse(adapter.is_live_ready)
                self.assertIn(capability, adapter.missing_capabilities)
        hub.qualification = dict(type(hub).qualification)
        hub.resume_creation = None
        self.assertFalse(StagedHubAdapter(hub).is_live_ready)


    def test_progress_rejects_corrupt_publication_audit(self):
        engine, store, hub, job_id = self.workflow
        engine.advance(job_id)
        finish_live(self.workflow)
        row = store.conn.execute("SELECT id,result_json FROM operations WHERE action='publish'").fetchone()
        original = json.loads(row["result_json"])
        for field, value in (("status", "not_applied"), ("state", "archived"),
                             ("source_id", "other-source"), ("listing_id", "other-listing"),
                             ("accepted", False), ("source_revision_hash", "other-revision")):
            with self.subTest(field=field):
                altered = {**original, field: value}
                store.conn.execute("UPDATE operations SET result_json=? WHERE id=?", (json.dumps(altered), row["id"]))
                with self.assertRaises(SafetyBlocked):
                    engine.creation_progress(job_id)
        store.conn.execute("UPDATE operations SET result_json=? WHERE id=?", (json.dumps(original), row["id"]))
        self.assertEqual(engine.creation_progress(job_id)["status"], "complete")

    def test_progress_rejects_corrupt_publication_request_context(self):
        engine, store, hub, job_id = self.workflow
        engine.advance(job_id)
        finish_live(self.workflow)
        row = store.conn.execute("SELECT * FROM operations WHERE action='publish'").fetchone()
        original = json.loads(row["payload_json"])
        audit = json.loads(row["result_json"])
        store.conn.execute("DROP TRIGGER immutable_operation_request")
        for field, value in (("operation_id", "another-operation"), ("expected_source_revision", "another-revision")):
            with self.subTest(field=field):
                altered = {**original, field: value}
                forged = MutationRequest.from_dict(altered)
                altered_audit = {**audit, "request_hash": forged.fingerprint}
                store.conn.execute("UPDATE operations SET payload_json=?,result_json=? WHERE id=?",
                                   (json.dumps(altered), json.dumps(altered_audit), row["id"]))
                with self.assertRaises(SafetyBlocked):
                    engine.creation_progress(job_id)
        store.conn.execute("UPDATE operations SET payload_json=?,result_json=?,previous_state='created' WHERE id=?",
                           (json.dumps(original), json.dumps(audit), row["id"]))
        with self.assertRaises(SafetyBlocked):
            engine.creation_progress(job_id)



if __name__ == "__main__":
    unittest.main()
