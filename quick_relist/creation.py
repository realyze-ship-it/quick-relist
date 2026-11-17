"""Durable staged draft creation, OFFLINE SYNTHETIC EXECUTION ONLY.

No Hub provider or live qualification is supplied. Normalized unknown fields are
preserved; coverage of business data present only in raw capture remains an
explicit live-qualification gap. Providers must atomically enforce each command,
consume the supplied bytes, and keep authoritative idempotency records. Read-only
reconciliation must never retry a write. Use a separate private local database
from the outer Engine; an incomplete child workflow is never outer not_applied.

Known lifecycle limitation: this store conservatively rejects reuse of any known
source or replacement identity by another parent, including relisting a prior
replacement later. Supporting that legitimate future lifecycle requires explicit
reuse/lineage qualification; this slice never silently relaxes collision guards.
The test seam is not a sandbox against hostile Python code and does not establish
provider authenticity, Hub capabilities, or native Windows runtime qualification.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Protocol

from .media import _prepare_root
from .core import Store
from .mutations import MutationRequest, MutationContractError, canonical, digest


class CreationBlocked(MutationContractError):
    pass


def _require(condition):
    if not condition:
        raise CreationBlocked("Creation evidence is incomplete or inconsistent. Manual review is required.")


@dataclass(frozen=True, slots=True)
class ChildCommand:
    operation_id: str
    parent_operation_id: str
    parent_hash: str
    account_id: str
    agency_id: str
    source_id: str
    source_rxcu: str
    expected_source_revision: str
    ordinal: int
    stage: str
    draft_id: str | None
    expected_draft_revision: str | None
    payload_json: str
    payload_hash: str

    @property
    def payload(self):
        return json.loads(self.payload_json)

    @property
    def fingerprint(self):
        return digest(asdict(self))


class CreationProvider(Protocol):
    """Synthetic contract, NOT a Hub API.

    execute atomically checks account, archived source lineage/revision and exact
    previous draft revision/state. An operation key is permanently bound to one
    command. Upload receipts attest SHA-256/size of bytes actually consumed.
    reconcile is authoritative and read-only; terminal absence excludes late
    commit. inspect is read-only and returns a complete, request-bound draft.
    """
    def preflight(self, command: ChildCommand) -> dict: ...
    def execute(self, command: ChildCommand, content: bytes | None) -> dict: ...
    def reconcile(self, command: ChildCommand) -> dict: ...
    def inspect(self, command: ChildCommand) -> dict: ...


def _windows_private_acl(path, *, protected=True):
    """Verify an owner-only protected DACL on an existing Windows directory.

    The media helper creates this DACL for new directories; unlike a new media
    import, this database is reused, so inheriting an unchecked existing ACL is
    insufficient. Any unsupported ACE/layout fails closed.
    """
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor, owner, dacl = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    get_security = advapi.GetNamedSecurityInfoW
    get_security.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get_security.restype = wintypes.DWORD
    free = kernel.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    equal = advapi.EqualSid
    equal.argtypes, equal.restype = [ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL
    convert = advapi.ConvertStringSidToSidW
    convert.argtypes, convert.restype = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)], wintypes.BOOL
    get_control = advapi.GetSecurityDescriptorControl
    get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    get_ace = advapi.GetAce
    get_ace.argtypes, get_ace.restype = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)], wintypes.BOOL
    owner_rights = ctypes.c_void_p()
    token = wintypes.HANDLE()
    open_token = advapi.OpenProcessToken
    open_token.argtypes, open_token.restype = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL
    current_process = kernel.GetCurrentProcess
    current_process.argtypes, current_process.restype = [], wintypes.HANDLE
    token_info = advapi.GetTokenInformation
    token_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    try:
        _require(get_security(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor)) == 0)
        _require(bool(owner.value) and bool(dacl.value))
        _require(open_token(current_process(), 0x0008, ctypes.byref(token)))
        required = wintypes.DWORD()
        token_info(token, 1, None, 0, ctypes.byref(required))  # TokenUser sizing
        _require(required.value > 0)
        user = ctypes.create_string_buffer(required.value)
        _require(token_info(token, 1, user, required.value, ctypes.byref(required)))
        user_sid = ctypes.c_void_p.from_buffer(user)
        _require(equal(owner, user_sid))
        control, revision = wintypes.WORD(), wintypes.DWORD()
        _require(get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)) and (not protected or control.value & 0x1000))
        _require(convert("S-1-3-4", ctypes.byref(owner_rights)))
        # ACL header: revision/reserved (2), size (2), ACE count (2).
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        _require(count > 0)
        allowed = False
        for index in range(count):
            ace = ctypes.c_void_p()
            _require(get_ace(dacl, index, ctypes.byref(ace)))
            kind = ctypes.c_ubyte.from_address(ace.value).value
            _require(kind in (0, 1))  # plain ACCESS_ALLOWED / ACCESS_DENIED only
            if kind == 0:
                flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                if protected:
                    # The directory must protect future SQLite sidecars too.
                    _require(flags & 0x03 == 0x03 and not flags & 0x0C)
                sid = ctypes.c_void_p(ace.value + 8)
                _require(equal(sid, owner) or equal(sid, owner_rights))
                allowed = True
        _require(allowed)
    finally:
        if token.value:
            close(token)
        if owner_rights.value:
            free(owner_rights)
        if descriptor.value:
            free(descriptor)


class CreationStore(Store):
    def __init__(self, path):
        # Keep DB, journals and lock in a private directory, reject links.
        if str(path) != ":memory:":
            path = Path(path).absolute()
            try:
                _prepare_root(path.parent)
                if os.name == "nt":
                    _windows_private_acl(path.parent)
                for candidate in (path, Path(str(path) + ".operations.lock"),
                                  Path(str(path) + "-journal"), Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                    if candidate.exists() or candidate.is_symlink():
                        details = candidate.lstat()
                        _require(stat.S_ISREG(details.st_mode) and details.st_nlink == 1 and not getattr(details, "st_file_attributes", 0) & 0x400)
                        if os.name == "nt":
                            _windows_private_acl(candidate, protected=False)
                        else:
                            _require(details.st_uid == os.getuid() and not details.st_mode & 0o077)
                fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
                fd = os.open(str(path) + ".operations.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
                if os.name == "nt":
                    _windows_private_acl(path, protected=False)
                    _windows_private_acl(Path(str(path) + ".operations.lock"), protected=False)
            except Exception:
                raise CreationBlocked("A private local creation database is required.") from None
        super().__init__(path)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS creation_bindings(
          operation_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, source_key TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS creation_events(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          operation_id TEXT NOT NULL REFERENCES creation_bindings(operation_id),
          event_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_creation_binding_update BEFORE UPDATE ON creation_bindings
          BEGIN SELECT RAISE(ABORT, 'Immutable creation binding'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_creation_binding_delete BEFORE DELETE ON creation_bindings
          BEGIN SELECT RAISE(ABORT, 'Immutable creation binding'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_creation_event_update BEFORE UPDATE ON creation_events
          BEGIN SELECT RAISE(ABORT, 'Append-only creation journal'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_creation_event_delete BEFORE DELETE ON creation_events
          BEGIN SELECT RAISE(ABORT, 'Append-only creation journal'); END;
        """)


class DraftCreator:
    is_live_ready = False
    qualification = {}
    raw_only_coverage_verified = False

    def __init__(self, store: CreationStore, provider: CreationProvider | None = None):
        self.store, self.provider = store, provider

    def execute(self, request):
        raise CreationBlocked("Live staged creation is unavailable. No action was started.")

    @staticmethod
    def _plan(request):
        steps = [("allocate", {"state": "draft", "source_snapshot_hash": request.snapshot_hash,
                               "portal_targets": list(request.portal_targets)})]
        steps += [("section", {"name": key, "value": value})
                  for key, value in sorted(request.replacement_fields.items())]
        steps += [("upload", {key: value for key, value in asset.items() if key != "local_path"})
                  for asset in request.snapshot["media_manifest"]]
        steps.append(("verify", {"fields": request.replacement_fields,
                                 "media_manifest": [p for s, p in steps if s == "upload"],
                                 "portal_targets": list(request.portal_targets),
                                 "source_snapshot_hash": request.snapshot_hash}))
        return steps

    @classmethod
    def _command(cls, request, ordinal, draft_id, revision):
        stage, payload = cls._plan(request)[ordinal]
        return ChildCommand(digest({"parent": request.fingerprint, "ordinal": ordinal}),
            request.operation_id, request.fingerprint, request.account_id, request.agency_id,
            request.source_id, request.source_rxcu, request.expected_source_revision,
            ordinal, stage, draft_id, revision, canonical(payload), digest(payload))

    @staticmethod
    def _binding(command):
        return {"operation_id": command.operation_id, "request_hash": command.fingerprint,
                "authoritative": True, "account_id": command.account_id,
                "agency_id": command.agency_id, "source_id": command.source_id,
                "source_revision": command.expected_source_revision}

    def _receipt(self, command, response):
        _require(isinstance(response, dict))
        for key, value in self._binding(command).items():
            _require(canonical(response.get(key)) == canonical(value))
        _require(response.get("status") == "applied" and response.get("state") == "draft")
        _require(isinstance(response.get("draft_id"), str) and bool(response["draft_id"].strip())
                 and response["draft_id"] != command.source_id)
        _require(isinstance(response.get("draft_revision"), str) and bool(response["draft_revision"].strip()))
        _require(response.get("previous_draft_revision") == command.expected_draft_revision)
        if command.stage != "allocate":
            _require(response["draft_id"] == command.draft_id and response["draft_revision"] != command.expected_draft_revision)
        if command.stage == "upload":
            _require(canonical(response.get("consumed")) == canonical({k: command.payload[k] for k in ("sha256", "size")}))
        # Store only constrained data, never provider text, exceptions or paths.
        return {**self._binding(command), "status": "applied", "state": "draft",
                "draft_id": response["draft_id"], "draft_revision": response["draft_revision"],
                "previous_draft_revision": command.expected_draft_revision,
                **({"consumed": response["consumed"]} if command.stage == "upload" else {})}

    def _append(self, request, event):
        with self.store._tx() as db:
            if event["phase"] == "applied" and event["ordinal"] == 0:
                for row in db.execute("SELECT event_json FROM creation_events WHERE operation_id != ?", (request.operation_id,)):
                    prior = json.loads(row[0])
                    _require(prior.get("receipt", {}).get("draft_id") != event["receipt"]["draft_id"])
                for row in db.execute("SELECT request_json FROM creation_bindings"):
                    _require(json.loads(row[0])["source_id"] != event["receipt"]["draft_id"])
            db.execute("INSERT INTO creation_events(operation_id,event_json) VALUES(?,?)",
                       (request.operation_id, canonical(event)))

    def _load(self, request, create=False):
        _require(isinstance(request, MutationRequest) and request.action == "create")
        MutationRequest.from_dict(request.to_dict())
        row = self.store.conn.execute("SELECT request_json,source_key FROM creation_bindings WHERE operation_id=?", (request.operation_id,)).fetchone()
        if row is None:
            _require(create)
            with self.store._tx() as db:
                for prior in db.execute("SELECT event_json FROM creation_events"):
                    _require(json.loads(prior[0]).get("receipt", {}).get("draft_id") != request.source_id)
                source_key = digest({"account": request.account_identity, "source": request.source_id})
                _require(db.execute("SELECT 1 FROM creation_bindings WHERE source_key=?", (source_key,)).fetchone() is None)
                db.execute("INSERT INTO creation_bindings VALUES(?,?,?)", (request.operation_id, canonical(request.to_dict()), source_key))
        else:
            _require(row[0] == canonical(request.to_dict()) and row[1] == digest({"account": request.account_identity, "source": request.source_id}))
        ordinal, draft_id, revision, pending, retry, complete = 0, None, None, False, False, False
        for row in self.store.conn.execute("SELECT event_json FROM creation_events WHERE operation_id=? ORDER BY sequence", (request.operation_id,)):
            try:
                event = json.loads(row[0])
                _require(not complete and event["ordinal"] == ordinal and type(event["ordinal"]) is int)
                command = self._command(request, ordinal, draft_id, revision)
                phase = event["phase"]
                if phase == "intent":
                    _require(not pending and not retry and command.stage != "verify")
                    _require(canonical(event) == canonical({"ordinal": ordinal, "phase": "intent", "command": asdict(command)}))
                    pending = True
                elif phase == "applied":
                    _require(pending and not retry)
                    receipt = self._receipt(command, event["receipt"])
                    _require(canonical(event) == canonical({"ordinal": ordinal, "phase": phase, "receipt": receipt}))
                    draft_id, revision = receipt["draft_id"], receipt["draft_revision"]
                    ordinal, pending = ordinal + 1, False
                elif phase in {"not_applied", "retry"}:
                    _require(pending and retry == (phase == "retry"))
                    _require(canonical(event) == canonical({"ordinal": ordinal, "phase": phase, "request_hash": command.fingerprint}))
                    retry = phase == "not_applied"
                elif phase == "complete":
                    _require(not pending and command.stage == "verify")
                    _require(canonical(event) == canonical({"ordinal": ordinal, "phase": phase, "request_hash": command.fingerprint}))
                    complete = True
                else:
                    _require(False)
            except (ValueError, KeyError, IndexError, TypeError):
                raise CreationBlocked("Stored creation journal is invalid. Manual review is required.") from None
        return ordinal, draft_id, revision, pending, retry, complete

    def _result(self, request, state):
        ordinal, draft_id, revision, pending, retry, complete = state
        return {"status": "complete" if complete else "retry_available" if retry else "needs_reconciliation" if pending else "in_progress",
                "ordinal": ordinal, "draft_id": draft_id, "draft_revision": revision}

    def status(self, request):
        with self.store.operation_lock():
            return self._result(request, self._load(request))

    def _checkpoint(self, request, state):
        ordinal, draft_id, revision, pending, retry, complete = state
        _require(draft_id is not None and (not pending or retry) and not complete)
        events = [json.loads(row[0]) for row in self.store.conn.execute(
            "SELECT event_json FROM creation_events WHERE operation_id=? ORDER BY sequence", (request.operation_id,))]
        return {"version": 1, "parent_operation_id": request.operation_id,
                "parent_request_hash": request.fingerprint, "source_revision": request.expected_source_revision,
                "account_identity": request.account_identity, "draft_id": draft_id,
                "draft_revision": revision, "completed_chain_hash": digest(events),
                "next_stage": ordinal, "next_child_hash": self._command(request, ordinal, draft_id, revision).fingerprint,
                "retry_not_applied": retry, "no_pending": True}

    def checkpoint_for_testing(self, request):
        with self.store.operation_lock():
            return self._checkpoint(request, self._load(request))

    @staticmethod
    def _outer_binding(request):
        return {"operation_id": request.operation_id, "request_hash": request.fingerprint,
                "action": "create", "authoritative": True, "evidence": "Synthetic durable creation ledger"}

    def _verify(self, request, state):
        ordinal, draft_id, revision, *_ = state
        command = self._command(request, ordinal, draft_id, revision)
        try:
            response = self.provider.inspect(command)
            expected = {**self._binding(command), **command.payload, "draft_id": draft_id,
                        "draft_revision": revision, "state": "draft", "complete": True,
                        "creation_operation_id": request.operation_id}
            _require(canonical(response) == canonical(expected))
        except Exception:
            raise CreationBlocked("Draft fidelity could not be verified. No completion was recorded.") from None

    def _complete_receipt(self, request, state):
        _require(state[-1] is True)
        self._verify(request, state)
        return {**self._outer_binding(request), "status": "applied", "account_identity": request.account_identity,
                "source_id": request.source_id, "previous_source_revision": request.expected_source_revision,
                "source_revision": request.expected_source_revision, "listing_id": state[1],
                "listing_revision": state[2], "listing_state": "draft"}

    def complete_receipt_for_testing(self, request):
        with self.store.operation_lock():
            return self._complete_receipt(request, self._load(request))

    def progress_for_testing(self, request):
        with self.store.operation_lock():
            state = self._load(request)
            envelope = self._outer_binding(request)
            if state[-1]:
                return {**envelope, "status": "applied", "result": self._complete_receipt(request, state)}
            if state[1] is not None and (not state[3] or state[4]):
                checkpoint = self._checkpoint(request, state)
                return {**envelope, "status": "partial", "checkpoint": checkpoint,
                        "checkpoint_hash": digest(checkpoint)}
            if state[1] is None and state[4]:
                return {**envelope, "status": "not_applied", "terminal": True}
            return {**envelope, "status": "unknown"}

    @staticmethod
    def _validate_media(request):
        try:
            manifest = request.snapshot["media_manifest"]
            _require(len({(a["kind"], a["position"]) for a in manifest}) == len(manifest))
            for asset in manifest:
                content = Path(asset["local_path"]).read_bytes()
                _require(len(content) == asset["size"] and hashlib.sha256(content).hexdigest() == asset["sha256"])
        except Exception:
            raise CreationBlocked("Original media integrity could not be verified. No write was started.") from None

    def advance_for_testing(self, request, *, retry_not_applied=False, expected_checkpoint=None):
        """One synthetic stage only. Never called by the production adapter."""
        with self.store.operation_lock():
            _require(self.provider is not None)
            # Validate every input before allocation; re-read exact upload bytes later.
            _require(isinstance(request, MutationRequest) and request.action == "create")
            MutationRequest.from_dict(request.to_dict())
            self._validate_media(request)
            state = self._load(request, create=True)
            ordinal, draft_id, revision, pending, retry, complete = state
            if expected_checkpoint is not None:
                _require(canonical(expected_checkpoint) == canonical(self._checkpoint(request, state)))
                retry_not_applied = expected_checkpoint["retry_not_applied"]
            if complete:
                return self._result(request, state)
            _require(not pending or (retry and retry_not_applied is True))
            command = self._command(request, ordinal, draft_id, revision)
            if command.stage == "verify":
                self._verify(request, state)
                self._append(request, {"ordinal": ordinal, "phase": "complete", "request_hash": command.fingerprint})
            else:
                try:
                    expected_guard = {**self._binding(command), "non_mutating": True,
                        "archived": True, "archive_reason": "mandate_expired", "portals_removed": True,
                        "portal_targets": list(request.portal_targets), "draft_id": draft_id,
                        "draft_revision": revision, "draft_state": "draft" if draft_id else None}
                    _require(canonical(self.provider.preflight(command)) == canonical(expected_guard))
                except Exception:
                    raise CreationBlocked("Creation prerequisites could not be verified. No write was started.") from None
                content = None
                if command.stage == "upload":
                    asset = request.snapshot["media_manifest"][ordinal - 1 - len(request.replacement_fields)]
                    try:
                        content = Path(asset["local_path"]).read_bytes()
                        _require(len(content) == asset["size"] and hashlib.sha256(content).hexdigest() == asset["sha256"])
                    except Exception:
                        raise CreationBlocked("Original media integrity could not be verified. No upload was started.") from None
                self._append(request, {"ordinal": ordinal, "phase": "retry", "request_hash": command.fingerprint} if retry else
                             {"ordinal": ordinal, "phase": "intent", "command": asdict(command)})
                try:
                    receipt = self._receipt(command, self.provider.execute(command, content))
                except Exception:
                    raise CreationBlocked("Child action outcome is uncertain. Reconcile before continuing.") from None
                self._append(request, {"ordinal": ordinal, "phase": "applied", "receipt": receipt})
            return self._result(request, self._load(request))

    def reconcile_for_testing(self, request):
        """Read remote evidence only; persist resolution but never execute a child."""
        with self.store.operation_lock():
            state = self._load(request)
            ordinal, draft_id, revision, pending, retry, complete = state
            _require(pending and not retry and not complete)
            command = self._command(request, ordinal, draft_id, revision)
            try:
                response = self.provider.reconcile(command)
                _require(isinstance(response, dict))
                for key, value in self._binding(command).items():
                    _require(canonical(response.get(key)) == canonical(value))
                if response.get("status") == "applied":
                    receipt = self._receipt(command, response.get("result"))
                    event = {"ordinal": ordinal, "phase": "applied", "receipt": receipt}
                else:
                    _require(response.get("status") == "not_applied" and response.get("terminal") is True)
                    event = {"ordinal": ordinal, "phase": "not_applied", "request_hash": command.fingerprint}
            except Exception:
                raise CreationBlocked("Reconciliation remains uncertain. No retry is allowed.") from None
            self._append(request, event)
            return self._result(request, self._load(request))
