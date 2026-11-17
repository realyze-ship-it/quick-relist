# Relisting mutation boundary

This is a development contract, not a working Hub connection. The desktop app
still starts with an unavailable live adapter. No browser selectors, Hub API
endpoints or qualification flags may be invented to make it appear connected.

## An operation has one exact meaning

Every mutation must carry an immutable request identifying the operation key,
action, source RXCU and listing ID, account and agency, expected source revision,
complete original capture, approved address, exact portal targets and archive
settings. Replacement operations also bind the known replacement ID and its
expected revision. The archive status is cancelled and the reason is
mandate_expired. No other field change is implicitly approved.

The request is fingerprinted. The provider must enforce its expected revisions
atomically when applying the operation, including source lineage during
creation. Reading a revision and then clicking a button is not that guarantee.
Post-write revisions must come from a receipt bound to the same request, and
subsequent reads must agree. An unrelated later revision cannot silently become
the new expected revision.

## Validate the replacement before destructive work

The provider must support verified, non-mutating replacement validation. It
checks the exact source data and approved address against the applicable sale
or rental requirements. Validation must not create a draft, upload files, save
fields or alter the source as a side effect.

An unavailable, unverified or invalid result blocks mutation. A required user
decision also blocks mutation. Merely possessing a partial DOM capture or a
matching set of historical fields is not a successful validation result.

### Mandate dates are an unresolved business decision

The current policy preserves the source's signed, expiry and other date values
exactly. It never substitutes today's date, extends a mandate, invents a renewal
or guesses date formats. If the actual form requires different dates or a new
mandate, the operator must provide the facts and approve that change through a
future supported review flow before destructive work can proceed. Retaining an
expired value is not a claim that Hub will accept it.

## Durable intent and uncertain outcomes

The exact request is recorded durably before the provider can mutate anything.
A crash, timeout, malformed response or failed post-write check leaves the
operation uncertain. The next action is read-only reconciliation, not another
click or a fresh operation key.

Authoritative reconciliation must identify the same operation and request. An
applied operation is verified without repeating it. A not-applied result must
prove the earlier request cannot commit later; only then may an explicit retry
reuse the exact key and request. A current absence in an eventually consistent
page is insufficient. Legacy operations without the bound request are not
silently upgraded or replayed.

## Qualification and verification still required

A real provider needs independent evidence for complete source/media capture,
account binding, required-field and address validation, atomic revision
preconditions, idempotency, authoritative reconciliation and portal outcomes.
Boolean capability declarations are not that evidence. Synthetic providers
exercise the control flow but do not qualify Hub.

After creation, verify the replacement's identity, source lineage, unpublished
draft state, full fields, approved address, media and portal targets. Recheck
before publishing. An accepted publication request remains pending until every
approved portal is independently confirmed live.

The generic fidelity check compares normalized business fields, media and
portal targets. The raw source record is preserved for recovery, but is not a
substitute for that normalized coverage. Before qualification, demonstrate that
every transferable business value present only in raw source data is included
in the normalized record and checked after recreation. Raw lifecycle IDs and
transport metadata must not be copied blindly into a new listing.

Keep requests, receipts, source records and media private. Do not write full
provider responses or arbitrary exception messages to the activity journal.
The workflow cannot promise rollback once the original has been archived.
