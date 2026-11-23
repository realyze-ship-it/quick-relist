"""Sequential relisting with durable intent and no retry of uncertain writes."""
import re
import time
from .collection import latest_collection
from .form_copy import CopyStopped
from .relist_plan import build_relist_plan
from .run_store import RunNeedsReview
from .brochure_review import apply_review
from .relist_dates import apply_relist_dates, relist_today
from .portal_ui import only_moderation_pending


def run_status(run):
    return next((e['data'] for e in reversed((run or {}).get('events', []))
                 if e['kind'] in ('status', 'manual_recovery', 'manual_publication_check')), {})


def batch_can_advance(status):
    status = status or {}
    return (status.get('state') in ('published', 'manual_completed')
            or status.get('state') == 'publication_pending'
            and only_moderation_pending(status.get('publication', {}).get('portals', {})))


def display_status(run):
    """Display a stopped attempt without presenting old receipts as live state."""
    if not run:
        return None
    status = dict(run_status(run))
    if status.get('mode') == 'manual_recovery':
        return status
    if archived_for_review(run):
        status.update(state='archived', original_archived=True)
    unresolved = [a['action'] for a in run.get('actions', []) if a['state'] in ('started', 'uncertain')]
    latest_outcome = next((e['kind'] for e in reversed(run.get('events', []))
                           if e['kind'] in ('status', 'stopped')), None)
    if unresolved or latest_outcome == 'stopped':
        status.update(last_verified_state=status.get('state'), state='needs_review',
                      unresolved_actions=unresolved)
    return status or {'state': 'needs_review'}


def removal_can_be_checked(run):
    actions = (run or {}).get('actions', [])
    states = {a['action']:a['state'] for a in actions}
    return (set(states) == {'remove_portals'} and states['remove_portals'] in ('started','uncertain')) or (
        states.get('remove_portals') == 'done' and set(states) <= {'remove_portals','archive'}
        and states.get('archive') in (None, 'uncertain', 'retry_ready'))


def copy_can_be_checked(run):
    actions = {a['action']: a for a in (run or {}).get('actions', [])}
    if any(actions.get(name, {}).get('state') != 'done' for name in ('remove_portals','archive','create')):
        return False
    commission = actions.get('copy_commission', {})
    if commission.get('state') in ('started', 'uncertain'):
        return (set(actions) - {'save_estate_name'} == {'remove_portals', 'archive', 'create', 'copy_commission'}
                and actions.get('save_estate_name', {}).get('state', 'done') == 'done')
    if actions.get('save_estate_name', {}).get('state') in ('started','uncertain'):
        return set(actions) == {'remove_portals','archive','create','save_estate_name'}
    for name, action in actions.items():
        if name in ('remove_portals','archive','create'):
            continue
        if re.fullmatch(r'upload_photo_[0-9]+_attempt_[1-3]', name):
            if action['state'] not in ('done','started','uncertain'):
                return False
        elif name == 'copy_media_text':
            if action['state'] not in ('done','uncertain','started','retry_ready'):
                return False
        elif name in ('copy_commission', 'save_estate_name') or re.fullmatch(r'caption_[0-9]+', name):
            if action['state'] != 'done':
                return False
        elif name == 'verify_content':
            if action['state'] not in ('started','uncertain'):
                return False
        else:
            return False
    return True


def archived_for_review(run):
    return bool(run and run.get('binding', {}).get('archive_before_review') and
                {a['action']:a['state'] for a in run['actions']} == {'remove_portals':'done','archive':'done'}
                and not any(e['kind'] == 'manual_recovery' for e in run['events']))


class RelistRunner:
    def __init__(self, drafts, runs, provider, *, cancelled=lambda: False, progress=lambda text: None,
                 today=relist_today):
        self.drafts, self.runs, self.provider = drafts, runs, provider
        self.cancelled, self.progress = cancelled, progress
        self.today = today

    def batch(self, jobs):
        results = []
        for job in jobs:
            if self.cancelled():
                break
            if job.get('state') == 'cancelled':
                continue
            results.append(self.one(job))
            if not batch_can_advance(results[-1]):
                break
        return results

    def require_new_write_session(self, job_id, action):
        # A completed receipt is returned by perform_once without another write.
        # Keep read-only verification/reconciliation available in stopped runs.
        previous = self.runs.get(job_id)
        if any(a['action'] == action and a['state'] == 'done'
               for a in (previous or {}).get('actions', [])):
            return
        self.provider.require_resolved_editor_session()

    def remove_original(self, run_id, plan, resuming=False):
        return self.provider.unpublish(plan, lambda key, ident:self.runs.record_portal_intent(run_id, key, ident), resuming)

    def check_removal(self, previous, collection, plan, *, preflight=False):
        actions = {a['action']:a['state'] for a in previous['actions']}
        unfinished = set(actions) == {'remove_portals'} and actions['remove_portals'] in ('started','uncertain')
        receipt = self.provider.check_removed_source(collection, plan, unfinished)
        remaining = set(receipt.get('remaining', []))
        attempted = {e['data']['destination'] for e in previous['events'] if e['kind'] == 'portal_unpublish_intent'}
        blocked = sorted(remaining & attempted)
        if blocked:
            raise RunNeedsReview('Hub still shows a previously requested withdrawal as live: ' + ', '.join(blocked))
        if not unfinished or preflight:
            return
        if remaining:
            self.provider.require_resolved_editor_session()
            receipt = self.remove_original(previous['id'], plan, resuming=True)
            receipt['reconciled_by'] = 'fresh_source_and_portal_read'
        self.runs.reconcile_removal(previous['id'], receipt)

    def preflight_archive_collection(self, job, collection, *, freshly_collected=False):
        """Read-only batch barrier; never start/reconcile a run or change Hub."""
        from .address import propose_address_fields
        from .relist_dates import date_policy
        if not job.get('archive_before_review') or collection['rxcu'] != job['rxcu']:
            raise RunNeedsReview('This batch was not started for early archiving.')
        self.runs.assert_source_available(job['id'], collection['rxcu'], collection['listing_id'])
        original = {f['path']:f['field'] for f in propose_address_fields(collection['source'])['fields'].values()}
        plan = build_relist_plan(collection, {'original':original, 'edited':original})
        date_policy(plan['particulars'], self.today())
        previous = self.runs.get(job['id'])
        expected = {'source_capture_id':collection['source_capture_id'], 'address_revision_id':None,
                    'reference':job['rxcu'], 'listing_id':collection['listing_id'], 'archive_before_review':True}
        if previous and previous['binding'] != expected:
            raise RunNeedsReview('The saved source changed after this run. Check its existing outcome.')
        if archived_for_review(previous):
            return
        states = {a['action']:a['state'] for a in (previous or {}).get('actions', [])}
        if set(states) - {'remove_portals','archive'} or any(
                e['kind']=='manual_recovery' for e in (previous or {}).get('events', [])):
            raise RunNeedsReview('This listing has a later relist action. Check its existing outcome.')
        if self.cancelled():
            raise CopyStopped('Stopped during batch preflight. No new original was changed.')
        if 'archive' in states:
            receipt = self.provider.inspect_archive(plan)
            if receipt['archived']:
                return
        if 'remove_portals' in states:
            self.check_removal(previous, collection, plan, preflight=True)
            self.provider.require_resolved_editor_session()
        else:
            self.provider.require_resolved_editor_session()
            if freshly_collected and not previous:
                self.provider.check_collected_portals(collection, plan)
            else:
                self.provider.check_source(collection, plan)

    def archive_collected(self, job, collection, *, freshly_collected):
        """Use the saved source as-is for backup validation. No address approval is recorded here."""
        from .address import propose_address_fields
        from .relist_dates import date_policy
        if not job.get('archive_before_review') or collection['rxcu'] != job['rxcu']:
            raise RunNeedsReview('This batch was not started for early archiving.')
        original = {f['path']:f['field'] for f in propose_address_fields(collection['source'])['fields'].values()}
        plan = build_relist_plan(collection, {'original':original, 'edited':original})
        date_policy(plan['particulars'], self.today())
        run_id = self.runs.start(job['id'], source_capture_id=collection['source_capture_id'], address_revision_id=None,
                                 reference=job['rxcu'], listing_id=collection['listing_id'], archive_before_review=True)
        previous = self.runs.get(job['id'])
        if archived_for_review(previous):
            return
        states = {a['action']:a['state'] for a in previous['actions']}
        if set(states) - {'remove_portals','archive'} or any(e['kind']=='manual_recovery' for e in previous['events']):
            raise RunNeedsReview('This listing has a later relist action. Check its existing outcome.')
        if self.cancelled():
            raise CopyStopped('Stopped before archiving. The saved batch can be resumed.')
        try:
            if 'archive' in states:
                receipt = self.provider.inspect_archive(plan)
                if receipt['archived']:
                    self.runs.reconcile_observed_archive(run_id, receipt)
                    self.runs.note(run_id, 'status', {'state':'archived','original_archived':True})
                    return
                self.runs.reconcile_archive_not_applied(run_id, receipt)
            if 'remove_portals' in states:
                self.check_removal(previous, collection, plan)
            elif freshly_collected:
                self.provider.use_collected_source(collection)
            else:
                self.progress(job['rxcu'] + ': verifying the original against its saved backup')
                self.provider.check_source(collection, plan)
            self.progress(job['rxcu'] + ': removing the original from its portals')
            self.require_new_write_session(job['id'], 'remove_portals')
            self.runs.perform_once(run_id, 'remove_portals', lambda:self.remove_original(run_id, plan))
            self.runs.note(run_id, 'status', {'state':'portals_removed'})
            if self.cancelled():
                raise CopyStopped('Stopped after portal removal. Resume the archive step in this batch.')
            self.progress(job['rxcu'] + ': archiving the original')
            self.require_new_write_session(job['id'], 'archive')
            self.runs.perform_once(run_id, 'archive', lambda:self.provider.archive(plan))
            self.runs.note(run_id, 'status', {'state':'archived','original_archived':True})
        except Exception:
            self.runs.note(run_id,'stopped',{'message':'Check the recorded removal and archive outcome.'})
            raise

    def one(self, job):
        started = time.monotonic()
        self.runs.assert_source_available(job['id'], job['rxcu'])
        previous = self.runs.get(job['id'])
        status = run_status(previous)
        if batch_can_advance(display_status(previous)):
            return status
        resuming_copy = copy_can_be_checked(previous)
        archived = archived_for_review(previous)
        if archived:
            status['original_archived'] = True
        if job.get('archive_before_review') and not (archived or resuming_copy):
            raise RunNeedsReview('Finish collecting and archiving this original before starting its replacement.')
        if previous and not (removal_can_be_checked(previous) or resuming_copy or archived):
            raise RunNeedsReview('This listing has a previous run. Check its existing outcome before continuing; no new copy was created.')
        collected = latest_collection(self.drafts, job['id'])
        if not collected:
            raise CopyStopped('Collect every listing before starting the batch.')
        data = collected['capture']
        self.runs.assert_source_available(job['id'], data['rxcu'], data['listing_id'])
        reviews = self.drafts.list_addresses(data['source_capture_id'])
        if not reviews:
            raise CopyStopped('Save the address review for every listing before starting the batch.')
        review = reviews[0]
        plan = build_relist_plan(data, review)
        apply_review(plan, self.drafts, job['id'], data)
        binding = {'source_capture_id':data['source_capture_id'], 'address_revision_id':review['id'],
                   'reference':data['rxcu'], 'listing_id':data['listing_id']}
        if (previous or {}).get('binding', {}).get('archive_before_review'):
            binding['archive_before_review'] = True
        first_review = archived and previous['binding'].get('address_revision_id') is None
        expected_binding = {**binding, 'address_revision_id':None} if first_review else binding
        if previous and previous['binding'] != expected_binding:
            raise RunNeedsReview('The source or reviewed address changed after the stopped run. Review it before continuing.')
        if resuming_copy:
            plan['commission_review'] = next((event['data'] for event in reversed(previous['events'])
                                              if event['kind'] == 'commission_review'), None)
        policy = apply_relist_dates(plan, None if first_review else previous, self.today())
        if self.cancelled():
            raise CopyStopped('Relisting stopped before the original was changed.')
        # Preparing an unsaved form cannot remove the source. Recheck the
        # original afterwards, immediately before the destructive sequence.
        self.progress(job['rxcu'] + (': checking the saved replacement and received photos' if resuming_copy
                                    else ': preparing the reviewed form without saving'))
        target = self.provider.recover_copy(plan, previous, self.runs) if resuming_copy else None
        if archived:
            plan['source_already_archived'] = True
        prepared = None if resuming_copy else self.provider.prepare(plan)
        if self.cancelled():
            raise CopyStopped('Stopped before saving the replacement.')
        if previous and not (resuming_copy or archived):
            self.progress(job['rxcu'] + ': checking the recorded removal before continuing')
            self.check_removal(previous, data, plan)
            actions = {a['action']:a['state'] for a in previous['actions']}
            if actions.get('archive') in ('uncertain', 'retry_ready'):
                archive_state = self.provider.check_unarchived_source(plan)
                self.runs.reconcile_archive_not_applied(previous['id'], archive_state)
        elif not previous:
            self.progress(job['rxcu'] + ': rechecking the original before portal removal')
            self.provider.check_source(data, plan)
        if first_review:
            self.runs.bind_archived_review(job['id'], binding, policy)
        run_id = previous['id'] if archived or resuming_copy else self.runs.start(job['id'], source_capture_id=data['source_capture_id'],
            address_revision_id=review['id'], reference=data['rxcu'], listing_id=data['listing_id'])
        if policy and not previous:
            self.runs.note(run_id, 'date_policy', policy)
        if plan.get('brochure_review_id'):
            self.runs.note(run_id, 'brochure_review', {'review_id':plan['brochure_review_id']})
        def record(state, **extra):
            status.update(state=state, **extra)
            if policy:
                status['dates'] = policy['replacement_dates']
            self.runs.note(run_id, 'status', status)
        def step(action, operation, message):
            if self.cancelled():
                raise CopyStopped('Stopped between steps. Check the saved progress before resuming.')
            self.progress(job['rxcu'] + ': ' + message)
            if action != 'verify_content':
                self.require_new_write_session(job['id'], action)
            return self.runs.perform_once(run_id, action, operation)
        try:
            if not resuming_copy and not archived:
                step('remove_portals', lambda: self.remove_original(run_id, plan), 'removing the original from its portals')
                record('portals_removed')
                step('archive', lambda: self.provider.archive(plan), 'archiving the original as ' +
                     ('withdrawn' if plan.get('particulars',{}).get('mode') == 'rent' else 'mandate expired'))
                record('archived', original_archived=True)
            if not resuming_copy:
                target = step('create', lambda: self.provider.create(prepared, plan), 'saving the reviewed replacement')
            record('created', target_reference=target['reference'], target_listing_id=target['listing_id'])
            if not target.get('content_already_submitted'):
                self.provider.require_resolved_editor_session()
                self.progress(job['rxcu'] + ': copying descriptions and checking every photo')
                self.provider.copy_content(target, plan, self.runs, run_id)
            verified = step('verify_content', lambda: self.provider.verify_content(target, plan), 'checking the saved replacement against the original')
            record('verified', verification=verified)
            for destination in plan['destinations']:
                step('publish_' + destination,
                     lambda destination=destination: self.provider.publish(target, destination),
                     'sending the replacement to ' + destination)
            outcome = self.provider.publication(target, plan['destinations'])
            record('published' if outcome['complete'] else 'publication_pending', publication=outcome,
                   relist_session_seconds=round(time.monotonic() - started, 2))
            return dict(status)
        except Exception:
            self.runs.note(run_id, 'stopped', {'after_state': status.get('state','prepared'),
                'message':'The run stopped. Reconcile the observed outcome before any retry.'})
            raise
