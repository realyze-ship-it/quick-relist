# Read-only segmented date rejection diagnostics

A supported-name candidate must still pass the existing unique input, wrapper,
group and exact label binding gates before any new diagnostic is emitted. Legacy
v1 records and their emission remain unchanged. The separately qualified v2
false-container variant below retains all existing segment and value checks.
A rejection retains `segmented_date_segments_unresolved` and adds static reason
codes to the page limitations. This does not identify the cause of any prior
capture, support a new picker shape, or make date semantics/business rules known.

A `segmented_date_rejection` entry in page `discovery` is explicitly unresolved
structural evidence, not a field value or a resolved date record. It contains:

- One of the five existing `DATE_NAMES` field keys.
- Six element counts: spinbuttons, contenteditable attributes, contenteditable
  attributes exactly equal to `true`, section-list containers, buttons, and
  visible buttons. Counts saturate at `count_cap: 100`; 100 means 100 or more.
- Static failed-predicate codes. At most three segment positions have their own
  failed-predicate codes, and only after a qualified count/container gate.
- `complete: false` and `date_value_observed: false`.

No date/input/segment value, arbitrary attribute string, text, label, identifier,
class string, URL or source identifier is added. Unexpected attributes produce
only a static failed-condition code. Existing rich-text and generic discovery
observations are unchanged; they are not part of this value-free projection.
Repeated root/dialog scans emit at most one diagnostic per known field key, so
there are at most five diagnostic records per page. This does not establish
source completeness or authenticate evidence. It cannot enable a live action.

The Python capture boundary requires exact diagnostic keys, known names and
reason codes, bounded integer counts, at most three ordered segment positions,
consistent segment-reason aggregation, one record per name, and matching page
limitations including the original umbrella code. Extra strings/keys and invalid
claims fail the capture rather than being silently discarded. Existing URL
sanitization and the 16 MB observation limit still apply.

## Synthetic checks

- `PYTHONPATH=tests python -m unittest test_date_diagnostics test_segmented_dates test_segmented_dates_runtime test_table_capture -v`
- The runtime suite executes the actual `READ_PAGE_SCRIPT` against the existing
  synthetic date fixture, with read/write traps and a documented layout shim.
  It tests accepted-record equality, early binding gates, count/structure
  rejection codes, privacy markers, count saturation and repeated-root bounds.
- The canonical DOM runtime suite requires Node and an already-installed jsdom.
  It skips explicitly if either is unavailable; tests do not install packages.
  A DOM simulator is not real Chrome or live Hub qualification.

## Opt-in native Chrome script qualification

`tests/test_segmented_dates_chrome.py` reuses the existing loopback HTML server
and synthetic date fixture. Its focused cases cover:

1. All five accepted date records exactly match the existing saved-record fixture.
2. A segment `aria-readonly` rejection emits its precise static reason while a
   synthetic privacy marker in values, text and a class stays out of diagnostics.
3. Excess segments/buttons saturate counts at 100, and a separate hidden-button
   case reports the precise visibility rejection. Neither reads the rejected
   hidden input's value.
4. The exact false-container v2 variant and rejection of an unrelated node with
   the same false attribute and total count.

The test executes the unmodified `READ_PAGE_SCRIPT` with real Chrome visibility,
uses a disposable profile and checks for DOM/control changes. The URL remains
localhost and is explicitly rejected by the production editor-route parser.
This qualifies the script only, not source/account binding, the full controller,
Windows UI behavior, source completeness or any real Hub operation.

Set `QUICK_RELIST_CHROME_TESTS=1`, `QUICK_RELIST_CHROMEDRIVER` to an existing driver
executable, and optionally `QUICK_RELIST_CHROME_BINARY` to an existing Chrome.
Then run `PYTHONPATH=tests python -m unittest test_segmented_dates_chrome -v` or
`python -m pytest -q --maxfail=1 tests/test_segmented_dates_chrome.py`.
Without opt-in, it skips before a server, profile or browser starts. Missing
Selenium or an existing driver also produces an explicit skip; nothing installs
or downloads automatically. The cloud development check only compiles this file
and verifies the no-opt-in skip. Native execution remains a separate check.


## Exact false-container read-only variant

The legacy `mui-segmented-date-dom/v1` record remains unchanged. The new
`mui-segmented-date-dom/v2` record requires `shape_variant: bound-false-container`.
It is accepted only when the wrapper's entire set of contenteditable-bearing
nodes is exactly the three existing strict spinbutton segments plus its unique
section-list container. That container must have literal `contenteditable="false"`,
be a visible, nonsensitive DIV with both required container classes, and be a
direct child of the already-bound role=group. All three segments must still pass
every original label/order/class/parent/visibility/attribute predicate.

The v2 record explicitly retains container `contenteditable: "false"`,
total editable-attribute count 4, exactly-true count 3, exactly-false count 1,
other-value count 0, and true direct-parent and exact-node-set relationship
flags. Its validator requires all of that evidence plus the unchanged input,
label, wrapper, group and segment checks. Count 4 or the schema marker alone is
insufficient. Conflicting v2 evidence cannot be passed through the v1 path.
Extra attributes elsewhere, extra fifth nodes, empty/true/invalid container
values, nested or unbound containers and malformed segments still reject.

A rejected candidate that passes this exact contenteditable binding can retain
segment diagnostics with count 4. Such diagnostic records carry only the static
`contenteditable_binding: container_false_and_three_true_segments` enum, and the
validator requires the corresponding 4/3 counts and unique container/three
segments. This remains unresolved structural evidence, not a resolved date.

The variant observes raw values exactly; it does not parse, reconcile, renew or
extend dates or mandates. Semantics, business validation and requiredness remain
unverified. Captures and source inventories remain partial and not live-ready.
No writer, editor identity, account binding or live operation is enabled.

Synthetic coverage includes v1 equality, all five v2 records, absent attributes
returning to v1, incorrect values/nodes/relations/counts, strict segment rejection,
privacy markers and unchanged raw-value sanitization. The native localhost
suite adds an exact v2 case and rejects the same count/false value on an unrelated
node, using the existing server/profile framework. Its execution is still opt-in.
