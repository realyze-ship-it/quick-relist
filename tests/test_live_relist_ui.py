"""Synthetic form-readiness checks; no browser, network or real listing data."""
from types import SimpleNamespace
import pytest

from quick_relist import live_relist_ui as module
from quick_relist.form_copy import CopyStopped


@pytest.mark.parametrize('button_count,enabled', [(0, True), (2, True), (1, False), (1, True)])
def test_preparation_requires_one_enabled_save_without_clicking_it(monkeypatch, tmp_path, button_count, enabled):
    events = []
    button = SimpleNamespace(is_displayed=lambda: True, is_enabled=lambda: enabled,
                             get_attribute=lambda name: 'Save')
    driver = SimpleNamespace(current_window_handle='unsaved', current_url='', window_handles=['source'])
    driver.switch_to = SimpleNamespace(new_window=lambda kind: events.append('new_unsaved_tab'),
                                      window=lambda handle: events.append('select_' + handle))
    driver.get = lambda url: setattr(driver, 'current_url', url)
    driver.find_elements = lambda by, selector: ([button] * button_count
                                                if selector == 'main button' else [object()])
    monkeypatch.setattr(module, 'ParticularsFiller', lambda *args, **kwargs:
                        SimpleNamespace(fill=lambda plan: events.append('fill'), guard=lambda: None))
    hub = SimpleNamespace(driver=driver, editor_bindings={'RXCU12345': {'handle':'source'}},
                          verify_editor_reference=lambda *args: events.append('verify_source'))
    provider = module.HubRelistUI(hub, tmp_path, clipboard=lambda *args: None)
    plan = {'source_reference':'RXCU12345', 'source_listing_id':'123456', 'particulars': {'mode': 'rent'}}
    def wait_once(read, message, **kwargs):
        result = read()
        if not result:
            raise CopyStopped(message)
        return result
    provider.wait = wait_once
    if button_count == 1 and enabled:
        assert provider.prepare(plan)['handle'] == 'unsaved'
    else:
        with pytest.raises(CopyStopped, match='original remains active'):
            provider.prepare(plan)
    assert events == ['select_source', 'verify_source', 'new_unsaved_tab', 'fill']


@pytest.mark.parametrize('conflict', [False, True])
def test_reload_waits_for_missing_headings_but_never_accepts_a_conflict(monkeypatch, tmp_path, conflict):
    ready = {'outer_count': 1, 'inner_count': 1, 'outer_matches': True, 'inner_matches': True}
    first = dict(ready, inner_matches=False) if conflict else dict(ready, inner_count=0, inner_matches=False)
    observations = iter([first, ready])
    driver = SimpleNamespace(current_window_handle='editor', window_handles=['editor'],
                             current_url='https://hub.remax.co.za/app/listings/edit/123456/particulars',
                             refresh=lambda: None, find_elements=lambda *args: [object()],
                             switch_to=SimpleNamespace(window=lambda handle: None))
    hub = SimpleNamespace(driver=driver, editor_bindings={'RXCU12345': {'handle':'editor','listing_id':'123456'}},
        _read_editor_identity=lambda *args: next(observations), _verified_editor_identity=lambda value: value)
    accepted = []
    monkeypatch.setattr(module, 'MatchedHubCapture', lambda *args, **kwargs:
                        SimpleNamespace(capture=lambda: accepted.append(True) or {'stop_reason': None}))
    monkeypatch.setattr(module.time, 'sleep', lambda value: None)
    provider = module.HubRelistUI(hub, tmp_path, clipboard=lambda *args: None)
    if conflict:
        with pytest.raises(CopyStopped, match='does not match'):
            provider.captured_editor('RXCU12345')
        assert accepted == []
    else:
        assert provider.captured_editor('RXCU12345') == {'stop_reason': None}
        assert accepted == [True]


def test_error_diagnostic_excludes_exception_message_and_values(tmp_path):
    import json
    class Subject:
        media_root = tmp_path / 'media'
        @module.diagnosed
        def prepare(self, value):
            raise ValueError('SYNTHETIC_PRIVATE_MESSAGE ' + value)
    with pytest.raises(ValueError):
        Subject().prepare('SYNTHETIC_PRIVATE_VALUE')
    paths = list((tmp_path / 'diagnostics').glob('error-*.json'))
    assert len(paths) == 1
    encoded = paths[0].read_text()
    assert 'SYNTHETIC_PRIVATE' not in encoded
    record = json.loads(encoded)
    assert record['stage'] == 'prepare' and record['exception_type'] == 'ValueError'
    assert record['frames'][-1]['function'] == 'prepare'


@pytest.mark.parametrize('conflict',[False,True])
def test_upload_identity_waits_for_absent_headings_but_rejects_conflicts(tmp_path,monkeypatch,conflict):
    ready={'outer_count':1,'inner_count':1,'outer_matches':True,'inner_matches':True}
    first=dict(ready,inner_matches=False) if conflict else dict(ready,inner_count=0,inner_matches=False)
    observations=iter([first,ready])
    def temporarily_missing(*args):raise ValueError('headings refreshing')
    hub=SimpleNamespace(driver=SimpleNamespace(current_window_handle='target'),
        verify_editor_reference=temporarily_missing,_read_editor_identity=lambda *args:next(observations),
        _verified_editor_identity=lambda state:True)
    provider=module.HubRelistUI(hub,tmp_path,clipboard=lambda *args:None)
    provider.current={'handle':'target','reference':'RXCU2000','listing_id':'654321'}
    monkeypatch.setattr(module.time,'sleep',lambda *args:None)
    if conflict:
        with pytest.raises(CopyStopped,match='identity changed'):provider.guard()
    else:provider.guard()


def test_description_formatting_repair_rejects_word_or_other_field_changes():
    expected={'brochure_description':'Reviewed summary','description_text':'**Room** with balcony.',
              'description_html':'<p>**Room**<br>with balcony.</p>'}
    actual={**expected,'description_text':'Room with balcony.','description_html':'<p><strong>Room</strong> with balcony.</p>'}
    assert module.only_description_formatting_changed(expected, actual)
    assert not module.only_description_formatting_changed(expected, {**actual,'description_text':'Room without balcony.'})
    assert not module.only_description_formatting_changed(expected, {**actual,'brochure_description':'Different wording'})


def test_literal_heading_paste_preserves_closing_marks_and_all_line_breaks():
    source='<p>**Synthetic heading**<br><br>Original body.</p>'
    html,prefix=module.description_clipboard_html(source)
    assert html=='<p>Synthetic heading**<br><br>Original body.</p>' and prefix=='**'
    assert module.description_clipboard_html('<p><strong>Heading</strong><br>Body</p>') == ('<p><strong>Heading</strong><br>Body</p>','')


@pytest.mark.parametrize('mode,reason', [('rent','Listing has been withdrawn'),('sale','Listing has been cancelled')])
def test_archive_uses_type_specific_reason_and_accepts_success_redirect(tmp_path, mode, reason):
    events=[]
    radio=SimpleNamespace(send_keys=lambda *keys:events.append('selected'),is_selected=lambda:True)
    label=SimpleNamespace(get_attribute=lambda name:reason,find_elements=lambda *args:[radio])
    toast=SimpleNamespace(get_attribute=lambda name:'Listing archived successfully!')
    driver=SimpleNamespace(current_window_handle='source',current_url='')
    driver.get=lambda url:setattr(driver,'current_url',url)
    def find(by,selector):
        return {'main input[type="radio"]':[radio], 'main label':[label],
                'main [role="combobox"]':[], '[role="status"],[role="alert"],[data-sonner-toast]':[toast]}[selector]
    driver.find_elements=find
    provider=module.HubRelistUI(SimpleNamespace(driver=driver),tmp_path,clipboard=lambda *args:None)
    provider.select_expired_reason=lambda guard:events.append('mandate expired')
    def submit(text):
        events.append(text)
        driver.current_url='https://hub.remax.co.za/app/listings'
    provider.button=submit
    result=provider.archive({'source_listing_id':'123456','particulars':{'mode':mode}})
    assert result['archived'] is True and result['reason']==reason
    assert events==(['selected','Archive Listing'] if mode=='rent' else ['selected','mandate expired','Archive Listing'])
