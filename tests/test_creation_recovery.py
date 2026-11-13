from copy import deepcopy
from types import SimpleNamespace
import pytest

from quick_relist import creation_recovery as recovery
from quick_relist.run_store import RunStore, RunNeedsReview
from quick_relist.relist_runner import copy_can_be_checked


def stopped_store():
    store = RunStore(':memory:')
    run = store.start('job', source_capture_id='capture', address_revision_id='review',
                      reference='RXCU1000', listing_id='123456')
    for action in ('remove_portals', 'archive'):
        store.perform_once(run, action, lambda: {'observed': True})
    def uncertain():
        raise TimeoutError('Synthetic Save result')
    with pytest.raises(TimeoutError):
        store.perform_once(run, 'create', uncertain)
    return store, run


def receipt():
    return {'reference': 'RXCU2000', 'listing_id': '654321', 'handle': 'synthetic-copy',
            'source_reference': 'RXCU1000', 'source_listing_id': '123456',
            'reconciled_by': 'fresh_archived_row_and_empty_replacement_read',
            'source_archived': True, 'particulars_verified': True,
            'empty_media': True, 'unpublished': True}


def test_recovery_keeps_failed_save_and_never_replays_creation():
    store, run = stopped_store()
    before = deepcopy(store.get('job'))
    assert recovery.can_check_creation(before)
    store.reconcile_created_copy(run, receipt())
    after = store.get('job')
    assert copy_can_be_checked(after)
    assert not recovery.can_check_creation(after)
    event = next(e for e in after['events'] if e['kind'] == 'creation_reconciled')
    assert event['data']['prior_action'] == before['actions'][-1]
    assert event['data']['prior_action']['state'] == 'uncertain'
    assert store.perform_once(run, 'create', lambda: pytest.fail('Never repeat Save')) == receipt()
    with pytest.raises(RunNeedsReview):
        store.reconcile_created_copy(run, receipt())


@pytest.mark.parametrize('key,value', [
    ('source_archived', False), ('particulars_verified', False),
    ('empty_media', False), ('unpublished', False), ('source_reference', 'RXCU9999'),
    ('source_listing_id', '999999'), ('reference', 'RXCU1000'), ('listing_id', '123456'),
    ('listing_id', 'not-an-id'), ('handle', ''), ('reconciled_by', 'operator_guess')])
def test_incomplete_or_wrong_copy_proof_does_not_change_the_journal(key, value):
    store, run = stopped_store()
    before = store.get('job')
    proof = receipt()
    proof[key] = value
    with pytest.raises(RunNeedsReview):
        store.reconcile_created_copy(run, proof)
    assert store.get('job') == before


def test_copy_already_claimed_by_another_run_is_rejected():
    store, run = stopped_store()
    other = store.start('other', source_capture_id='other-capture', address_revision_id='other-review',
                        reference='RXCU9999', listing_id='999999')
    store.perform_once(other, 'create', lambda: {'reference': 'RXCU2000', 'listing_id': '654321'})
    with pytest.raises(RunNeedsReview, match='another run'):
        store.reconcile_created_copy(run, receipt())
    assert recovery.can_check_creation(store.get('job'))


def test_manually_recovered_copy_cannot_be_claimed_by_a_second_run():
    store, run = stopped_store()
    other = store.start('other', source_capture_id='other-capture', address_revision_id='other-review',
                        reference='RXCU9999', listing_id='999999')
    store.note(other, 'manual_recovery', {'target_reference':'RXCU2000','target_listing_id':'654321'})
    with pytest.raises(RunNeedsReview, match='another run'):
        store.reconcile_created_copy(run, receipt())


@pytest.mark.parametrize('blocker', [None, 'archive', 'identity', 'particulars', 'photos', 'description', 'published', 'pending_other_portal'])
def test_live_existing_copy_check_requires_each_read_before_accepting(tmp_path, monkeypatch, blocker):
    from quick_relist import live_relist_ui as module
    from quick_relist.form_copy import CopyStopped
    calls = []
    def removed(*args):
        pytest.fail('Archived source content is not available through the normal editor')
    def archived(*args, **kwargs):
        calls.append('archive')
        assert kwargs == {'archived':True, 'restore':False}
        if blocker == 'archive': raise CopyStopped('Source is not archived')
    binding = {'listing_id':'999999' if blocker=='identity' else '654321','handle':'copy'}
    hub = SimpleNamespace(driver=SimpleNamespace(window_handles=['source','copy']),
                          editor_bindings={'RXCU2000':binding, 'RXCU1000':{'listing_id':'123456','handle':'source'}},
                          search_reference=lambda *a,**kw: calls.append('search'),
                          open_listing_editor=lambda *a,**kw: calls.append('open'))
    ui = module.HubRelistUI(hub, tmp_path, clipboard=lambda *a: None)
    ui.check_removed_source = removed
    ui._check_source_archive = archived
    def captured(*args, **kwargs):
        assert kwargs == {'include_media_captions':False}
        calls.append('capture')
        ui.current = {'reference':'RXCU2000',**binding}
        return {'pages':[{'page_key':'media','media':['synthetic-photo'] if blocker=='photos' else [],'photo_captions':[]}]}
    ui.captured_editor = captured
    def particulars(*args):
        calls.append('particulars')
        if blocker == 'particulars': raise CopyStopped('Mismatched particulars')
    ui._verify_particulars = particulars
    ui.tab = lambda name: calls.append('tab_'+name)
    monkeypatch.setattr(module, '_wait_search', lambda *a: None)
    monkeypatch.setattr(module, 'media_plan', lambda *a: {'description_html':'<p></p>',
        'description_text':'Existing text' if blocker=='description' else '', 'marketing_header':''})
    monkeypatch.setattr(module, 'read_portals', lambda *a,**kw: {'published':blocker=='published',
        'portals':{'website':{'state':'unpublished'},'pp':{'state':'pending' if blocker=='pending_other_portal' else 'unpublished'}}})
    plan = {'source_reference':'RXCU1000','source_listing_id':'123456','destinations':['website']}
    if blocker:
        with pytest.raises(CopyStopped): ui.check_created_copy({},plan,'RXCU2000','654321')
    else:
        proof = ui.check_created_copy({},plan,'RXCU2000','654321')
        assert proof == receipt() | {'handle':'copy'}
        assert calls == ['archive','search','open','capture','particulars','tab_Portals']


@pytest.mark.parametrize('change', ['missing_archive', 'uncertain_removal', 'active_save', 'later_action', 'manual_recovery'])
def test_only_the_narrow_stopped_creation_state_is_eligible(change):
    store, _ = stopped_store()
    run = store.get('job')
    if change == 'missing_archive': run['actions'].pop(1)
    elif change == 'uncertain_removal': run['actions'][0]['state'] = 'uncertain'
    elif change == 'active_save': run['actions'][-1]['state'] = 'started'
    elif change == 'later_action': run['actions'].append({'action': 'publish_website', 'state': 'done'})
    else: run['events'].append({'kind': 'manual_recovery'})
    assert not recovery.can_check_creation(run)


@pytest.mark.parametrize('reference,url', [
    ('RXCU1000', 'https://hub.remax.co.za/app/listings/edit/654321'),
    ('RXCU2000', 'https://hub.remax.co.za/app/listings/edit/123456'),
    ('RXCU2000', 'https://example.test/app/listings/edit/654321'),
    ('RXCU2000', 'https://hub.remax.co.za/app/listings/archive/654321')])
def test_recovery_identity_rejects_source_or_unrelated_routes(reference, url):
    with pytest.raises(ValueError):
        recovery.existing_identity(reference, url, {'reference':'RXCU1000','listing_id':'123456'})


def test_recovery_identity_normalizes_the_reference():
    assert recovery.existing_identity('RXCU-2000', 'https://hub.remax.co.za/app/listings/edit/654321/media',
                                      {'reference':'RXCU1000','listing_id':'123456'}) == ('RXCU2000','654321')


def test_recovery_dialog_opens_empty_and_requires_a_distinct_complete_identity():
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
    app = QApplication.instance() or QApplication([])
    dialog, values = recovery.recovery_dialog({'reference':'RXCU1000','listing_id':'123456'})
    fields = {field.accessibleName():field for field in dialog.findChildren(QLineEdit)}
    check = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Check existing copy')
    assert not check.isEnabled()
    fields['Replacement RXCU'].setText('RXCU-2000')
    assert not check.isEnabled()
    fields['Replacement Hub edit URL'].setText('https://hub.remax.co.za/app/listings/edit/654321')
    assert check.isEnabled()
    assert values() == ('RXCU2000','654321')
    fields['Replacement RXCU'].setText('RXCU1000')
    assert not check.isEnabled()
    fields['Replacement RXCU'].clear()
    assert not check.isEnabled()
    dialog.close()


@pytest.mark.parametrize('blocker', [None, 'changed_review', 'failed_read', 'cancelled'])
def test_recovery_records_only_fresh_reads_of_the_bound_capture(monkeypatch, blocker):
    from quick_relist import collection, relist_plan, brochure_review, relist_dates
    store, _ = stopped_store()
    before = store.get('job')
    data = {'source_capture_id':'capture','rxcu':'RXCU1000','listing_id':'123456'}
    monkeypatch.setattr(collection, 'latest_collection', lambda *a: {'capture':data})
    monkeypatch.setattr(relist_plan, 'build_relist_plan', lambda *a: {'synthetic':True})
    monkeypatch.setattr(brochure_review, 'apply_review', lambda *a: None)
    dates = []
    monkeypatch.setattr(relist_dates, 'apply_relist_dates', lambda plan, run, today: dates.append(run['id']))
    drafts = SimpleNamespace(list_addresses=lambda *a: [{'id':'changed' if blocker=='changed_review' else 'review'}])
    calls = []
    def read(*args):
        calls.append(args[-2:])
        if blocker == 'failed_read': raise RunNeedsReview('Synthetic mismatch')
        return receipt()
    provider = SimpleNamespace(check_created_copy=read)
    operation = lambda: recovery.check_existing_replacement(drafts, store, provider, {'id':'job'},
                      'RXCU2000', '654321', cancelled=lambda: blocker=='cancelled')
    if blocker:
        with pytest.raises(RunNeedsReview): operation()
        assert store.get('job') == before
    else:
        assert operation() == receipt()
        assert calls == [('RXCU2000', '654321')]
        assert dates == [before['id']]
        assert copy_can_be_checked(store.get('job'))
