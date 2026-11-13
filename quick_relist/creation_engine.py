"""Optional staged-creation integration. Not wired to the desktop launcher.

The default adapter remains unavailable. This API supplies no Hub provider and
no qualification. A staged provider must compare the exact durable checkpoint
under its creation lock and atomically enforce each child command's revisions.
"""
from copy import deepcopy
import json
import re

from .core import (Engine, SafetyBlocked, RelistError, _json, _safe_error,
                   _reconciliation_audit)
from .mutations import (GatedHubAdapter, MutationRequest, MutationContractError,
                        canonical, digest, validate_receipt,
                        validate_reconciliation, validate_replacement, _bound)


def validate_creation_progress(request, response):
    """A partial result is never an applied mutation or a fresh-create permit."""
    if request.action != "create":
        raise MutationContractError("Staged progress is only valid for creation.")
    _bound(request, response)
    if response.get("status") != "partial":
        return validate_reconciliation(request, response)
    checkpoint = response.get("checkpoint")
    keys = {"version", "parent_operation_id", "parent_request_hash", "source_revision",
            "account_identity", "draft_id", "draft_revision", "completed_chain_hash",
            "next_stage", "next_child_hash", "no_pending", "retry_not_applied"}
    if not isinstance(checkpoint, dict) or set(checkpoint) != keys:
        raise MutationContractError("An exact durable creation checkpoint is required.")
    if (type(checkpoint["version"]) is not int or checkpoint["version"] != 1
            or checkpoint["parent_operation_id"] != request.operation_id
            or checkpoint["parent_request_hash"] != request.fingerprint
            or checkpoint["source_revision"] != request.expected_source_revision
            or checkpoint["account_identity"] != request.account_identity
            or checkpoint["no_pending"] is not True
            or type(checkpoint["retry_not_applied"]) is not bool
            or type(checkpoint["next_stage"]) is not int
            or not 1 <= checkpoint["next_stage"] <= 1 + len(request.replacement_fields) + len(request.snapshot["media_manifest"])
            or any(not isinstance(checkpoint[key], str) or not checkpoint[key].strip()
                   for key in ("draft_id", "draft_revision"))
            or checkpoint["draft_id"] == request.source_id
            or any(not isinstance(checkpoint[key], str) or not re.fullmatch(r"[a-f0-9]{64}", checkpoint[key])
                   for key in ("completed_chain_hash", "next_child_hash"))
            or response.get("checkpoint_hash") != digest(checkpoint)):
        raise MutationContractError("Creation checkpoint does not match the immutable request.")
    return response


STAGED_QUALIFICATIONS = frozenset({"bound_creation_checkpoints", "atomic_checkpoint_resume"})


class StagedHubAdapter(GatedHubAdapter):
    """Optional provider seam; existing generic adapter behavior stays strict.

    A provider implementing these methods must have independent qualification,
    including stale-checkpoint rejection. This wrapper cannot confer it.
    """
    @property
    def missing_capabilities(self):
        missing = list(super().missing_capabilities)
        missing.extend(key for key in sorted(STAGED_QUALIFICATIONS) if self.qualification.get(key) is not True)
        missing.extend("provider." + name for name in ("reconcile_creation", "resume_creation")
                       if not callable(getattr(self._provider, name, None)))
        return tuple(missing)

    def reconcile_creation(self, request):
        self._check_request(request)
        return validate_creation_progress(request, self._read("reconcile_creation", request))

    def resume_creation(self, request, checkpoint):
        self._check_request(request)
        # Validate checkpoint shape/binding again at dispatch, without claiming
        # that this local check replaces provider-side atomic comparison.
        validate_creation_progress(request, {
            "operation_id": request.operation_id, "request_hash": request.fingerprint,
            "action": "create", "authoritative": True, "evidence": "Local dispatch binding",
            "status": "partial", "checkpoint": checkpoint, "checkpoint_hash": digest(checkpoint),
        })
        return validate_receipt(request, self._read("resume_creation", request, deepcopy(checkpoint)))


class CreationEngine(Engine):
    """Explicit API-only recovery. Ordinary advance never resumes a partial draft."""
    def __init__(self, store, adapter=None):
        super().__init__(store, adapter)
        with store._db_lock:
            store.conn.executescript("""
            CREATE TABLE IF NOT EXISTS creation_resume_events(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              operation_id TEXT NOT NULL REFERENCES operations(id),
              request_hash TEXT NOT NULL, kind TEXT NOT NULL,
              checkpoint_json TEXT, checkpoint_hash TEXT);
            CREATE TRIGGER IF NOT EXISTS immutable_resume_event_update BEFORE UPDATE ON creation_resume_events
              BEGIN SELECT RAISE(ABORT, 'Creation resume history is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_resume_event_delete BEFORE DELETE ON creation_resume_events
              BEGIN SELECT RAISE(ABORT, 'Creation resume history is append-only'); END;
            """)

    def _creation_request(self, job):
        if job["state"] != "needs_reconciliation":
            raise SafetyBlocked("Only an uncertain creation can be resumed.")
        with self.store._db_lock:
            row = self.store.conn.execute(
                "SELECT * FROM operations WHERE job_id=? AND action='create' AND status='uncertain'",
                (job["id"],)).fetchone()
        if row is None:
            raise SafetyBlocked("No uncertain immutable creation request exists.")
        try:
            request = MutationRequest.from_dict(json.loads(row["payload_json"]))
            if (row["previous_state"] != "archived"
                    or request != self._build_request(job, "create", row["id"])):
                raise MutationContractError("Invalid parent binding")
        except (MutationContractError, TypeError, ValueError):
            raise SafetyBlocked("Stored creation does not match the exact approved job. Manual review is required.") from None
        return request

    def _record_creation(self, job, request, kind, checkpoint=None):
        with self.store._tx() as db:
            db.execute("INSERT INTO creation_resume_events(operation_id,request_hash,kind,checkpoint_json,checkpoint_hash) VALUES(?,?,?,?,?)",
                       (request.operation_id, request.fingerprint, kind,
                        canonical(checkpoint) if checkpoint is not None else None,
                        digest(checkpoint) if checkpoint is not None else None))
            self.store._event(db, job["id"], "create", kind,
                              {"request_hash": request.fingerprint}, request.operation_id)

    def _observe_creation(self, job, request):
        response = self._call("reconcile_creation", request)
        try:
            validate_creation_progress(request, response)
        except MutationContractError:
            raise SafetyBlocked("No authoritative creation checkpoint is available. No write was started.") from None
        # Once a draft was observed, whole-parent absence is contradictory.
        # Neither a new draft identity nor a regressed chain can be adopted.
        with self.store._db_lock:
            prior = self.store.conn.execute(
                "SELECT checkpoint_json,checkpoint_hash,request_hash FROM creation_resume_events WHERE operation_id=? AND checkpoint_json IS NOT NULL ORDER BY sequence DESC LIMIT 1",
                (request.operation_id,)).fetchone()
        if prior is not None:
            saved = json.loads(prior["checkpoint_json"])
            if prior["request_hash"] != request.fingerprint:
                raise SafetyBlocked("Stored creation checkpoint integrity failed.")
            validate_creation_progress(request, {"operation_id": request.operation_id, "request_hash": request.fingerprint,
                "action": "create", "authoritative": True, "evidence": "Stored observation",
                "status": "partial", "checkpoint": saved, "checkpoint_hash": prior["checkpoint_hash"]})
            if response["status"] == "not_applied":
                raise SafetyBlocked("A known partial draft cannot become a fresh creation. Manual review is required.")
            if response["status"] == "partial":
                current = response["checkpoint"]
                if (current["draft_id"] != saved["draft_id"]
                        or current["next_stage"] < saved["next_stage"]
                        or (current["next_stage"] == saved["next_stage"]
                            and current["draft_revision"] != saved["draft_revision"])):
                    raise SafetyBlocked("Creation progress conflicts with the known draft or child revision chain.")
            elif response["status"] == "applied" and response["result"]["listing_id"] != saved["draft_id"]:
                raise SafetyBlocked("Completed creation does not match the known partial draft.")
        self._record_creation(job, request, "partial_observed" if response["status"] == "partial" else response["status"],
                              response.get("checkpoint") if response["status"] == "partial" else None)
        return response

    def reconcile(self, job_id):
        # Leave every non-create path on the original strict implementation.
        with self.store._db_lock:
            row = self.store.conn.execute("SELECT 1 FROM operations WHERE job_id=? AND action='create' AND status='uncertain'", (job_id,)).fetchone()
        if row is None:
            return super().reconcile(job_id)
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            try:
                self._guard(job)
                request = self._creation_request(job)
                self._source_unchanged(job, request.expected_source_revision)
                response = self._observe_creation(job, request)
                if response["status"] == "applied":
                    self._finish(job, request, response["result"], reconciled=True)
                elif response["status"] == "partial":
                    with self.store._tx() as db:
                        self.store._update(db, job_id, error="A bound partial draft exists. Creation is incomplete; only explicit creation resume may continue it.")
                elif response["status"] == "not_applied":
                    # Only a proof covering the entire parent request, not one
                    # child, may unlock the ordinary same-key retry path.
                    self._identity(request.account_identity)
                    audit = _reconciliation_audit(request, response)
                    with self.store._tx() as db:
                        db.execute("UPDATE operations SET status='not_applied',result_json=? WHERE id=?", (_json(audit), request.operation_id))
                        self.store._update(db, job_id, state="archived", error="Creation was terminally not applied. Explicit Advance may retry the same request.")
                else:
                    raise SafetyBlocked("Creation remains uncertain. Reconcile before resuming.")
            except Exception as exc:
                self.store._error(job_id, _safe_error(exc))
                if isinstance(exc, RelistError):
                    raise
                raise SafetyBlocked("Creation reconciliation failed. No write was started.") from None
            return self.store.get_job(job_id)

    def resume_creation(self, job_id):
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            intended = False
            try:
                self._guard(job)
                request = self._creation_request(job)
                self._source_unchanged(job, request.expected_source_revision)
                self._removed(job)
                self._archived(job)
                response = self._observe_creation(job, request)  # always fresh and read-only
                if response["status"] == "applied":
                    self._finish(job, request, response["result"], reconciled=True)
                    return self.store.get_job(job_id)
                if response["status"] != "partial":
                    raise SafetyBlocked("No safe partial checkpoint exists. Use reconciliation; no creation was repeated.")
                validation = self._call("validate_replacement", request)
                validate_replacement(request, validation)
                if validation["decision_required"] or not validation["valid"]:
                    raise SafetyBlocked("The unchanged replacement requires validation or an explicit business decision. No resume was started.")
                self._verify_files(request.snapshot["media_manifest"])
                self._identity(request.account_identity)
                # Exact checkpoint retained durably before dispatch, including a
                # process death between this commit and the provider response.
                checkpoint = deepcopy(response["checkpoint"])
                self._record_creation(job, request, "resume_intended", checkpoint)
                intended = True
                result = self._call("resume_creation", request, checkpoint)
                self._finish(job, request, result)
                self._record_creation(job, request, "resume_completed")
            except Exception as exc:
                self.store._error(job_id, "Creation resume outcome is uncertain. Reconcile before continuing." if intended else _safe_error(exc))
                if intended:
                    raise SafetyBlocked("Creation resume outcome is uncertain. Reconcile before continuing.") from None
                if isinstance(exc, RelistError):
                    raise
                raise SafetyBlocked("Creation resume checks failed. No write was started.") from None
            return self.store.get_job(job_id)

    def creation_progress(self, job_id):
        """Local read-only view. Never presents an incomplete draft as created."""
        job = self.store.get_job(job_id)
        with self.store._db_lock:
            operation = self.store.conn.execute("SELECT * FROM operations WHERE job_id=? AND action='create'", (job_id,)).fetchone()
            if operation is None:
                return {"status": "not_started", "draft_id": None}
            rows = self.store.conn.execute("SELECT * FROM creation_resume_events WHERE operation_id=? ORDER BY sequence DESC", (operation["id"],)).fetchall()
        try:
            request = MutationRequest.from_dict(json.loads(operation["payload_json"]))
            if (operation["previous_state"] != "archived"
                    or request != self._build_request(job, "create", operation["id"])):
                raise MutationContractError("Stored parent context changed")
            checkpoint = None
            previous = None
            kinds = {"partial_observed", "resume_intended", "resume_completed", "applied", "not_applied", "unknown"}
            for row in reversed(rows):
                if row["request_hash"] != request.fingerprint or row["kind"] not in kinds:
                    raise MutationContractError("Stored progress binding changed")
                if row["kind"] in {"partial_observed", "resume_intended"}:
                    value = json.loads(row["checkpoint_json"])
                    validate_creation_progress(request, {"operation_id": request.operation_id, "request_hash": request.fingerprint,
                        "action": "create", "authoritative": True, "evidence": "Stored observation",
                        "status": "partial", "checkpoint": value, "checkpoint_hash": row["checkpoint_hash"]})
                    if previous is not None and (value["draft_id"] != previous["draft_id"]
                            or value["next_stage"] < previous["next_stage"]
                            or (value["next_stage"] == previous["next_stage"] and value["draft_revision"] != previous["draft_revision"])):
                        raise MutationContractError("Stored draft chain changed")
                    if row["kind"] == "resume_intended" and (previous is None or canonical(value) != canonical(previous)):
                        raise MutationContractError("Resume intent lacks exact observed checkpoint")
                    previous = checkpoint = value
                elif row["checkpoint_json"] is not None or row["checkpoint_hash"] is not None:
                    raise MutationContractError("Unexpected stored checkpoint")
            total = 2 + len(request.replacement_fields) + len(request.snapshot["media_manifest"])
            if operation["status"] == "applied":
                result = json.loads(operation["result_json"])
                revision_hash = result.get("listing_revision_hash")
                if (job["state"] not in {"created", "fidelity_verified", "publication_pending", "live"}
                        or result.get("status") != "applied" or result.get("state") != "created"
                        or result.get("request_hash") != request.fingerprint
                        or result.get("source_id") != request.source_id
                        or not isinstance(job["new_listing_id"], str) or not job["new_listing_id"].strip()
                        or job["new_listing_id"] == request.source_id
                        or result.get("listing_id") != job["new_listing_id"]
                        or not isinstance(job["replacement_revision"], str) or not job["replacement_revision"].strip()
                        or not isinstance(revision_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", revision_hash)
                        or (checkpoint and checkpoint["draft_id"] != job["new_listing_id"])):
                    raise MutationContractError("Stored completion is inconsistent")
                if job["state"] in {"created", "fidelity_verified"}:
                    if digest(job["replacement_revision"]) != revision_hash:
                        raise MutationContractError("Stored completion revision changed")
                else:
                    with self.store._db_lock:
                        publication = self.store.conn.execute("SELECT * FROM operations WHERE job_id=? AND action='publish' AND status='applied'", (job_id,)).fetchone()
                    if publication is None:
                        raise MutationContractError("No bound publication explains the changed draft revision")
                    published_request = MutationRequest.from_dict(json.loads(publication["payload_json"]))
                    published_result = json.loads(publication["result_json"])
                    expected_publication = self._build_request(
                        {**job, "replacement_revision": published_request.expected_replacement_revision},
                        "publish", publication["id"])
                    if (publication["previous_state"] != "fidelity_verified"
                            or published_request != expected_publication
                            or published_request.replacement_id != job["new_listing_id"]
                            or digest(published_request.expected_replacement_revision) != revision_hash
                            or published_result.get("status") != "applied"
                            or published_result.get("state") != "publication_pending"
                            or published_result.get("accepted") is not True
                            or published_result.get("source_id") != request.source_id
                            or published_result.get("listing_id") != job["new_listing_id"]
                            or published_result.get("source_revision_hash") != digest(request.expected_source_revision)
                            or published_result.get("request_hash") != published_request.fingerprint
                            or published_result.get("listing_revision_hash") != digest(job["replacement_revision"])):
                        raise MutationContractError("Stored publication revision chain changed")
                return {"status": "complete", "draft_id": job["new_listing_id"], "completed_stages": total, "remaining_stages": 0, "uncertain": False}
            if operation["status"] not in {"uncertain", "not_applied"}:
                raise MutationContractError("Unknown creation state")
            if operation["status"] == "uncertain" and job["state"] != "needs_reconciliation":
                raise MutationContractError("Uncertain creation has an inconsistent job state")
            if operation["status"] == "not_applied" and (checkpoint is not None or job["state"] != "archived"):
                raise MutationContractError("A known partial draft cannot be not-applied")
            latest = rows[0] if rows else None
            uncertain = latest is None or latest["kind"] != "partial_observed"
            return {"status": "needs_reconciliation" if uncertain else "partial", "draft_id": checkpoint["draft_id"] if checkpoint else None,
                    "draft_revision": checkpoint["draft_revision"] if checkpoint else None,
                    "completed_stages": checkpoint["next_stage"] if checkpoint else None,
                    "remaining_stages": total - checkpoint["next_stage"] if checkpoint else None,
                    "uncertain": uncertain, "observation_only": True}
        except (MutationContractError, ValueError, TypeError, KeyError):
            raise SafetyBlocked("Stored creation progress is inconsistent. Manual investigation is required.") from None
