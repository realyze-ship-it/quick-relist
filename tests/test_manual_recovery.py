from copy import deepcopy
import pytest

from quick_relist.manual_recovery import CONFIRMATIONS, recovery_receipt
from quick_relist.relist_runner import display_status, run_status
from quick_relist.run_store import RunStore, RunNeedsReview


@pytest.fixture
def stopped():
    store = RunStore(':memory:')
    run = store.start('job', source_capture_id='capture', address_revision_id='review',
                      reference='RXCU1000', listing_id='123456')
    store.perform_once(run, 'remove_portals', lambda: {'removed': True})
    store.perform_once(run, 'archive', lambda: {'archived': True})
    def unknown():
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        store.perform_once(run, 'create', unknown)
    store.note(run, 'stopped', {'after_state': 'archived'})
    return store, run


def values(pending=False):
    return {'reference': 'RXCU-2000', 'editor_url': 'https://hub.remax.co.za/app/listings/edit/654321/portals',
            'destinations': ['website', 'p24'],
            'portals': {'website': {'state': 'published', 'id': '654321'},
                        'p24': {'state': 'pending' if pending else 'published', 'id': None if pending else '999999'}},
            'confirmations': dict.fromkeys(CONFIRMATIONS, True), 'notes': 'Synthetic reviewed recovery'}


def test_manual_recovery_keeps_failed_action_and_blocks_further_writes(stopped):
    store, run = stopped
    before = deepcopy(store.get('job')['actions'])
    result = store.record_manual_recovery('job', source_capture_id='capture', **values())
    assert result['state'] == 'manual_completed'
    assert result['mode'] == 'manual_recovery'
    assert store.get('job')['actions'] == before
    assert display_status(store.get('job')) == result
    for action in ('create', 'publish', 'remove_portals'):
        with pytest.raises(RunNeedsReview, match='recovered in Hub'):
            store.perform_once(run, action, lambda: pytest.fail('No Hub action is allowed'))
    with pytest.raises(RunNeedsReview):
        store.record_manual_recovery('job', source_capture_id='capture', **values())


@pytest.mark.parametrize('change', ['source', 'reference', 'url', 'confirmation', 'missing_portal', 'missing_id'])
def test_manual_record_requires_matching_source_distinct_target_and_all_checks(stopped, change):
    store, run = stopped
    data = values()
    source = 'different' if change == 'source' else 'capture'
    if change == 'reference': data['reference'] = 'RXCU1000'
    if change == 'url': data['editor_url'] = 'https://example.com/app/listings/edit/654321'
    if change == 'confirmation': data['confirmations']['reviewed_content_and_photos'] = False
    if change == 'missing_portal': data['portals'].pop('p24')
    if change == 'missing_id': data['portals']['p24']['id'] = ''
    before = store.get('job')
    with pytest.raises((ValueError, RunNeedsReview)):
        store.record_manual_recovery('job', source_capture_id=source, **data)
    assert store.get('job') == before


def test_pending_manual_recovery_requires_a_matching_complete_portal_read(stopped):
    store, run = stopped
    result = store.record_manual_recovery('job', source_capture_id='capture', **values(pending=True))
    assert result['state'] == 'manual_publication_pending'
    with pytest.raises(RunNeedsReview):
        store.check_manual_publication('job', {**result, 'target_listing_id': '999999'},
                                       {'complete': True, 'portals': values()['portals']})
    with pytest.raises(RunNeedsReview):
        store.check_manual_publication('job', result, {'complete': True, 'portals': values(pending=True)['portals']})
    checked = store.check_manual_publication('job', result, {'complete': True, 'portals': values()['portals']})
    assert checked['state'] == 'manual_completed'
    assert checked['content_checked_by'] == 'operator_in_hub'
    assert checked['publication_checked_by'] == 'app_read_only'
    assert run_status(store.get('job')) == checked
    assert store.get('job')['actions'][-1]['state'] == 'uncertain'


def test_recording_cannot_interrupt_an_in_progress_action(stopped):
    store, run = stopped
    # Simulate a process still executing an action, rather than a stopped run.
    store.conn.execute("UPDATE relist_actions SET state='started' WHERE run_id=? AND action='create'", (run,))
    store.conn.commit()
    with pytest.raises(RunNeedsReview):
        store.record_manual_recovery('job', source_capture_id='capture', **values())
