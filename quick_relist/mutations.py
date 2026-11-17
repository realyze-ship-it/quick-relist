"""Immutable mutation commands and a fail-closed, provider-independent adapter.

There is deliberately no Hub transport, selector, endpoint or credential here.
A provider must implement atomic server-side comparisons of *these* revisions
and identities, not read newer revisions and substitute them. Read-only preflight
is an additional gate, never a substitute for the atomic write precondition.
Qualifications are integration assertions requiring independent review; this
module cannot manufacture Hub support or prove a provider tells the truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol


REQUIRED_ADAPTER_QUALIFICATIONS = frozenset({
    "complete_capture", "account_binding", "required_address_schema",
    "atomic_revision_preconditions", "idempotent_writes",
    "authoritative_reconciliation", "portal_observations",
    "immutable_mutation_requests", "bound_operation_receipts",
    "non_mutating_replacement_validation",
})
ACTIONS = frozenset({"remove_portals", "archive", "create", "publish"})


class MutationContractError(ValueError):
    """A command or provider response does not meet the mutation contract."""


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MutationContractError("Mutation data must be finite JSON.") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, slots=True)
class MutationRequest:
    """A fully bound command; JSON strings avoid mutable nested dictionaries.

    Decoded properties return new copies. Only the address may differ from the
    original business fields. Mandate dates and all unknown fields stay exact.
    The original raw business capture and media manifest are included, rather
    than a lossy subset of fields. Providers must create a new unpublished draft.
    """
    action: str
    operation_id: str
    account_id: str
    agency_id: str
    source_id: str
    source_rxcu: str
    expected_source_revision: str
    portal_targets: tuple[str, ...]
    snapshot_json: str
    snapshot_hash: str
    approved_address_json: str
    approval_hash: str
    replacement_id: str | None = None
    expected_replacement_revision: str | None = None
    archive_status: str = "cancelled"
    archive_reason: str = "mandate_expired"
    mandate_policy: str = "preserve_exact_source"
    replacement_state: str = "draft"
    schema_version: int = 1

    def __post_init__(self):
        if self.action not in ACTIONS or type(self.schema_version) is not int or self.schema_version != 1:
            raise MutationContractError("Unsupported mutation command version or action.")
        if any(not _text(getattr(self, name)) for name in (
            "operation_id", "account_id", "agency_id", "source_id", "source_rxcu",
            "expected_source_revision", "snapshot_hash", "approval_hash",
        )):
            raise MutationContractError("Mutation identity and expected revision are required.")
        if type(self.portal_targets) is not tuple or any(not _text(target) for target in self.portal_targets) or len(set(self.portal_targets)) != len(self.portal_targets):
            raise MutationContractError("Mutation portal targets must be an immutable exact set.")
        if (self.archive_status, self.archive_reason, self.mandate_policy, self.replacement_state) != (
            "cancelled", "mandate_expired", "preserve_exact_source", "draft"
        ):
            raise MutationContractError("Only exact-source recreation and cancelled/mandate_expired archive are supported.")
        try:
            snapshot = json.loads(self.snapshot_json)
            address = json.loads(self.approved_address_json)
        except (TypeError, ValueError) as exc:
            raise MutationContractError("Mutation snapshot and address must be canonical JSON.") from exc
        if not isinstance(snapshot, dict) or not isinstance(address, dict) or not address:
            raise MutationContractError("A complete snapshot and approved address are required.")
        if canonical(snapshot) != self.snapshot_json or canonical(address) != self.approved_address_json:
            raise MutationContractError("Mutation data must use canonical immutable JSON.")
        fields = snapshot.get("fields")
        if (snapshot.get("complete") is not True or not isinstance(fields, dict)
                or not isinstance(fields.get("address"), dict)
                or set(address) != set(fields["address"])
                or snapshot.get("source_id") != self.source_id
                or snapshot.get("rxcu") != self.source_rxcu
                or snapshot.get("account_identity") != self.account_identity
                or snapshot.get("portal_targets") != list(self.portal_targets)
                or digest(snapshot) != self.snapshot_hash):
            raise MutationContractError("Mutation context does not match its immutable source capture.")
        if (not _text(snapshot.get("revision")) or not isinstance(snapshot.get("raw"), dict)
                or not snapshot["raw"] or not re.fullmatch(r"RXCU[0-9]{3,20}", self.source_rxcu)):
            raise MutationContractError("A complete original revision, raw business record and normalized RXCU are required.")
        required = snapshot.get("required_address_fields")
        if (not isinstance(required, list) or not required
                or any(not isinstance(key, str) or key not in fields["address"] for key in required)
                or len(required) != len(set(required))
                or any(not _text(address.get(key)) for key in required)):
            raise MutationContractError("Every required approved-address field must be supplied with an unambiguous schema.")
        manifest = snapshot.get("media_manifest")
        if (not isinstance(manifest, list) or type(snapshot.get("media_count")) is not int
                or snapshot["media_count"] != len(manifest)):
            raise MutationContractError("A complete original media manifest and exact count are required.")
        seen = set()
        for asset in manifest:
            if (not isinstance(asset, dict)
                    or any(not _text(asset.get(key)) for key in ("id", "kind", "local_path"))
                    or type(asset.get("position")) is not int or asset["position"] < 0
                    or type(asset.get("size")) is not int or asset["size"] < 0
                    or not isinstance(asset.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", asset["sha256"])):
                raise MutationContractError("Every original media asset requires identity, order, local source, size and SHA-256.")
            if asset["id"] in seen:
                raise MutationContractError("Original media identities must be unique.")
            seen.add(asset["id"])
        if self.approval_hash != digest({"snapshot_hash": self.snapshot_hash, "address": address, "archive_reason": self.archive_reason}):
            raise MutationContractError("Mutation address/archive approval does not match the source capture.")
        if self.action == "publish":
            if not _text(self.replacement_id) or self.replacement_id == self.source_id or not _text(self.expected_replacement_revision):
                raise MutationContractError("Publication requires a distinct replacement and its exact revision.")
        elif self.replacement_id is not None or self.expected_replacement_revision is not None:
            raise MutationContractError("A replacement identity is only accepted for publication.")

    @property
    def account_identity(self) -> dict:
        return {"account_id": self.account_id, "agency_id": self.agency_id}

    @property
    def snapshot(self) -> dict:
        return json.loads(self.snapshot_json)

    @property
    def approved_address(self) -> dict:
        return json.loads(self.approved_address_json)

    @property
    def replacement_fields(self) -> dict:
        fields = self.snapshot["fields"]
        fields["address"] = self.approved_address
        return fields

    def to_dict(self) -> dict:
        return {name: (list(value) if name == "portal_targets" else value)
                for name in self.__dataclass_fields__ for value in (getattr(self, name),)}

    @classmethod
    def from_dict(cls, value: dict) -> "MutationRequest":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise MutationContractError("Stored mutation request has an unsupported or incomplete schema. Manual review is required.")
        value = dict(value)
        if not isinstance(value["portal_targets"], list):
            raise MutationContractError("Stored portal targets are invalid.")
        value["portal_targets"] = tuple(value["portal_targets"])
        return cls(**value)

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())


def _bound(request: MutationRequest, response: Any):
    if (not isinstance(response, dict) or response.get("operation_id") != request.operation_id
            or response.get("request_hash") != request.fingerprint
            or response.get("action") != request.action
            or response.get("authoritative") is not True
            or not _text(response.get("evidence"))):
        raise MutationContractError("An authoritative response bound to the exact mutation request is required.")


def validate_replacement(request: MutationRequest, response: Any) -> dict:
    """Validate structure only; Engine decides how to report a business decision."""
    _bound(request, response)
    if (response.get("non_mutating") is not True or response.get("verified") is not True
            or type(response.get("valid")) is not bool or type(response.get("decision_required")) is not bool
            or response.get("mandate_policy") != request.mandate_policy):
        raise MutationContractError("Replacement validation must be verified, strictly non-mutating and preserve all source mandate values.")
    return response


def validate_receipt(request: MutationRequest, response: Any) -> dict:
    _bound(request, response)
    if (response.get("status") != "applied" or response.get("account_identity") != request.account_identity
            or response.get("source_id") != request.source_id
            or response.get("previous_source_revision") != request.expected_source_revision
            or not _text(response.get("source_revision"))):
        raise MutationContractError("Operation receipt identity or source revision does not match the intended command.")
    if request.action in {"create", "publish"} and response["source_revision"] != request.expected_source_revision:
        raise MutationContractError("Creation or publication unexpectedly changed the source revision.")
    if request.action == "archive" and (response.get("archive_status"), response.get("archive_reason")) != (request.archive_status, request.archive_reason):
        raise MutationContractError("Archive receipt must confirm cancelled status with mandate_expired reason.")
    if request.action in {"create", "publish"}:
        if not _text(response.get("listing_id")) or response["listing_id"] == request.source_id or not _text(response.get("listing_revision")):
            raise MutationContractError("A distinct replacement identity and revision are required.")
    if request.action == "create" and response.get("listing_state") != "draft":
        raise MutationContractError("Creation must produce an unpublished draft.")
    if request.action == "publish" and (response.get("listing_id") != request.replacement_id
            or response.get("previous_listing_revision") != request.expected_replacement_revision
            or response.get("accepted") is not True):
        raise MutationContractError("Publication receipt does not match the verified draft and revision.")
    return response


def validate_reconciliation(request: MutationRequest, response: Any) -> dict:
    _bound(request, response)
    if response.get("status") not in {"applied", "not_applied", "unknown"}:
        raise MutationContractError("Reconciliation status is unsupported.")
    if response["status"] == "applied":
        validate_receipt(request, response.get("result"))
    elif response["status"] == "not_applied" and response.get("terminal") is not True:
        raise MutationContractError("Not-applied evidence must exclude any later commit of the original request.")
    return response


class MutationProvider(Protocol):
    """No provider is bundled. A live provider needs independently reviewed tests.

    execute MUST atomically enforce source/account identity, both expected
    revisions, source lineage, and applicable lifecycle/portal preconditions.
    Reuse of a key MUST refer to exactly the same command. Create copies all
    fields, raw-supported data and original media without implicit defaults.
    The provider MUST verify SHA-256 and size of the actual media bytes consumed
    for upload, not trust earlier reads/manifest claims. Reject changed bytes
    before committing a replacement. local_path is local-only and MUST NOT be
    sent as remote business metadata. Preserve order, cover, captions and kinds.
    Publication must atomically require the exact validated replacement draft.
    validate_replacement MUST NOT save or create anything, including temporary
    drafts, and must check actual sale/rental requirements with retained dates.
    reconcile is read-only. terminal not_applied means no late commit is possible.
    """
    is_live_ready: bool
    qualification: dict[str, bool]

    def execute(self, request: MutationRequest) -> dict: ...
    def reconcile(self, request: MutationRequest) -> dict: ...
    def validate_replacement(self, request: MutationRequest) -> dict: ...
    def authenticated_identity(self) -> dict: ...
    def read_source(self, rxcu: str) -> dict: ...
    def portals_removed(self, source_id: str) -> dict: ...
    def archive_status(self, source_id: str) -> dict: ...
    def read_listing(self, listing_id: str) -> dict: ...
    def publication_status(self, listing_id: str) -> dict: ...


class GatedHubAdapter:
    """Executable dispatch boundary for an explicitly injected qualified provider."""
    _METHODS = ("execute", "reconcile", "validate_replacement", "authenticated_identity",
                "read_source", "portals_removed", "archive_status", "read_listing", "publication_status")

    def __init__(self, provider: MutationProvider | None = None):
        self._provider = provider

    @property
    def qualification(self) -> dict:
        value = getattr(self._provider, "qualification", None)
        return dict(value) if isinstance(value, dict) else {}

    @property
    def missing_capabilities(self) -> tuple[str, ...]:
        missing = [key for key in sorted(REQUIRED_ADAPTER_QUALIFICATIONS) if self.qualification.get(key) is not True]
        missing.extend("provider." + name for name in self._METHODS if not callable(getattr(self._provider, name, None)))
        if getattr(self._provider, "is_live_ready", False) is not True:
            missing.insert(0, "qualified_provider")
        return tuple(missing)

    @property
    def is_live_ready(self) -> bool:
        return not self.missing_capabilities

    @property
    def reason(self) -> str:
        return "Hub mutation connection is unavailable. Missing verified capabilities: " + ", ".join(self.missing_capabilities) + ". No action was started."

    def _read(self, method: str, *args):
        if not self.is_live_ready:
            raise MutationContractError(self.reason)
        return getattr(self._provider, method)(*args)

    def authenticated_identity(self): return self._read("authenticated_identity")
    def read_source(self, rxcu): return self._read("read_source", rxcu)
    def portals_removed(self, source_id): return self._read("portals_removed", source_id)
    def archive_status(self, source_id): return self._read("archive_status", source_id)
    def read_listing(self, listing_id): return self._read("read_listing", listing_id)
    def publication_status(self, listing_id): return self._read("publication_status", listing_id)

    @staticmethod
    def _check_request(request: MutationRequest):
        if not isinstance(request, MutationRequest):
            raise MutationContractError("An immutable mutation request is required.")
        # Revalidate at every dispatch, including when a caller bypassed the
        # dataclass constructor or resumed a command from persistent storage.
        MutationRequest.from_dict(request.to_dict())

    def validate_replacement(self, request: MutationRequest):
        self._check_request(request)
        return validate_replacement(request, self._read("validate_replacement", request))

    def _execute(self, action: str, request: MutationRequest):
        self._check_request(request)
        if request.action != action:
            raise MutationContractError("The immutable mutation request does not match this action.")
        return validate_receipt(request, self._read("execute", request))

    def remove_from_portals(self, request: MutationRequest): return self._execute("remove_portals", request)
    def archive(self, request: MutationRequest): return self._execute("archive", request)
    def create_listing(self, request: MutationRequest): return self._execute("create", request)
    def publish(self, request: MutationRequest): return self._execute("publish", request)

    def reconcile_action(self, request: MutationRequest):
        self._check_request(request)
        return validate_reconciliation(request, self._read("reconcile", request))
