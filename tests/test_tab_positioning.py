"""Real matched-controller positioning with synthetic browser/source responses."""
from types import SimpleNamespace
import json

import pytest

from quick_relist.capture import verify_capture_integrity
from quick_relist.tab_positioning import TAB_POSITION_SCRIPT
import quick_relist.matched_capture as module
from test_matched_capture import BASE, REF, URLS, NativeTab, capture, setup, use_native_tabs  # noqa: F401

PRIVATE = 'PRIVATE_POSITION_EXCEPTION_DOM_URL'


@pytest.fixture
def positioned(setup):
    hub, driver, clock = setup
    tabs = use_native_tabs(driver)
    state = SimpleNamespace(status='clear', after='clear', scrolls=0, reads=0,
        hook=lambda event: None, error=None, scroll_error=None, scroll_return='scrolled')
    original = driver.execute_script
    def execute(script,*args):
        if script != TAB_POSITION_SCRIPT: return original(script,*args)
        source, source_label, target, expected, scroll = args
        assert source == driver.current_url
        assert source_label == next(label for label,key in module.TAB_PAGES.items() if key == module.parse_editor_url(source)['page_key'])
        assert expected[target] in tabs
        assert set(expected) == set(module.TAB_PAGES)
        assert expected[target].get_attribute('aria-selected') == 'false'
        if scroll:
            state.scrolls += 1
            driver.calls.append(('scroll',target))
            state.status = state.after
            state.hook('scroll')
            if state.scroll_error: raise state.scroll_error
            return state.scroll_return
        state.reads += 1
        state.hook('position')
        if state.error: raise state.error
        return state.status
    driver.execute_script = execute
    return hub, driver, clock, tabs, state


def actions(driver):
    return [c for c in driver.calls if c[0] in ('scroll','tab_click','get')]


def one_navigation(driver):
    # Fail verification after the first click so click count is unambiguous.
    driver.on_tab_click = lambda key: setattr(driver,'matches',False)


@pytest.mark.parametrize('scroll',[False,True])
def test_clear_or_scrolled_tab_dispatches_exactly_once(positioned,scroll):
    hub,driver,_,tabs,state = positioned
    state.status='needs_scroll' if scroll else 'clear'
    one_navigation(driver)
    result=capture(hub)
    assert actions(driver) == ([('scroll','Deal Particulars')] if scroll else []) + [('tab_click','deal_particulars')]
    assert state.scrolls == int(scroll) and state.reads == 2
    assert result['browser_failure'] is None and verify_capture_integrity(result)
    assert result['complete'] is result['live_ready'] is False
    assert driver.timeouts.page_load == 300


def test_all_four_pages_still_use_three_native_clicks(positioned):
    hub,driver,_,_,state=positioned
    result=capture(hub)
    assert result['status']=='partial' and len(result['pages'])==4
    assert actions(driver)==[('tab_click',key) for key in list(module.TAB_PAGES.values())[1:]]
    assert state.scrolls==0 and state.reads==6


@pytest.mark.parametrize('after',['needs_scroll','changed','invalid',None,{},PRIVATE,'scrolled'])
def test_persistent_or_unverifiable_postscroll_state_never_clicks(positioned,after):
    hub,driver,_,_,state=positioned
    state.status,state.after='needs_scroll',after
    result=capture(hub)
    assert result['status']=='stopped' and len(result['pages'])==1
    assert actions(driver)==[('scroll','Deal Particulars')] and state.scrolls==1
    assert result['browser_failure'] is None and PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('bad',['changed','invalid',None,{},PRIVATE,'scrolled'])
def test_invalid_initial_probe_never_scrolls_or_clicks(positioned,bad):
    hub,driver,_,_,state=positioned
    state.status=bad
    result=capture(hub)
    assert result['status']=='stopped' and actions(driver)==[]
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('stage',['position','scroll'])
@pytest.mark.parametrize('change',['binding','driver','reference','route','handle','windows','busy','dialog','unready','node','missing','selection','disabled','label'])
def test_source_target_or_selection_change_stops_without_click(positioned,stage,change):
    hub,driver,_,tabs,state=positioned
    state.status='needs_scroll'
    def hook(event):
        if event!=stage: return
        state.hook=lambda _:None
        if change=='binding': hub.editor_bindings.clear()
        if change=='driver': hub.driver=object()
        if change=='reference': driver.matches=False
        if change=='route': driver.current_url=URLS[1]
        if change=='handle': driver.current_window_handle='results'
        if change=='windows': driver.window_handles.append('extra')
        if change=='busy': driver.busy=True
        if change=='dialog': driver.dialog=True
        if change=='unready': driver.ready=False
        if change=='node': tabs[1]=NativeTab(driver,'Deal Particulars','deal_particulars')
        if change=='missing': tabs.pop(2)
        if change=='selection': tabs[0].attrs['aria-selected']='false'
        if change=='disabled': tabs[1].enabled=False
        if change=='label': tabs[1].label='Other'
    state.hook=hook
    result=capture(hub)
    assert result['status']=='stopped'
    assert actions(driver)==([('scroll','Deal Particulars')] if stage=='scroll' else [])
    assert result['browser_failure'] is None


@pytest.mark.parametrize('stage',['position','scroll'])
def test_cancellation_prevents_later_scroll_or_click(positioned,stage):
    hub,driver,_,_,state=positioned
    state.status='needs_scroll'
    flag=[False]
    state.hook=lambda event: flag.__setitem__(0,True) if event==stage else None
    result=capture(hub,cancelled=lambda:flag[0])
    assert result['status']=='cancelled'
    assert actions(driver)==([('scroll','Deal Particulars')] if stage=='scroll' else [])


@pytest.mark.parametrize('stage',['position','scroll'])
@pytest.mark.parametrize('name,kind',[('StaleElementReferenceException','stale_element'),('JavascriptException','javascript_error'),('TimeoutException','timeout')])
def test_position_errors_are_typed_private_and_never_retried(positioned,stage,name,kind):
    hub,driver,_,_,state=positioned
    errors=pytest.importorskip('selenium.common.exceptions')
    state.status='needs_scroll'
    if stage=='position': state.error=getattr(errors,name)(PRIVATE)
    else: state.scroll_error=getattr(errors,name)(PRIVATE)
    result=capture(hub)
    assert result['browser_failure']=={'phase':'tab_position' if stage=='position' else 'tab_scroll',
        'target_page_key':'deal_particulars','kind':kind,'navigation_attempted_in_capture':False}
    assert actions(driver)==([('scroll','Deal Particulars')] if stage=='scroll' else [])
    assert state.reads==1 and PRIVATE not in json.dumps(result)


def test_late_obstruction_on_clear_path_does_not_trigger_second_scroll(positioned):
    hub,driver,_,_,state=positioned
    state.hook=lambda event:setattr(state,'status','needs_scroll') if state.reads==2 else None
    result=capture(hub)
    assert result['status']=='stopped' and state.reads==2 and not actions(driver)


def test_becoming_clear_before_scroll_never_forces_scroll(positioned):
    hub,driver,_,_,state=positioned
    state.status='needs_scroll'
    # Model the RPC finding a newly clear point and declining its scroll.
    original=driver.execute_script
    def execute(script,*args):
        if script==TAB_POSITION_SCRIPT and args[-1]:
            state.status='clear'
            return 'clear'
        return original(script,*args)
    driver.execute_script=execute
    one_navigation(driver)
    capture(hub)
    assert actions(driver)==[('tab_click','deal_particulars')] and state.scrolls==0


@pytest.mark.parametrize('primary',['error','cancel','guard'])
def test_timeout_cleanup_cannot_replace_primary_position_failure(positioned,primary):
    hub,driver,_,_,state=positioned
    errors=pytest.importorskip('selenium.common.exceptions')
    if primary=='error': state.error=errors.JavascriptException(PRIVATE)
    elif primary=='cancel': state.error=KeyboardInterrupt()
    else: state.status='changed'
    original=driver.set_page_load_timeout
    def timeout(seconds):
        if seconds==300: raise errors.TimeoutException(PRIVATE)
        original(seconds)
    driver.set_page_load_timeout=timeout
    result=capture(hub)
    assert result['status']==('cancelled' if primary=='cancel' else 'stopped')
    assert result['timeout_restore_failure']['phase']=='timeout_restore'
    assert result['browser_failure'] is None if primary!='error' else result['browser_failure']['phase']=='tab_position'
    assert not actions(driver) and PRIVATE not in json.dumps(result)


def test_later_intercepted_click_retains_existing_uncertainty(positioned):
    hub,driver,_,_,state=positioned
    errors=pytest.importorskip('selenium.common.exceptions')
    state.status='needs_scroll'
    def click(_): raise errors.ElementClickInterceptedException(PRIVATE)
    driver.on_tab_click=click
    result=capture(hub)
    assert actions(driver)==[('scroll','Deal Particulars'),('tab_click','deal_particulars')]
    assert result['browser_failure']['phase']=='tab_click'
    assert result['browser_failure']['navigation_attempted_in_capture'] is True
    assert result['browser_failure']['kind']=='click_intercepted' and PRIVATE not in json.dumps(result)
