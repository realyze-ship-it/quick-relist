# Concrete field transfer, with no live provider

`quick_relist/hub_form_writer.py` implements browser field interactions, not a
complete recreation, draft creation, relist adapter or end-to-end workflow. The
normal `HubDraftFormWriter(driver).transfer(request)` entry point returns
`live_provider_unqualified` without reading or touching the driver. It is not
wired into the app, Engine or launchers. There is no enable-live flag.

## Partial immutable request and report

`FormTransferRequest` binds a tuple of `FieldTransfer` values to operation,
account/agency, source ID/RXCU, source revision/capture hash, approved-address
hash, target draft ID/revision and editor page. These bindings resemble
`MutationRequest`, but intentionally do not forge its required complete capture
or claim to enforce revisions. JSON strings make values immutable. Construction
checks the known field/value shapes. A future adapter must establish provenance
and authorization independently; a caller-supplied hash is not authentication.

A field contains its exact captured source value and exact expected draft
baseline. Only the five explicitly allowlisted address text fields can receive
an override in the hashed approved-address object. All other fields, especially
mandate dates, are copied from source without conversion or renewal. Suburb
selection is unsupported. This is not a complete address schema.

`TransferReport` reports static codes and field keys, never source values or
arbitrary browser errors. It distinguishes exact verified fields, completed
edits, attempted fields and an uncertain current field. On an exception it stops
without rollback or retry. One writer can be used once only, including after a
preflight failure. `saved` stays unknown (`None`), `persistence_verified` is false,
and `commit_action_clicked` is false. An exact field readback is not evidence
that the server persisted a change, or that it did not.

## Concrete supported handlers

- Text inputs use allowlisted exact names/IDs; count inputs use the seven exact
  `feature-*-input` IDs, observed type=text and absent name. Text is not trimmed
  or numerically normalized. Unsupported keyboard-control/private-use strings
  are rejected, not silently changed.
- Textareas use ordinary `clear` and `send_keys`. Marketing header is explicitly
  a textarea, preserves multiline content, and additionally requires the exact
  ID/name and its label. Neither source line endings nor whitespace are silently
  normalized; if browser behavior changes them, exact readback fails. Brochure requires the unique visible
  observed ID and exact associated label. Hidden measurement textareas are never
  written; currently their presence is conservatively rejected as unsupported.
- Four raw checkbox states require exact names/types, observed containing label,
  `MuiCheckbox-root` and `PrivateSwitchBase-input`. `address_hidden=false` remains
  false despite the “Show address on web” label. Five sale-state radio inputs
  require the observed radio-group/label/`MuiRadio-root` nesting and exact code
  sequence. A source with no selected radio is unsupported, not guessed.
- Nine named MUI selector bases require the visible named combo followed by its
  associated named input, with the observed aria-hidden/tabindex/type shape.
  Native input values are read only after validating that association and are
  never assigned. Selection uses a normal combo click and an exact code/text
  option click, then reads back both. Every changed requested option is checked
  before field edits; options are checked again immediately before selection.
- Five date controls use the existing strict `date_capture.py` structure and
  validation. Only their visible ordered Year/Month/Day segments are clicked
  and typed with WebDriver select-all/keys/Tab. Hidden input values are never
  assigned. Each completed part and all earlier parts are checked immediately;
  the final raw input and all ordered text/value/valuetext components must equal
  the source exactly. Clamping, format mismatch and malformed pickers stop.

The DOM scripts only read state. Writing uses ordinary WebDriver `click`,
`clear` and `send_keys`; no property setter or dispatched JavaScript event is
used by the writer. Full supported form state, constraints and actual element
references are checked around interactions, including unrequested controls.
Unknown controls are rejected without reading their values. Sensitive contexts
are rejected before a value read. No script examines storage, cookies, APIs,
network responses, files or credentials.

## Explicitly test-only execution binding

`bind_synthetic_fixture_for_tests(driver, request, nonce=...)` is the sole
execution factory. It requires Chrome and a fresh 64-hex nonce, an exact
HTTP localhost/127.0.0.1 port and nonce/draft/page route, a synthetic-only document
marker, an exact context marker, unchanged pinned window/handle set and exact
initial baseline. These checks remain in the private executor itself; passing
another origin to it cannot enable Hub. The initial nonce is consumed even if
binding fails, and no alternate root locator/provider guard can be injected.

The test seam is not a security boundary against hostile code in the same
Python process. The fixture and its markers are test evidence, not authenticated
Hub account/draft identity. No future provider registration API exists yet.
A reviewed live provider needs its own identity, authorization, persistence and
concurrency contract; this localhost test seam must not be relaxed to add it.

The fixture has application-controlled state updates and input/change/click
counters. Its event handlers update their own model and render it back; the
writer does not inject React state. This is MUI-like behavior, not MUI itself.
The hidden-value trap fixture fails if an unknown password value is read.
A dependent-field reset fixture changes another field when an option is chosen;
the writer must stop instead of accepting the new baseline.

## Running checks

From the repository root:

```
python -m unittest discover -s tests -p 'test_hub_form_writer.py' -v
```

For actual Chrome, use an existing Selenium installation, Chrome installation
and matching chromedriver. No dependencies or drivers are installed by tests:

```
export QUICK_RELIST_CHROME_TESTS=1
export QUICK_RELIST_CHROMEDRIVER=/path/to/existing/chromedriver
python -m unittest discover -s tests -p 'test_hub_form_writer*.py' -v
```

Set the variables using the syntax appropriate to the operating system.
`QUICK_RELIST_CHROME_BINARY` is optional. The opt-in test creates a disposable
Chrome profile and a fresh nonce-bound localhost HTTP fixture for each case.
It never uses an existing browser profile or navigates to Hub. Without opt-in
it reports a skip, not a browser qualification pass.

## Evidence still missing before live use

1. All actual field keyboard/clear/change/blur behavior and persistence semantics
   are unqualified. No Save-labelled control was found in the inspected tabs;
   absence of that label does not establish absence of autosave.
2. Only the property-type dropdown's open menu structure was observed: dynamic
   combo aria-controls, presentation/MUI popup, paper, UL listbox and LI options
   with data-value. The other eight closed base controls were observed, but their
   menus/options have **not** been independently verified. All nine use the
   property-style menu in synthetic tests only. Office and listing type were
   disabled in the observed source and are rejected if disabled in a target.
3. Date keyboard semantics, calendars, locale, requiredness, validation and
   server acceptance are not known. No date parsing or renewal is invented.
   Passing the controlled fixture proves only the fixture behavior.
4. Complete source capture, sale/rental required-field branches, selected suburb
   identity, related parties/dialogs, rich-text formatting, media/file handling,
   account ownership, authorized target draft identity and draft creation remain
   unsupported here.
5. There are no save/create/archive/publish handlers or outcome assertions.
   There is no server CAS, idempotency, operation receipt or authoritative
   reconciliation. Preflight plus Selenium events is not atomic. Concurrent
   changes can happen between reads and events, and the browser can fail after
   changing a field. A changed field might already have autosaved in a live app.

Do not infer live readiness, complete capture, replacement fidelity, publication
or safe archive from any synthetic result. Independently qualify each future
stage before connecting it to the app workflow.
