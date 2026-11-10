"""Fail-closed relisting state machine. No Hub endpoints or private code are used.

An adapter must exhaustively capture every transferable field, every media asset
and every portal target. ``complete=True`` is an adapter assertion, not a claim
that the generic engine can discover fields an integration omitted. Integration
qualification, authorization and real portal checks remain required before use.

All external writes have a committed intention before the call. An interrupted
write is never repeated by advance(); reconcile() requires authoritative read-
only evidence. Publication acceptance is distinct from verified live publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Protocol
from uuid import uuid4


# These are integration qualification assertions, not implemented Hub features.
# A real connector must not enable them without independently reviewed tests.
REQUIRED_ADAPTER_QUALIFICATIONS = frozenset({
    "complete_capture", "account_binding", "required_address_schema",
    "atomic_revision_preconditions", "idempotent_writes",
    "authoritative_reconciliation", "portal_observations",
})


class RelistError(Exception):
    """A recoverable, user-visible workflow error."""


class ValidationError(RelistError):
    pass


class SafetyBlocked(RelistError):
    pass


class AdapterUnavailable(RelistError):
    pass


class OperationBusy(RelistError):
    pass


class VerificationUnavailable(RelistError):
    """A provider call failed, without asserting a verified safety mismatch.

    Before an intention is recorded, retrying the read is safe. After an
    intention is recorded, _write still converts this into an uncertain action
    that requires reconciliation rather than a write retry.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Only finite JSON-compatible values may be captured.") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _safe_error(exc: Exception) -> str:
    # Adapter exceptions are wrapped at the boundary before reaching here.
    return str(exc) if isinstance(exc, RelistError) else "Verification failed. Inspect the source safely before continuing."


def _audit_details(details: dict) -> dict:
    """Do not copy arbitrary adapter evidence, tokens or vendor payloads to logs."""
    allowed = {"rxcu", "position", "source_id", "listing_id", "snapshot_hash", "media_count",
               "approval_hash", "archive_reason", "reason", "state", "status", "accepted",
               "verified", "archived", "all_removed"}
    result = {key: value for key, value in details.items()
              if key in allowed and isinstance(value, (str, int, bool, type(None)))}
    if "address" in details:
        result["address_hash"] = _digest(details["address"])
    if "evidence" in details:
        result["evidence_present"] = bool(details["evidence"])
        result["evidence_hash"] = _digest(details["evidence"])
    if "portal_targets" in details:
        result["portal_count"] = len(details["portal_targets"])
    return result


def normalize_rxcu(value: str) -> str:
    value = str(value).strip().upper()
    if not re.fullmatch(r"RXCU-?[0-9]{3,20}", value):
        raise ValidationError("Use an RXCU reference followed by 3 to 20 digits.")
    return value.replace("-", "")


class HubAdapter(Protocol):
    """Required integration contract, intentionally not implemented for real Hub.

    read_source returns a full, normalized business record plus the untouched
    raw listing record (never authentication/session/transport data). Lifecycle
    metadata belongs outside fields. Source shape:
    {source_id, rxcu, revision, complete: True, account_identity: {...},
     fields: {address: {...}, ...},
     raw: {...}, media_manifest: [...], media_count, portal_targets: [str, ...]}.
    Each media item: {id, kind, position, sha256, size, local_path, ...}.
    local_path points to a downloaded original, not a URL or thumbnail. The
    other keys (including order/captions/tags) are fidelity-critical metadata.

    All writes receive a stable operation_id for provider-side idempotency.
    reconcile_action is READ ONLY and returns {status: applied|not_applied|
    unknown, evidence: str, result: dict}. not_applied must mean the original
    request cannot later commit, not merely that an eventually consistent read
    currently finds nothing. create results must include listing_id.

    authenticated_identity returns {account_id, agency_id, verified: True}.
    Capture account_identity contains only account_id and agency_id. Archive
    must select BOTH cancelled status and mandate_expired reason; archive_status
    must independently confirm status='cancelled' and reason='mandate_expired'.
    read_listing must also return account_identity, identifying the actual
    account and agency that own the recreated record, not just the signed-in user.

    required_address_fields in a source capture enumerates the connector's
    required address keys. Approval rejects missing/null/blank required values.
    qualification must explicitly mark every REQUIRED_ADAPTER_QUALIFICATIONS
    item True, based on reviewed integration tests, before is_live_ready can be
    enabled. In particular, all write implementations must atomically enforce
    the verified source/replacement revision, including create-source lineage.
    A preflight read alone is NOT an atomic revision precondition. This module
    does not implement provider CAS, and Hub support is unknown. Missing atomic
    support is a hard live-connector blocker, not permission to omit the check.
    """
    is_live_ready: bool
    qualification: dict[str, bool]

    def authenticated_identity(self) -> dict: ...
    def read_source(self, rxcu: str) -> dict: ...
    def remove_from_portals(self, source_id: str, operation_id: str) -> dict: ...
    def portals_removed(self, source_id: str) -> dict: ...
    def archive(self, source_id: str, reason: str, operation_id: str) -> dict: ...
    def archive_status(self, source_id: str) -> dict: ...
    def create_listing(self, snapshot: dict, corrected_address: dict, operation_id: str) -> dict: ...
    def read_listing(self, listing_id: str) -> dict: ...
    def publish(self, listing_id: str, operation_id: str) -> dict: ...
    def publication_status(self, listing_id: str) -> dict: ...
    def reconcile_action(self, action: str, operation_id: str, payload: dict) -> dict: ...


class UnavailableHubAdapter:
    """Production default: cannot read or modify any listing."""
    is_live_ready = False
    reason = "Hub is not connected. A qualified live Hub adapter is required. No listing has been changed."

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        def unavailable(*args, **kwargs):
            raise AdapterUnavailable(self.reason)
        return unavailable


class Store:
    """SQLite store with immutable captures and append-only journal.

    A portable process lock excludes overlapping external actions across local
    app instances. SQLite access is also protected across UI/worker threads.
    Keep the database and downloaded media on a private local disk, not a share.
    """
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.RLock()
        self._action_lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          position INTEGER NOT NULL, rxcu TEXT NOT NULL, state TEXT NOT NULL,
          source_id TEXT, snapshot_json TEXT, snapshot_hash TEXT, expected_revision TEXT,
          corrected_address_json TEXT, archive_reason TEXT, approved INTEGER NOT NULL DEFAULT 0,
          approval_hash TEXT, new_listing_id TEXT, error TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(batch_id, position), UNIQUE(batch_id, rxcu));
        CREATE TABLE IF NOT EXISTS operations(
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), action TEXT NOT NULL,
          payload_json TEXT NOT NULL, previous_state TEXT NOT NULL, status TEXT NOT NULL,
          result_json TEXT, UNIQUE(job_id, action));
        CREATE TABLE IF NOT EXISTS journal(
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
          operation_id TEXT, action TEXT NOT NULL, phase TEXT NOT NULL,
          details_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_snapshot BEFORE UPDATE ON jobs
        WHEN OLD.snapshot_json IS NOT NULL AND (
          NEW.snapshot_json IS NOT OLD.snapshot_json OR NEW.snapshot_hash IS NOT OLD.snapshot_hash
          OR NEW.source_id IS NOT OLD.source_id OR NEW.rxcu IS NOT OLD.rxcu)
        BEGIN SELECT RAISE(ABORT, 'Original snapshot and identity are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_journal_update BEFORE UPDATE ON journal
        BEGIN SELECT RAISE(ABORT, 'Journal is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_journal_delete BEFORE DELETE ON journal
        BEGIN SELECT RAISE(ABORT, 'Journal is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_job_delete BEFORE DELETE ON jobs
        BEGIN SELECT RAISE(ABORT, 'Workflow history cannot be deleted'); END;
        """)
        self.conn.commit()

    def close(self) -> None:
        with self._db_lock:
            self.conn.close()

    @contextmanager
    def _tx(self):
        with self._db_lock, self.conn:
            yield self.conn

    @contextmanager
    def operation_lock(self):
        if not self._action_lock.acquire(blocking=False):
            raise OperationBusy("Another operation is running. Wait for its result.")
        handle = None
        try:
            if self.path != ":memory:":
                handle = open(str(Path(self.path).resolve()) + ".operations.lock", "a+b")
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise OperationBusy("Another Quick Relist instance is running an operation.") from exc
                else:
                    import fcntl
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise OperationBusy("Another Quick Relist instance is running an operation.") from exc
            yield
        finally:
            if handle is not None:
                handle.close()
            self._action_lock.release()

    def _job(self, row) -> dict:
        if row is None:
            raise ValidationError("Listing job was not found.")
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json")) if result["snapshot_json"] else None
        result["corrected_address"] = json.loads(result.pop("corrected_address_json")) if result["corrected_address_json"] else None
        # pop nullable storage fields too, keeping a single public schema.
        result.pop("snapshot_json", None)
        result.pop("corrected_address_json", None)
        result["original_address"] = deepcopy(result["snapshot"]["fields"]["address"]) if result["snapshot"] else None
        result["approved"] = bool(result["approved"])
        return result

    def get_job(self, job_id: str) -> dict:
        with self._db_lock:
            return self._job(self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, batch_id: str | None = None) -> list[dict]:
        with self._db_lock:
            rows = self.conn.execute(
                "SELECT j.* FROM jobs j JOIN batches b ON j.batch_id=b.id "
                + ("WHERE batch_id=? " if batch_id else "") + "ORDER BY b.rowid,j.position",
                (batch_id,) if batch_id else (),
            ).fetchall()
            return [self._job(row) for row in rows]

    def list_batches(self) -> list[dict]:
        with self._db_lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT b.*,count(j.id) AS job_count FROM batches b LEFT JOIN jobs j ON j.batch_id=b.id "
                "GROUP BY b.id ORDER BY b.rowid"
            )]

    def journal(self, job_id: str) -> list[dict]:
        with self._db_lock:
            rows = self.conn.execute("SELECT * FROM journal WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
            return [{**dict(r), "details": json.loads(r["details_json"])} for r in rows]

    @staticmethod
    def _event(db, job_id, action, phase, details, operation_id=None):
        db.execute("INSERT INTO journal(job_id,operation_id,action,phase,details_json,created_at) VALUES(?,?,?,?,?,?)",
                   (job_id, operation_id, action, phase, _json(_audit_details(details)), _now()))

    def _update(self, db, job_id, **values):
        values["updated_at"] = _now()
        db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                   (*values.values(), job_id))

    def _error(self, job_id: str, message: str, blocked: bool = False):
        with self._tx() as db:
            values = {"error": message}
            if blocked:
                values["state"] = "blocked"
            self._update(db, job_id, **values)


class Engine:
    ARCHIVE_REASONS = {"mandate_expired"}
    def __init__(self, store: Store, adapter: HubAdapter | None = None):
        self.store = store
        self.adapter = adapter if adapter is not None else UnavailableHubAdapter()

    def _ready(self):
        if getattr(self.adapter, "is_live_ready", False) is not True:
            raise AdapterUnavailable("Hub is not connected. A qualified live Hub adapter is required. No action was started.")
        qualified = getattr(self.adapter, "qualification", None)
        if not isinstance(qualified, dict) or any(qualified.get(key) is not True for key in REQUIRED_ADAPTER_QUALIFICATIONS):
            raise AdapterUnavailable("Hub connector qualification is incomplete. Atomic revision protection, complete capture, identity, address validation and safe recovery must be independently verified before live use.")

    def _call(self, method: str, *args):
        try:
            return getattr(self.adapter, method)(*args)
        except Exception:
            # Exception text can include record contents, credentials or URLs.
            # Do not persist or expose it through the UI or operation journal.
            raise VerificationUnavailable(f"Hub {method} is temporarily unavailable. No outcome can be inferred from this failed call.") from None

    def _identity(self, expected: dict | None = None) -> dict:
        observed = self._call("authenticated_identity")
        if not isinstance(observed, dict) or observed.get("verified") is not True or any(
            not isinstance(observed.get(key), str) or not observed[key] for key in ("account_id", "agency_id")
        ):
            raise SafetyBlocked("The authenticated Hub account and agency could not be verified.")
        identity = {key: observed[key] for key in ("account_id", "agency_id")}
        if expected is not None and identity != expected:
            raise SafetyBlocked("The authenticated Hub account or agency changed. No new action is allowed.")
        return identity

    def new_batch(self, rxcus: list[str]) -> str:
        if not isinstance(rxcus, (list, tuple)) or not rxcus:
            raise ValidationError("Enter at least one RXCU reference.")
        normalized = [normalize_rxcu(rxcu) for rxcu in rxcus]
        if len(normalized) != len(set(normalized)):
            raise ValidationError("A batch cannot contain the same RXCU twice.")
        with self.store.operation_lock(), self.store._tx() as db:
            # Even a second batch must not schedule the same source twice.
            placeholders = ",".join("?" for _ in normalized)
            if db.execute(f"SELECT 1 FROM jobs WHERE rxcu IN ({placeholders}) "
                          "AND (state<>'cancelled' OR EXISTS(SELECT 1 FROM operations WHERE job_id=jobs.id)) LIMIT 1", normalized).fetchone():
                raise SafetyBlocked("An RXCU already has workflow history. Resume that job instead of relisting it twice.")
            batch_id, now = uuid4().hex, _now()
            db.execute("INSERT INTO batches VALUES(?,?)", (batch_id, now))
            for index, rxcu in enumerate(normalized):
                job_id = uuid4().hex
                db.execute("INSERT INTO jobs(id,batch_id,position,rxcu,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (job_id, batch_id, index, rxcu, "queued", now, now))
                self.store._event(db, job_id, "queue", "saved", {"rxcu": rxcu, "position": index})
            return batch_id

    def cancel(self, job_id: str) -> dict:
        """Cancel local queued work only if no external write was ever intended."""
        with self.store.operation_lock(), self.store._tx() as db:
            job = self.store.get_job(job_id)
            if job["state"] == "cancelled":
                return job
            if job["state"] not in {"queued", "awaiting_address", "ready", "blocked"}:
                raise SafetyBlocked("Only an unstarted job can be cancelled. Reconcile or investigate any started work.")
            if db.execute("SELECT 1 FROM operations WHERE job_id=? LIMIT 1", (job_id,)).fetchone():
                raise SafetyBlocked("This job has a write intention in its history and cannot be cancelled, even if an action was not applied.")
            self.store._update(db, job_id, state="cancelled", approved=0, error=None)
            self.store._event(db, job_id, "cancel", "cancelled", {"rxcu": job["rxcu"]})
            return self.store.get_job(job_id)

    cancel_job = cancel

    @staticmethod
    def _media_business(manifest: list) -> list:
        return [{k: v for k, v in asset.items() if k != "local_path"} for asset in manifest]

    @staticmethod
    def _verify_files(manifest: list):
        for asset in manifest:
            path = Path(asset["local_path"])
            try:
                if not path.is_absolute() or not path.is_file() or path.stat().st_size != asset["size"]:
                    raise SafetyBlocked("An original media file is missing or its size changed.")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != asset["sha256"]:
                    raise SafetyBlocked("An original media file failed its SHA-256 integrity check.")
            except OSError as exc:
                raise SafetyBlocked("An original media file is not readable.") from exc

    def _validate_capture(self, capture: dict, rxcu: str):
        if not isinstance(capture, dict) or capture.get("complete") is not True:
            raise SafetyBlocked("The Hub adapter has not certified a complete original capture.")
        for key in ("source_id", "revision"):
            if not isinstance(capture.get(key), str) or not capture[key].strip():
                raise SafetyBlocked(f"Source capture has no verified {key}.")
        if capture.get("rxcu") != rxcu:
            raise SafetyBlocked("Source identity does not match the requested RXCU.")
        identity = capture.get("account_identity")
        if not isinstance(identity, dict) or set(identity) != {"account_id", "agency_id"} or any(not isinstance(value, str) or not value for value in identity.values()):
            raise SafetyBlocked("The source capture must identify its verified Hub account and agency.")
        fields = capture.get("fields")
        if not isinstance(fields, dict) or not isinstance(fields.get("address"), dict) or not fields["address"]:
            raise SafetyBlocked("The original full address was not captured.")
        required = capture.get("required_address_fields")
        if not isinstance(required, list) or not required or any(not isinstance(key, str) or key not in fields["address"] for key in required) or len(required) != len(set(required)):
            raise SafetyBlocked("The connector's complete required-address schema was not captured.")
        if not isinstance(capture.get("raw"), dict) or not capture["raw"]:
            raise SafetyBlocked("The untouched full source record was not captured.")
        manifest = capture.get("media_manifest")
        if not isinstance(manifest, list) or type(capture.get("media_count")) is not int or capture["media_count"] != len(manifest):
            raise SafetyBlocked("The complete original media manifest is required.")
        seen = set()
        for asset in manifest:
            if not isinstance(asset, dict):
                raise SafetyBlocked("Invalid original media manifest.")
            if not all(isinstance(asset.get(k), str) and asset[k] for k in ("id", "kind", "local_path")):
                raise SafetyBlocked("Every original media asset needs identity, type and a local original file.")
            if asset["id"] in seen or type(asset.get("position")) is not int or asset["position"] < 0:
                raise SafetyBlocked("Media identities and ordering must be unambiguous.")
            seen.add(asset["id"])
            if type(asset.get("size")) is not int or asset["size"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", str(asset.get("sha256", ""))):
                raise SafetyBlocked("Every original media asset needs a verified size and SHA-256 hash.")
        targets = capture.get("portal_targets")
        if not isinstance(targets, list) or any(not isinstance(t, str) or not t for t in targets) or len(targets) != len(set(targets)):
            raise SafetyBlocked("Capture must enumerate all original portal targets.")
        _json(capture)
        self._verify_files(manifest)

    def snapshot(self, job_id: str) -> dict:
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            if job["state"] == "cancelled":
                raise SafetyBlocked("This job was cancelled. Create a new queue entry if you want to capture it again.")
            if job["snapshot"] is not None:
                return job
            try:
                self._ready()
                identity = self._identity()
                capture = deepcopy(self._call("read_source", job["rxcu"]))
                self._validate_capture(capture, job["rxcu"])
                if capture["account_identity"] != identity:
                    raise SafetyBlocked("The source belongs to a different Hub account or agency.")
                self._identity(identity)
                with self.store._tx() as db:
                    if db.execute("SELECT 1 FROM jobs WHERE (source_id=? OR new_listing_id=?) AND id<>? "
                                  "AND (state<>'cancelled' OR EXISTS(SELECT 1 FROM operations WHERE job_id=jobs.id))",
                                  (capture["source_id"], capture["source_id"], job_id)).fetchone():
                        raise SafetyBlocked("This source identity already belongs to another job.")
                    self.store._update(db, job_id, snapshot_json=_json(capture), snapshot_hash=_digest(capture),
                                       source_id=capture["source_id"], expected_revision=capture["revision"],
                                       state="awaiting_address", error=None)
                    self.store._event(db, job_id, "capture", "verified", {"snapshot_hash": _digest(capture), "media_count": len(capture["media_manifest"])})
            except Exception as exc:
                self.store._error(job_id, _safe_error(exc))
                if isinstance(exc, RelistError):
                    raise
                raise SafetyBlocked("Source capture failed. No destructive work was started.") from exc
            return self.store.get_job(job_id)

    def approve_address(self, job_id: str, corrected_address: dict, archive_reason: str) -> dict:
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            if job["state"] not in {"awaiting_address", "ready"} or not job["snapshot"]:
                raise SafetyBlocked("Capture the original listing before approving its address.")
            if not isinstance(corrected_address, dict) or not corrected_address:
                raise ValidationError("Approve a complete corrected address.")
            if set(corrected_address) != set(job["original_address"]):
                raise ValidationError("Corrected address must preserve every original address field.")
            if any(not isinstance(corrected_address.get(key), str) or not corrected_address[key].strip()
                   for key in job["snapshot"]["required_address_fields"]):
                raise ValidationError("Every required address field must contain a nonempty text value before approval.")
            if archive_reason not in self.ARCHIVE_REASONS:
                raise ValidationError("The required archive path is cancelled status with mandate_expired as its reason.")
            address = json.loads(_json(corrected_address))
            approval = _digest({"snapshot_hash": job["snapshot_hash"], "address": address, "archive_reason": archive_reason})
            with self.store._tx() as db:
                self.store._update(db, job_id, corrected_address_json=_json(address), archive_reason=archive_reason,
                                   approved=1, approval_hash=approval, state="ready", error=None)
                self.store._event(db, job_id, "approve_address", "approved", {"address": address, "archive_reason": archive_reason, "snapshot_hash": job["snapshot_hash"]})
            return self.store.get_job(job_id)

    def _guard(self, job: dict):
        self._ready()
        if not job["snapshot"] or _digest(job["snapshot"]) != job["snapshot_hash"]:
            raise SafetyBlocked("The original snapshot integrity could not be verified.")
        self._identity(job["snapshot"]["account_identity"])
        approval = _digest({"snapshot_hash": job["snapshot_hash"], "address": job["corrected_address"], "archive_reason": job["archive_reason"]})
        if not job["approved"] or approval != job["approval_hash"]:
            raise SafetyBlocked("Explicit approval of the corrected address and archive reason is required.")
        if job["archive_reason"] not in self.ARCHIVE_REASONS:
            raise SafetyBlocked("Only cancelled status with mandate_expired reason is authorized by this workflow.")
        # Global queue ordering also prevents independent batches interleaving.
        pending = [item for item in self.store.list_jobs() if item["state"] not in {"live", "cancelled"}]
        if pending and pending[0]["id"] != job["id"]:
            raise OperationBusy("Finish or reconcile the earlier RXCU before changing this listing.")
        self._verify_files(job["snapshot"]["media_manifest"])

    def _source_unchanged(self, job: dict, allow_revision_change=False) -> dict:
        current = self._call("read_source", job["rxcu"])
        self._validate_capture(current, job["rxcu"])
        if current["source_id"] != job["source_id"]:
            raise SafetyBlocked("Source identity changed. Manual investigation is required.")
        if current["account_identity"] != job["snapshot"]["account_identity"]:
            raise SafetyBlocked("The current source belongs to a different Hub account or agency.")
        if current["required_address_fields"] != job["snapshot"]["required_address_fields"]:
            raise SafetyBlocked("The required address schema changed after capture. Review is required.")
        original = job["snapshot"]
        if current["fields"] != original["fields"] or self._media_business(current["media_manifest"]) != self._media_business(original["media_manifest"]) or current["portal_targets"] != original["portal_targets"]:
            raise SafetyBlocked("The source listing or original media changed after capture. Stop and review the source.")
        if not allow_revision_change and current["revision"] != job["expected_revision"]:
            raise SafetyBlocked("The source revision changed outside the verified workflow.")
        return current

    def _removed(self, job: dict):
        result = self._call("portals_removed", job["source_id"])
        if not isinstance(result, dict) or result.get("source_id") != job["source_id"] or result.get("verified") is not True or result.get("all_removed") is not True or not result.get("evidence"):
            raise SafetyBlocked("Portal removal is not yet independently verified. Archiving is blocked.")
        states = result.get("portals", {})
        if not isinstance(states, dict) or set(states) != set(job["snapshot"]["portal_targets"]) or any(state != "removed" for state in states.values()):
            raise SafetyBlocked("Removal must be verified for every captured portal target.")
        return result

    def _archived(self, job: dict):
        result = self._call("archive_status", job["source_id"])
        if not isinstance(result, dict) or result.get("source_id") != job["source_id"] or result.get("verified") is not True or result.get("archived") is not True or result.get("status") != "cancelled" or result.get("reason") != job["archive_reason"] or not result.get("evidence"):
            raise SafetyBlocked("The original listing archive and reason are not independently verified.")
        return result

    def _fidelity(self, job: dict):
        listing = self._call("read_listing", job["new_listing_id"])
        if not isinstance(listing, dict) or listing.get("listing_id") != job["new_listing_id"] or listing.get("complete") is not True:
            raise SafetyBlocked("The recreated listing identity or full contents cannot be verified.")
        if listing.get("account_identity") != job["snapshot"]["account_identity"]:
            raise SafetyBlocked("The recreated listing account or agency does not match the captured source. Publication is blocked.")
        expected_fields = deepcopy(job["snapshot"]["fields"])
        expected_fields["address"] = job["corrected_address"]
        if listing.get("fields") != expected_fields or self._media_business(listing.get("media_manifest", [])) != self._media_business(job["snapshot"]["media_manifest"]):
            raise SafetyBlocked("Recreated listing fidelity failed. Publication is blocked.")
        if listing.get("portal_targets") != job["snapshot"]["portal_targets"]:
            raise SafetyBlocked("Recreated listing portal targets do not match the approved original.")
        if job["state"] in {"created", "fidelity_verified"} and listing.get("state") != "draft":
            raise SafetyBlocked("The new listing must remain an unpublished draft until the fidelity check passes.")
        return listing

    def _begin(self, job: dict, action: str, payload: dict) -> str:
        with self.store._tx() as db:
            existing = db.execute("SELECT * FROM operations WHERE job_id=? AND action=?", (job["id"], action)).fetchone()
            if existing:
                if existing["status"] != "not_applied" or existing["payload_json"] != _json(payload):
                    raise SafetyBlocked("This action already has an outcome or an unresolved intention. Reconcile it first.")
                op_id = existing["id"]
                db.execute("UPDATE operations SET status='uncertain',result_json=NULL WHERE id=?", (op_id,))
            else:
                op_id = uuid4().hex
                db.execute("INSERT INTO operations(id,job_id,action,payload_json,previous_state,status) VALUES(?,?,?,?,?,?)",
                           (op_id, job["id"], action, _json(payload), job["state"], "uncertain"))
            self.store._event(db, job["id"], action, "intended", payload, op_id)
            # This state is committed BEFORE any external write. Process death
            # at any subsequent instruction leaves an explicit uncertainty.
            self.store._update(db, job["id"], state="needs_reconciliation", error="Action outcome is not yet recorded. Reconcile before continuing.")
        return op_id

    def _finish(self, job: dict, action: str, op_id: str, result: dict, reconciled=False):
        values = {"error": None}
        if action == "remove_portals":
            self._removed(job)
            values.update(state="portals_removed", expected_revision=self._source_unchanged(job, True)["revision"])
        elif action == "archive":
            self._removed(job)
            self._archived(job)
            values.update(state="archived", expected_revision=self._source_unchanged(job, True)["revision"])
        elif action == "create":
            new_id = result.get("listing_id") if isinstance(result, dict) else None
            if not isinstance(new_id, str) or not new_id or new_id == job["source_id"]:
                raise SafetyBlocked("Creation returned no distinct verified new listing identity. Reconcile before retrying.")
            with self.store._db_lock:
                if self.store.conn.execute("SELECT 1 FROM jobs WHERE (new_listing_id=? OR source_id=?) AND id<>?", (new_id, new_id, job["id"])).fetchone():
                    raise SafetyBlocked("Creation identity conflicts with another tracked listing.")
            # Persist identity before making any further network read.
            values.update(state="created", new_listing_id=new_id)
        elif action == "publish":
            if result.get("accepted") is not True or result.get("listing_id") != job["new_listing_id"] or not result.get("evidence"):
                raise SafetyBlocked("Publication acceptance has not been confirmed for the exact recreated listing.")
            values.update(state="publication_pending")
        with self.store._tx() as db:
            db.execute("UPDATE operations SET status='applied',result_json=? WHERE id=?", (_json(_audit_details(result)), op_id))
            self.store._update(db, job["id"], **values)
            self.store._event(db, job["id"], action, "reconciled_applied" if reconciled else "confirmed", result, op_id)

    def _write(self, job: dict, action: str, payload: dict, call):
        self._identity(job["snapshot"]["account_identity"])
        operation_id = self._begin(job, action, payload)
        try:
            result = call(operation_id)
            if not isinstance(result, dict):
                raise SafetyBlocked("Adapter returned no structured operation receipt.")
            self._finish(job, action, operation_id, result)
        except Exception as exc:
            with self.store._tx() as db:
                self.store._event(db, job["id"], action, "uncertain", {"error": _safe_error(exc)}, operation_id)
                self.store._update(db, job["id"], error="Outcome uncertain. Reconcile; do not repeat the action.")
            raise SafetyBlocked("Action outcome is uncertain. Reconcile before continuing.") from exc

    def advance(self, job_id: str) -> dict:
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            if job["state"] == "live":
                return job
            if job["state"] in {"needs_reconciliation", "blocked"}:
                raise SafetyBlocked("This job requires reconciliation or manual investigation; no action was repeated.")
            try:
                self._guard(job)
                state = job["state"]
                if state not in {"ready", "portals_removed", "archived", "created", "fidelity_verified", "publication_pending"}:
                    raise SafetyBlocked("Capture and approve the corrected address first.")
                self._source_unchanged(job)
                if state == "ready":
                    self._write(job, "remove_portals", {"source_id": job["source_id"], "portal_targets": job["snapshot"]["portal_targets"]},
                                lambda op: self._call("remove_from_portals", job["source_id"], op))
                elif state == "portals_removed":
                    self._removed(job)
                    self._write(job, "archive", {"source_id": job["source_id"], "reason": job["archive_reason"]},
                                lambda op: self._call("archive", job["source_id"], job["archive_reason"], op))
                elif state == "archived":
                    self._removed(job)
                    self._archived(job)
                    self._write(job, "create", {"source_id": job["source_id"], "snapshot_hash": job["snapshot_hash"], "address": job["corrected_address"]},
                                lambda op: self._call("create_listing", deepcopy(job["snapshot"]), deepcopy(job["corrected_address"]), op))
                elif state == "created":
                    self._fidelity(job)
                    with self.store._tx() as db:
                        self.store._update(db, job_id, state="fidelity_verified", error=None)
                        self.store._event(db, job_id, "compare", "verified", {"listing_id": job["new_listing_id"], "snapshot_hash": job["snapshot_hash"]})
                elif state == "fidelity_verified":
                    self._removed(job)
                    self._archived(job)
                    self._fidelity(job)  # Recheck immediately before publication.
                    self._write(job, "publish", {"listing_id": job["new_listing_id"], "approval_hash": job["approval_hash"]},
                                lambda op: self._call("publish", job["new_listing_id"], op))
                else:
                    self._check_publication(job)
            except Exception as exc:
                latest = self.store.get_job(job_id)
                if latest["state"] != "needs_reconciliation":
                    # Before approval or in offline mode the job can simply be
                    # resumed. Safety violations after approval need review.
                    blocked = isinstance(exc, SafetyBlocked) and job["state"] in {"ready", "portals_removed", "archived", "created", "fidelity_verified", "publication_pending"}
                    self.store._error(job_id, _safe_error(exc), blocked=blocked)
                if isinstance(exc, RelistError):
                    raise
                raise SafetyBlocked("A verification read failed. No new write was started.") from exc
            return self.store.get_job(job_id)

    def _check_publication(self, job: dict):
        self._fidelity(job)
        result = self._call("publication_status", job["new_listing_id"])
        if not isinstance(result, dict) or result.get("listing_id") != job["new_listing_id"]:
            raise SafetyBlocked("Publication status identity could not be verified.")
        states = result.get("portals", {})
        targets = set(job["snapshot"]["portal_targets"])
        live = result.get("state") == "live" and result.get("verified") is True and bool(result.get("evidence")) and isinstance(states, dict) and set(states) == targets and all(s == "live" for s in states.values())
        with self.store._tx() as db:
            self.store._event(db, job["id"], "publication_status", "live" if live else "pending", result)
            self.store._update(db, job["id"], state="live" if live else "publication_pending", error=None if live else "Publication is not yet verified live on every target portal.")

    def reconcile(self, job_id: str) -> dict:
        with self.store.operation_lock():
            job = self.store.get_job(job_id)
            self._ready()
            if job["state"] == "publication_pending":
                self._guard(job)
                self._source_unchanged(job)
                self._check_publication(job)
                return self.store.get_job(job_id)
            if job["state"] != "needs_reconciliation":
                raise SafetyBlocked("There is no uncertain action to reconcile. Blocked jobs require manual investigation.")
            with self.store._db_lock:
                row = self.store.conn.execute("SELECT * FROM operations WHERE job_id=? AND status='uncertain'", (job_id,)).fetchone()
            if row is None:
                raise SafetyBlocked("No uncertain operation receipt was found. Do not repeat any action.")
            operation = dict(row)
            payload = json.loads(operation["payload_json"])
            try:
                self._guard(job)
                evidence = self._call("reconcile_action", operation["action"], operation["id"], deepcopy(payload))
                if not isinstance(evidence, dict) or not evidence.get("evidence"):
                    raise SafetyBlocked("Reconciliation returned no authoritative evidence.")
                with self.store._tx() as db:
                    self.store._event(db, job_id, operation["action"], "reconciliation_observed", evidence, operation["id"])
                if evidence.get("status") == "applied":
                    self._finish(job, operation["action"], operation["id"], evidence.get("result", {}), reconciled=True)
                elif evidence.get("status") == "not_applied":
                    self._source_unchanged(job)
                    with self.store._tx() as db:
                        db.execute("UPDATE operations SET status='not_applied',result_json=? WHERE id=?", (_json(_audit_details(evidence)), operation["id"]))
                        self.store._update(db, job_id, state=operation["previous_state"], error="Confirmed not applied. An explicit Advance may retry with the same operation key.")
                        self.store._event(db, job_id, operation["action"], "reconciled_not_applied", evidence, operation["id"])
                else:
                    raise SafetyBlocked("The action remains uncertain. No retry is allowed.")
            except Exception as exc:
                self.store._error(job_id, _safe_error(exc))
                if isinstance(exc, RelistError):
                    raise
                raise SafetyBlocked("Reconciliation failed. The action remains uncertain and was not retried.") from exc
            return self.store.get_job(job_id)
