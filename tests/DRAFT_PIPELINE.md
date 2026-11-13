# One-service synthetic draft pipeline

This is an unregistered development composition, not a live Hub provider. The
public `HubDraftPipeline.transfer` and `DraftPipeline.execute` refuse before
accessing the browser or dispatching an action. The desktop app and launcher
remain unchanged. The pipeline starts from an explicitly pinned **existing
unpublished draft**; it does not allocate a draft, remove portals, archive,
publish, or prove a complete original listing was captured.

## What is connected

One immutable `DraftPipelineRequest` binds selected supported text fields across
up to three editor pages, the full upload manifest, source/account/agency/draft
identities, source capture and parent-operation bindings, and a canonical
approved text-address map. Every approved address key must be supported by the
existing field writer and present in the details request. Each form carries the
exact allowed projection and its existing per-page approval hash. Unsupported
keys, including suburb selection, reject the request; none are silently dropped.
Non-address forms retain the empty projection and its hash. Source values remain
separate from approved overrides.

The composition uses the **existing** native form writer, file uploader and
metadata workflow. One localhost service receives native form-event values,
hashes actual browser-uploaded file bodies, and handles native metadata actions.
It retains one draft identity, source binding and continuous receipt/revision
history. The fixture is not a mock that copies a request directly into success:
its browser test must submit the actual field values, file bytes and caption /
cover / ordering choices through the same existing handlers.

The selected form slice is text-only. Other standalone field handlers, rich
text, nested records, dates, suburb and general source completeness are not
qualified by this integration. The service models staged field persistence only
when the received selected fields exactly match the immutable command. This is
not evidence of Hub autosave behavior.

Uploads retain the existing empty-target, all-assets batch limitation. The
service records the actual arrival order, consumed hashes, upload identities,
explicit empty captions and no-cover initial state. Only after all expected
bytes have arrived does it issue the upload-stage receipt. The metadata request
is then derived from **that upload snapshot and revision on the same service**,
not a new caller-invented baseline or independently initialized server. Metadata
uses the existing caption, explicit cover/no-cover and global keyboard ordering
workflow. Final readback compares all selected saved fields and the exact media
manifest with the request. The report remains partial/synthetic: full fidelity,
Hub persistence, allocation and live-support flags are always false.

## Concurrency and interruption

Every stage has a deterministic key, ordinal, expected revision and bound outer
receipt. The synthetic service pins the rendered stage token to an immutable
command. Before mutation it checks the complete authoritative history and exact
expected revision under its lock. It does not create a new command using whatever
revision happens to exist when a POST arrives. These modeled CAS and ledger
semantics are not discovered Hub capabilities.

A private SQLite journal reuses `MetadataStore` / `CreationStore` permission and
process-lock safeguards. The outer process lock covers durable intent, browser
dispatch and readback. Existing metadata public APIs still acquire that same
lock; narrow private under-lock helpers allow composition without releasing it
or adding a public bypass flag. Tests exercise competing outer and inner calls
from a separate Store instance while the first external effect is held.

Before the first metadata interaction, the inner binding is persisted and its
identity/hash enrolled durably in the outer journal. An absent or changed inner
binding is never recreated during reconciliation. Explicit reconciliation
re-derives its request from validated same-service upload history and checks the
inner receipt progress. A lost inner intent/receipt cannot silently restart the
metadata stage.

A pending `advance_for_testing` returns without navigating or dispatching.
Reconciliation is a separate, read-only external operation. Field pages render
a command-bound receipt only after a successful service response. The uploader
stays on its page until exact consumed-byte receipts are observed or a bounded
wait expires; metadata similarly polls read-only reconciliation on its current
page. Timeout/partial outcomes remain uncertain and do not navigate away during
the action or automatically repeat input/file selection. A later explicit
reconciliation may inspect the authoritative state.

A browser shutdown and reopened local journal can recover completed stages from
the same still-running service without retyping fields or reuploading images.
Partial uploads cannot be resumed by this slice and remain for manual review.
Missing receipts are never terminal non-application. There is no retry method,
rollback, deletion, durable service-restart recovery or client-journal ownership
enrollment. Fresh local state cannot accept an already progressed service, but
empty remote history cannot prove that a pre-dispatch local intention was never
lost. The service is an in-memory synthetic model, not a production authority.

## Verification

Offline coordinator faults and independent review:

```
python -m unittest discover -s tests -p 'test_draft_pipeline*.py' -v
```

Opt-in real Chrome composition uses the existing runtime only:

```
QUICK_RELIST_CHROME_TESTS=1
QUICK_RELIST_CHROMEDRIVER=/path/to/existing/chromedriver
python -m unittest discover -s tests -p 'test_draft_pipeline_chrome.py' -v
```

Optional `QUICK_RELIST_CHROME_BINARY` selects the existing browser. No dependency,
driver or browser is installed. All test fields and one-pixel images are generated
synthetic data. A skipped browser class is not native qualification.

CI also runs the integrated and metadata browser checks using the Ubuntu
runner's existing Chrome and ChromeDriver. Missing executables fail that step;
the tests do not download a browser or driver. This is Linux browser evidence,
not Windows qualification. The target Windows runtime still needs its separate
focused browser checks, full regression suite and compilation pass.

The remaining production boundary is explicit: this is a composed **existing
synthetic draft** pipeline, not an implementation of `CreationProvider` or
`GatedHubAdapter`. The older creation coordinator uses per-asset stages while
this qualified upload component uses one empty-target batch. Integrating real
allocation, complete capture, field coverage, upload recovery and service
revision/idempotency evidence is still required before a named live pilot can
be considered. No test result supplies user authorization for real changes.
