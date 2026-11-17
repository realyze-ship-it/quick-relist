# Synthetic media metadata and recovery

This is an **unregistered synthetic-only slice**, not a live Hub writer. The
public `HubMediaMetadataWriter.transfer` and `MetadataWorkflow.execute` block
before browser/file access or changes. The existing application, creation
adapter, media-upload writer, and qualified read-only launcher are unchanged.

## What is implemented

`MetadataRequest.from_transfer` binds the existing `MediaTransferRequest` and
its exact modeled consumed-byte receipts to source captions, explicit cover
flags, and source global order. Receipt IDs, byte hashes/sizes, kinds, positions,
request hash, account/agency/source/draft identity and capture bindings must
match. There are no local paths in the metadata request or journal.

A separately pinned target baseline and metadata-stage draft revision are
mandatory. The upload receipt does **not** establish a revision transition or
Hub persistence. The composition Chrome test obtains receipts from real
localhost POSTs of generated PNG bytes, then explicitly initializes a separate
metadata fixture revision. That verifies compatible data binding, not a fully
qualified upload-to-Hub persistence pipeline.

The narrow supported source metadata schema requires exactly `caption` (string)
and `cover` (boolean) for every asset. Missing values are unknown, not empty or
false. Empty strings, spaces, leading/trailing LF newlines and mixed photo /
floorplan global order are preserved exactly. CR, tabs, NUL/control characters
and WebDriver private-use key tokens are rejected instead of silently normalized
by textarea/key handling. Unknown keys, including neutral rotation/tags, block.
The **synthetic contract** allows zero or one cover, including a floorplan; this
is not evidence of Hub's actual cover semantics. No first-image default is used.

Stages are one caption save per asset, one explicit cover selection (including
no cover), then a single global-order save. The fixture uses native textarea,
radio and save-button interactions. Its order editor uses Space / ArrowUp /
Space on a native-focusable sortable wrapper, matching the keyboard instructions
observed passively on Hub. No real Hub drag, reorder, caption, cover or save was
attempted. Caption/cover editing and save controls are invented fixture controls.

## Binding and browser behavior

The explicit `bind_synthetic_metadata_for_tests` seam requires existing Chrome,
exact nonce-bound localhost media route, request/context/epoch markers and the
same window/handle set. It cannot be enabled on a Hub URL. Read-only DOM scripts
validate complete control structure, unexpected fields/roles, visibility and
native disabled fieldsets **before** reading business values. Getter-trap Chrome
tests exercise this boundary. No writer script sets values, dispatches events,
reads application storage, fetches an endpoint or mutates application state.
The fixture's own trusted UI handlers perform its modeled HTTP operations.

After each native clear/key/click, the writer checks the expected editor state,
unchanged saved cards/revision, command and control nodes. It never infers saved
state from a local text field or the displayed order. Only request/command-bound
modeled receipts and complete server-rendered readback qualify the synthetic
stage. Private captions and raw browser errors do not enter reports.

## Durable intent and recovery

`MetadataStore` reuses `CreationStore`'s private directory/ACL/link checks and
portable process lock, adding separate immutable bindings and append-only events.
Use a separate private database. The complete request and service origin/epoch
are bound once, and one draft identity cannot silently receive another request.
Every command has a deterministic operation key, ordinal, exact before/after
hashes and expected revision. Intent is committed before the first local editor
interaction. Each modeled service operation atomically checks the command,
revision, before-state and payload and persists its modeled receipt before
responding. The service model is in-memory and synthetic, not a Hub endpoint.

All local and remote history is replayed and validated. Reused revision tokens,
wrong typed values, identity substitutions, changed media, unexpected future
operations and missing earlier receipts block. A pending `advance_for_testing`
does not reconcile and then write the next stage. It returns pending; a separate
`reconcile_for_testing` observes receipts only. A later explicit advance can
continue after confirmed application. Interaction and post-dispatch readback
failures remain uncertain, with the durable original key retained.

A fresh browser/page and reopened local journal can recover against the **same
still-running authoritative fixture server** without replay. A new server has a
new epoch and cannot resume the old journal. Server-ledger loss is not repaired;
missing evidence stays unknown or blocks. A fresh empty local database cannot
restart against progressed remote state. This slice does not provide durable
server restart recovery, rollback, deletion, or upload retry.

An empty remote history cannot prove that no local intention was lost before a
dispatch. The fixture has no client-journal ownership enrollment. Its modeled
atomic same-key handling prevents duplicate server effects for an identical
command, but this is not general lost-journal recovery or evidence of Hub safety.

An explicit modeled terminal rejection is reported as manual review, not as a
retry opportunity. **No retry method exists**, even for terminal rejection.
Absence of a receipt is never terminal non-application. The conservative draft
binding does not support starting another metadata workflow on that same draft.

## Running checks

Offline contracts and independent regression review:

```
python -m unittest discover -s tests -p 'test_media_metadata*.py' -v
```

Actual native browser checks use the existing Chrome test convention. No browser,
driver or dependency is installed or downloaded:

```
QUICK_RELIST_CHROME_TESTS=1
QUICK_RELIST_CHROMEDRIVER=/path/to/existing/chromedriver
python -m unittest discover -s tests -p 'test_media_metadata_chrome.py' -v
```

Optional `QUICK_RELIST_CHROME_BINARY` selects the existing Chrome executable.
Set variables with your platform's shell syntax. A skipped class is not browser
qualification. Tests cover exact captions/mixed-kind order/cover/no-cover,
server-hashed upload receipt composition, delayed/lost response, actual browser
and local-store restart, terminal rejection, structural/context getter traps,
and service epoch changes. Reports always keep Hub persistence, live support and
full fidelity false, and saved state unknown.
