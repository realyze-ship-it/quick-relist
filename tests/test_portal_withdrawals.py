from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quick_relist import live_relist_ui as live
from quick_relist.form_copy import CopyStopped
from quick_relist.portal_ui import PORTALS, matches_unpublish_dialog, remaining_destinations
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
    ui.source = {'reference':plan['source_reference'], 'listing_id':plan['source_listing_id'], 'handle':'source'}
    ui.current = ui.source
    driver.current_window_handle = 'source'
    driver.current_url = 'https://hub.remax.co.za/app/listings/edit/1000/portals'
    ui.bind, ui.tab, ui.guard = Mock(), Mock(), Mock()
    clicks, intents, confirmations = [], [], []
    cells = {key:object() for key, _, _ in PORTALS.values()}
    def find(by, xpath):
        if by == 'css selector':
            return []
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
            assert not read()
            offline(state, clicks[-1])
            confirmations.append(clicks[-1])
        assert read(), message
        return read()
    ui.wait = wait
    monkeypatch.setattr(live, 'read_portals', lambda *a, **kw:deepcopy(state))
    monkeypatch.setattr(live, 'portal_snapshot', lambda *a:deepcopy(state))
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
                    record(key, state['portals'][key]['id'], 'prompt')
                    record(key, state['portals'][key]['id'], 'yes')
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


@pytest.mark.parametrize('key,name', [('website','RE/MAX website'), ('p24','Property24'), ('pp','Private Property'),
                        ('global','RemaxGlobal'), ('iol','IOL Property'), ('immoafrica','ImmoAfrica'), ('flow','Flow Living')])
def test_confirmation_matches_only_its_named_destination(key, name):
    text = f'Unpublish\n\nAre you sure you want to unpublish from {name}?\n\nNO\nYES'
    assert matches_unpublish_dialog(text, key)
    assert not matches_unpublish_dialog(text, 'pp' if key == 'p24' else 'p24')
    assert not matches_unpublish_dialog(text.replace('unpublish from', 'publish to'), key)
    assert not matches_unpublish_dialog(text.replace(name, 'all portals'), key)


def test_older_p24_opener_resumes_on_website_offline_source_but_yes_is_never_replayed(tmp_path):
    plan, state = scenario()
    offline(state, 'website')
    state['published'] = False  # Hub reports its own website status here.
    ui = live.HubRelistUI(SimpleNamespace(driver=Mock()), tmp_path, clipboard=None)
    ui.captured_editor = Mock(return_value={})
    ui._source_matches = Mock(return_value=True)
    ui.tab = Mock()
    with patch.object(live, 'read_portals', return_value=state):
        receipt = ui.check_removed_source({'listing_id':'1000'}, plan, True)
    assert set(receipt['remaining']) == {'p24','pp','global'}
    with closing(RunStore(tmp_path / 'runs.sqlite3')) as store:
        run = store.start('job', source_capture_id='capture', address_revision_id=None,
                          reference='RXCU1000', listing_id='1000', archive_before_review=True)
        def old_opener():
            store.note(run, 'portal_unpublish_intent', {'destination':'p24','portal_id':'2000'})
            raise TimeoutError()
        with pytest.raises(TimeoutError):
            store.perform_once(run, 'remove_portals', old_opener)
        provider = Mock()
        provider.check_removed_source.return_value = receipt
        runner = RelistRunner(None, store, provider)
        runner.check_removal(store.get('job'), {}, plan, preflight=True)
        provider.unpublish.assert_not_called()
        with pytest.raises(RunNeedsReview):
            store.record_portal_intent(run,'p24','different-id')
        for stage in ('open','prompt','yes'):
            store.record_portal_intent(run,'p24','2000',stage)
        with pytest.raises(RunNeedsReview):
            runner.check_removal(store.get('job'), {}, plan, preflight=True)
        with pytest.raises(RunNeedsReview):
            store.record_portal_intent(run,'p24','2000','yes')


@pytest.mark.parametrize('failure', ['wrong_tab','wrong_id','different_portal','second_dialog','wrong_buttons','cancelled'])
def test_never_confirms_an_unrelated_or_ambiguous_prompt(tmp_path, failure):
    driver = Mock(current_window_handle='B' if failure == 'wrong_tab' else 'A')
    ident = '2000' if failure == 'wrong_id' else '1000'
    driver.current_url = f'https://hub.remax.co.za/app/listings/edit/{ident}/portals'
    ui = live.HubRelistUI(SimpleNamespace(driver=driver,has_unresolved_editor_opening=lambda:False),
                         tmp_path,clipboard=None,cancelled=lambda:failure == 'cancelled')
    target = {'handle':'A','listing_id':'1000','reference':'RXCU1000'}
    ui.current = target
    name = 'Private Property' if failure == 'different_portal' else 'Property24'
    dialog = Mock(text=f'Unpublish Are you sure you want to unpublish from {name}? NO YES')
    dialog.find_elements.return_value = [Mock(text='Yes'), Mock(text='Yes' if failure == 'wrong_buttons' else 'No')]
    driver.find_elements.return_value = [dialog, Mock()] if failure == 'second_dialog' else [dialog]
    record = Mock()
    ui.button = Mock()
    with pytest.raises(CopyStopped):
        ui.wait_for_portal_withdrawal(target, 'p24', '2000', record)
    ui.button.assert_not_called()
    record.assert_not_called()


def test_uncertain_single_click_cannot_be_reopened_until_a_real_prompt_was_observed():
    from quick_relist.run_store import can_open_portal_prompt
    for destination in ('website','iol','immoafrica','flow'):
        data = {'destination':destination,'portal_id':'1234'}
        events = [{'kind':'portal_unpublish_intent','data':data}]
        assert not can_open_portal_prompt(events,destination,'1234')
        events.append({'kind':'portal_unpublish_prompt','data':data})
        assert can_open_portal_prompt(events,destination,'1234')
        events.append({'kind':'portal_unpublish_yes_intent','data':data})
        assert not can_open_portal_prompt(events,destination,'1234')
