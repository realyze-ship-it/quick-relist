"""Synthetic form-readiness checks; no browser, network or real listing data."""
from types import SimpleNamespace
import pytest

from quick_relist import live_relist_ui as module
from quick_relist.form_copy import CopyStopped


def commission_page():
    return {'page_key':'deal_particulars', 'fields':[{'name':'total_commission_percentage','value':'3.0'}],
        'rich_text':[], 'dialogs':[], 'media':[], 'tables':[{'rows':[
            ['Agent','Brand','Agent Type','Deal Commission Split %','Commission Sub Total','Office Service Fee',
             'RE/MAX SA Service Fee','Commission Total Minus Fees','Action'],
            ['Example A','RE/MAX','Listing Agent','50','100','0','2','98',''],
            ['Example B','RE/MAX','Listing Agent','50','100','0','2','98',''],
            ['Totals:','100%','200','0','4','196','']]}]}


def test_commission_row_order_does_not_change_agent_values_or_source():
    from copy import deepcopy
    source=commission_page(); original=deepcopy(source); copy=deepcopy(source)
    copy['tables'][0]['rows'][1:3]=reversed(copy['tables'][0]['rows'][1:3])
    assert module.same_commission(source,copy)
    assert source==original


@pytest.mark.parametrize('column',range(8))
def test_commission_comparison_rejects_changed_agent_money_or_identity(column):
    source=commission_page(); copy=commission_page()
    copy['tables'][0]['rows'][1][column]='changed'
    assert not module.same_commission(source,copy)


@pytest.mark.parametrize('change',['duplicate','missing','totals','percentage','header'])
def test_commission_comparison_rejects_incomplete_or_changed_totals(change):
    source=commission_page(); copy=commission_page(); rows=copy['tables'][0]['rows']
    if change=='duplicate':rows[2]=rows[1][:]
    elif change=='missing':rows.pop(1)
    elif change=='totals':rows[-1][1]='99%'
    elif change=='percentage':copy['fields'][0]['value']='4.0'
    else:rows[0][3]='Other share'
    assert not module.same_commission(source,copy)


@pytest.mark.parametrize('changed_saved_amount', [False, True])
def test_commission_writer_preserves_amount_when_displayed_percentage_is_rounded(monkeypatch, changed_saved_amount):
    from copy import deepcopy
    from decimal import Decimal
    source = commission_page()
    source['fields'] = [
        {'name':'total_commission_value','value':'200'},
        {'name':'total_commission_percentage','value':'1.35'}]
    values = {'total_commission_value':'0','total_commission_percentage':'0'}
    writes, tabs, saved_pages = [], [], []
    class Input:
        def __init__(self, name): self.name = name
        def send_keys(self, *keys):
            value = keys[3]
            writes.append((self.name, value))
            values[self.name] = value
            if self.name == 'total_commission_percentage':
                values['total_commission_value'] = str(Decimal(value) * Decimal('14800') / 100)
            else:
                values['total_commission_percentage'] = str(Decimal(value) * 100 / Decimal('14800'))
    class Cell:
        def __init__(self, value): self.value = value
        def get_attribute(self, name): return self.value
    class Row:
        def __init__(self, row): self.cells = [Cell(value) for value in row]
        def find_elements(self, by, selector):
            assert selector == 'td'
            return self.cells
    def find(by, selector):
        if selector == 'main tbody tr':
            return [Row(row) for row in source['tables'][0]['rows'][1:-1]]
        for name in values:
            if selector == f'input[name="{name}"]': return [Input(name)]
        raise AssertionError(selector)
    driver = SimpleNamespace(current_url='https://hub.remax.co.za/app/listings/edit/42/deal_particulars',find_elements=find)
    ui = object.__new__(module.HubRelistUI)
    ui.driver = driver
    ui.bind = lambda target: None
    ui.guard = lambda: None
    ui.wait = lambda read, message: read() or pytest.fail(message)
    def tab(name):
        tabs.append(name)
        driver.current_url = 'https://hub.remax.co.za/app/listings/edit/42/' + ('media' if name == 'Media' else 'deal_particulars')
    ui.tab = tab
    def save(name):
        assert name == 'Next'
        page = deepcopy(source)
        page['fields'][0]['value'] = '201' if changed_saved_amount else values['total_commission_value']
        page['fields'][1]['value'] = str(Decimal(values['total_commission_percentage']).quantize(Decimal('.01')))
        saved_pages.append(page)
        driver.current_url = 'https://hub.remax.co.za/app/listings/edit/42/media'
    ui.button = save
    class Reader:
        def __init__(self, observed_driver): assert observed_driver is driver
        def capture_current_page(self, listing_id):
            assert listing_id == '42' and driver.current_url.endswith('/deal_particulars')
            return {'pages':saved_pages}
    monkeypatch.setattr(module, 'PartialHubCapture', Reader)
    if changed_saved_amount:
        with pytest.raises(CopyStopped, match='No photos were uploaded'):
            ui.copy_commission({'listing_id':'42'}, source)
        assert tabs[-1] == 'Deal Particulars'
    else:
        assert ui.copy_commission({'listing_id':'42'}, source) == {'commission_copied':True}
        assert module.same_commission(source, saved_pages[0])
        assert tabs[-1] == 'Media'
    assert writes == [('total_commission_percentage','1.35'),('total_commission_value','200')]


@pytest.mark.parametrize('outcome', ['already_published','published_after_reload','still_pending','wrong_page'])
def test_publication_refreshes_stale_status_without_sending_another_request(monkeypatch, outcome):
    clock, reloads, bindings = [0], [], []
    driver = SimpleNamespace(current_url='https://hub.remax.co.za/app/listings/edit/42/portals')
    driver.refresh = lambda: reloads.append(clock[0])
    ui = object.__new__(module.HubRelistUI)
    ui.driver = driver
    ui.bind = lambda target: bindings.append(target)
    ui.tab = lambda name: None
    ui.guard = lambda: None
    def read(observed_driver, *, guard, timeout):
        assert observed_driver is driver
        guard()
        if outcome == 'wrong_page' and clock[0] >= 10:
            driver.current_url = 'https://hub.remax.co.za/app/listings/edit/42/media'
        published = outcome == 'already_published' or (outcome == 'published_after_reload' and bool(reloads))
        return {'portals':{'website':{'state':'published' if published else 'pending'}}}
    monkeypatch.setattr(module, 'read_portals', read)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0,clock[0]+seconds))
    target = {'listing_id':'42'}
    if outcome == 'wrong_page':
        with pytest.raises(CopyStopped, match='status page changed'):
            ui.publication(target, ['website'])
        assert not reloads
    else:
        result = ui.publication(target, ['website'])
        assert result['complete'] == (outcome != 'still_pending')
        if outcome == 'already_published': assert not reloads
        elif outcome == 'published_after_reload': assert reloads == [10]
        else: assert clock[0] == 120 and reloads == list(range(10,120,10))
    assert bindings == [target]


@pytest.mark.parametrize('saved_selection,accepted', [
    ('Example Park, First City, Province', True),
    ('Example Park, Other City, Province', False), ('Example Park', False)])
def test_saved_short_suburb_requires_exact_selected_city(saved_selection, accepted):
    from copy import deepcopy
    expected={'controls':[{'key':'suburb','kind':'suburb','value':'Example Park, First City, Province'}]}
    actual={'controls':[{'key':'suburb','kind':'suburb','value':'Example Park'}]}
    original=deepcopy(actual)
    calls=[]
    def read(short):
        calls.append(short)
        return saved_selection
    if accepted:
        result, proof=module.resolve_saved_suburb(expected,actual,read)
        assert result==expected and proof==saved_selection
    else:
        with pytest.raises(CopyStopped,match='city or province'):
            module.resolve_saved_suburb(expected,actual,read)
    assert calls==['Example Park'] and actual==original


@pytest.mark.parametrize('actual_value', ['Different Park','Example Park, Other City, Province',''])
def test_other_saved_suburbs_cannot_be_rewritten_by_matching_prefix(actual_value):
    expected={'controls':[{'key':'suburb','kind':'suburb','value':'Example Park, First City, Province'}]}
    actual={'controls':[{'key':'suburb','kind':'suburb','value':actual_value}]}
    def prohibited(short):
        raise AssertionError('Do not resolve an unrelated value')
    result,proof=module.resolve_saved_suburb(expected,actual,prohibited)
    assert result is actual and proof is None


@pytest.mark.parametrize('selected_count,own_menu',[(1,True),(0,True),(2,True),(1,False)])
def test_saved_suburb_reads_owned_selected_option_without_selecting(tmp_path,selected_count,own_menu):
    from selenium.webdriver.common.keys import Keys
    events=[]
    field=SimpleNamespace(is_displayed=lambda:True,get_property=lambda name:'Example Park',
        get_attribute=lambda name:'suburb-options' if name=='aria-controls' else None,
        send_keys=lambda *keys:events.append(keys))
    choice=SimpleNamespace(is_displayed=lambda:True,
        get_attribute=lambda name:{'aria-selected':'true','textContent':'Example Park, First City, Province'}.get(name))
    # Focus alone is not selection, and an unrelated list cannot supply proof.
    unselected=SimpleNamespace(is_displayed=lambda:True,
        get_attribute=lambda name:{'aria-selected':'false','textContent':'Example Park, Other City, Province'}.get(name))
    menu=SimpleNamespace(is_displayed=lambda:True,
        get_attribute=lambda name:'suburb-options' if own_menu else 'other-options',
        find_elements=lambda *args:[choice]*selected_count+[unselected])
    driver=SimpleNamespace(find_elements=lambda by,selector:[field] if selector.startswith('main input') else [menu],
        execute_script=lambda *args:True)
    provider=module.HubRelistUI(SimpleNamespace(driver=driver),tmp_path,clipboard=lambda *args:None)
    provider.tab=lambda name:events.append(name)
    provider.guard=lambda:None
    def wait_once(read,message,**kwargs):
        result=read()
        if not result:raise CopyStopped(message)
        return result
    provider.wait=wait_once
    if selected_count==1 and own_menu:
        assert provider.selected_suburb_label('Example Park')=='Example Park, First City, Province'
    else:
        with pytest.raises(CopyStopped):provider.selected_suburb_label('Example Park')
    assert events==['Listing particulars',(Keys.CONTROL,'a',Keys.NULL,'Example Park'),(Keys.ESCAPE,Keys.TAB)]


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
@pytest.mark.parametrize('captions', [False, True])
def test_reload_waits_for_missing_headings_but_never_accepts_a_conflict(monkeypatch, tmp_path, conflict, captions):
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
    def capture_reader(*args, **kwargs):
        assert kwargs['include_media_captions'] is captions
        assert kwargs['include_room_options'] and kwargs['include_general_features']
        return SimpleNamespace(capture=lambda: accepted.append(True) or {'stop_reason': None})
    monkeypatch.setattr(module, 'MatchedHubCapture', capture_reader)
    monkeypatch.setattr(module.time, 'sleep', lambda value: None)
    provider = module.HubRelistUI(hub, tmp_path, clipboard=lambda *args: None)
    if conflict:
        with pytest.raises(CopyStopped, match='does not match'):
            provider.captured_editor('RXCU12345', include_media_captions=captions)
        assert accepted == []
    else:
        assert provider.captured_editor('RXCU12345', include_media_captions=captions) == {'stop_reason': None}
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


def test_browser_message_stops_once_without_exposing_private_alert_text(tmp_path):
    from selenium.common.exceptions import UnexpectedAlertPresentException
    calls = []
    class Subject:
        media_root = tmp_path / 'media'
        @module.diagnosed
        def create(self):
            calls.append('save')
            raise UnexpectedAlertPresentException('SYNTHETIC_PRIVATE_MESSAGE',
                                                   alert_text='SYNTHETIC_PRIVATE_ALERT')
    with pytest.raises(CopyStopped, match='Read it in Chrome') as caught:
        Subject().create()
    assert calls == ['save']
    assert 'SYNTHETIC_PRIVATE' not in str(caught.value)
    records = list((tmp_path / 'diagnostics').glob('error-*.json'))
    assert len(records) == 1 and 'SYNTHETIC_PRIVATE' not in records[0].read_text()


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


@pytest.mark.parametrize('html,text,matching', [
    ('<p><strong>Heading</strong></p><p>Original<br>body.</p>', 'Heading Original body.', True),
    ('<p>Heading</p><p>Original<br>body.</p>', 'Heading Original body.', False),
    ('<p><strong>Heading</strong></p><p>Original body.</p>', 'Heading Original body.', False),
    ('<p><strong>Heading</strong><br>Original<br>body.</p>', 'Heading Original body.', False),
    ('<p><strong>Heading</strong></p><p>Changed<br>body.</p>', 'Heading Changed body.', False),
])
def test_media_comparison_accepts_only_inert_span_removal(html, text, matching):
    from copy import deepcopy
    from quick_relist.relist_plan import media_plan
    fields = [{'name': key, 'value': ''} for key in ('virtualtour_url','videotour_url',
        'p24_youtube_video_id','matterport_video_id','eyespy360_url','marketing_header')]
    fields.append({'label':'Brochure Description','value':'Original summary'})
    source = {'fields':fields, 'rich_text':[{'observed_description_label':True,
        'sanitized_html':'<p><span><strong>Heading</strong></span></p><p><span>Original<br>body.</span></p>',
        'text':'Heading Original body.'}]}
    before = deepcopy(source)
    actual = deepcopy(source)
    actual['rich_text'][0].update(sanitized_html=html, text=text)
    assert (media_plan(source) == media_plan(actual)) is matching
    assert source == before  # Original captured markup stays immutable.
    actual['fields'][-1]['value'] = 'Different summary'
    assert media_plan(source) != media_plan(actual)


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
