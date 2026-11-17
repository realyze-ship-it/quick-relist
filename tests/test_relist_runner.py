from copy import deepcopy
from types import SimpleNamespace
from datetime import date
import pytest
from quick_relist import relist_runner as module
from quick_relist.run_store import RunStore, RunNeedsReview


class Provider:
    def __init__(self, fail=None, pending=False):
        self.calls=[];self.fail=fail;self.pending=pending
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


def test_dates_are_saved_before_removal_and_survive_a_later_day_recovery(setup):
    drafts,runs,job=setup
    first=Provider(fail='remove')
    def inspect_removal():
        events=runs.get(job['id'])['events']
        policy=next(e['data'] for e in events if e['kind']=='date_policy')
        assert policy['replacement_dates']['date_listed']=='2099-08-31'
        return first.operation('remove')
    first.unpublish=lambda plan:inspect_removal()
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
