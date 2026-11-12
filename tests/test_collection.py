from types import SimpleNamespace
import pytest
from quick_relist import collection
from quick_relist.drafts import DraftStore


def jobs():
    return [{'id': str(i), 'rxcu': f'RXCU{1000+i}', 'state': 'queued'} for i in range(3)]


def test_batch_stops_before_next_listing_when_current_collection_fails(monkeypatch):
    drafts = DraftStore(':memory:')
    calls = []
    def collect(hub, store, job, root, **kwargs):
        calls.append(job['id'])
        if job['id'] == '1':
            raise collection.CollectionStopped('Synthetic missing source')
        return {'capture': {}}
    monkeypatch.setattr(collection, 'collect_listing', collect)
    with pytest.raises(collection.CollectionStopped):
        collection.collect_batch(SimpleNamespace(), drafts, jobs(), None)
    assert calls == ['0', '1']


def test_saved_collections_are_resumed_without_downloading_again(monkeypatch):
    drafts = DraftStore(':memory:')
    drafts.save_capture('0', {'schema_version': 'hub-collected-listing/v1', 'complete': False})
    calls = []
    def collect(hub, store, job, root, **kwargs):
        calls.append(job['id'])
        return {'capture': {}}
    monkeypatch.setattr(collection, 'collect_listing', collect)
    assert len(collection.collect_batch(SimpleNamespace(), drafts, jobs(), None)) == 3
    assert calls == ['1', '2']


def test_user_stop_does_not_begin_another_listing(monkeypatch):
    drafts = DraftStore(':memory:')
    calls = []
    def collect(hub, store, job, root, **kwargs):
        calls.append(job['id'])
        return {'capture': {}}
    monkeypatch.setattr(collection, 'collect_listing', collect)
    collection.collect_batch(SimpleNamespace(), drafts, jobs(), None, cancelled=lambda: bool(calls))
    assert calls == ['0']


def test_resuming_does_not_skip_an_unresolved_download(monkeypatch):
    drafts = DraftStore(':memory:')
    drafts.save_capture('0', {'schema_version': 'hub-collected-listing/v1', 'complete': False,
                             'download_needs_review': True})
    monkeypatch.setattr(collection, 'collect_listing', lambda *a, **kw: pytest.fail('must not start another listing'))
    with pytest.raises(collection.CollectionStopped, match='previous photo download'):
        collection.collect_batch(SimpleNamespace(), drafts, jobs(), None)


@pytest.mark.parametrize('code,expected', [('1', {'details','deal_particulars','media','portals'}), ('2', {'details','media','portals'})])
def test_required_pages_follow_observed_listing_type(code, expected):
    assert collection.required_pages({'pages':[{'page_key':'details','fields':[
        {'name':'listing_type_id','selected_options':[{'value':code,'text':'Synthetic'}]}]}]}) == expected
