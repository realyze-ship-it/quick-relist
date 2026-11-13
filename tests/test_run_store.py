from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from quick_relist.run_store import RunStore, RunNeedsReview


def start(store):
    return store.start('job', source_capture_id='capture', address_revision_id='review',
                       reference='RXCU1000', listing_id='123456')


@pytest.mark.parametrize('blocker',[None,'missing_media','published'])
def test_only_content_read_can_retry_after_completed_unpublished_copy(blocker):
    store=RunStore(':memory:'); run=start(store)
    for action in ('remove_portals','archive','create','copy_media_text'):
        if action=='copy_media_text' and blocker=='missing_media':continue
        store.perform_once(run,action,lambda:{'confirmed':True})
    if blocker=='published':store.perform_once(run,'publish_website',lambda:{'confirmed':True})
    def fail():raise RuntimeError('synthetic comparison mismatch')
    with pytest.raises(RuntimeError):store.perform_once(run,'verify_content',fail)
    calls=[]
    if blocker:
        with pytest.raises(RunNeedsReview):store.perform_once(run,'verify_content',lambda:calls.append('read') or {'checked':True})
        assert calls==[]
    else:
        assert store.perform_once(run,'verify_content',lambda:calls.append('read') or {'checked':True})=={'checked':True}
        assert calls==['read']
        retry=next(e for e in store.get('job')['events'] if e['kind']=='checked_verify_content_retry')
        assert retry['data']['state']=='uncertain'
        assert retry['data']['action']=='verify_content'
        assert store.perform_once(run,'create',lambda:pytest.fail('Never recreate'))=={'confirmed':True}


def test_receipt_survives_restart_and_action_is_not_repeated(tmp_path):
    path = tmp_path / 'runs.sqlite3'
    store = RunStore(path)
    run = start(store)
    assert store.perform_once(run, 'create', lambda: {'target_id': '654321'}) == {'target_id': '654321'}
    store.close()
    reopened = RunStore(path)
    assert start(reopened) == run
    assert reopened.perform_once(run, 'create', lambda: pytest.fail('must not create twice')) == {'target_id': '654321'}


def test_uncertain_create_is_not_retried_and_blocks_later_steps():
    store = RunStore(':memory:')
    run = start(store)
    def timeout():
        raise TimeoutError('synthetic browser timeout after click')
    with pytest.raises(TimeoutError):
        store.perform_once(run, 'create', timeout)
    for action in ('create', 'publish'):
        with pytest.raises(RunNeedsReview):
            store.perform_once(run, action, lambda: pytest.fail('no more mutations'))
    assert store.get('job')['actions'][0]['state'] == 'uncertain'


def test_changed_review_cannot_replace_a_started_run():
    store = RunStore(':memory:')
    start(store)
    with pytest.raises(RunNeedsReview):
        store.start('job', source_capture_id='capture', address_revision_id='changed',
                    reference='RXCU1000', listing_id='123456')


@pytest.mark.parametrize('reference,listing_id', [('RXCU-1000','987654'), ('RXCU9999','123456')])
@pytest.mark.parametrize('completed', [False, True])
def test_new_batch_cannot_bypass_an_existing_source_record(tmp_path, reference, listing_id, completed):
    path = tmp_path / 'runs.sqlite3'
    store = RunStore(path)
    run = start(store)
    if completed:
        store.note(run, 'status', {'state': 'published', 'target_reference': 'RXCU2000'})
    else:
        def unknown():
            raise TimeoutError()
        with pytest.raises(TimeoutError):
            store.perform_once(run, 'create', unknown)
    original = store.get('job')
    store.close()
    reopened = RunStore(path)
    with pytest.raises(RunNeedsReview, match='another saved batch'):
        reopened.start('new-job', source_capture_id='new-capture', address_revision_id='new-review',
                       reference=reference, listing_id=listing_id)
    assert reopened.get('new-job') is None
    assert reopened.get('job') == original
    # Relisting the new reference later is a different original.
    reopened.start('replacement-job', source_capture_id='other-capture', address_revision_id='other-review',
                   reference='RXCU2000', listing_id='654321')


def test_two_batches_cannot_claim_the_same_source_concurrently(tmp_path):
    path = tmp_path / 'runs.sqlite3'
    stores = [RunStore(path), RunStore(path)]
    def attempt(index):
        try:
            return stores[index].start(str(index), source_capture_id='capture', address_revision_id='review',
                                       reference='RXCU1000', listing_id='123456')
        except RunNeedsReview:
            return None
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1


def test_two_workers_cannot_send_the_same_request(tmp_path):
    path = tmp_path / 'runs.sqlite3'
    first, second = RunStore(path), RunStore(path)
    run = start(first)
    entered, release = threading.Event(), threading.Event()
    def slow():
        entered.set()
        assert release.wait(5)
        return {'accepted': True}
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(first.perform_once, run, 'publish', slow)
        assert entered.wait(5)
        with pytest.raises(RunNeedsReview):
            second.perform_once(run, 'publish', lambda: pytest.fail('duplicate request'))
        release.set()
        assert pending.result() == {'accepted': True}


def test_moderation_receipt_is_recorded_without_calling_it_published():
    store = RunStore(':memory:')
    run = start(store)
    store.perform_once(run, 'publish_pp', lambda: {'status': 'in_moderation'})
    assert store.get('job')['actions'][0]['receipt']['status'] == 'in_moderation'


def test_removal_reconciliation_cannot_clear_an_uncertain_create():
    store=RunStore(':memory:');run=start(store)
    def unknown():raise TimeoutError()
    with pytest.raises(TimeoutError):store.perform_once(run,'create',unknown)
    with pytest.raises(RunNeedsReview):
        store.reconcile_removal(run,{'reconciled_by':'fresh_source_and_portal_read','observed':{'published':False}})
    assert store.get('job')['actions'][0]['state']=='uncertain'


def test_archive_retry_rejects_wrong_source_and_preserves_uncertainty():
    store=RunStore(':memory:');run=start(store)
    store.perform_once(run,'remove_portals',lambda:{'removed':True})
    def unknown():raise TimeoutError()
    with pytest.raises(TimeoutError):store.perform_once(run,'archive',unknown)
    with pytest.raises(RunNeedsReview,match='different listing'):
        store.reconcile_archive_not_applied(run,{'archived':False,'reference':'RXCU9999','listing_id':'999999',
            'reconciled_by':'fresh_manage_listing_archive_action'})
    assert store.get('job')['actions'][-1]['state']=='uncertain'


@pytest.mark.parametrize('wrong_target', [False, True])
def test_received_photo_reconciliation_keeps_receipt_and_never_reuploads(wrong_target):
    store=RunStore(':memory:');run=start(store)
    for action in ('remove_portals','archive'):
        store.perform_once(run,action,lambda:{'observed':True})
    store.perform_once(run,'create',lambda:{'reference':'RXCU2000','listing_id':'654321'})
    def unknown():raise TimeoutError()
    action='upload_photo_0_attempt_1'
    with pytest.raises(TimeoutError):store.perform_once(run,action,unknown)
    receipt={'reconciled_by':'fresh_replacement_photo_comparison','reference':'RXCU9999' if wrong_target else 'RXCU2000',
             'listing_id':'654321','received_count':1,
             'photo_comparison':{'all_received':True,'order_matches':True,'matches':[{'source_position':0,'received_position':0}]}}
    if wrong_target:
        with pytest.raises(RunNeedsReview):store.reconcile_uploaded_photo(run,action,receipt)
        assert store.get('job')['actions'][-1]['state']=='uncertain'
    else:
        store.reconcile_uploaded_photo(run,action,receipt)
        assert store.perform_once(run,action,lambda:pytest.fail('duplicate upload'))==receipt
        assert store.get('job')['events'][-1]['kind']=='upload_reconciled'


@pytest.mark.parametrize('outcome',['empty','matching','formatting_changed'])
def test_description_recovery_is_bound_to_existing_target_and_preserves_history(outcome):
    store=RunStore(':memory:');run=start(store)
    store.perform_once(run,'create',lambda:{'reference':'RXCU2000','listing_id':'654321'})
    def unknown():raise TimeoutError()
    with pytest.raises(TimeoutError):store.perform_once(run,'copy_media_text',unknown)
    receipt={'reconciled_by':'fresh_unpublished_media_read','outcome':outcome,'reference':'RXCU9999','listing_id':'654321'}
    with pytest.raises(RunNeedsReview):store.reconcile_media_text(run,receipt)
    receipt['reference']='RXCU2000'
    store.reconcile_media_text(run,receipt)
    calls=[]
    store.perform_once(run,'copy_media_text',lambda:calls.append('save') or {'saved':True})
    assert calls==([] if outcome=='matching' else ['save'])
    assert any(e['kind']=='media_text_reconciled' for e in store.get('job')['events'])
