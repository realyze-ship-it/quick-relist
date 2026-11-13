from types import SimpleNamespace
import pytest
from quick_relist.hub_readonly import ReadOnlyHub, EditorNavigationInProgress
from quick_relist.live_relist_ui import HubRelistUI
from quick_relist.form_copy import CopyStopped


def identity():
    return {'outer_count':1, 'inner_count':1, 'outer_matches':True, 'inner_matches':True}


@pytest.mark.parametrize('final_url,transient', [
    ('https://hub.remax.co.za/app/listings/edit/123456/deal_particulars', True),
    ('https://hub.remax.co.za/app/listings/edit/999999/deal_particulars', False),
    ('https://example.test/app/listings/edit/123456/deal_particulars', False)])
def test_only_a_section_transition_of_the_same_listing_can_be_waited_for(final_url, transient):
    driver = SimpleNamespace(current_window_handle='copy', window_handles=['copy'],
        current_url='https://hub.remax.co.za/app/listings/edit/123456')
    def execute(*args):
        driver.current_url = final_url
        return identity()
    driver.execute_script = execute
    with pytest.raises(ValueError) as error:
        ReadOnlyHub(driver)._read_editor_identity('RXCU1000', '123456')
    assert isinstance(error.value, EditorNavigationInProgress) is transient


@pytest.mark.parametrize('change', ['handle', 'handles'])
def test_other_browser_context_changes_are_not_section_transitions(change):
    driver = SimpleNamespace(current_window_handle='copy', window_handles=['copy'],
        current_url='https://hub.remax.co.za/app/listings/edit/123456')
    def execute(*args):
        if change == 'handle': driver.current_window_handle = 'other'
        else: driver.window_handles.append('other')
        return identity()
    driver.execute_script = execute
    with pytest.raises(ValueError) as error:
        ReadOnlyHub(driver)._read_editor_identity('RXCU1000', '123456')
    assert not isinstance(error.value, EditorNavigationInProgress)


@pytest.mark.parametrize('wrong_identity', [False, True])
def test_guard_waits_for_transition_but_never_for_conflicting_identity(tmp_path, monkeypatch, wrong_identity):
    from quick_relist import live_relist_ui
    monkeypatch.setattr(live_relist_ui.time, 'sleep', lambda *a: None)
    driver = SimpleNamespace(current_window_handle='copy')
    calls = []
    def initial(*args): raise EditorNavigationInProgress()
    def read(*args):
        calls.append('read')
        if len(calls) == 1: raise EditorNavigationInProgress()
        result = identity()
        if wrong_identity: result['inner_matches'] = False
        return result
    hub = SimpleNamespace(driver=driver, verify_editor_reference=initial, _read_editor_identity=read,
                          _verified_editor_identity=ReadOnlyHub._verified_editor_identity)
    ui = HubRelistUI(hub, tmp_path, clipboard=lambda *a: None)
    ui.current = {'reference':'RXCU1000','listing_id':'123456','handle':'copy'}
    if wrong_identity:
        with pytest.raises(CopyStopped, match='identity changed'): ui.guard()
    else:
        ui.guard()
    assert calls == ['read','read']
