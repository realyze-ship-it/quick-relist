from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quick_relist import live_relist_ui as live
from quick_relist.form_copy import CopyStopped
from quick_relist.portal_ui import PORTALS, remaining_destinations
from quick_relist.relist_runner import RelistRunner
from quick_relist.run_store import RunNeedsReview, RunStore


def scenario():
    ids = {'website':'1000', 'p24':'2000', 'pp':'T3000', 'global':'4000-5'}
    plan = {'source_reference':'RXCU1000', 'source_listing_id':'1000',
            'destinations':list(ids), 'source_portal_ids':ids}
    state = {'published':True, 'portals':{}}
    for key, _, _ in PORTALS.values():
        state['portals'][key] = {'id':ids.get(key), 'state':'published' if key in ids else 'unpublished'}
    return plan, state


def offline(store, key):
    store['portals'][key] = {'id':None, 'state':'unpublished'}
    store['published'] = any(p['state'] == 'published' for p in store['portals'].values())


@pytest.mark.parametrize('resume', [True, False])
def test_withdraws_one_matched_column_at_a_time_and_waits_for_each(tmp_path, monkeypatch, resume):
    plan, state = scenario()
    if resume:
        offline(state, 'p24')
    driver = Mock()
    ui = live.HubRelistUI(SimpleNamespace(driver=driver, has_unresolved_editor_opening=lambda:False),
                         tmp_path, clipboard=None)
    ui.source = {'reference':plan['source_reference'], 'listing_id':plan['source_listing_id']}
    ui.bind, ui.tab, ui.guard = Mock(), Mock(), Mock()
    clicks, intents, confirmations = [], [], []
    cells = {key:object() for key, _, _ in PORTALS.values()}
    def find(by, xpath):
        assert by == 'xpath'
        selected = [key for i, (header, (key, _, _)) in enumerate(PORTALS.items(), 2)
                    if f'normalize-space(.)="{header}"' in xpath and xpath.endswith(f'/td[{i}]')]
        return [cells[k] for k in selected]
    driver.find_elements = find
    def click(label, *, root, guard, before_send):
        key, publish, _ = next(values for values in PORTALS.values() if cells[values[0]] is root)
        assert label == publish.replace('Publish to ', 'Unpublish from ')
        assert state['portals'][key]['state'] == 'published' and clicks == confirmations
        guard()
        before_send()
        assert intents[-1] == (key, plan['source_portal_ids'][key])
        clicks.append(key)
    ui.button = click
    def wait(read, message, *, timeout):
        if clicks != confirmations:
            assert read() is None
            offline(state, clicks[-1])
            confirmations.append(clicks[-1])
        assert read(), message
        return read()
    ui.wait = wait
    monkeypatch.setattr(live, 'read_portals', lambda *a, **kw:deepcopy(state))
    receipt = ui.unpublish(plan, lambda k,i:intents.append((k,i)), resume)
    assert clicks == (['pp','global','website'] if resume else ['p24','pp','global','website'])
    assert receipt['observed']['published'] is False


@pytest.mark.parametrize('change', ['other_portal', 'changed_id', 'pending', 'unknown'])
def test_ambiguous_or_changed_publications_block_removal(change):
    plan, state = scenario()
    if change == 'other_portal':
        state['portals']['iol'] = {'state':'published','id':'unexpected'}
    elif change == 'changed_id':
        state['portals']['pp']['id'] = 'changed'
    else:
        state['portals']['pp']['state'] = change
    with pytest.raises(CopyStopped):
        remaining_destinations(state, plan)


def test_legacy_partial_removal_and_crash_resume_preserve_individual_intents(tmp_path):
    plan, state = scenario()
    offline(state, 'p24')
    db = tmp_path / 'runs.sqlite3'
    store = RunStore(db)
    run = store.start('job', source_capture_id='capture', address_revision_id=None,
                      reference=plan['source_reference'], listing_id=plan['source_listing_id'], archive_before_review=True)
    def old_bulk_call():
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        store.perform_once(run, 'remove_portals', old_bulk_call)
    def inspect(collection, plan, allow_partial):
        assert allow_partial
        remaining = remaining_destinations(state, plan)
        return {'observed':deepcopy(state), 'remaining':remaining}
    clicks = []
    def withdraw(plan, record, resuming):
        assert resuming
        for key in ('p24','pp','global','website'):
            if key in remaining_destinations(state, plan):
                record(key, state['portals'][key]['id'])
                clicks.append(key)
                if key == 'pp':
                    raise TimeoutError()
                offline(state, key)
        return {'observed':deepcopy(state)}
    provider = SimpleNamespace(check_removed_source=inspect, unpublish=withdraw,
                               require_resolved_editor_session=lambda:None)
    runner = RelistRunner(None, store, provider)
    runner.check_removal(store.get('job'), {}, plan, preflight=True)
    assert not clicks
    with pytest.raises(TimeoutError):
        runner.check_removal(store.get('job'), {}, plan)
    store.close()
    with closing(RunStore(db)) as reopened:
        runner = RelistRunner(None, reopened, provider)
        with pytest.raises(RunNeedsReview, match='pp'):
            runner.check_removal(reopened.get('job'), {}, plan)
        with pytest.raises(RunNeedsReview):
            reopened.record_portal_intent(run, 'pp', 'T3000')
        offline(state, 'pp')
        runner.check_removal(reopened.get('job'), {}, plan)
        assert clicks == ['pp','global','website']
        assert reopened.get('job')['actions'][0]['state'] == 'done'
