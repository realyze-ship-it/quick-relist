# Synthetic media file transfer, not a Hub provider

`quick_relist/hub_media_writer.py` implements ordinary Selenium `send_keys` to
native file inputs. `HubMediaWriter(driver).transfer(request)` always refuses
before browser or filesystem access. There is no live switch, provider
registration, creation-coordinator hookup, UI change or launcher change.

## Request and scope

The immutable request binds the parent operation/hash, operation/account/agency,
source ID/RXCU/revision/capture hash, approved-address hash, target draft/revision
and media page. Each file has an explicit source asset ID, photo/floorplan kind,
global source position, exact size/SHA-256, absolute local path and immutable JSON
metadata. An exact per-file source evidence object must agree with the enclosing
context. These caller-supplied bindings are consistency checks, **not source
provenance authentication**. The factory only operates on synthetic fixtures.
Unclassified ZIP imports, filenames, ZIP indices and shared-grid headings cannot
satisfy an independently verified source mapping. No adapter promotes imported
ZIP entries to qualified source media.

This conservative slice supports only PNG/JPEG files and an explicitly empty
initial target grid, receipt list and selected-file lists. It requires the exact
control baseline. Nonempty destinations are rejected because thumbnail URLs
cannot establish duplicate byte identity. Manifest duplicate IDs, content hashes,
paths and local file identities are rejected. Limits are 100 files, 32 MiB per file, 128 MiB total bytes, 8 KiB metadata per
file and 128 KiB total metadata (plus 2 KiB per-file evidence). Local hashing
reads 64 KiB chunks. Local regular
files are hashed with identity/stat checks. Cross-API path/handle checks compare
identity, size, mtime and available birthtime. Ctime is compared separately within
path stat and within handle fstat, retaining change detection without assuming
matching Windows ctime semantics across those APIs. Symlinks/reparse components are
refused. There are no uploads from existing user sample photographs.

Each input is selected at most once, with its same-kind files in source order.
**Mixed photo/floorplan source order is not reproduced in the shared grid.** The
plan retains original positions; the report always includes
`global_order_unqualified`. Tests explicitly exercise an interleaved manifest.
No reset, clear, repeated multi-input selection, ordering, rotation, caption,
cover, tagging, deletion or cleanup interaction is implemented. Supplied metadata
is retained in the immutable request and always reported as unsupported. The
adapter never claims that these fields were transferred.

## Browser binding and interaction

`bind_synthetic_media_for_tests` is the sole execution factory. It consumes a
fresh 64-hex nonce, requires Chrome, an exact localhost/127.0.0.1 HTTP port and
nonce/draft/media path, exact synthetic/context/request markers, and pinned
window handle/set and input element references. These checks remain inside the
executor; no callback/selector can substitute a live origin. Markers are checked
before file metadata is read. Unknown controls, ARIA-value widgets, fallback role
tokens, ambiguous headings, disabled controls/fieldsets, dialogs and iframes are
rejected. This test seam is not a security boundary against hostile Python code.

The two inputs are scoped through the exact `Listing images` / `Floor plan
images` H6 and their `MuiBox-root` / `rh__fileInput` nesting. Both require the
observed accept string and multiple flag, absent id/name/aria-label/role/hidden/
style/tabindex and enabled visible structure. The **single shared grid** is never
classified by proximity to those headings. This is an intentionally small,
media-only synthetic document; unrelated real Hub form fields are unsupported.

The writer only reads DOM or File references through scripts. An asynchronous
read hashes `File.arrayBuffer()` contents using SHA-256 and checks the exact
selected metadata, order and local manifest bytes after normal WebDriver
selection. Full selected-list name/size/type/timestamp metadata is validated before
any asynchronous byte reads. The byte-reader rechecks exact nonce/context/request
markers before File references, then rechecks metadata and bounds before allocating
array buffers. Receipts, control snapshots and File metadata comparisons use
canonical JSON so booleans cannot substitute for numeric positions or sizes. It never assigns files/values, dispatches events, injects application
state, reads storage or calls an upload API. All local files are preflighted
before the first send and the current batch is hashed immediately before its
selection. A path can still change between checking it and WebDriver opening it;
actual selected bytes are checked afterward. There is no atomic upload or
protected staging guarantee. A mismatch after selection remains uncertain,
because the application's change handler may already have consumed those bytes.

## Real browser transfer evidence in the synthetic application

The fixture's own trusted file-input change handler POSTs selected bytes to its
nonce-scoped localhost HTTP server. That server bounds the input, hashes the
**received body**, verifies kind/size/hash against its bound manifest, rejects
repeated asset IDs, and returns a request/asset/upload-ID/byte-bound receipt.
The fixture renders receipts and cards in its one shared grid. The writer reads
and validates these exact receipts and cards; counts or thumbnails alone never
prove success. Async completion can be observed later without selecting again.
These are deliberately **modeled local-server receipts**, not a discovered Hub
endpoint or evidence of Hub persistence, deduplication, auth or idempotency.

## Uncertainty and recovery

A writer can attempt transfer once only, even after a preflight failure. Attempt
IDs are recorded before `send_keys`; timeout, exception or KeyboardInterrupt
stops without retry. `observe()` is read-only and may recover exact selected-byte
and modeled receipt evidence on the same still-bound page/writer. No inferred
absence is terminal; no automatic upload retry follows missing receipts. A
refresh/new process/window/node replacement requires manual review, not rebinding
and retrying an interrupted operation. There is no durable process-restart
recovery or integration with the creation journal in this slice.

Reports contain static status/codes, asset IDs and fidelity gaps, never paths,
filenames, caption contents, raw browser errors or image bytes. `saved` is unknown
(`None`); live support, Hub persistence verification and full fidelity remain
false. `upload_state_uncertain` remains true after any attempted selection, even
when the local model has complete receipts. This deliberately distinguishes
synthetic receipt completion from any Hub saved-state claim.

## Checks

Offline stdlib contracts:

```
python -m unittest discover -s tests -p 'test_hub_media_writer*.py' -v
```

Actual browser qualification requires an existing Selenium installation, Chrome
and matching chromedriver. The test does not install/download anything and uses
a disposable profile and generated one-pixel PNGs, never a signed-in profile.

```
QUICK_RELIST_CHROME_TESTS=1
QUICK_RELIST_CHROMEDRIVER=/path/to/existing/chromedriver
python -m unittest discover -s tests -p 'test_hub_media_writer_chrome.py' -v
```

Set environment variables using your OS shell syntax; optional
`QUICK_RELIST_CHROME_BINARY` selects the executable. Missing opt-in or driver is a
skip, not a browser pass. Chrome checks include actual received bytes/trusted
changes, async recovery, disabled/ambiguous/unknown controls, wrong context,
missing/wrong receipts and source-file changes before selection. Offline doubles
also exercise interruption, no retries, byte mismatch and private error redaction.

No live upload selection, completion, persistence, floorplan-specific behavior,
server identity or full-fidelity media behavior has been qualified. Only the
native uploader structure was observed read-only on Hub. Do not relax the test
factory into a production provider.
