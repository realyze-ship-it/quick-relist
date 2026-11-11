# Read-only segmented date rejection diagnostics

A supported-name candidate must still pass the existing unique input, wrapper,
group and exact label binding gates before any new diagnostic is emitted. All
acceptance predicates, accepted records and raw value extraction are unchanged.
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
  failed-predicate codes, and only after the original exact count/container gate.
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
and synthetic date fixture. Its three focused cases cover:

1. All five accepted date records exactly match the existing saved-record fixture.
2. A segment `aria-readonly` rejection emits its precise static reason while a
   synthetic privacy marker in values, text and a class stays out of diagnostics.
3. Excess segments/buttons saturate counts at 100, and a separate hidden-button
   case reports the precise visibility rejection. Neither reads the rejected
   hidden input's value.

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
