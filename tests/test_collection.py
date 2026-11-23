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


def test_incomplete_later_backup_prevents_every_archive(monkeypatch):
    drafts = DraftStore(':memory:')
    drafts.save_capture('0', {'schema_version':'hub-collected-listing/v1','complete':False})
    events = []
    def collect(hub, store, job, root, **kwargs):
        events.append(('collect',job['id']))
        return {'capture':{'download_needs_review':job['id']=='2'}}
    def archive(job, data, *, freshly_collected):
        events.append(('archive',job['id'],freshly_collected))
    monkeypatch.setattr(collection,'collect_listing',collect)
    with pytest.raises(collection.CollectionStopped, match='Review the completed download'):
        collection.collect_batch(None,drafts,jobs(),None,after_collection=archive)
    assert events == [('collect','1'),('collect','2')]


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


@pytest.mark.parametrize('change', ['none', 'different_archive', 'unverified_bytes', 'wrong_listing', 'pending_download'])
def test_operator_review_resumes_only_exact_saved_original_bytes(tmp_path, monkeypatch, change):
    import hashlib, json
    from quick_relist.downloads import MARKER_NAME, inspect_download, finish_download_review
    root = tmp_path / 'downloads'
    root.mkdir(mode=0o700)
    directory = root / ('download-' + '1' * 32)
    directory.mkdir(mode=0o700)
    archive = b'synthetic original archive'
    (directory / 'photos.zip').write_bytes(archive)
    marker = {'version': 1, 'state': 'attempted', 'directory': directory.name}
    (root / MARKER_NAME).write_text(json.dumps(marker))
    drafts = DraftStore(':memory:')
    capture = {'schema_version': 'hub-collected-listing/v1', 'complete': False,
               'listing_id': '123', 'source_capture_id': 'preserved-source',
               'download_needs_review': True, 'media': {'source_bytes_verified': True,
               'listing_id': '123', 'pack_archive_sha256': hashlib.sha256(archive).hexdigest()}}
    if change == 'different_archive': capture['media']['pack_archive_sha256'] = '0' * 64
    if change == 'unverified_bytes': capture['media']['source_bytes_verified'] = False
    if change == 'wrong_listing': capture['media']['listing_id'] = '456'
    original_id = drafts.save_capture('0', capture)
    review = inspect_download(root)
    finish_download_review(root, review['review_token'], browsers_closed=True, files_reviewed=True)
    if change == 'pending_download':
        (root / MARKER_NAME).write_text(json.dumps(marker))
    calls = []
    def collect(hub, store, job, media_root, **kwargs):
        calls.append(job['id'])
        return {'capture': {}}
    monkeypatch.setattr(collection, 'collect_listing', collect)
    if change != 'none':
        with pytest.raises(collection.CollectionStopped, match='previous photo download'):
            collection.collect_batch(SimpleNamespace(), drafts, jobs(), tmp_path / 'media')
        assert calls == []
    else:
        result = collection.collect_batch(SimpleNamespace(), drafts, jobs(), tmp_path / 'media')
        assert calls == ['1', '2']
        assert result[0]['capture']['download_needs_review'] is False
        assert result[0]['capture']['source_capture_id'] == 'preserved-source'
        assert 'download_review_receipt' in result[0]['capture']
    assert drafts.get_capture(original_id)['capture'] == capture


@pytest.mark.parametrize('changed', [False, True])
def test_media_backup_matches_pack_and_rechecks_order_before_leaving_media(monkeypatch, tmp_path, changed):
    from test_original_media import inventory, picture
    from quick_relist import capture, original_media
    import zipfile
    records = inventory()
    page = {'matched_source':{'rxcu':'RXCU12345','listing_id':'123'}, 'media':records,
            'photo_captions':[{'id':item['observed_asset_id'], 'caption':'caption '+str(index), 'close_verified':True}
                              for index, item in enumerate(records)]}
    contents = [picture(0xff0000ff), picture(0xffff0000)]
    lookup = {r['original_source_ref']['url']: b for r,b in zip(records, contents)}
    monkeypatch.setattr(collection, 'download_originals',
        lambda *args, **kw: original_media.download_originals(*args, fetch=lookup.__getitem__, **kw))
    def pack(reference, ident, root):
        assert (reference, ident) == ('RXCU12345','123')
        archive = tmp_path / 'pack.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for index, item in enumerate(contents):
                z.writestr(f'{index}.png', item)
        return {'candidate_path':str(archive), 'attribution_verified':False}
    hub = SimpleNamespace(driver=object(), download_photo_pack=pack)
    monkeypatch.setattr(capture, 'read_gallery_media', lambda driver, ident, **kw: records[:1] if changed else records)
    store = DraftStore(':memory:')
    def backup():
        return collection._backup_media(hub, store, 'job', page, tmp_path/'media', guard=lambda:True,
                                        progress=lambda message:None, cancelled=lambda:False)
    if changed:
        with pytest.raises(collection.CollectionStopped, match='photos changed'):
            backup()
    else:
        _, media_id, verified, attributed = backup()
        assert [m['caption'] for m in verified['media_manifest']] == ['caption 0','caption 1']
        assert store.get_capture(media_id)['capture']['source_bytes_verified'] is True
        assert attributed is False


@pytest.mark.parametrize('failure', ['none', 'collect', 'preflight', 'cancel'])
def test_batch_barrier_finishes_all_read_only_work_before_archive(monkeypatch, failure):
    drafts = DraftStore(':memory:')
    events = []
    def collect(hub, store, job, root, **kwargs):
        events.append(('collect', job['id']))
        if failure == 'collect' and job['id'] == '2':
            raise collection.CollectionStopped('Synthetic tab identity conflict')
        return {'capture': {'id': job['id']}}
    def preflight(job, data, **kwargs):
        events.append(('preflight', job['id']))
        if failure == 'preflight' and job['id'] == '2':
            raise collection.CollectionStopped('Synthetic unsupported related record')
    def archive(job, data, *, freshly_collected):
        assert freshly_collected is False
        events.append(('archive', job['id']))
    monkeypatch.setattr(collection, 'collect_listing', collect)
    def run():
        return collection.collect_batch(None, drafts, jobs(), None,
            preflight_collection=preflight, after_collection=archive,
            cancelled=lambda: failure == 'cancel' and ('preflight', '1') in events)
    if failure in ('collect', 'preflight'):
        with pytest.raises(collection.CollectionStopped): run()
    else:
        run()
    if failure == 'none':
        assert events == [(phase, str(i)) for phase in ('collect','preflight','archive') for i in range(3)]
    else:
        assert not any(phase == 'archive' for phase, _ in events)


def test_saved_and_skipped_rows_preserve_barrier_order(monkeypatch):
    drafts = DraftStore(':memory:')
    drafts.save_capture('0', {'schema_version':'hub-collected-listing/v1','complete':False})
    events = []
    rows = jobs()
    rows[1]['state'] = 'complete'
    monkeypatch.setattr(collection, 'collect_listing', lambda h,d,j,r,**kw:
        events.append(('collect',j['id'])) or {'capture':{}})
    collection.collect_batch(None,drafts,rows,None,
        preflight_collection=lambda j,c,**kw:events.append(('check',j['id'],kw['freshly_collected'])),
        after_collection=lambda j,c,**kw:events.append(('archive',j['id'],kw['freshly_collected'])))
    assert events == [('collect','2'),('check','0',False),('check','2',True),('archive','0',False),('archive','2',False)]


@pytest.mark.parametrize('cancel_at', ['all_captured','first_archive'])
def test_stop_between_phases_or_archive_rows_never_advances(monkeypatch,cancel_at):
    drafts=DraftStore(':memory:')
    events=[]
    def collect(h,d,j,r,**kw):
        events.append(('collect',j['id']))
        return {'capture':{}}
    def archive(j,c,**kw): events.append(('archive',j['id']))
    def cancelled():
        return (('collect','2') in events if cancel_at=='all_captured' else ('archive','0') in events)
    monkeypatch.setattr(collection,'collect_listing',collect)
    collection.collect_batch(None,drafts,jobs(),None,cancelled=cancelled,
        preflight_collection=lambda j,c,**kw:events.append(('check',j['id'])),after_collection=archive)
    assert [event for event in events if event[0]=='archive'] == ([] if cancel_at=='all_captured' else [('archive','0')])
