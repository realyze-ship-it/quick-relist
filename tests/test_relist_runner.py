from copy import deepcopy
from types import SimpleNamespace
from datetime import date
import pytest
from quick_relist import relist_runner as module
from quick_relist.run_store import RunStore, RunNeedsReview


class Provider:
    def __init__(self, fail=None, pending=False):
        self.calls=[];self.fail=fail;self.pending=pending
    def require_resolved_editor_session(self):
        pass
    def operation(self,name):
        self.calls.append(name)
        if name==self.fail:raise RuntimeError('synthetic uncertain action')
        return {'observed':True}
    def check_source(self,*args):return self.operation('check')
    def check_removed_source(self,*args):
        self.calls.append('check_removed')
        if self.fail == 'remove':
            raise RunNeedsReview('Removal is not confirmed')
        return {'reconciled_by':'fresh_source_and_portal_read', 'observed':{'published':False}}
    def prepare(self,plan):
        self.prepared_plan=deepcopy(plan)
        return self.operation('prepare')
    def check_unarchived_source(self,*args):
        self.calls.append('check_unarchived')
        if self.fail=='archive':raise RunNeedsReview('Archive outcome is unknown')
        return {'archived':False,'reference':'RXCU1000','listing_id':'111111','reconciled_by':'fresh_manage_listing_archive_action'}
    def unpublish(self,*args):return self.operation('remove')
    def archive(self,*args):return self.operation('archive')
    def create(self,*args):
        self.operation('create');return {'reference':'RXCU2000','listing_id':'222222'}
    def copy_content(self,*args):return self.operation('copy')
    def recover_copy(self,*args):
        self.calls.append('recover_copy')
        if self.fail == 'copy': raise RunNeedsReview('Received photos are not yet confirmed')
        return {'reference':'RXCU2000','listing_id':'222222'}
    def verify_content(self,*args):return self.operation('verify')
    def publish(self,target,destination):return self.operation('publish_'+destination)
    def publication(self,*args):
        self.operation('publication');return {'complete':not self.pending}


@pytest.fixture
def setup(monkeypatch):
    data={'source_capture_id':'capture','rxcu':'RXCU1000','listing_id':'111111'}
    monkeypatch.setattr(module,'latest_collection',lambda *args:{'capture':deepcopy(data)})
    monkeypatch.setattr(module,'build_relist_plan',lambda *args:{'destinations':['website','p24'],
        'particulars':{'mode':'sale','controls':[{'key':key,'kind':'date','value':'2090-01-01'}
            for key in ('date_listed','on_market_since','expiry_date','mandate_signed_date','mandate_expiry')]}})
    drafts=SimpleNamespace(list_addresses=lambda *args:[{'id':'review'}])
    runs=RunStore(':memory:')
    return drafts,runs,{'id':'job','rxcu':'RXCU1000','state':'queued'}


def test_original_is_archived_before_copy_is_created_and_fidelity_precedes_publish(setup):
    drafts,runs,job=setup; provider=Provider()
    result=module.RelistRunner(drafts,runs,provider).one(job)
    assert provider.calls==['prepare','check','remove','archive','create','copy','verify','publish_website','publish_p24','publication']
    assert result['state']=='published'
    assert module.RelistRunner(drafts,runs,Provider(fail='check')).one(job)==result


@pytest.mark.parametrize('failure', ['prepare','check','remove','archive','create','copy','verify'])
def test_a_failed_step_never_publishes_or_starts_the_next_listing(setup,failure):
    drafts,runs,job=setup;provider=Provider(fail=failure)
    runner=module.RelistRunner(drafts,runs,provider)
    with pytest.raises(RuntimeError):runner.batch([job,{**job,'id':'next'}])
    assert not any(c.startswith('publish_') for c in provider.calls)
    assert runs.get('next') is None
    if failure not in ('prepare','check'):
        with pytest.raises(RunNeedsReview):runner.one(job)


def test_pending_portal_stops_batch_without_claiming_published(setup):
    drafts,runs,job=setup;provider=Provider(pending=True)
    results=module.RelistRunner(drafts,runs,provider).batch([job,{**job,'id':'next'}])
    assert len(results)==1 and results[0]['state']=='publication_pending'
    assert runs.get('next') is None


def test_completed_manual_pilot_is_never_recreated(setup):
    drafts,runs,job=setup
    run=runs.start(job['id'],source_capture_id='manual-capture',address_revision_id='manual-review',reference='RXCU1000',listing_id='111111')
    runs.note(run,'status',{'state':'published','target_reference':'RXCU2000','mode':'reviewed_live_pilot'})
    provider=Provider(fail='check')
    assert module.RelistRunner(drafts,runs,provider).one(job)['state']=='published'
    assert provider.calls==[]


def test_archive_before_review_restarts_without_recollecting_or_rearchiving(setup):
    drafts, runs, job = setup
    job = {**job, 'archive_before_review':1}
    provider = Provider()
    provider.use_collected_source = lambda data:provider.operation('bind_recent_collection')
    data = {**module.latest_collection(drafts,job['id'])['capture'], 'source':{}}
    runner = module.RelistRunner(drafts, runs, provider, today=lambda:date(2099,8,31))
    with pytest.raises(RunNeedsReview, match='Finish collecting'):
        runner.one(job)
    runner.archive_collected(job, data, freshly_collected=True)
    runner.archive_collected(job, data, freshly_collected=False)
    assert provider.calls == ['bind_recent_collection','remove','archive']
    assert runs.get(job['id'])['binding']['address_revision_id'] is None
    drafts.list_addresses = lambda capture:[]
    with pytest.raises(module.CopyStopped, match='address review'):
        runner.one(job)
    drafts.list_addresses = lambda capture:[{'id':'review'}]
    result = module.RelistRunner(drafts, runs, provider, today=lambda:date(2099,9,1)).one(job)
    assert result['state'] == 'published' and result['original_archived']
    assert result['dates']['date_listed'] == '2099-09-01'
    assert runs.get(job['id'])['binding']['address_revision_id'] == 'review'
    assert provider.calls == ['bind_recent_collection','remove','archive','prepare','create','copy','verify',
                              'publish_website','publish_p24','publication']


def test_interrupted_early_archive_reconciles_visible_outcome_once(setup):
    drafts, runs, job = setup
    job = {**job, 'archive_before_review':1}
    provider = Provider(fail='archive')
    data = {**module.latest_collection(drafts,job['id'])['capture'], 'source':{}}
    runner = module.RelistRunner(drafts, runs, provider)
    with pytest.raises(RuntimeError):
        runner.archive_collected(job, data, freshly_collected=False)
    provider.inspect_archive = lambda plan: {'reference':job['rxcu'], 'listing_id':data['listing_id'],
        'reconciled_by':'fresh_manage_listing_archive_action','archived':True}
    runner.archive_collected(job, data, freshly_collected=False)
    assert module.archived_for_review(runs.get(job['id']))
    assert provider.calls == ['check','remove','archive']


def test_moderation_is_tracked_while_next_reviewed_listing_runs(setup):
    drafts,runs,job=setup
    runner=module.RelistRunner(drafts,runs,Provider())
    run=runs.start('prior',source_capture_id='c',address_revision_id='r',reference='RXCU3000',listing_id='333333')
    outcome={'website':{'state':'published','id':'444444'}, 'pp':{'state':'pending','pending_reason':'moderation'}}
    pending={'state':'publication_pending','publication':{'complete':False,'portals':outcome}}
    runs.note(run,'status',pending)
    results=runner.batch([{'id':'prior','rxcu':'RXCU3000'},job])
    assert results[0]==pending and results[1]['state']=='published'
    assert runs.get('prior')['actions']==[]
    for blocked in ({'state':'pending'}, {'state':'unknown'}, {'state':'unpublished'}):
        assert not module.batch_can_advance({**pending,'publication':{'portals':{**outcome,'other':blocked}}})


def test_same_original_in_new_batch_stops_before_preparing_any_form(setup):
    drafts, runs, job = setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts, runs, Provider(fail='create')).one(job)
    provider = Provider()
    with pytest.raises(RunNeedsReview, match='another saved batch'):
        module.RelistRunner(drafts, runs, provider).one({**job, 'id': 'new-batch-job'})
    assert provider.calls == []


@pytest.mark.parametrize('pending', [False, True])
def test_operator_recovery_never_recreates_the_recorded_copy(setup, pending):
    drafts, runs, job = setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts, runs, Provider(fail='create')).one(job)
    from quick_relist.manual_recovery import CONFIRMATIONS
    runs.record_manual_recovery(job['id'], source_capture_id='capture', reference='RXCU2000',
        editor_url='https://hub.remax.co.za/app/listings/edit/222222/portals', destinations=['website'],
        portals={'website': {'state': 'pending' if pending else 'published', 'id': None if pending else '222222'}},
        confirmations=dict.fromkeys(CONFIRMATIONS, True))
    provider = Provider()
    runner = module.RelistRunner(drafts, runs, provider)
    if pending:
        with pytest.raises(RunNeedsReview): runner.one(job)
    else:
        assert runner.one(job)['state'] == 'manual_completed'
    assert provider.calls == []


def test_stopped_display_does_not_claim_original_is_still_archived(setup):
    drafts, runs, job = setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts, runs, Provider(fail='create')).one(job)
    record = runs.get(job['id'])
    assert module.run_status(record)['state'] == 'archived'
    shown = module.display_status(record)
    assert shown['state'] == 'needs_review'
    assert shown['last_verified_state'] == 'archived'
    assert shown['unresolved_actions'] == ['create']
    assert module.run_status(runs.get(job['id']))['state'] == 'archived'


def test_display_recognizes_nonjournal_copy_failure_and_completed_recovery(setup):
    drafts, runs, job = setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts, runs, Provider(fail='copy')).one(job)
    assert module.display_status(runs.get(job['id']))['state'] == 'needs_review'
    module.RelistRunner(drafts, runs, Provider()).one(job)
    assert module.display_status(runs.get(job['id']))['state'] == 'published'


def test_confirmed_removal_resumes_without_repeating_the_removal_click(setup):
    drafts,runs,job=setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='remove')).one(job)
    provider=Provider()
    assert module.RelistRunner(drafts,runs,provider).one(job)['state']=='published'
    assert provider.calls==['prepare','check_removed','archive','create','copy','verify','publish_website','publish_p24','publication']
    assert any(e['kind']=='removal_reconciled' for e in runs.get(job['id'])['events'])


def test_changed_address_cannot_resume_a_stopped_removal(setup):
    drafts,runs,job=setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='remove')).one(job)
    drafts.list_addresses=lambda *args:[{'id':'different-review'}]
    provider=Provider()
    with pytest.raises(RunNeedsReview, match='address changed'):
        module.RelistRunner(drafts,runs,provider).one(job)
    assert provider.calls==[]


def test_unapplied_archive_requires_fresh_check_and_keeps_prior_attempt(setup):
    drafts,runs,job=setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='archive')).one(job)
    provider=Provider()
    assert module.RelistRunner(drafts,runs,provider).one(job)['state']=='published'
    assert provider.calls[:5]==['prepare','check_removed','check_unarchived','archive','create']
    assert 'remove' not in provider.calls
    assert any(e['kind']=='archive_not_applied' for e in runs.get(job['id'])['events'])


def test_checked_copy_resume_never_repeats_original_or_creation_steps(setup):
    drafts,runs,job=setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='copy')).one(job)
    provider=Provider()
    assert module.RelistRunner(drafts,runs,provider).one(job)['state']=='published'
    assert provider.calls == ['recover_copy','copy','verify','publish_website','publish_p24','publication']


def test_failed_content_read_resumes_same_copy_and_retains_failure(setup):
    drafts,runs,job=setup
    class SavedContentProvider(Provider):
        def copy_content(self,target,plan,store,run_id):
            self.calls.append('copy')
            return store.perform_once(run_id,'copy_media_text',lambda:{'saved':True})
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,SavedContentProvider(fail='verify')).one(job)
    provider=SavedContentProvider()
    assert module.RelistRunner(drafts,runs,provider).one(job)['state']=='published'
    assert provider.calls==['recover_copy','copy','verify','publish_website','publish_p24','publication']
    assert any(e['kind']=='checked_verify_content_retry' and e['data']['state']=='uncertain'
               for e in runs.get(job['id'])['events'])


@pytest.mark.parametrize('opening', ['blocked', 'pending_other'])
def test_same_session_unresolved_other_editor_stops_archived_relist_before_writes(setup, monkeypatch, tmp_path, opening):
    from unittest.mock import patch
    import sys
    from quick_relist.form_copy import CopyStopped
    from quick_relist.hub_readonly import ReadOnlyHub
    from quick_relist.live_relist_ui import HubRelistUI
    from test_editor_recheck import Driver, REF, selector_modules

    drafts, runs, job = setup
    job = {**job, 'archive_before_review': True}
    rid = runs.start(job['id'], source_capture_id='capture', address_revision_id=None,
                     reference=job['rxcu'], listing_id='111111', archive_before_review=True)
    for action in ('remove_portals', 'archive'):
        runs.perform_once(rid, action, lambda: {'verified': True})
    saved = runs.get(job['id'])

    driver = Driver()
    hub = ReadOnlyHub(driver)
    assert REF != job['rxcu']
    if opening == 'blocked':
        driver.link.action = lambda: (driver.add_target(), driver.add_target('synthetic-extra'))
    with patch.dict(sys.modules, selector_modules()):
        with pytest.raises(RuntimeError, match='cannot be safely matched' if opening == 'blocked' else 'still unverified'):
            hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
    assert hub.editor_open_state(job['rxcu']) == ('blocked' if opening == 'blocked' else 'other')
    existing_intent = hub._pending_editor
    provider = HubRelistUI(hub, tmp_path / 'media', clipboard=None)
    def forbidden(*args, **kwargs): pytest.fail('Unresolved editor must stop before Hub input or a new tab')
    for name in ('fill_new_form', 'unpublish', 'archive', 'copy_content', 'verify_content', 'publish'):
        monkeypatch.setattr(provider, name, forbidden)
    monkeypatch.setattr(driver, 'new_window', forbidden, raising=False)
    monkeypatch.setattr(driver, 'window', forbidden)
    with pytest.raises(CopyStopped, match='An earlier Hub editor opening is unresolved'):
        module.RelistRunner(drafts, runs, provider).one(job)
    # Defend the later Save entry point too, even when a form was prepared already.
    with pytest.raises(CopyStopped, match='Close only Quick Relist'):
        provider.create({'handle':'synthetic-prepared','url':'https://fixture.invalid/'}, {})
    assert hub._pending_editor is existing_intent
    assert runs.get(job['id']) == saved
    assert driver.link.clicks == 1 and driver.archive.clicks == 0 and driver.quit_calls == 0


def test_fresh_session_retries_only_verification_on_existing_completed_copy(setup, tmp_path):
    from quick_relist.hub_readonly import ReadOnlyHub
    from quick_relist.live_relist_ui import HubRelistUI
    from test_editor_recheck import Driver

    drafts, _, job = setup
    job = {**job, 'archive_before_review': True}
    path = tmp_path / 'synthetic-run.sqlite3'
    journal = RunStore(path)
    rid = journal.start(job['id'], source_capture_id='capture', address_revision_id=None,
                        reference=job['rxcu'], listing_id='111111', archive_before_review=True)
    for action in ('remove_portals', 'archive'):
        journal.perform_once(rid, action, lambda: {'verified': True})
    binding = {**journal.get(job['id'])['binding'], 'address_revision_id': 'review'}
    journal.bind_archived_review(job['id'], binding, module.apply_relist_dates({
        'particulars': {'mode': 'sale', 'controls': [{'key': key, 'kind': 'date', 'value': '2090-01-01'}
            for key in ('date_listed','on_market_since','expiry_date','mandate_signed_date','mandate_expiry')]}},
        None, date(2099, 8, 31)))
    created = {'reference':'RXCU2000','listing_id':'222222','handle':'old-session-handle'}
    journal.perform_once(rid, 'create', lambda: created)
    journal.perform_once(rid, 'copy_commission', lambda: {'commission_copied':True})
    for i in range(22):
        journal.perform_once(rid, f'upload_photo_{i}_attempt_1', lambda i=i: {'position':i})
    journal.perform_once(rid, 'copy_media_text', lambda: {'descriptions_submitted':True,'unpublished_observed':True})
    with pytest.raises(RuntimeError):
        journal.perform_once(rid, 'verify_content', lambda: (_ for _ in ()).throw(RuntimeError('synthetic stopped read')))
    before = journal.get(job['id'])
    journal.close()
    journal = RunStore(path)
    try:
        fresh = HubRelistUI(ReadOnlyHub(Driver()), tmp_path / 'media', clipboard=None)
        fresh.require_resolved_editor_session()
        class CheckedExisting(Provider):
            def recover_copy(self, plan, previous, runs):
                assert previous == before and runs is journal
                self.calls.append('recover_exact_receipt')
                return {**created,'handle':'fresh-confirmed-tab','content_already_submitted':True}
            def verify_content(self, target, plan):
                assert target['reference'] == created['reference'] and target['listing_id'] == created['listing_id']
                assert target['handle'] == 'fresh-confirmed-tab'
                return self.operation('verify')
        # Use a synthetic cancellation after verification so no publication is tested/authorized here.
        provider = CheckedExisting()
        runner = module.RelistRunner(drafts, journal, provider, today=lambda:date(2099,9,1),
                                     cancelled=lambda: 'verify' in provider.calls)
        with pytest.raises(module.CopyStopped, match='Stopped between steps'):
            runner.one(job)
        after = journal.get(job['id'])
        assert provider.calls == ['recover_exact_receipt','verify']
        assert after['id'] == before['id'] and after['binding'] == before['binding']
        assert [a for a in after['actions'] if a['action'] != 'verify_content'] == [a for a in before['actions'] if a['action'] != 'verify_content']
        assert any(e['kind']=='checked_verify_content_retry' and e['data']['state']=='uncertain' for e in after['events'])
        assert next(a for a in after['actions'] if a['action']=='verify_content')['state']=='done'
        assert not any(a['action'].startswith('publish') for a in after['actions'])
    finally:
        journal.close()


def test_dates_are_saved_before_removal_and_survive_a_later_day_recovery(setup):
    drafts,runs,job=setup
    first=Provider(fail='remove')
    def inspect_removal():
        events=runs.get(job['id'])['events']
        policy=next(e['data'] for e in events if e['kind']=='date_policy')
        assert policy['replacement_dates']['date_listed']=='2099-08-31'
        return first.operation('remove')
    first.unpublish=lambda *args:inspect_removal()
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,first,today=lambda:date(2099,8,31)).one(job)
    resumed=Provider()
    result=module.RelistRunner(drafts,runs,resumed,today=lambda:date(2099,9,3)).one(job)
    dates={c['key']:c['value'] for c in resumed.prepared_plan['particulars']['controls']}
    assert dates['date_listed']=='2099-08-31'
    assert dates['expiry_date']=='2100-02-28'
    assert result['dates']==dates
    assert 'remove' not in resumed.calls


@pytest.mark.parametrize('extra', ['publish_website','create_other'])
def test_unrecognized_or_later_uncertain_action_cannot_resume_photos(setup, extra):
    drafts,runs,job=setup
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='copy')).one(job)
    run=runs.get(job['id'])['id']
    def unknown():raise TimeoutError()
    with pytest.raises(TimeoutError):runs.perform_once(run,extra,unknown)
    assert not module.copy_can_be_checked(runs.get(job['id']))


def test_recovered_completed_copy_still_verifies_before_every_publication(setup):
    drafts,runs,job = setup
    provider = Provider(fail='verify')
    provider.copy_content = lambda target, plan, store, run_id: store.perform_once(
        run_id, 'copy_media_text', lambda:{'descriptions_submitted':True})
    runner = module.RelistRunner(drafts,runs,provider)
    with pytest.raises(RuntimeError):
        runner.one(job)
    provider.fail = None
    provider.calls.clear()
    def completed(*args):
        provider.calls.append('recover_copy')
        return {'reference':'RXCU2000','listing_id':'222222','content_already_submitted':True}
    provider.recover_copy = completed
    assert runner.one(job)['state'] == 'published'
    assert provider.calls == ['recover_copy','verify','publish_website','publish_p24','publication']


@pytest.mark.parametrize('failure', [None, 'check'])
def test_archive_preflight_is_read_only_and_fresh_source_is_rechecked(setup, failure):
    drafts, runs, job = setup
    job = {**job, 'archive_before_review':1}
    data = {**module.latest_collection(drafts,job['id'])['capture'], 'source':{}}
    provider = Provider(fail=failure)
    runner = module.RelistRunner(drafts,runs,provider)
    if failure:
        with pytest.raises(RuntimeError): runner.preflight_archive_collection(job,data)
    else:
        runner.preflight_archive_collection(job,data)
    assert provider.calls == ['check']
    assert runs.get(job['id']) is None
    if not failure:
        provider.fail = 'check'
        with pytest.raises(RuntimeError): runner.archive_collected(job,data,freshly_collected=False)
        assert provider.calls == ['check','check']
        assert runs.get(job['id'])['actions'] == []


def test_preflight_keeps_confirmed_archive_receipts_unchanged(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    provider=Provider()
    runner=module.RelistRunner(drafts,runs,provider)
    runner.archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    provider.calls.clear()
    runner.preflight_archive_collection(job,data)
    assert runs.get(job['id']) == before
    assert provider.calls == []


def test_preflight_does_not_reconcile_or_repeat_uncertain_removal(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    runner=module.RelistRunner(drafts,runs,Provider(fail='remove'))
    with pytest.raises(RuntimeError): runner.archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    provider=Provider()
    module.RelistRunner(drafts,runs,provider).preflight_archive_collection(job,data)
    assert provider.calls == ['check_removed']
    assert runs.get(job['id']) == before


@pytest.mark.parametrize('change', ['none','material','destinations'])
def test_deferred_archive_reacquires_a_from_b_and_rejects_changed_source(setup, change):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    provider=Provider()
    provider.selected='B'
    def check(collection,plan):
        provider.selected='A'
        provider.calls.append('check')
        if change != 'none':
            raise module.CopyStopped('Synthetic changed '+change)
    provider.check_source=check
    provider.use_collected_source=lambda data:pytest.fail('Deferred source must be freshly reread')
    runner=module.RelistRunner(drafts,runs,provider)
    if change == 'none':
        runner.archive_collected(job,data,freshly_collected=False)
        assert provider.calls == ['check','remove','archive']
    else:
        with pytest.raises(module.CopyStopped): runner.archive_collected(job,data,freshly_collected=False)
        assert provider.calls == ['check']
        assert runs.get(job['id'])['actions'] == []
    assert provider.selected == 'A'


def test_cancellation_after_removal_preserves_capture_and_resumes_without_replay(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    provider=Provider()
    runner=module.RelistRunner(drafts,runs,provider,cancelled=lambda:'remove' in provider.calls)
    with pytest.raises(module.CopyStopped): runner.archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    assert provider.calls == ['check','remove']
    assert before['binding']['source_capture_id'] == data['source_capture_id']
    assert [(a['action'],a['state']) for a in before['actions']] == [('remove_portals','done')]
    later=Provider()
    resume=module.RelistRunner(drafts,runs,later)
    resume.preflight_archive_collection(job,data)
    resume.archive_collected(job,data,freshly_collected=False)
    after=runs.get(job['id'])
    assert later.calls == ['check_removed','check_removed','archive']
    assert after['binding'] == before['binding']
    assert after['actions'][0] == before['actions'][0]


def test_uncertain_archive_preflight_preserves_receipt_and_stops_when_unproven(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    runner=module.RelistRunner(drafts,runs,Provider(fail='archive'))
    with pytest.raises(RuntimeError): runner.archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    provider=Provider()
    def inspect(plan):
        provider.calls.append('inspect_archive')
        raise RunNeedsReview('Synthetic archive outcome unproven')
    provider.inspect_archive=inspect
    with pytest.raises(RunNeedsReview):
        module.RelistRunner(drafts,runs,provider).preflight_archive_collection(job,data)
    assert provider.calls == ['inspect_archive']
    assert runs.get(job['id']) == before


@pytest.mark.parametrize('change', ['same_capture_stale','different_capture'])
def test_preflight_never_repins_existing_no_action_run(setup,change):
    drafts,runs,job=setup
    job={**job,'archive_before_review':1}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    runs.start(job['id'],source_capture_id=data['source_capture_id'],address_revision_id=None,
               reference=job['rxcu'],listing_id=data['listing_id'],archive_before_review=True)
    before=deepcopy(runs.get(job['id']))
    provider=Provider(fail='check')
    if change=='different_capture': data['source_capture_id']='new-capture'
    with pytest.raises((RuntimeError,RunNeedsReview)):
        module.RelistRunner(drafts,runs,provider).preflight_archive_collection(job,data)
    assert runs.get(job['id'])==before
    assert provider.calls==(['check'] if change=='same_capture_stale' else [])


@pytest.mark.parametrize('pending_at', ['preflight','remove','archive'])
def test_pending_editor_blocks_early_archive_with_cached_source(setup,monkeypatch,tmp_path,pending_at):
    from quick_relist import live_relist_ui as live
    drafts,runs,job=setup
    job={**job,'archive_before_review':True}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    build=module.build_relist_plan
    monkeypatch.setattr(module,'build_relist_plan',lambda *a:{**build(*a),'source_reference':job['rxcu']})
    intent=object()
    hub=SimpleNamespace(driver=SimpleNamespace(),_pending_editor=intent if pending_at=='preflight' else None)
    hub.has_unresolved_editor_opening=lambda:hub._pending_editor is not None
    provider=live.HubRelistUI(hub,tmp_path/'media',clipboard=None)
    provider.current={'handle':'synthetic-cached-source'}
    calls=[]
    def capture(*a,**kw):
        assert kw['expected_listing_id']==data['listing_id']
        calls.append('read_bound_source')
        if pending_at=='remove': hub._pending_editor=intent
        return {}
    provider.captured_editor=capture
    provider._source_matches=lambda *a:True
    provider.tab=lambda *a:None
    monkeypatch.setattr(live,'read_portals',lambda *a,**kw:{})
    monkeypatch.setattr(live,'active_destinations',lambda *a:['website','p24'])
    def remove(*a):
        calls.append('remove')
        hub._pending_editor=intent
        return {'observed':True}
    provider.unpublish=remove
    provider.archive=lambda *a:pytest.fail('No archive may follow an unresolved intent')
    runner=module.RelistRunner(drafts,runs,provider)
    if pending_at=='preflight':
        with pytest.raises(module.CopyStopped,match='unresolved'):runner.preflight_archive_collection(job,data)
        assert calls==[] and runs.get(job['id']) is None
    else:
        with pytest.raises(module.CopyStopped,match='unresolved'):runner.archive_collected(job,data,freshly_collected=False)
        assert calls==(['read_bound_source'] if pending_at=='remove' else ['read_bound_source','remove'])
        assert [a['action'] for a in runs.get(job['id'])['actions']]==([] if pending_at=='remove' else ['remove_portals'])
    assert hub._pending_editor is intent


@pytest.mark.parametrize('outcome',['archived','unknown','not_archived'])
def test_pending_intent_does_not_block_archive_observation_or_replay_writes(setup,outcome):
    drafts,runs,job=setup
    job={**job,'archive_before_review':True}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='archive')).archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    provider=Provider()
    def blocked():raise module.CopyStopped('Synthetic unresolved intent')
    provider.require_resolved_editor_session=blocked
    def inspect(plan):
        provider.calls.append('inspect_archive')
        if outcome=='unknown':raise RunNeedsReview('Synthetic unproven archive')
        return {'reference':job['rxcu'],'listing_id':data['listing_id'],
                'reconciled_by':'fresh_manage_listing_archive_action','archived':outcome=='archived'}
    provider.inspect_archive=inspect
    runner=module.RelistRunner(drafts,runs,provider)
    if outcome=='archived':
        runner.preflight_archive_collection(job,data)
        runner.archive_collected(job,data,freshly_collected=False)
        assert module.archived_for_review(runs.get(job['id']))
    else:
        with pytest.raises((module.CopyStopped,RunNeedsReview)):
            runner.preflight_archive_collection(job,data)
        assert runs.get(job['id'])==before
    assert not any(c in provider.calls for c in ('remove','archive','create'))
    assert runs.get(job['id'])['actions'][0]==before['actions'][0]


def test_pending_intent_allows_removal_reconciliation_but_not_archive(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':True}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    with pytest.raises(RuntimeError):
        module.RelistRunner(drafts,runs,Provider(fail='remove')).archive_collected(job,data,freshly_collected=False)
    provider=Provider()
    def blocked():raise module.CopyStopped('Synthetic unresolved intent')
    provider.require_resolved_editor_session=blocked
    with pytest.raises(module.CopyStopped,match='unresolved'):
        module.RelistRunner(drafts,runs,provider).archive_collected(job,data,freshly_collected=False)
    assert provider.calls==['check_removed']
    record=runs.get(job['id'])
    assert [(a['action'],a['state']) for a in record['actions']]==[('remove_portals','done')]
    assert record['binding']['source_capture_id']==data['source_capture_id']


def test_confirmed_archive_is_noop_even_with_pending_intent(setup):
    drafts,runs,job=setup
    job={**job,'archive_before_review':True}
    data={**module.latest_collection(drafts,job['id'])['capture'],'source':{}}
    runner=module.RelistRunner(drafts,runs,Provider())
    runner.archive_collected(job,data,freshly_collected=False)
    before=deepcopy(runs.get(job['id']))
    runner.provider.require_resolved_editor_session=lambda:pytest.fail('Completed receipt needs no new-write permission')
    runner.preflight_archive_collection(job,data)
    runner.archive_collected(job,data,freshly_collected=False)
    assert runs.get(job['id'])==before


@pytest.mark.parametrize('pending_after',['prepare','create','verify'])
def test_reviewed_path_rechecks_session_before_each_new_write(setup,pending_after):
    drafts,runs,job=setup
    provider=Provider()
    unresolved=False
    original=provider.operation
    def operation(name):
        nonlocal unresolved
        result=original(name)
        if name==pending_after:unresolved=True
        return result
    provider.operation=operation
    def require():
        if unresolved:raise module.CopyStopped('Synthetic unresolved intent')
    provider.require_resolved_editor_session=require
    with pytest.raises(module.CopyStopped,match='unresolved'):
        module.RelistRunner(drafts,runs,provider).one(job)
    if pending_after=='prepare':assert provider.calls==['prepare','check']
    elif pending_after=='create':assert provider.calls==['prepare','check','remove','archive','create']
    else:assert provider.calls==['prepare','check','remove','archive','create','copy','verify']
    assert not any(c.startswith('publish') for c in provider.calls)
