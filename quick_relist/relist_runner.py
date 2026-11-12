"""Sequential relisting with durable intent and no retry of uncertain writes."""
import re
from .collection import latest_collection
from .form_copy import CopyStopped
from .relist_plan import build_relist_plan
from .run_store import RunNeedsReview
from .brochure_review import apply_review


def run_status(run):
    return next((e['data'] for e in reversed((run or {}).get('events', []))
                 if e['kind'] == 'status'), {})


def removal_can_be_checked(run):
    actions = (run or {}).get('actions', [])
    states = {a['action']:a['state'] for a in actions}
    return states == {'remove_portals':'uncertain'} or (
        states.get('remove_portals') == 'done' and set(states) <= {'remove_portals','archive'}
        and states.get('archive') in (None, 'uncertain', 'retry_ready'))


def copy_can_be_checked(run):
    actions = {a['action']: a for a in (run or {}).get('actions', [])}
    if any(actions.get(name, {}).get('state') != 'done' for name in ('remove_portals','archive','create')):
        return False
    for name, action in actions.items():
        if name in ('remove_portals','archive','create'):
            continue
        if re.fullmatch(r'upload_photo_[0-9]+_attempt_[1-3]', name):
            if action['state'] not in ('done','started','uncertain'):
                return False
        elif name == 'copy_media_text':
            if action['state'] not in ('done','uncertain','started','retry_ready'):
                return False
        elif name == 'copy_commission' or re.fullmatch(r'caption_[0-9]+', name):
            if action['state'] != 'done':
                return False
        else:
            return False
    return True


class RelistRunner:
    def __init__(self, drafts, runs, provider, *, cancelled=lambda: False, progress=lambda text: None):
        self.drafts, self.runs, self.provider = drafts, runs, provider
        self.cancelled, self.progress = cancelled, progress

    def batch(self, jobs):
        results = []
        for job in jobs:
            if self.cancelled():
                break
            if job.get('state') == 'cancelled':
                continue
            results.append(self.one(job))
            if results[-1]['state'] != 'published':
                break  # A pending portal or incomplete copy blocks the next source.
        return results

    def one(self, job):
        previous = self.runs.get(job['id'])
        status = run_status(previous)
        if status.get('state') == 'published':
            return status  # Includes a completed, explicitly recorded manual pilot.
        resuming_copy = copy_can_be_checked(previous)
        if previous and not (removal_can_be_checked(previous) or resuming_copy):
            raise RunNeedsReview('This listing has a previous run. Check its existing outcome before continuing; no new copy was created.')
        collected = latest_collection(self.drafts, job['id'])
        if not collected:
            raise CopyStopped('Collect every listing before starting the batch.')
        data = collected['capture']
        reviews = self.drafts.list_addresses(data['source_capture_id'])
        if not reviews:
            raise CopyStopped('Save the address review for every listing before starting the batch.')
        review = reviews[0]
        plan = build_relist_plan(data, review)
        apply_review(plan, self.drafts, job['id'], data)
        binding = {'source_capture_id':data['source_capture_id'], 'address_revision_id':review['id'],
                   'reference':data['rxcu'], 'listing_id':data['listing_id']}
        if previous and previous['binding'] != binding:
            raise RunNeedsReview('The source or reviewed address changed after the stopped run. Review it before continuing.')
        if self.cancelled():
            raise CopyStopped('Relisting stopped before the original was changed.')
        # Preparing an unsaved form cannot remove the source. Recheck the
        # original afterwards, immediately before the destructive sequence.
        self.progress(job['rxcu'] + (': checking the saved replacement and received photos' if resuming_copy
                                    else ': preparing the reviewed form without saving'))
        target = self.provider.recover_copy(plan, previous, self.runs) if resuming_copy else None
        prepared = None if resuming_copy else self.provider.prepare(plan)
        if self.cancelled():
            raise CopyStopped('Stopped with an unsaved form. The original remains active.')
        self.progress(job['rxcu'] + (': checking the recorded removal before continuing' if previous
                                    else ': rechecking the original before portal removal'))
        if resuming_copy:
            pass  # The existing replacement was freshly checked; never recreate it.
        elif previous:
            receipt = self.provider.check_removed_source(data, plan)
            actions = {a['action']:a['state'] for a in previous['actions']}
            if actions['remove_portals'] == 'uncertain':
                self.runs.reconcile_removal(previous['id'], receipt)
            if actions.get('archive') in ('uncertain', 'retry_ready'):
                archive_state = self.provider.check_unarchived_source(plan)
                self.runs.reconcile_archive_not_applied(previous['id'], archive_state)
        else:
            self.provider.check_source(data, plan)
        run_id = self.runs.start(job['id'], source_capture_id=data['source_capture_id'],
            address_revision_id=review['id'], reference=data['rxcu'], listing_id=data['listing_id'])
        if plan.get('brochure_review_id'):
            self.runs.note(run_id, 'brochure_review', {'review_id':plan['brochure_review_id']})
        def record(state, **extra):
            status.update(state=state, **extra)
            self.runs.note(run_id, 'status', status)
        def step(action, operation, message):
            if self.cancelled():
                raise CopyStopped('Stopped between steps. Check the saved progress before resuming.')
            self.progress(job['rxcu'] + ': ' + message)
            return self.runs.perform_once(run_id, action, operation)
        try:
            if not resuming_copy:
                step('remove_portals', lambda: self.provider.unpublish(plan), 'removing the original from its portals')
                record('portals_removed')
                step('archive', lambda: self.provider.archive(plan), 'archiving the original as ' +
                     ('withdrawn' if plan.get('particulars',{}).get('mode') == 'rent' else 'mandate expired'))
                record('archived', original_archived=True)
                target = step('create', lambda: self.provider.create(prepared, plan), 'saving the reviewed replacement')
            record('created', target_reference=target['reference'], target_listing_id=target['listing_id'])
            self.progress(job['rxcu'] + ': copying descriptions and checking every photo')
            self.provider.copy_content(target, plan, self.runs, run_id)
            verified = step('verify_content', lambda: self.provider.verify_content(target, plan), 'checking the saved replacement against the original')
            record('verified', verification=verified)
            for destination in plan['destinations']:
                step('publish_' + destination,
                     lambda destination=destination: self.provider.publish(target, destination),
                     'sending the replacement to ' + destination)
            outcome = self.provider.publication(target, plan['destinations'])
            record('published' if outcome['complete'] else 'publication_pending', publication=outcome)
            return dict(status)
        except Exception:
            self.runs.note(run_id, 'stopped', {'after_state': status.get('state','prepared'),
                'message':'The run stopped. Reconcile the observed outcome before any retry.'})
            raise
