"""Hub's observed sale/rental UI, used only by the app's confirmed relist action.

Every external write is called through RelistRunner's durable journal. This
module uses ordinary WebDriver UI input; it has no hidden Hub API or tokens.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from functools import wraps
import re
import time

from .address import propose_address_fields
from .capture import PartialHubCapture, parse_editor_url
from .collection import _wait_search
from .core import normalize_rxcu
from .form_copy import CopyStopped, ParticularsFiller, particulars_plan
from .matched_capture import MatchedHubCapture
from .original_media import download_originals
from .portal_ui import PORTALS, read_portals, active_destinations, only_moderation_pending
from .relist_plan import media_plan, require_empty_related
from .upload_review import compare_uploaded_images


def diagnosed(method):
    """Keep local error locations without browser text, values or credentials."""
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            import json, traceback
            from uuid import uuid4
            from .media import _private_directory, _private_file
            try:
                root = self.media_root.parent / 'diagnostics'
                if not root.exists():
                    _private_directory(root)
                if not root.is_symlink():
                    record = {'stage': method.__name__, 'exception_type': type(exc).__name__,
                              'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                                         for f in traceback.extract_tb(exc.__traceback__)[-12:]]}
                    with _private_file(root / ('error-' + uuid4().hex + '.json')) as stream:
                        stream.write(json.dumps(record).encode('utf-8'))
            except Exception:
                pass  # A diagnostic failure cannot alter or retry the operation.
            from selenium.common.exceptions import UnexpectedAlertPresentException
            if isinstance(exc, UnexpectedAlertPresentException):
                raise CopyStopped('Hub displayed a browser message. Read it in Chrome, then check the existing listing outcome before continuing. No uncertain action was retried.') from None
            raise
    return call


def content_signature(capture):
    """Business values only, excluding changing render IDs and portal status."""
    result = {}
    for page in capture['pages']:
        if page['page_key'] == 'portals':
            continue
        def fields(items):
            return [(f.get('name') or (f.get('id') if str(f.get('id','')).startswith('feature-') else f.get('label')),
                     f.get('type'), f.get('value'), f.get('checked'), f.get('selected_options')) for f in items]
        result[page['page_key']] = {
            'fields':fields(page['fields']),
            'rich':[r.get('sanitized_html') for r in page['rich_text']],
            'tables':[(t['rows'], t.get('observed_agents')) for t in page['tables']],
            'rooms':[(d.get('source',{}).get('opener'), fields(d['fields'])) for d in page['dialogs']],
            'photos':[(m.get('observed_asset_id'), m.get('observed_position'), m.get('original_source_ref'))
                      for m in page['media'] if m.get('observed_asset_id')],
            'captions':[(c['id'],c['caption']) for c in page.get('photo_captions',[])]}
    return result


def normalized_value(control):
    value = control['value']
    numeric = {'land_area','floor_area','selling_price','rental_price','deposit','rates','levy','special_levies','ensuites'}
    if control['kind'] == 'count' or control['key'] in numeric:
        if value != '':
            try:
                return Decimal(value.replace(',',''))
            except InvalidOperation:
                pass
    return value


def only_media_interface_artwork(media):
    """The observed Blaze generator icon is page artwork, not listing media."""
    def decoration(item):
        if not isinstance(item, dict):
            return False
        ref = item.get('source_ref')
        return (item.get('tag') == 'img' and item.get('kind_hint') == 'img'
                and all(item.get(key) == '' for key in ('alt', 'title', 'caption'))
                and all(item.get(key) is None for key in ('observed_asset_id', 'observed_position', 'original_source_ref'))
                and isinstance(ref, dict) and ref.get('query_omitted') is False and ref.get('fragment_omitted') is False
                and isinstance(ref.get('url'), str)
                and re.fullmatch(r'https://hub\.remax\.co\.za/build/assets/blaze-flame-white-[A-Za-z0-9_-]+\.png', ref['url']) is not None)
    return isinstance(media, list) and all(decoration(item) for item in media)


def particulars_differences(expected, actual):
    def controls(plan):
        return {(c['key'],c.get('code')):normalized_value(c) for c in plan['controls']}
    def rooms(plan):
        return [(r['opener'],r['groups'],[(f.get('name'),f.get('label'),f.get('value'),f.get('checked')) for f in r['fields']]) for r in plan['rooms']]
    differences = []
    if expected['mode'] != actual['mode']:
        differences.append('listing type')
    left, right = controls(expected), controls(actual)
    for key in sorted(left.keys() | right.keys(), key=str):
        if key not in left or key not in right or left[key] != right[key]:
            label = key[0].replace('_',' ') + (f' ({key[1]})' if key[1] else '')
            differences.append(f'{label}: expected {left.get(key, "[missing]")!s}, saved {right.get(key, "[missing]")!s}')
    if sorted(expected['agents'], key=lambda a:a['name']) != sorted(actual['agents'], key=lambda a:a['name']):
        differences.append('listing agents or primary selection')
    if rooms(expected) != rooms(actual):
        differences.append('room options')
    return differences


def same_particulars(expected, actual):
    return not particulars_differences(expected, actual)


def resolve_saved_suburb(expected, actual, read_selected):
    """Hub reloads a short suburb name. Require its selected full option as proof.

    Keep the capture unchanged. Only the comparison plan is qualified, after
    observing the saved selection's city/province; matching a prefix is not proof.
    """
    wanted = [c for c in expected['controls'] if c['kind'] == 'suburb']
    found = [c for c in actual['controls'] if c['kind'] == 'suburb']
    if len(wanted) != 1 or len(found) != 1:
        return actual, None
    full, short = wanted[0]['value'], found[0]['value']
    if ',' not in full or short != full.split(',')[0].strip():
        return actual, None
    observed = read_selected(short)
    if observed != full:
        raise CopyStopped('The saved suburb city or province differs from the reviewed address.')
    result = deepcopy(actual)
    next(c for c in result['controls'] if c['kind'] == 'suburb')['value'] = observed
    return result, observed


def same_commission(expected, actual, review=None):
    """Compare each agent's complete commission row, independent of row order."""
    if review:
        from .commission_review import reviewed_deal
        expected = reviewed_deal(expected, actual, review)
        if expected is None:
            return False
    def signature(page):
        page = deepcopy(page)
        tables = [t for t in page['tables'] if t['rows'] and t['rows'][0][0] == 'Agent']
        if len(tables) != 1:
            return None
        rows = tables[0]['rows']
        if len(rows) < 3 or len(rows[0]) != 9 or rows[-1][0] != 'Totals:':
            return None
        agents = rows[1:-1]
        if (any(len(row) != 9 or not row[0] or row[0] == 'Totals:' for row in agents)
                or len({row[0] for row in agents}) != len(agents)):
            return None
        tables[0]['rows'] = [rows[0], *sorted(agents, key=lambda row: row[0]), rows[-1]]
        return content_signature({'pages':[page]})
    left, right = signature(expected), signature(actual)
    return left is not None and right is not None and left == right


def same_saved_media(expected, actual):
    # Hub's save converts typographic punctuation in the long description to
    # plain characters. Keep the source HTML for writing and compare both the
    # formatted markup and the plain wording after this one-way substitution.
    if expected.keys() != actual.keys():
        return False
    punctuation = str.maketrans({'–':'-', '—':'-', '’':"'"})
    for key, value in expected.items():
        if actual[key] == value:
            continue
        if key not in ('description_html', 'description_text') or actual[key] != value.translate(punctuation):
            return False
    return True


def only_description_formatting_changed(expected, actual):
    if any(actual.get(k) != v for k,v in expected.items() if not k.startswith('description_')):
        return False
    def literal_words(value):
        # Hub's paste handler interprets literal Markdown bold markers. This
        # repair is limited to identical wording, with no arbitrary text loss.
        return re.sub(r'(\*\*|__)(.+?)\1', r'\2', value)
    return literal_words(expected['description_text']) == literal_words(actual['description_text'])


def description_clipboard_html(value):
    # TipTap's bold paste rule converts a literal **heading**. Pasting its
    # closing marks first and then typing its opening marks avoids that rule.
    literal_heading = value.startswith('<p>**') and value.count('**') == 2
    return ('<p>' + value[5:], '**') if literal_heading else (value, '')


class HubRelistUI:
    def __init__(self, hub, media_root, *, clipboard, cancelled=lambda: False, progress=lambda message: None):
        self.hub, self.driver = hub, hub.driver
        self.media_root, self.clipboard = Path(media_root), clipboard
        self.cancelled, self.progress = cancelled, progress
        self.current = None

    def guard(self):
        if self.cancelled():
            raise CopyStopped('Stopped between Hub actions. Saved progress has been kept.')
        if self.current:
            if self.driver.current_window_handle != self.current['handle']:
                raise CopyStopped('The controlled Hub tab changed. No further click was sent.')
            try:
                self.hub.verify_editor_reference(self.current['reference'], self.current['listing_id'])
            except ValueError:
                # Uploading can temporarily unmount both editor headings.
                # Wait only for missing identity, never a conflicting identity.
                def ready():
                    if self.driver.current_window_handle != self.current['handle']:
                        raise CopyStopped('The controlled Hub tab changed during loading.')
                    from .hub_readonly import EditorNavigationInProgress
                    try:
                        observed = self.hub._read_editor_identity(self.current['reference'], self.current['listing_id'])
                    except EditorNavigationInProgress:
                        return False
                    for prefix in ('outer', 'inner'):
                        count = observed.get(prefix + '_count')
                        if count and (count != 1 or observed.get(prefix + '_matches') is not True):
                            raise CopyStopped('The editor identity changed. No further action was sent.')
                    if not observed.get('outer_count') or not observed.get('inner_count'):
                        return False
                    return self.hub._verified_editor_identity(observed)
                self.wait(ready, 'The listing headings did not return after Hub refreshed.', timeout=30)

    def wait(self, read, message, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancelled():
                raise CopyStopped('Stopped while waiting for Hub. Check the recorded outcome before retrying.')
            result = read()
            if result:
                return result
            time.sleep(.2)
        raise CopyStopped(message)

    def one(self, selector, root=None):
        found = (root or self.driver).find_elements('css selector', selector)
        if len(found) != 1:
            raise CopyStopped('A required Hub control is missing or ambiguous.')
        return found[0]

    def button(self, text, root=None, *, guard=None):
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.common.keys import Keys
        validate = guard or self.guard
        validate()
        handle, url = self.driver.current_window_handle, self.driver.current_url
        unavailable = 'The ' + text + ' control is unavailable or ambiguous.'

        def check_page():
            validate()
            if self.driver.current_window_handle != handle or self.driver.current_url != url:
                raise CopyStopped('Hub changed pages while locating the ' + text + ' control.')

        def find_button():
            check_page()
            try:
                buttons = [e for e in (root or self.driver).find_elements('css selector', 'button')
                           if ' '.join(e.get_attribute('textContent').split()).casefold() == text.casefold()
                           and e.is_displayed()]
                if len(buttons) > 1:
                    raise CopyStopped(unavailable)
                return buttons[0] if buttons and buttons[0].is_enabled() else None
            except StaleElementReferenceException:
                if root is not None:
                    # A replaced dialog cannot safely be rediscovered outside its scope.
                    raise CopyStopped(unavailable) from None
                return None

        button = self.wait(find_button, unavailable)
        check_page()
        # A keypress can reach Hub even if the browser loses its response.
        # Only the lookup above may repeat.
        button.send_keys(Keys.ENTER)

    def bind(self, target):
        self.current = deepcopy(target)
        self.driver.switch_to.window(target['handle'])
        self.guard()

    def tab(self, name):
        self.guard()
        controls = [e for e in self.driver.find_elements('css selector','main button[role="tab"]')
                    if e.get_attribute('textContent').strip() == name]
        if len(controls) != 1:
            raise CopyStopped('The expected ' + name + ' page is unavailable.')
        if controls[0].get_attribute('aria-selected') != 'true':
            from selenium.webdriver.common.keys import Keys
            controls[0].send_keys(Keys.ENTER)
        key = {'Listing particulars':'details','Deal Particulars':'deal_particulars','Media':'media','Portals':'portals'}[name]
        self.wait(lambda: parse_editor_url(self.driver.current_url)['page_key'] == key and
                  any(e.get_attribute('textContent').strip()==name and e.get_attribute('aria-selected')=='true'
                      for e in self.driver.find_elements('css selector','main button[role="tab"]')),
                  'Hub did not finish opening the selected page.')
        self.guard()

    def captured_editor(self, reference, *, include_media_captions=True, include_suburb_selection=False):
        self.current = None
        binding = self.hub.editor_bindings.get(reference)
        if binding and binding['handle'] in self.driver.window_handles:
            self.driver.switch_to.window(binding['handle'])
            try:
                route = parse_editor_url(self.driver.current_url)
            except ValueError:
                route = None
            if not route or route['listing_id'] != binding['listing_id']:
                self.driver.get('https://hub.remax.co.za/app/listings/edit/' + binding['listing_id'])
        else:
            self.hub.search_reference(reference, all_listing_types=True)
            _wait_search(self.hub, reference, self.cancelled)
            self.hub.open_listing_editor(reference, timeout=30)
            binding = self.hub.editor_bindings[reference]
        self.driver.refresh()
        self.wait(lambda: bool(self.driver.find_elements('css selector','main button[role="tab"]')),
                  'The saved listing did not finish loading.')
        def identity_ready():
            from .hub_readonly import EditorNavigationInProgress
            try:
                observed = self.hub._read_editor_identity(reference, binding['listing_id'])
            except EditorNavigationInProgress:
                return False
            # Tabs can appear before the listing headings after a reload.
            # Wait for absent headings, but never wait away a contradiction.
            for prefix in ('outer', 'inner'):
                count = observed.get(prefix + '_count')
                if count and (count != 1 or observed.get(prefix + '_matches') is not True):
                    raise CopyStopped('The reloaded editor does not match the intended listing.')
            if not observed.get('outer_count') or not observed.get('inner_count'):
                return False
            return self.hub._verified_editor_identity(observed)
        self.wait(identity_ready, 'The listing headings did not finish loading. No further action was taken.')
        self.current = {'reference':reference, **binding}
        reader = MatchedHubCapture(self.hub, reference, timeout=30, cancelled=self.cancelled,
            include_room_options=True, include_general_features=True, include_media_captions=include_media_captions,
            include_suburb_selection=include_suburb_selection)
        capture = reader.capture()
        if capture.get('stop_reason'):
            from .matched_capture import matched_capture_failure_note
            detail = matched_capture_failure_note(capture.get('browser_failure'))
            raise CopyStopped(('The saved listing could not be read completely. No further action was taken. ' + detail).strip())
        return capture

    def _source_matches(self, collection, fresh):
        source = collection['source']
        if content_signature(fresh) != content_signature(source):
            return False
        suburb = propose_address_fields(source)['fields'].get('suburb', {}).get('field', {})
        full = suburb.get('selected_suburb_label')
        return not full or self.selected_suburb_label(suburb['value']) == full

    @diagnosed
    def check_source(self, collection, plan):
        fresh = self.captured_editor(plan['source_reference'])
        if not self._source_matches(collection, fresh):
            raise CopyStopped('The original changed after collection. Collect it again and review its address before relisting.')
        self.source = deepcopy(self.current)
        self.tab('Portals')
        state = read_portals(self.driver, guard=self.guard, timeout=45)
        if active_destinations(state) != plan['destinations']:
            raise CopyStopped('The original portal destinations changed. Review the collection first.')

    @diagnosed
    def check_removed_source(self, collection, plan):
        fresh = self.captured_editor(plan['source_reference'])
        if not self._source_matches(collection, fresh):
            raise CopyStopped('The original content changed after removal. Review it before continuing.')
        self.source = deepcopy(self.current)
        self.tab('Portals')
        state = read_portals(self.driver, guard=self.guard, timeout=45)
        if state['published'] or any(state['portals'][d]['state'] != 'unpublished' for d in plan['destinations']):
            raise CopyStopped('Hub has not confirmed removal from every original destination. No removal was repeated.')
        return {'observed':state, 'reconciled_by':'fresh_source_and_portal_read'}

    @diagnosed
    def prepare(self, plan):
        self.current = None
        # A discarded unsaved tab may still be WebDriver's selected context.
        # Re-anchor to the collected editor before asking Chrome for a new tab.
        binding = self.hub.editor_bindings.get(plan['source_reference'])
        if not binding or binding['handle'] not in self.driver.window_handles:
            self.hub.search_reference(plan['source_reference'], all_listing_types=True)
            _wait_search(self.hub, plan['source_reference'], self.cancelled)
            self.hub.open_listing_editor(plan['source_reference'], timeout=30)
            binding = self.hub.editor_bindings[plan['source_reference']]
        self.driver.switch_to.window(binding['handle'])
        try:
            route = parse_editor_url(self.driver.current_url)
        except ValueError:
            route = None
        if not route or route['listing_id'] != plan['source_listing_id']:
            self.driver.get('https://hub.remax.co.za/app/listings/edit/' + plan['source_listing_id'])
            self.wait(lambda:bool(self.driver.find_elements('css selector','main button[role="tab"]')), 'The original editor did not reopen.')
        self.hub.verify_editor_reference(plan['source_reference'], plan['source_listing_id'])
        self.driver.switch_to.new_window('tab')
        self.driver.get('https://hub.remax.co.za/app/listings/add/' + plan['particulars']['mode'])
        self.wait(lambda: self.driver.find_elements('css selector','input[name="listing_type_id"]'),
                  'The new listing form did not load.')
        filler = ParticularsFiller(self.driver, cancelled=self.cancelled, progress=self.progress)
        filler.fill(plan['particulars'])
        def ready_to_save():
            filler.guard()
            buttons = [e for e in self.driver.find_elements('css selector', 'main button')
                       if e.is_displayed() and e.get_attribute('textContent').strip() == 'Save']
            if len(buttons) != 1:
                raise CopyStopped('The new listing Save control is missing or ambiguous. The original remains active.')
            return buttons[0].is_enabled()
        # Readiness is client-side only; saving can still be rejected by Hub.
        # Never remove the original while the form is visibly invalid.
        self.wait(ready_to_save,
                  'Hub has not enabled Save for the reviewed form. Check its required fields; the original remains active.', timeout=15)
        return {'handle':self.driver.current_window_handle,'url':self.driver.current_url}

    @diagnosed
    def unpublish(self, plan):
        self.bind(self.source)
        self.tab('Portals')
        state = read_portals(self.driver, guard=self.guard)
        if active_destinations(state) != plan['destinations']:
            raise CopyStopped('The source publication status changed after preparation.')
        self.button('Remove from all portals')
        def removed():
            state = read_portals(self.driver, guard=self.guard)
            return state if not state['published'] and all(state['portals'][d]['state']=='unpublished' for d in plan['destinations']) else None
        return {'observed':self.wait(removed, 'Hub has not confirmed removal. The source was not archived.', timeout=120)}

    @diagnosed
    def check_unarchived_source(self, plan):
        return self._check_source_archive(plan, archived=False)

    def _check_source_archive(self, plan, *, archived, restore=True):
        from selenium.webdriver.common.keys import Keys
        self.current = None
        reference, ident = plan['source_reference'], plan['source_listing_id']
        self.hub.search_reference(reference, all_listing_types=True, archived=archived)
        self.driver.refresh()
        self.wait(lambda:bool(self.driver.find_elements('css selector','input[placeholder="Enter fusion ref, listing ID or address"]')), 'The fresh listing search did not load.')
        self.hub.search_reference(reference, all_listing_types=True, archived=archived)
        _wait_search(self.hub, reference, self.cancelled)
        rows = [r for r in self.driver.find_elements('css selector','[role="row"]')
                if [normalize_rxcu(m[1]) for p in r.find_elements('tag name','p')
                    if (m:=re.fullmatch(r'Ref:\s*(RXCU-?[0-9]+)',p.get_attribute('textContent').strip(),re.I))] == [reference]]
        if len(rows) != 1:
            raise CopyStopped('The original archive status is ambiguous.')
        control = self.one('[role="combobox"]',rows[0])
        control.send_keys(Keys.SPACE)
        menu = self.wait(lambda:next((m for m in self.driver.find_elements('css selector','[role="listbox"]')
                    if m.is_displayed() and m.get_attribute('id') == control.get_attribute('aria-controls')),None), 'The original action menu did not open.')
        kind = '2' if plan['particulars']['mode']=='rent' else '1'
        expected = f'https://hub.remax.co.za/app/listings/archive/{ident}?unarchive={str(archived).lower()}&type={kind}'
        label = 'Unarchive Listing' if archived else 'Archive Listing'
        choices = [a for a in menu.find_elements('tag name','a') if a.is_displayed()
                   and a.get_attribute('textContent').strip()==label and a.get_attribute('href')==expected]
        opposite = 'unarchive=' + str(not archived).lower()
        if len(choices)!=1 or any(opposite in (a.get_attribute('href') or '') for a in menu.find_elements('tag name','a')):
            raise CopyStopped('The original archive status does not match the expected state. No archive action was repeated.')
        from .hub_readonly import ROW_BINDING_SCRIPT
        if self.driver.execute_script(ROW_BINDING_SCRIPT, rows[0], control, menu, choices[0], reference) is not True:
            raise CopyStopped('The original row changed during the archive check.')
        # MUI makes the background combobox inert while its menu is open.
        # Dismiss from the verified menu link without activating that link.
        choices[0].send_keys(Keys.ESCAPE)
        if restore:
            self.bind(self.source)
        return {'archived':archived,'reference':reference,'listing_id':ident,
                'reconciled_by':'fresh_manage_listing_archive_action'}

    @diagnosed
    def archive(self, plan):
        from selenium.webdriver.common.keys import Keys
        self.guard()
        ident = plan['source_listing_id']
        kind = '1' if plan['particulars']['mode']=='sale' else '2'
        self.current = None
        self.driver.get(f'https://hub.remax.co.za/app/listings/archive/{ident}?unarchive=false&type={kind}')
        url, handle = self.driver.current_url, self.driver.current_window_handle
        def archive_guard():
            if self.cancelled() or self.driver.current_url != url or self.driver.current_window_handle != handle:
                raise CopyStopped('The archive page changed. No further action was sent.')
        self.wait(lambda:self.driver.find_elements('css selector','main input[type="radio"]'), 'The archive form did not load.')
        reason = 'Listing has been cancelled' if kind == '1' else 'Listing has been withdrawn'
        choices = [e for e in self.driver.find_elements('css selector','main label')
                   if ' '.join(e.get_attribute('textContent').split()) == reason]
        if len(choices) != 1:
            raise CopyStopped('The cancellation reason could not be identified.')
        archive_guard()
        self.one('input[type="radio"]', choices[0]).send_keys(Keys.SPACE)
        if kind == '1':
            self.select_expired_reason(archive_guard)
        elif any(e.is_displayed() for e in self.driver.find_elements('css selector','main [role="combobox"]')):
            raise CopyStopped('The rental archive reasons changed. Review them before archiving.')
        archive_guard()
        if not self.one('input[type="radio"]',choices[0]).is_selected():
            raise CopyStopped('Hub did not retain the archive choice.')
        self.button('Archive Listing')
        self.wait(lambda:any('Listing archived successfully!' in e.get_attribute('textContent')
                             for e in self.driver.find_elements('css selector','[role="status"],[role="alert"],[data-sonner-toast]')),
                  'Hub did not confirm archiving. No replacement was saved.', timeout=45)
        if self.driver.current_window_handle != handle:
            raise CopyStopped('The archive tab changed before confirmation.')
        return {'archived':True,'reason':reason,'subreason':'Mandate expired' if kind=='1' else None,'listing_id':ident}

    def select_expired_reason(self, archive_guard):
        from selenium.webdriver.common.keys import Keys
        combos = self.wait(lambda:[e for e in self.driver.find_elements('css selector','main [role="combobox"]') if e.is_displayed()],
                           'The cancellation reason selector did not appear.')
        if len(combos) != 1:
            raise CopyStopped('The cancellation selector is ambiguous.')
        combos[0].send_keys(Keys.SPACE)
        expired = [e for e in self.driver.find_elements('css selector','[role="option"]')
                   if e.is_displayed() and e.get_attribute('textContent').strip().casefold()=='mandate expired']
        if len(expired) != 1:
            raise CopyStopped('The Mandate expired reason is unavailable.')
        expired[0].click()
        archive_guard()
        if 'mandate expired' not in combos[0].get_attribute('textContent').strip().casefold():
            raise CopyStopped('Hub did not retain the Mandate expired reason.')

    @diagnosed
    def create(self, prepared, plan):
        self.current = None
        self.driver.switch_to.window(prepared['handle'])
        if self.driver.current_url != prepared['url']:
            raise CopyStopped('The unsaved replacement form changed. No Save click was sent.')
        self.button('Save')
        def created():
            match = re.fullmatch(r'https://hub\.remax\.co\.za/app/listings/edit/([0-9]+)(?:/(?:particulars|deal_particulars|media|portals))?',self.driver.current_url)
            headings = self.driver.find_elements('css selector','main h4')
            if not match or len(headings)!=1:
                return None
            ref = re.search(r'Edit listing\s*-\s*(RXCU-?[0-9]+)\s*\(',headings[0].get_attribute('textContent'),re.I)
            if not ref:
                return None
            return {'listing_id':match[1], 'reference':normalize_rxcu(ref[1]), 'handle':prepared['handle']}
        target = self.wait(created,'The Save outcome is uncertain. Check Hub before creating another copy.',timeout=60)
        if target['listing_id'] == plan['source_listing_id'] or target['reference'] == plan['source_reference']:
            raise CopyStopped('Hub did not return a distinct replacement identity.')
        self.current = target
        self.guard()
        return target

    def fill_media_text(self, target, plan):
        from selenium.webdriver.common.keys import Keys
        self.bind(target)
        self.tab('Media')
        self.wait(lambda:self.driver.find_elements('css selector','textarea[name="marketing_header"]'), 'The media form did not load.')
        for key,value in plan['media'].items():
            if key.startswith('description_'):
                continue
            selector = 'textarea#outlined-multiline-flexible' if key=='brochure_description' else f'[name="{key}"]'
            field = self.one(selector)
            limit = field.get_attribute('maxlength')
            if limit and int(limit) >= 0 and len(value) > int(limit):
                raise CopyStopped('Hub shortened a description field limit. Review its wording before continuing.')
            field.send_keys(Keys.CONTROL,'a',Keys.NULL,Keys.BACKSPACE,value,Keys.TAB)
            if self.one(selector).get_property('value') != value:
                raise CopyStopped('Hub did not retain a copied description or media link.')
        editor = self.one('.tiptap.ProseMirror[role="textbox"][contenteditable="true"]')
        # Do not provide collapsed text/plain: Hub prefers it to the formatted
        # clipboard representation and loses every source line break.
        html, prefix = description_clipboard_html(plan['media']['description_html'])
        self.clipboard(html, None)
        editor.send_keys(Keys.CONTROL,'a',Keys.NULL,Keys.CONTROL,'v',Keys.NULL)
        if prefix:
            editor.send_keys(Keys.CONTROL, Keys.HOME, Keys.NULL, prefix)
        editor.send_keys(Keys.TAB)
        self.guard()
        entered = media_plan(PartialHubCapture(self.driver).capture_current_page()['pages'][-1])
        if entered != plan['media']:
            raise CopyStopped('Hub changed the pasted description. It was not saved or published.')
        self.button('Next')
        self.wait(lambda:parse_editor_url(self.driver.current_url)['page_key']=='portals', 'The descriptions were not confirmed saved.')
        state = read_portals(self.driver, guard=self.guard)
        if state['published']:
            raise CopyStopped('Hub reports publication during media saving. Review the replacement before further portal actions.')
        self.tab('Media')
        self.driver.refresh()
        self.wait(lambda:self.driver.find_elements('css selector','textarea[name="marketing_header"]'), 'The saved media page did not load.')
        actual = media_plan(PartialHubCapture(self.driver).capture_current_page()['pages'][-1])
        if not same_saved_media(plan['media'], actual):
            raise CopyStopped('The saved descriptions differ from the source. The replacement was not sent to portals.')
        return {'descriptions_saved_and_reloaded':True}

    @diagnosed
    def recover_copy(self, plan, previous, runs):
        """Reopen the recorded copy and reconcile only positively received photos."""
        actions = {a['action']: a for a in previous['actions']}
        recorded = actions['create']['receipt']
        reference, ident = recorded['reference'], recorded['listing_id']
        if reference == plan['source_reference'] or ident == plan['source_listing_id']:
            raise CopyStopped('The recorded replacement is not distinct from the original.')
        self.current = None
        self.hub.search_reference(reference, all_listing_types=True)
        _wait_search(self.hub, reference, self.cancelled)
        self.hub.open_listing_editor(reference, timeout=30)
        if self.hub.editor_bindings[reference]['listing_id'] != ident:
            raise CopyStopped('The existing replacement does not match the recorded identity.')
        # Only media is changed during this recovery. The complete particulars,
        # rooms and agents are checked again by verify_content before publishing.
        self.current = {'reference':reference, **self.hub.editor_bindings[reference]}
        self.guard()
        target = deepcopy(self.current)
        self.tab('Portals')
        state = read_portals(self.driver, guard=self.guard)
        if state['published'] or any(state['portals'][d]['state'] != 'unpublished' for d in plan['destinations']):
            raise CopyStopped('The unfinished replacement is already published or pending. Review it before copying.')
        estate = actions.get('save_estate_name', {})
        if estate.get('state') in ('started', 'uncertain'):
            wanted = self.planned_estate_name(plan)
            if not wanted or self.saved_estate_name() != wanted:
                raise CopyStopped('The estate name on the existing copy still differs. Review Listing Particulars before continuing.')
            runs.reconcile_estate_name(previous['id'], target)
        commission = actions.get('copy_commission', {})
        if commission.get('state') in ('started', 'uncertain'):
            self.tab('Deal Particulars')
            self.driver.refresh()
            self.wait(lambda:self.driver.find_elements('css selector', 'input[name="total_commission_value"]'),
                      'The existing sale commission did not load.')
            self.guard()
            saved = PartialHubCapture(self.driver).capture_current_page(ident)['pages'][-1]
            review = plan.get('commission_review')
            if review and (review['reference'] != reference or review['listing_id'] != ident):
                raise CopyStopped('The commission correction belongs to a different sale.')
            if not plan['deal'] or not same_commission(plan['deal'], saved, review):
                raise CopyStopped('The saved commission on the existing copy differs from the expected amount, rate or agent breakdown. Check Deal Particulars.')
            runs.reconcile_commission(previous['id'], {'reference':reference, 'listing_id':ident,
                'commission_copied':True, 'reconciled_by':'fresh_unpublished_commission_read'})
        self.tab('Media')
        self.driver.refresh()
        self.wait(lambda:self.driver.find_elements('css selector','textarea[name="marketing_header"]'),
                  'The existing replacement media did not finish loading.')
        self.guard()
        page = PartialHubCapture(self.driver).capture_current_page()['pages'][-1]
        count = len(self.driver.find_elements('css selector', 'main img[id^="image-"]'))
        if count > len(plan['photos']):
            raise CopyStopped('The existing copy has extra photos.')
        comparison = None
        if count:
            downloaded = download_originals(page['media'], ident, self.media_root, cancelled=self.cancelled)
            comparison = compare_uploaded_images(plan['photos'][:count], downloaded['media_manifest'])
            if not comparison['order_matches']:
                raise CopyStopped('The received photos differ from the saved originals or their order.')
        positions = set()
        unresolved = []
        for name, action in actions.items():
            match = re.fullmatch(r'upload_photo_([0-9]+)_attempt_[1-3]', name)
            if not match:
                continue
            position = int(match[1])
            if action['state'] == 'done' and action['receipt'].get('upload_failed'):
                continue
            if position >= count:
                raise CopyStopped('An earlier photo outcome is still unresolved. No upload was repeated.')
            positions.add(position)
            if action['state'] != 'done':
                unresolved.append(name)
        if positions != set(range(count)):
            raise CopyStopped('The existing photos include an unrecorded upload. Review them before continuing.')
        for name in unresolved:
            runs.reconcile_uploaded_photo(previous['id'], name, {
                'reconciled_by':'fresh_replacement_photo_comparison', 'reference': reference,
                'listing_id': ident, 'received_count': count, 'photo_comparison': comparison})
        media_action = actions.get('copy_media_text')
        if media_action and media_action['state'] != 'done':
            saved_media = media_plan(page)
            empty = not saved_media['description_text'].strip() and all(
                not value.strip() for key,value in saved_media.items() if key not in ('description_html','description_text'))
            outcome = ('matching' if same_saved_media(plan['media'], saved_media) else 'empty' if empty else
                       'formatting_changed' if only_description_formatting_changed(plan['media'], saved_media) else None)
            if not outcome:
                raise CopyStopped('The saved descriptions contain other text. Review them before overwriting.')
            runs.reconcile_media_text(previous['id'], {'reference':reference,'listing_id':ident,
                'reconciled_by':'fresh_unpublished_media_read','outcome':outcome})
        target['verified_photo_prefix_count'] = count
        return target

    @diagnosed
    def check_created_copy(self, collection, plan, reference, listing_id):
        reference = normalize_rxcu(reference)
        if reference == plan['source_reference'] or listing_id == plan['source_listing_id']:
            raise CopyStopped('The existing replacement must be distinct from the original.')
        # Hub no longer exposes archived content in the normal editor. The
        # original backup and completed removal action remain the source of
        # content/removal evidence; freshly verify the exact archived row/link.
        self._check_source_archive(plan, archived=True, restore=False)
        self.current = None
        self.hub.search_reference(reference, all_listing_types=True)
        _wait_search(self.hub, reference, self.cancelled)
        self.hub.open_listing_editor(reference, timeout=30)
        if self.hub.editor_bindings[reference]['listing_id'] != listing_id:
            raise CopyStopped('The replacement reference and Hub edit URL do not identify the same listing.')
        # Caption inventory intentionally requires photos for source capture.
        # This recovery accepts only an empty copy and rejects any media below.
        actual = self.captured_editor(reference, include_media_captions=False)
        self._verify_particulars(actual, plan)
        pages = {p['page_key']: p for p in actual['pages']}
        media = media_plan(pages['media'])
        if (not only_media_interface_artwork(pages['media'].get('media')) or pages['media'].get('photo_captions')
                or any(value.strip() for key, value in media.items() if key != 'description_html')):
            raise CopyStopped('This copy already has media or descriptions. No existing content was overwritten.')
        self.tab('Portals')
        portals = read_portals(self.driver, guard=self.guard)
        if (portals['published'] or any(p['state'] in ('published', 'pending') for p in portals['portals'].values())
                or any(portals['portals'][d]['state'] != 'unpublished' for d in plan['destinations'])):
            raise CopyStopped('The existing copy is published or has a pending portal request.')
        return {**deepcopy(self.current), 'source_reference': plan['source_reference'],
                'source_listing_id': plan['source_listing_id'],
                'reconciled_by': 'fresh_archived_row_and_empty_replacement_read',
                'source_archived': True, 'particulars_verified': True,
                'empty_media': True, 'unpublished': True}

    @diagnosed
    def copy_content(self, target, plan, runs, run_id):
        wanted = self.planned_estate_name(plan)
        if wanted:
            self.bind(target)
            saved = self.saved_estate_name()
            if saved != wanted:
                if saved:
                    raise CopyStopped('The saved estate name differs from the reviewed address. Check Listing Particulars.')
                runs.perform_once(run_id, 'save_estate_name', lambda:self.restore_estate_name(target, wanted, plan))
        # Keep the original commission facts; rental has no Deal Particulars.
        if plan['deal'] is not None:
            runs.perform_once(run_id,'copy_commission',lambda:self.copy_commission(target,plan['deal']))
        self.copy_photos(target,plan,runs,run_id)
        runs.perform_once(run_id,'copy_media_text',lambda:self.fill_media_text(target,plan))

    @staticmethod
    def planned_estate_name(plan):
        return next((c['value'] for c in plan['particulars']['controls'] if c['key'] == 'estate_name'), '')

    def saved_estate_name(self):
        self.tab('Listing particulars')
        self.wait(lambda:self.driver.find_elements('css selector', 'main input#estate_name'), 'The estate name field did not load.')
        self.guard()
        return self.one('main input#estate_name').get_property('value')

    def restore_estate_name(self, target, wanted, plan):
        from selenium.webdriver.common.keys import Keys
        self.bind(target)
        self.tab('Portals')
        portals = read_portals(self.driver, guard=self.guard)
        if portals['published'] or any(portals['portals'][d]['state'] != 'unpublished' for d in plan['destinations']):
            raise CopyStopped('Check the publication state before editing the estate name.')
        if self.saved_estate_name() != '':
            raise CopyStopped('The estate name changed while opening the copy. Review it before editing.')
        self.one('main input#estate_name').send_keys(wanted, Keys.TAB)
        if self.one('main input#estate_name').get_property('value') != wanted:
            raise CopyStopped('Hub did not retain the reviewed estate name.')
        self.button('Next')
        next_page = 'deal_particulars' if plan['particulars']['mode'] == 'sale' else 'media'
        self.wait(lambda:parse_editor_url(self.driver.current_url)['page_key'] == next_page, 'The particulars save did not finish.')
        if self.saved_estate_name() != wanted:
            raise CopyStopped('Hub did not save the estate name. Check the existing replacement.')
        return {'estate_name_verified':True, 'reference':target['reference'], 'listing_id':target['listing_id']}

    def guard_commission_dialog(self, target, dialog):
        # Hub hides the previously verified editor headings behind these modals.
        # Bind the modal to that same tab, listing ID and Deal Particulars route.
        route = parse_editor_url(self.driver.current_url)
        visible = [d for d in self.driver.find_elements('css selector', '[role="dialog"]') if d.is_displayed()]
        if (self.cancelled() or self.current != target or self.driver.current_window_handle != target['handle']
                or route['listing_id'] != target['listing_id'] or route['page_key'] != 'deal_particulars'
                or visible != [dialog]):
            raise CopyStopped('The active commission dialog or its listing changed. Check Deal Particulars.')

    def confirm_commission_warning(self, target, rate):
        dialogs = [d for d in self.driver.find_elements('css selector', '[role="dialog"]') if d.is_displayed()]
        if not dialogs:
            return
        dialog = dialogs[0]
        def validate():
            self.guard_commission_dialog(target, dialog)
            message = ' '.join(dialog.text.split()).replace('’', "'")
            match = re.fullmatch(r"Confirm commission rate You've entered a total commission rate of ([0-9]+(?:\.[0-9]+)?)\s*%, which is unusually (?:high|low)\. Please confirm this is correct before continuing\. Let me fix it Yes, this is correct", message, re.I)
            if not match or Decimal(match[1]) != Decimal(rate):
                raise CopyStopped('The commission warning differs from the original rate or wording. Check the rate in Hub.')
        self.button('Yes, this is correct', dialog, guard=validate)
        from selenium.webdriver.support.expected_conditions import invisibility_of_element
        self.wait(lambda:invisibility_of_element(dialog)(self.driver), 'Hub did not close its commission confirmation.')

    def commission_saved_to_media(self, target, rate):
        from selenium.common.exceptions import StaleElementReferenceException
        route = parse_editor_url(self.driver.current_url)
        if self.driver.current_window_handle != target['handle'] or route['listing_id'] != target['listing_id']:
            raise CopyStopped('Hub changed listings while saving the commission.')
        if route['page_key'] == 'media':
            return True
        if route['page_key'] != 'deal_particulars':
            raise CopyStopped('Hub opened an unexpected page while saving the commission.')
        # Next can show a temporary dialog while the save navigates to Media.
        # Only enter the warning handler for a recognizable rate confirmation.
        try:
            dialogs = [d for d in self.driver.find_elements('css selector', '[role="dialog"]') if d.is_displayed()]
            warning = len(dialogs) == 1 and ' '.join(dialogs[0].text.split()).casefold().startswith('confirm commission rate ')
        except StaleElementReferenceException:
            return False
        if warning:
            self.confirm_commission_warning(target, rate)
        return False

    def edit_commission_split(self, target, row, saved):
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.expected_conditions import invisibility_of_element
        self.guard()
        buttons = row.find_elements('css selector', 'button')
        if len(buttons) != 2:
            raise CopyStopped('The commission edit action changed.')
        buttons[1].send_keys(Keys.ENTER)
        dialog = self.wait(lambda:next((d for d in self.driver.find_elements('css selector','[role="dialog"]')
                                        if d.is_displayed()), None), 'The commission editor did not open.')
        def validate_agent():
            self.guard_commission_dialog(target, dialog)
            if dialog.text.splitlines()[:1] != ['Edit Agent Listing Commission']:
                raise CopyStopped('The expected agent commission editor is unavailable.')
            for key, expected in [('agent_id', saved[0]), ('agent_type', saved[2])]:
                selected = self.one(f'#mui-component-select-{key}[role="combobox"]', dialog)
                if not selected.is_displayed() or selected.text.strip().casefold() != expected.strip().casefold():
                    raise CopyStopped('The agent or role in the commission editor differs from the original.')
        selector = 'input[name="commission_sales_percentage"]'
        validate_agent()
        self.one(selector, dialog).send_keys(Keys.CONTROL, 'a', Keys.NULL, saved[3], Keys.TAB)
        def validate_save():
            validate_agent()
            entered = self.one(selector, dialog).get_property('value')
            if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', entered or '') or Decimal(entered) != Decimal(saved[3]):
                raise CopyStopped('The entered commission split differs from the original.')
        self.button('Save', dialog, guard=validate_save)
        self.wait(lambda:invisibility_of_element(dialog)(self.driver), 'Hub did not close its agent commission editor.')

    def copy_commission(self, target, source):
        from selenium.webdriver.common.keys import Keys
        self.bind(target)
        self.tab('Deal Particulars')
        fields = {f['name']:f['value'] for f in source['fields']}
        if set(fields) != {'total_commission_value','total_commission_percentage'}:
            raise CopyStopped('The original commission fields are incomplete.')
        self.wait(lambda:self.driver.find_elements('css selector','input[name="total_commission_percentage"]'), 'Commission fields did not load.')
        field = self.one('input[name="total_commission_percentage"]')
        field.send_keys(Keys.CONTROL,'a',Keys.NULL,fields['total_commission_percentage'],Keys.TAB)
        self.confirm_commission_warning(target, fields['total_commission_percentage'])
        # Hub rounds the displayed percentage. Re-entering that rounded value
        # alone can change the actual amount and every agent's fees. Set the
        # captured amount last and let Hub derive its percentage from it.
        amount = self.one('input[name="total_commission_value"]')
        amount.send_keys(Keys.CONTROL,'a',Keys.NULL,fields['total_commission_value'],Keys.TAB)
        self.confirm_commission_warning(target, fields['total_commission_percentage'])
        tables = [t for t in source['tables'] if t['rows'] and t['rows'][0][0]=='Agent']
        if len(tables)!=1:
            raise CopyStopped('The commission agent table is missing or ambiguous.')
        wanted = [r for r in tables[0]['rows'][1:] if r and r[0]!='Totals:']
        for saved in wanted:
            rows = [r for r in self.driver.find_elements('css selector','main tbody tr')
                    if r.find_elements('css selector','td') and r.find_elements('css selector','td')[0].get_attribute('textContent').strip()==saved[0]]
            if len(rows)!=1 or len(saved)!=9:
                raise CopyStopped('An original commission agent is missing from the copy.')
            cells = rows[0].find_elements('css selector','td')
            if cells[3].get_attribute('textContent').strip()!=saved[3]:
                self.edit_commission_split(target, rows[0], saved)
        self.confirm_commission_warning(target, fields['total_commission_percentage'])
        self.button('Next')
        self.wait(lambda:self.commission_saved_to_media(target, fields['total_commission_percentage']),
                  'Hub did not finish saving the commission. Check the open page or dialog before continuing.')
        # Verify the saved money before transferring photos, as well as in
        # the final full listing comparison. Never accept a recalculated total.
        self.tab('Deal Particulars')
        self.wait(lambda:self.driver.find_elements('css selector','input[name="total_commission_value"]'),
                  'The saved commission fields did not load.')
        self.guard()
        saved = PartialHubCapture(self.driver).capture_current_page(target['listing_id'])['pages'][-1]
        if not same_commission(source, saved):
            raise CopyStopped('Saved commission details differ. No photos were uploaded and the replacement remains unpublished.')
        self.tab('Media')
        return {'commission_copied':True}

    def copy_photos(self, target, plan, runs, run_id):
        from selenium.webdriver.common.keys import Keys
        self.bind(target)
        self.tab('Media')
        self.wait(lambda:self.driver.find_elements('css selector','main input[type="file"]'), 'The image chooser did not load.')
        received_count = len(self.driver.find_elements('css selector','main img[id^="image-"]'))
        prefix = target.get('verified_photo_prefix_count', 0)
        if received_count != prefix:
            raise CopyStopped('The new copy already contains images. Check them before sending more files.')
        # One photo at a time avoids Hub silently dropping a large batch. Each
        # received original is downloaded and compared before the next upload.
        for index, photo in enumerate(plan['photos']):
            if index < prefix:
                continue
            self.progress(f'Checking photo {index+1} of {len(plan["photos"])}')
            def upload(photo=photo,index=index):
                self.guard()
                def notices():
                    return {e.get_attribute('textContent').strip() for e in self.driver.find_elements(
                        'css selector','[role="alert"],[data-sonner-toast]') if e.is_displayed()}
                old_notices = notices()
                wrappers = [h.find_element('xpath','..') for h in self.driver.find_elements('css selector','main h6')
                            if h.get_attribute('textContent').strip()=='Listing images']
                if len(wrappers)!=1:
                    raise CopyStopped('The Listing images chooser is missing or ambiguous.')
                chooser = self.one('input[type="file"]',wrappers[0])
                chooser.send_keys(str(Path(photo['local_path']).resolve()))
                def received():
                    self.guard()
                    images = self.driver.find_elements('css selector','main img[id^="image-"]')
                    if len(images)==index+1:
                        return 'received'
                    if len(images)!=index:
                        raise CopyStopped('Hub contains an unexpected photo count. No more images were sent.')
                    if any(re.search(r'upload',s,re.I) and re.search(r'fail|error|could not',s,re.I)
                           for s in notices()-old_notices):
                        return 'failed'
                    return None
                outcome=self.wait(received,'Hub has not confirmed this photo. No duplicate or later photo was sent.',timeout=90)
                if outcome=='failed':
                    return {'upload_failed':True,'missing_position':index,'explicit_hub_error':True}
                page = PartialHubCapture(self.driver).capture_current_page()['pages'][-1]
                downloaded = download_originals(page['media'],target['listing_id'],self.media_root,cancelled=self.cancelled)
                check = compare_uploaded_images(plan['photos'][:index+1],downloaded['media_manifest'])
                if not check['order_matches']:
                    raise CopyStopped('Hub saved different, extra or reordered photos. Review them before continuing.')
                return {'received_count':index+1,'photo_comparison':check}
            for attempt in range(3):
                receipt=runs.perform_once(run_id,f'upload_photo_{index}_attempt_{attempt+1}',upload)
                if not receipt.get('upload_failed'):
                    break
                self.progress(f'Hub rejected photo {index+1}; retrying only this missing photo')
            else:
                raise CopyStopped('Hub rejected the same photo three times. The copy remains unpublished; earlier photos are saved.')
        self.guard()
        for index,photo in enumerate(plan['photos']):
            if not photo['caption']:
                continue
            def caption(index=index,photo=photo):
                self.guard()
                image = self.one(f'main img[id^="image-"][pos="{index}"]')
                card = image.find_element('xpath','ancestor::*[@aria-roledescription="sortable"][1]')
                self.one('button[aria-label="Add caption"]',card).send_keys(Keys.ENTER)
                dialog = self.wait(lambda:next((e for e in self.driver.find_elements('css selector','[role="dialog"]') if e.is_displayed()),None),'The caption editor did not open.')
                self.one('input#caption',dialog).send_keys(Keys.CONTROL,'a',Keys.NULL,photo['caption'],Keys.TAB)
                self.button('Save',dialog)
                self.wait(lambda:not any(e.is_displayed() for e in self.driver.find_elements('css selector','[role="dialog"]')),'Hub did not confirm the caption save.')
                return {'caption_saved':True,'position':index}
            runs.perform_once(run_id,f'caption_{index}',caption)

    def selected_suburb_label(self, short):
        """Read the existing selection via its owned autocomplete; never select."""
        from .suburb_selection import read_selected_suburb
        self.tab('Listing particulars')
        return read_selected_suburb(self.driver, short, self.guard, self.wait)

    def _verify_particulars(self, actual, plan):
        proposed = propose_address_fields(actual)
        original = {v['path']:v['field'] for v in proposed['fields'].values()}
        copied = particulars_plan(actual,{'original':original,'edited':deepcopy(original)})
        copied, suburb_label = resolve_saved_suburb(plan['particulars'], copied, self.selected_suburb_label)
        differences = particulars_differences(plan['particulars'], copied)
        if differences:
            raise CopyStopped('Saved particulars differ. ' + '\n'.join(differences) + '\nThe replacement remains unpublished.')
        pages = {p['page_key']:p for p in actual['pages']}
        require_empty_related(pages['details'])
        return suburb_label

    @diagnosed
    def verify_content(self, target, plan):
        # Open by the new reference through Manage Listings, establishing the
        # same observed row/link association as ordinary source collection.
        self.current = None
        self.hub.search_reference(target['reference'],all_listing_types=True)
        _wait_search(self.hub,target['reference'],self.cancelled)
        self.hub.open_listing_editor(target['reference'],timeout=30)
        actual = self.captured_editor(target['reference'])
        suburb_label = self._verify_particulars(actual, plan)
        pages = {p['page_key']:p for p in actual['pages']}
        if not same_saved_media(plan['media'], media_plan(pages['media'])):
            raise CopyStopped('Saved descriptions differ. The replacement remains unpublished.')
        photos = download_originals(pages['media']['media'],target['listing_id'],self.media_root,cancelled=self.cancelled)
        check = compare_uploaded_images(plan['photos'],photos['media_manifest'])
        if not check['order_matches']:
            raise CopyStopped('The saved photos do not match the original content and order.')
        captions = pages['media'].get('photo_captions',[])
        if [c['caption'] for c in captions] != [p['caption'] for p in plan['photos']]:
            raise CopyStopped('Saved photo captions do not match the original.')
        if plan['deal'] is not None:
            if not same_commission(plan['deal'], pages['deal_particulars'], plan.get('commission_review')):
                raise CopyStopped('Saved commission details differ. The replacement remains unpublished.')
        self.target_for_publication = deepcopy(self.current)
        return {'particulars':True,'descriptions':True,'photos':len(plan['photos']),'photo_order':True,'captions':True,
                **({'saved_suburb_selection':suburb_label} if suburb_label else {})}

    @diagnosed
    def publish(self, target, destination):
        self.bind(getattr(self,'target_for_publication',target))
        self.tab('Portals')
        state = read_portals(self.driver,guard=self.guard)
        portal = state['portals'][destination]
        if portal['state'] in ('published','pending'):
            return {'state':portal['state'],'id':portal['id'],'clicked':False}
        if not portal['can_publish']:
            raise CopyStopped('A source portal is unavailable for the replacement.')
        name = next(values[1] for values in PORTALS.values() if values[0]==destination)
        self.button(name)
        def sent():
            current = read_portals(self.driver,guard=self.guard)['portals'][destination]
            return current if current['state'] in ('published','pending') else None
        return self.wait(sent,'Hub has not confirmed the publication request. It was not submitted twice.',timeout=90)

    def publication(self, target, destinations):
        self.bind(getattr(self,'target_for_publication',target))
        self.tab('Portals')
        deadline=time.monotonic()+120
        refresh_at=time.monotonic()+10
        while True:
            state = read_portals(self.driver,guard=self.guard,timeout=45)
            selected = {d:state['portals'][d] for d in destinations}
            complete = all(p['state']=='published' for p in selected.values())
            if complete or only_moderation_pending(selected) or time.monotonic()>=deadline:
                break
            # Hub can leave a completed request displayed as pending until
            # this page reloads. Refresh only the bound read-only Portals page;
            # never submit another publication request while waiting.
            if time.monotonic() >= refresh_at:
                self.guard()
                if parse_editor_url(self.driver.current_url)['page_key'] != 'portals':
                    raise CopyStopped('The portal status page changed while waiting. No request was repeated.')
                self.driver.refresh()
                refresh_at=time.monotonic()+10
            time.sleep(2)
        return {'complete':complete, 'portals':selected}

    def recheck_publication(self,status):
        self.hub.search_reference(status['target_reference'],all_listing_types=True)
        _wait_search(self.hub,status['target_reference'],self.cancelled)
        self.hub.open_listing_editor(status['target_reference'],timeout=30)
        binding=self.hub.editor_bindings[status['target_reference']]
        if binding['listing_id']!=status['target_listing_id']:
            raise CopyStopped('The existing replacement identity does not match the saved run.')
        target={'reference':status['target_reference'],**binding}
        return self.publication(target,list(status['publication']['portals']))
