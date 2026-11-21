"""Synthetic related-table guards; no Hub session or listing data required."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from quick_relist.form_copy import CopyStopped
from quick_relist.relist_plan import RELATED, RELATED_NAMES, require_empty_related
from quick_relist import collection, relist_runner
from quick_relist.drafts import DraftStore
from quick_relist.run_store import RunStore


def details():
    return {'tables': [{'rows': [list(header), [empty]]} for header, empty in RELATED.items()]}


def viewing_table():
    rows = [['Note', 'User', 'Type', 'Date', 'Action'],
            ['No viewing arrangements', '', '', '', '']]
    return {'rows': rows, 'structure': {
        'schema_version': 'table-dom/v1', 'status': 'observed_native', 'tag': 'table',
        'rows': [{'row_group_tag': 'thead' if i == 0 else 'tbody',
                  'cells': [{'text': value, 'tag': 'th' if i == 0 else 'td',
                             'actions': [], 'chips': [], 'colspan_property': 1,
                             'rowspan_property': 1} for value in row]}
                 for i, row in enumerate(rows)]}}


def test_both_observed_empty_note_forms_preserve_capture():
    page = details()
    require_empty_related(page)
    page['tables'][-1] = viewing_table()
    before = deepcopy(page)
    require_empty_related(page)
    assert page == before


@pytest.mark.parametrize('index', range(4))
def test_populated_tables_identify_type_without_disclosing_values(index):
    page = details()
    page['tables'][index]['rows'][1] = ['SYNTHETIC_PRIVATE_VALUE']
    with pytest.raises(CopyStopped) as error:
        require_empty_related(page)
    assert list(RELATED_NAMES.values())[index] in str(error.value)
    assert 'SYNTHETIC_PRIVATE_VALUE' not in str(error.value)


@pytest.mark.parametrize('change', [
    lambda t: t.pop('structure'),
    lambda t: t['rows'].append(['Synthetic note', '', '', '', '']),
    lambda t: t['rows'][1].__setitem__(1, 'Synthetic author'),
    lambda t: t['rows'][1].__setitem__(0, 'No other records'),
    lambda t: t['structure'].__setitem__('status', 'unverified'),
    lambda t: t['structure']['rows'].pop(),
    lambda t: t['structure']['rows'][1]['cells'][0].__setitem__('text', 'Synthetic note'),
    lambda t: t['structure']['rows'][1]['cells'][4].__setitem__('actions', [{'label': 'Edit'}]),
    lambda t: t['structure']['rows'][1]['cells'][0].__setitem__('chips', [{'text': 'Synthetic'}]),
    lambda t: t['structure']['rows'][1]['cells'][0].__setitem__('colspan_property', 5),
    lambda t: t['structure']['rows'][1].__setitem__('row_group_tag', 'tfoot'),
])
def test_empty_variant_requires_exact_text_shape_and_structure(change):
    page = details()
    page['tables'][-1] = viewing_table()
    change(page['tables'][-1])
    with pytest.raises(CopyStopped):
        require_empty_related(page)


@pytest.mark.parametrize('duplicate', [False, True])
def test_missing_or_duplicate_notes_still_stop(duplicate):
    page = details()
    if duplicate:
        page['tables'].append(deepcopy(page['tables'][-1]))
    else:
        page['tables'].pop()
    with pytest.raises(CopyStopped, match='Refresh'):
        require_empty_related(page)


def test_related_stop_precedes_all_provider_calls_and_journal_actions(monkeypatch):
    page = details()
    page['tables'][-1]['rows'][1] = ['Synthetic note']
    def plan(*args):
        require_empty_related(page)
        pytest.fail('populated notes must stop')
    monkeypatch.setattr(relist_runner, 'build_relist_plan', plan)
    runs = RunStore(':memory:')
    job = {'id': 'synthetic-job', 'rxcu': 'RXCU1000', 'archive_before_review': True}
    runner = relist_runner.RelistRunner(None, runs, SimpleNamespace())
    with pytest.raises(CopyStopped, match='notes'):
        runner.archive_collected(job, {'rxcu': 'RXCU1000', 'source': {}}, freshly_collected=True)
    assert runs.get(job['id']) is None


def test_protected_row_stops_batch_but_local_cancel_preserves_later_order(monkeypatch):
    drafts = DraftStore(':memory:')
    jobs = [{'id': str(i), 'rxcu': f'RXCU{1000+i}', 'state': 'queued'} for i in range(3)]
    saved_id = drafts.save_capture('0', {'schema_version': 'hub-collected-listing/v1', 'complete': False})
    calls = []
    def collect(hub, store, job, root, **kwargs):
        calls.append(('collect', job['id']))
        return {'capture': {}}
    def archive(job, data, **kwargs):
        calls.append(('archive', job['id']))
        if job['id'] == '0':
            raise CopyStopped('Synthetic protected row')
    monkeypatch.setattr(collection, 'collect_listing', collect)
    with pytest.raises(CopyStopped):
        collection.collect_batch(None, drafts, jobs, None, after_collection=archive)
    assert calls == [('archive', '0')]
    jobs[0]['state'] = 'cancelled'  # The separately guarded local cancellation path.
    calls.clear()
    collection.collect_batch(None, drafts, jobs, None, after_collection=archive)
    assert calls == [('collect', '1'), ('archive', '1'), ('collect', '2'), ('archive', '2')]
    assert drafts.get_capture(saved_id)
