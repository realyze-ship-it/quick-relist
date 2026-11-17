# Staged creation and recovery

This is development code with offline synthetic providers, not a working Hub
creation connection. The desktop app and its default unavailable adapter are
unchanged. No live provider, create/save/upload selector, endpoint, credentials
or enable-live setting is supplied.

`creation.py` implements a durable child workflow for one immutable complete
`MutationRequest(action="create")`. `creation_engine.py` adds an optional Engine
API for incomplete creation and explicit continuation. Its tests exercise the
whole synthetic remove/archive/create/compare/publish lifecycle using the real
local coordinator, rather than treating creation as a single in-memory copy.

## One parent request, several durable stages

1. Verify the request and original media before allocating anything.
2. Allocate a distinct unpublished draft. Retain its bound identity and revision
   durably before any later field or media operation.
3. Copy each normalized top-level business section, retaining nested values and
   unknown normalized fields. Only the explicitly approved address replaces its
   original section. Dates are not interpreted or renewed.
4. Upload the original media in manifest order. The command carries business
   metadata and byte identity; the actual consumed bytes must match the stored
   SHA-256 and size. Local file paths are not remote business metadata.
5. Read and compare the entire normalized replacement, media metadata, targets,
   identity, lineage, exact revision and unpublished draft state before issuing
   a complete creation result. The outer workflow still performs its separate
   fidelity check before publication.

Each child has a deterministic operation key and immutable payload. Its command
binds the parent fingerprint, source/account context, expected source revision,
stage ordinal, and the previous draft identity and revision. The local journal
commits intent before dispatch and records only validated receipt fields.
Canonical JSON comparisons distinguish values such as `true` and `1`.

The synthetic provider models atomic source/account/draft comparisons, lifecycle
prerequisites, idempotency and authoritative receipts. Those modeled guarantees
must not be attributed to Hub. A read followed by a browser click is not an
atomic write precondition.

## An incomplete draft is not a failed allocation

A timeout or process death can happen after a remote write but before its local
receipt is committed. The next action is read-only reconciliation of the same
child command. An applied result recovers the draft or stage without another
write. An unknown result remains blocked. Only authoritative terminal
`not_applied` evidence, excluding a late commit, allows an explicit retry with
the exact same child key and request.

An existing partial draft never makes the parent creation `not_applied`. Normal
Engine `advance` stays blocked while creation is uncertain. The optional
`CreationEngine.resume_creation(job_id)` API obtains fresh read-only progress,
rechecks the exact parent and prerequisites, commits an intention containing
the exact checkpoint, then asks the provider to continue. The coordinator must
reject a stale checkpoint under its lock, and each remote child still needs its
own atomic revision check. Reconciliation itself never continues a write.

The checkpoint binds the completed child chain, next command, draft identity and
revision, and any terminal retry eligibility. Missing local state is not proof
that no draft was created. Ambiguous or damaged records require investigation.
There is no automatic rollback, deletion, cleanup of partial drafts, fresh
allocation after a partial result, or silent substitution of a newer revision.

## Local state and test execution

Use a separate private local creation database, not the outer job database or a
network share. It retains the immutable parent source capture, local media paths,
child requests and required recovery identities. It is not encrypted by this
application. Protect and back up the PC and its local data. State and lock files
must not be links or shared-access files. Platform-specific permission and lock
behavior requires Windows verification as well as portable tests.

The ordinary `DraftCreator.execute` entry point refuses. Explicitly named
`*_for_testing` methods exercise the coordinator with a supplied synthetic
provider. These APIs and provider assertions are not a security boundary
against arbitrary Python code or proof of real service identity. The offline
fixtures never use a browser, account, network request or real listing data.

Run the focused portable checks from the repository root:

```
python -m unittest discover -s tests -p 'test_creation*.py' -v
```

Full regression and native Windows qualification are separate checks. Tests use
synthetic records, temporary files and controlled failures. Keep all real
records, photographs, session data and local databases out of the repository.

## Still required before connecting Hub

- Complete source, nested-record and media capture and full normalized coverage
  of transferable data present only in the raw backup.
- Real sale/rental creation, mandatory-field and address/suburb validation,
  account/draft identity, persistence/autosave and upload-completion behavior.
- Actual atomic revision prerequisites, operation-key semantics, authoritative
  reconciliation and durable remote draft/asset identity recovery.
- Full metadata, ordering, captions, cover and source-to-upload attribution.
- Explicit decisions if new mandate facts are required. Preserving an expired
  source mandate is not evidence that a new listing is valid.
- Controlled end-to-end qualification and explicit authorization for particular
  live test listings. Passing offline recovery tests does not authorize one.

The conservative all-history source/draft collision guard can also block a later
legitimate relist of a replacement created by an earlier workflow. Reuse requires
separate lifecycle qualification; do not bypass the guard to make a test pass.
