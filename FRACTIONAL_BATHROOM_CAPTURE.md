# Fractional bathroom display observations

This is bounded read-only source observation, not permission to recreate or
publish a listing. It does not change the room parser, field writer, transfer
planner, comparison guards or any live-readiness gate.

## Evidence and scope

Native DOM inspection found an enabled text field with a positive non-integral
ASCII decimal bathroom display, and two separately numbered bathroom blocks
in the open dialog. Both blocks had the already supported sectioned structure
and a rendered Description textarea, with a separate hidden readonly measuring
textarea. The second block and descriptions were not all exposed in the initial
accessibility viewport. This is evidence for the DOM structure only. It does
not establish rounding, flooring, ceiling, truncation or any display-to-group
conversion rule. No real source value is included in this document or tests.

Only the exact existing Bathroom descriptor can use the additional count
format: ASCII digits, one dot, ASCII digits, at most 128 characters, finite,
positive and non-integral under both JavaScript and Python number checks. The
number checks classify the input only; their numeric result is not saved or
used to predict a room-group count. Signs, exponent notation, commas,
whitespace, incomplete decimals, whole-valued decimals, other categories and
unsupported shapes remain unqualified. Existing unsigned integer processing
and integer record shapes are unchanged. The probe response must explicitly
include a null `count` alongside the typed decimal observation; missing or
conflicting count metadata cannot qualify initially or during a recheck.

## Separate display and group observations

For this bathroom format only, a successfully read dialog source has:

- `feature_count: null`, so it cannot masquerade as the existing integer guard.
- `feature_count_observation: {format: "positive_nonintegral_decimal", raw: ...}`,
  preserving the exact display string privately, including leading/trailing
  zeroes. It is not a diagnostic or expected cardinality.
- `observed_room_group_count`, the actual length of the captured group list.
- `display_count_relationship_verified: false`.

The original details-page field remains unchanged. The raw display is bound
through the original main-field hash. Only 1–200 supported contiguous numbered
groups qualify for bounded observation, independently of the decimal. The
existing production parser's smaller DOM/field limits and unsupported-shape
markers still apply. Truncated observations remain pending and stop the
fractional path. There is no fallback from a damaged sectioned room to an
arbitrary description-only group.

The new count fields are private source observations, included in the existing
capture/page integrity hashes and local persistence. Summary observations
include only actual group cardinality and an unverified-relationship flag;
existing failure codes and static messages remain unchanged and contain no
raw display value. Group mapping, source completeness, account/fidelity and
live-readiness flags remain false.

## Unchanged safeguards

Source binding, pinned tab set, exact route/reference and main-field stability
are required. The exact raw display and format must match at every existing
opener recheck and positioning script. The existing leaf caption, geometry,
hit-test and optional single centered-scroll logic is unchanged. There is one
ordinary opener click, no JavaScript click, and at most one Escape. A verified
close plus unchanged reference/main fields is required before accepting the
captured dialog. Cancellation, uncertain opens/closes and changed context keep
the existing stop/no-retry behavior. The calls are not atomic, and unchanged
DOM fields do not prove an absence of server writes.

Inventory retains the complete raw dialog as an unresolved related record.
Replacement review intentionally still rejects its non-integer
`source.feature_count` under `room_group_structure_unresolved`; it does not
silently compare these groups as if their relationship were established. The
read-only transfer planner retains the unresolved related record. Its existing
raw count-text candidate behavior is not changed or newly qualified, and no
writer is enabled.

## Synthetic qualification

`tests/test_fractional_bathroom_capture.py` exercises the exact production
state/position scripts in jsdom, the unchanged production room parser, and the
actual room/matched orchestrators with synthetic driver responses. It covers
strict formats/categories, unchanged integer observations, raw-format changes,
identity/main-field guards, independent group cardinalities, structure and size
failures, hidden input exclusions, cancellation, one-click/one-Escape failures,
JSON/SQLite persistence, hash coverage, unresolved inventory/comparison and
read-only transfer treatment. Tests use fabricated values only.

The DOM geometry and controller responses are synthetic. Native Chrome and
real Hub qualification of this changed build have not been performed by this
slice. The live observation motivating the change is not a successful test of
the changed build, and no complete-source or live relisting claim follows.

`tests/test_fractional_bathroom_chrome.py` adds four opt-in native localhost
script/ordinary-click checks. They reuse the existing supplied-driver and
profile fixture, verify exact typed raw serialization with no inferred integer,
exercise clear/one-scroll paths, and stop on changed or unsupported raw values.
The module skips before browser/server/profile setup unless
`QUICK_RELIST_CHROME_TESTS=1` and an existing explicit ChromeDriver are provided.
It neither downloads tools nor relaxes Hub-origin guards. Adding these tests is
not evidence they have run natively; the parent owns that qualification.
