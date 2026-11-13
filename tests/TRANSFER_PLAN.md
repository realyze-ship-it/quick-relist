# Read-only saved-capture transfer preparation

`quick_relist.transfer_plan.build_transfer_plan` accepts two guarded saved
capture dictionaries and six mandatory expectations: source and target listing
ID, RXCU reference, and exact capture SHA256. IDs and references must be distinct.
The caller must choose these pins independently for the intended job; deriving
pins from an arbitrary substituted input does not establish identity. Hashes
only detect corruption/substitution relative to those pins, never authenticity,
account ownership, current server values, or revision freshness.

The detached JSON result contains `groups.details`, `groups.deal_particulars`,
and `groups.media`. Every known inventory entry is present, even if missing or
unsupported. Each row retains full source/target inventory entries (including
control observations and paths), the exact writer key/kind where supported, an
exact typed `candidate` and `baseline` where readable, a canonical JSON
comparison, and explicit issues/blockers. Unknown controls, dialog/room records,
superseded observations, and coverage gaps remain in `inventories` and
`unresolved_coverage`. No pending/unverified room contents are promoted.

`candidate_only` means a historical typed pair mapped, not permission or
readiness to copy. Global blockers always apply. Dates preserve raw text and
ordered segments without parsing or reconciling them. Date semantics and raw
`address_hidden` polarity remain blocked. Dropdowns preserve both selected code
and label; their complete target option menus remain unverified. Suburb entity
selection, rich formatting, nested record creation, and required creation fields
are unqualified. Disabled source controls can provide readable values; disabled
or unknown-editability target controls remain blocked even for exact no-ops.
Conflicting supported name/ID bindings are blocked on both sides; correctly
named text controls may retain dynamic IDs. Marketing header requires its exact
name and ID. Empty captured count names do not establish that the HTML name
attribute is absent, so that writer requirement remains an explicit blocker.

No `FieldTransfer`, `TransferContext`, `FormTransferRequest`, or mutation request
is constructed. No account/revision assertion or approved address override is
accepted. No browser, network, storage, UI, or launcher is called. All complete,
live-ready, and persistence flags remain false. Keep returned private data local.
Inputs are limited to 4 MB each and output to 32 MB of canonical UTF-8 JSON;
exceeding a limit rejects the plan instead of truncating observations.

Run the synthetic saved-capture tests with:

    python -m unittest discover -s tests -p test_transfer_plan.py -v

These tests cover all existing handler kinds through the real inventory mapper,
not mocked inventory rows. They do not qualify real Hub control interactions,
server persistence, listing recreation, or publication.
