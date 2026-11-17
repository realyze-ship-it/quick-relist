"""In-memory atomic server model. No real service, credentials or user data."""
from copy import deepcopy
import hashlib
import threading
from quick_relist.creation import DraftCreator
from quick_relist.mutations import canonical


class Crash(BaseException):
    pass


class SyntheticCreationProvider:
    def __init__(self, request):
        self.request = request
        self.source_revision = request.expected_source_revision
        self.account = request.account_identity
        self.archived = True
        self.portals_removed = True
        self.drafts = {}
        self.commands = {}
        self.receipts = {}
        self.calls = []
        self.uploads = []
        self.fail_before = None
        self.fail_after = None
        self.crash_after = None
        self.reconciliation = None
        self.inspection_change = None
        self.lock = threading.RLock()

    def preflight(self, command):
        draft = self.drafts.get(command.draft_id, {})
        return {**DraftCreator._binding(command), "source_revision": self.source_revision,
                "account_id": self.account["account_id"], "agency_id": self.account["agency_id"],
                "non_mutating": True, "archived": self.archived, "archive_reason": "mandate_expired",
                "portals_removed": self.portals_removed, "portal_targets": list(self.request.portal_targets),
                "draft_id": command.draft_id, "draft_revision": draft.get("revision"), "draft_state": draft.get("state")}

    def execute(self, command, content):
        with self.lock:
            self.calls.append(command)
            if command.operation_id in self.commands:
                assert self.commands[command.operation_id] == command
            self.commands[command.operation_id] = command
            assert command.operation_id not in self.receipts, "Blind duplicate write"
            if self.fail_before == command.ordinal:
                self.fail_before = None
                raise TimeoutError("SECRET/local/path")
            assert command.parent_hash == self.request.fingerprint
            assert command.account_id == self.account["account_id"]
            assert command.agency_id == self.account["agency_id"]
            assert command.source_id == self.request.source_id
            assert command.source_rxcu == self.request.source_rxcu
            assert command.expected_source_revision == self.source_revision
            assert self.archived and self.portals_removed
            if command.stage == "allocate":
                assert command.draft_id is None and command.expected_draft_revision is None
                draft_id = "synthetic-draft-" + str(len(self.drafts) + 1)
                draft = {"fields": {}, "media_manifest": [], "portal_targets": command.payload["portal_targets"],
                         "source_snapshot_hash": command.payload["source_snapshot_hash"], "revision": "d0", "state": "draft"}
                self.drafts[draft_id] = draft
            else:
                draft_id = command.draft_id
                draft = self.drafts[draft_id]
                assert draft["revision"] == command.expected_draft_revision and draft["state"] == "draft"
                if command.stage == "section":
                    draft["fields"][command.payload["name"]] = command.payload["value"]
                elif command.stage == "upload":
                    assert isinstance(content, bytes)
                    assert hashlib.sha256(content).hexdigest() == command.payload["sha256"]
                    assert len(content) == command.payload["size"]
                    assert "local_path" not in command.payload
                    self.uploads.append((command.operation_id, content, command.payload))
                    draft["media_manifest"].append(command.payload)
                else:
                    raise AssertionError("Unknown write")
                draft["revision"] = "d" + str(command.ordinal)
            result = {**DraftCreator._binding(command), "status": "applied", "state": "draft",
                      "draft_id": draft_id, "draft_revision": draft["revision"],
                      "previous_draft_revision": command.expected_draft_revision}
            if command.stage == "upload":
                result["consumed"] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            self.receipts[command.operation_id] = deepcopy(result)
            if self.crash_after == command.ordinal:
                self.crash_after = None
                raise Crash()
            if self.fail_after == command.ordinal:
                self.fail_after = None
                raise TimeoutError("SECRET/local/path")
            return result

    def reconcile(self, command):
        if self.reconciliation is not None:
            return self.reconciliation(command)
        if command.operation_id in self.receipts:
            return {**DraftCreator._binding(command), "status": "applied", "result": deepcopy(self.receipts[command.operation_id])}
        return {**DraftCreator._binding(command), "status": "not_applied", "terminal": True}

    def inspect(self, command):
        draft = deepcopy(self.drafts[command.draft_id])
        result = {**DraftCreator._binding(command), "source_revision": self.source_revision,
                  "account_id": self.account["account_id"], "agency_id": self.account["agency_id"], "fields": draft["fields"],
                  "media_manifest": draft["media_manifest"], "portal_targets": draft["portal_targets"],
                  "source_snapshot_hash": draft["source_snapshot_hash"], "draft_id": command.draft_id,
                  "draft_revision": draft["revision"], "state": draft["state"], "complete": True,
                  "creation_operation_id": command.parent_operation_id}
        if self.inspection_change:
            self.inspection_change(result)
        return result
