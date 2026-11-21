# Related-table preflight

The notes table can render the empty row `No viewing arrangements` followed by four empty cells. The previous preflight accepted only `No listing notes saved`, so an otherwise empty notes table stopped before a run or removal intent was recorded. The saved table structure supports this narrow empty-state recognition; it does not establish any API or UI workflow for copying populated notes.

The additional recognition requires the exact header and row text, matching native `table-dom/v1` structural cells, a header and body row, and no actions, chips or merged cells. Missing structure, another row, any author/type/date value, or inconsistent cell content remains blocked. Existing supported empty markers remain unchanged. Errors name the affected record type without including captured values.

Populated custom features, parties, documents and notes still require a faithful copy implementation and independent saved-content verification. No such handler is introduced. Unknown states remain protected. The same preflight is used for the source and final replacement verification.

## Batch behavior

A preflight exception still stops sequential collection. Later rows remain queued and the saved source is retained. The existing **Cancel unstarted job** action provides an explicit local skip, guarded against started destructive work and uncertain run actions. It preserves local history and does not change a Hub listing. After an eligible local cancellation, remaining collection proceeds in its original order. No automatic skip or archive reason was changed.

## Windows verification without Hub

From an isolated checkout with Python 3.11+ and the project's test dependencies:

```powershell
python -m pytest tests/test_related_preflight.py tests/test_relist_runner.py tests/test_collection.py tests/test_core.py tests/test_ui.py -q
```

These tests use synthetic data, in-memory stores and fake providers. They cover the observed empty shape, populated/ambiguous tables, no provider calls or run journal creation on a related-record stop, later-row order after a local cancellation, and the existing UI cancellation guards. Do not click **Collect and archive** or **Relist reviewed batch** as a verification step: those are live operations. This change is for draft review; it does not update the installed Windows package.
