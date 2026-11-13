"""Prepare a real Hub particulars form using observed business controls.

This module never saves, archives or publishes. It fills only an already-open
new-listing form and reads each value back. Navigation, approval and durable
mutation receipts and explicit date changes belong to the calling workflow.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
import re
import time

from .address import REGISTERED_ADDRESS_KEYS, propose_address_fields
from .business_fields import LIFESTYLE_FIELDS, GENERAL_FIELDS
from .date_capture import DATE_NAMES, segmented_date_record_valid


class CopyStopped(RuntimeError):
    """Messages contain control names, never private source values."""


TEXT_NAMES = frozenset((
    'land_area', 'floor_area', 'street_number', 'street_name', 'street_type',
    'estate_name', 'lightstone_erf_number', 'lightstone_sectional_scheme_unit_number',
    'scheme_number', 'scheme_name', 'lightstone_id_reg_number', 'door_number',
    'latitude', 'longitude', 'selling_price', 'rates', 'levy', 'special_levies',
    'ensuites', 'rental_price', 'deposit',
))
COMBO_ORDER = ('deed_office_id', 'office_id', 'listing_type_id', 'mandate_type_id',
               'property_type_id', 'apartment_descriptive_property_type', 'townhouse_descriptive_property_type',
               'land_area_units', 'floor_area_units', 'country_id', 'rental_price_period', 'bank_mandate')
CHECKS = frozenset((*LIFESTYLE_FIELDS, *GENERAL_FIELDS, 'poa', 'featured', 'transfer_cost', 'address_hidden'))
RADIOS = frozenset(('sale_state_id', 'rental_state_id'))
COUNTS = frozenset('feature-' + name + '-input' for name in
                  ('bedroom', 'kitchen', 'bathroom', 'garage', 'parking', 'lounge',
                   'dining', 'braai', 'study', 'entrance_hall'))


def _text(value):
    if (not isinstance(value, str) or len(value) > 50000 or
            any(ord(c) < 32 and c not in '\n\r\t' or 0xE000 <= ord(c) <= 0xF8FF for c in value)):
        raise CopyStopped('A source value cannot be entered safely. Review the copied fields.')
    return value


def suburb_matches(source, option):
    """An editor may show only the suburb, while search adds city/province.

    Callers must still require exactly one matching option. A full reviewed
    place name must match in full; never silently drop its city or province.
    """
    source, option = source.strip(), option.strip()
    return bool(source) and (option == source or (',' not in source and option.split(',')[0].strip() == source))


def particulars_plan(capture, address_revision):
    """Use one saved address revision; reject stale or unrelated overrides."""
    pages = capture.get('pages', [])
    details = [(i, p) for i, p in enumerate(pages) if p.get('page_key') == 'details']
    if len(details) != 1 or not address_revision:
        raise CopyStopped('Collect the listing and save its address review first.')
    page_index, details = details[0]
    proposal = propose_address_fields(capture)
    if proposal['missing'] or proposal['ambiguous']:
        raise CopyStopped('The address has missing or ambiguous fields.')
    original = {v['path']: v['field'] for v in proposal['fields'].values()}
    if address_revision.get('original') != original or set(address_revision.get('edited', {})) != set(original):
        raise CopyStopped('The address review does not match this source capture.')
    for path, field in address_revision['edited'].items():
        if set(field) != set(original[path]) or any(field[k] != original[path][k] for k in field if k not in ('value', 'checked')):
            raise CopyStopped('Address control metadata changed. Review the address again.')
    for key in REGISTERED_ADDRESS_KEYS:
        path = proposal['fields'][key]['path']
        if address_revision['edited'][path] != original[path]:
            raise CopyStopped('Erf and registered scheme numbers must match the original. Open Verify address and save the review again before relisting.')
    controls, ignored = [], []
    for index, source in enumerate(details['fields']):
        field = deepcopy(address_revision['edited'].get(f'pages/{page_index}/fields/{index}', source))
        name, identifier, kind = field.get('name'), field.get('id'), field.get('type')
        key = name or identifier
        label = str(field.get('label') or '').strip().rstrip(' *:')
        if (name in COMBO_ORDER or field.get('observed_label_bound_selector') == 'Bank mandate') and kind == 'custom-combobox':
            options = field.get('selected_options') or []
            if len(options) != 1 or not field.get('observed_business_selection_code'):
                raise CopyStopped('A source dropdown selection is unresolved.')
            controls.append({'key': name or 'bank_mandate', 'kind': 'combo', 'value': _text(options[0]['value']),
                             'text': _text(options[0]['text'])})
        elif name in DATE_NAMES and kind == 'mui-segmented-date':
            value = _text(field['value'])
            if (name in ('mandate_signed_date','mandate_expiry') and value == ''
                    and field.get('date_picker',{}).get('empty_observed') is True
                    and segmented_date_record_valid(field)):
                controls.append({'key':name,'kind':'date','value':''})
                continue  # The explicit relist-date policy supplies these later.
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError
                parts = {p['aria_label']: int(p['aria_valuenow']) for p in field['date_picker']['segments']}
                if parts != {'Year': parsed.year, 'Month': parsed.month, 'Day': parsed.day}:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise CopyStopped('A date differs from its visible date parts. Check it in Hub.') from None
            controls.append({'key': name, 'kind': 'date', 'value': value})
        elif name in CHECKS and kind == 'checkbox':
            if type(field.get('checked')) is not bool:
                raise CopyStopped('A checkbox state was not captured.')
            controls.append({'key': name, 'kind': 'checkbox', 'value': field['checked']})
        elif name in RADIOS and kind == 'radio':
            if type(field.get('checked')) is not bool or not re.fullmatch(r'[0-9]{1,6}', str(field.get('value', ''))):
                raise CopyStopped('A listing status was not captured.')
            controls.append({'key': name, 'kind': 'radio', 'code': _text(field['value']), 'value': field['checked']})
        elif key in TEXT_NAMES or identifier in COUNTS:
            controls.append({'key': key, 'kind': 'count' if identifier in COUNTS else 'text',
                             'value': _text(field['value'])})
        elif label.casefold() == 'suburb':
            controls.append({'key': 'suburb', 'kind': 'suburb', 'value': _text(field['value'])})
        elif name in ('custom_ft_name', 'custom_ft_value') and not field.get('value'):
            pass  # Empty add-feature inputs are not saved custom features.
        else:
            ignored.append(key or label or 'unidentified control')
    if ignored:
        raise CopyStopped('Some source controls have no copy mapping. Review the collected listing before continuing.')
    keys = [(c['key'], c.get('code')) for c in controls]
    if any(n != 1 for n in Counter(keys).values()):
        raise CopyStopped('The source contains duplicate control mappings.')
    listing_types = [c['value'] for c in controls if c['key'] == 'listing_type_id']
    if listing_types not in (['1'], ['2']):
        raise CopyStopped('The sale or rental listing type is unresolved.')
    mode = 'sale' if listing_types == ['1'] else 'rent'
    property_types = [c for c in controls if c['key'] == 'property_type_id']
    if any(c.get('text') == 'Townhouse' for c in property_types) and not any(
            c['key'] == 'townhouse_descriptive_property_type' for c in controls):
        raise CopyStopped('Collect this townhouse again to include its descriptive property type. The original will stay active.')
    required = {'property_type_id', 'office_id', 'country_id', 'suburb', 'street_number',
                'street_name', 'date_listed', 'expiry_date',
                'selling_price' if mode == 'sale' else 'rental_price'}
    required.update((*LIFESTYLE_FIELDS, *COUNTS, 'poa', 'featured', 'address_hidden',
                     'floor_area','land_area','floor_area_units','land_area_units','rates','levy','special_levies','ensuites'))
    required.update(('mandate_signed_date', 'mandate_expiry', 'on_market_since', 'mandate_type_id')
                    if mode == 'sale' else ('occupation_date', 'on_market_since', 'rental_price_period'))
    if not required.issubset(c['key'] for c in controls):
        raise CopyStopped('Required source fields were not captured.')
    status_name = 'sale_state_id' if mode=='sale' else 'rental_state_id'
    states = [c for c in controls if c['key']==status_name]
    if not states or sum(c['value'] for c in states)!=1:
        raise CopyStopped('Exactly one source listing status must be captured.')
    if details.get('general_features_expanded') is not True or not set(GENERAL_FIELDS).issubset(c['key'] for c in controls):
        raise CopyStopped('Refresh the collection with General Property Features expanded before copying.')
    agents = [t['observed_agents'] for t in details.get('tables', []) if 'observed_agents' in t]
    if len(agents) != 1 or not agents[0] or sum(a['primary'] for a in agents[0]) != 1:
        raise CopyStopped('Refresh the collection to capture the primary agent before preparing a copy.')
    rooms = []
    from .room_capture import ROOM_OPTIONS
    allowed_openers = {r.opener for r in ROOM_OPTIONS}
    for dialog in details.get('dialogs', []):
        source = dialog.get('source', {})
        if source.get('opener') not in allowed_openers or source.get('close_verified') is not True:
            raise CopyStopped('A room dialog is missing its verified source association.')
        if dialog.get('rich_text'):
            raise CopyStopped('A room has formatted content that needs a copy handler.')
        fields = dialog.get('fields', [])
        for field in fields:
            if field.get('type') == 'checkbox':
                if type(field.get('checked')) is not bool or not field.get('name'):
                    raise CopyStopped('A room checkbox was not captured.')
            elif field.get('type') == 'textarea' and field.get('name') == 'description':
                _text(field['value'])
            else:
                raise CopyStopped('An unsupported room control needs review.')
        rooms.append({'opener': source['opener'], 'fields': deepcopy(fields),
                      'groups': [g['title'] for g in dialog.get('room_groups', [])]})
    return {'mode': mode, 'controls': controls, 'agents': deepcopy(agents[0]), 'rooms': rooms}


class ParticularsFiller:
    """Ordinary UI input with a pinned tab and strict post-input comparisons."""
    def __init__(self, driver, *, cancelled=lambda: False, progress=lambda message: None):
        self.driver, self.cancelled, self.progress = driver, cancelled, progress
        self.handle = driver.current_window_handle
        self.url = driver.current_url
        if self.url not in ('https://hub.remax.co.za/app/listings/add/sale',
                            'https://hub.remax.co.za/app/listings/add/rent'):
            raise CopyStopped('Open a new, unsaved sale or rental form first.')

    def guard(self):
        if self.cancelled():
            raise CopyStopped('Preparation stopped. The form has not been saved.')
        if self.driver.current_window_handle != self.handle or self.driver.current_url != self.url:
            raise CopyStopped('The Hub tab changed. Preparation stopped before further input.')

    def one(self, selector, root=None):
        self.guard()
        matches = (root or self.driver).find_elements('css selector', selector)
        if len(matches) != 1:
            raise CopyStopped('A required form control is missing or ambiguous.')
        return matches[0]

    def wait(self, read, message, timeout=15):
        from selenium.common.exceptions import StaleElementReferenceException
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            try:
                result = read()
            except StaleElementReferenceException:
                # React replaces menu nodes as it opens a dropdown. Retry
                # only the read; never replay an input whose outcome is unknown.
                result = None
            if result:
                return result
            time.sleep(.1)
        raise CopyStopped(message)

    def enter(self, element, value):
        from selenium.webdriver.common.keys import Keys
        self.guard()
        if not element.is_enabled():
            raise CopyStopped('Hub has disabled a required input.')
        element.send_keys(Keys.CONTROL, 'a', Keys.NULL, value, Keys.TAB)
        self.guard()

    def combo(self, key, code, text):
        from selenium.webdriver.common.keys import Keys
        def pair():
            if key != 'bank_mandate':
                return self.one(f'#mui-component-select-{key}'), self.one(f'input[name="{key}"]')
            wrappers = [e for e in self.driver.find_elements('css selector', 'main div.MuiFormControl-root')
                        if any(l.get_attribute('textContent').strip() == 'Bank mandate'
                               for l in e.find_elements('xpath', './label'))]
            if len(wrappers) != 1:
                raise CopyStopped('The Bank mandate control is missing or ambiguous.')
            return self.one('div[role="combobox"]', wrappers[0]), self.one('input.MuiSelect-nativeInput', wrappers[0])
        combo, native = pair()
        if native.get_property('value') == code:
            return
        if combo.get_attribute('aria-disabled') == 'true':
            raise CopyStopped('A fixed Hub dropdown does not match the source.')
        combo.send_keys(Keys.SPACE)
        def options():
            return [e for e in self.driver.find_elements('css selector', '[role="option"]')
                    if e.is_displayed() and e.get_attribute('textContent').strip() == text
                    and e.get_attribute('data-value') in (None, code)]
        found = self.wait(options, 'The original dropdown choice is not available.')
        if len(found) != 1:
            raise CopyStopped('The dropdown choice is ambiguous.')
        self.guard()
        found[0].send_keys(Keys.ENTER)
        self.wait(lambda: pair()[1].get_property('value') == code,
                  'Hub did not retain the copied dropdown choice.')

    def fill_control(self, control):
        key, kind, value = control['key'], control['kind'], control['value']
        self.progress('Copying ' + key.replace('_', ' ').replace('-', ' '))
        if kind == 'combo':
            return self.combo(key, value, control['text'])
        if kind == 'suburb':
            from selenium.webdriver.common.keys import Keys
            inputs = self.driver.find_elements('css selector', 'input[role="combobox"]')
            matches = [e for e in inputs if e.is_displayed() and self.driver.execute_script(
                'return Array.from(arguments[0].labels||[]).some(e=>e.textContent.replace(/\\s*\\*\\s*$/, "").trim()==="Suburb")', e)]
            if len(matches) != 1:
                raise CopyStopped('The suburb search control is ambiguous.')
            # Hub discards unselected search text on blur. Keep focus until
            # the exact reviewed result has been selected.
            self.guard()
            matches[0].send_keys(Keys.CONTROL, 'a', Keys.NULL, value.split(',')[0].strip())
            choices = self.wait(lambda: [e for e in self.driver.find_elements('css selector', '[role="option"]')
                              if e.is_displayed() and suburb_matches(value, e.get_attribute('textContent'))],
                                'Select the reviewed suburb in Hub; no exact match was available.')
            if len(choices) != 1:
                raise CopyStopped('The reviewed suburb has multiple matches. Enter its full suburb, city and province in Verify address.')
            choices[0].click()
            return
        selector = f'#{key}' if kind == 'count' else f'input[name="{key}"]'
        if kind == 'radio':
            selector += f'[value="{control["code"]}"]'
        element = self.one(selector)
        if kind in ('checkbox', 'radio'):
            if element.is_selected() != value:
                if kind == 'radio' and not value:
                    return  # The selected member is set after other controls.
                from selenium.webdriver.common.keys import Keys
                element.send_keys(Keys.SPACE)
            if element.is_selected() != value:
                raise CopyStopped('Hub did not retain a copied checkbox or status.')
        elif kind == 'date':
            from selenium.webdriver.common.keys import Keys
            parsed = date.fromisoformat(value)
            wrapper = element.find_element('xpath', 'ancestor::div[contains(@class,"MuiFormControl-root")][1]')
            for label, part in (('Year', parsed.year), ('Month', parsed.month), ('Day', parsed.day)):
                segment = self.one(f'[role="spinbutton"][aria-label="{label}"]', wrapper)
                self.driver.execute_script('arguments[0].scrollIntoView({block:"center"})', segment)
                self.guard()
                # Ctrl+A selects the whole MUI date, not this section. Clicking
                # the section selects just its digits for ordinary replacement.
                segment.click()
                segment.send_keys(str(part).zfill(4 if label == 'Year' else 2))
                if segment.get_attribute('aria-valuenow') != str(part):
                    raise CopyStopped('Hub did not retain the original ' + label.lower() + ' in ' + key.replace('_', ' ') + '.')
            segment.send_keys(Keys.TAB)
            if self.one(selector).get_property('value') != value:
                raise CopyStopped('Hub did not retain the planned listing date.')
        else:
            if element.get_property('value') != value:
                self.enter(element, value)
            if self.one(selector).get_property('value') != value:
                raise CopyStopped('Hub changed a copied field value. Review the unsaved form.')

    def agent_rows(self):
        self.guard()
        tables = [t for t in self.driver.find_elements('css selector', 'main table')
                  if [e.get_attribute('textContent').strip() for e in t.find_elements('css selector', 'thead th')]
                  == ['Is primary agent?', 'Name', 'Action']]
        if len(tables) != 1:
            raise CopyStopped('The listing-agent table could not be matched.')
        rows = []
        for row in tables[0].find_elements('css selector', 'tbody tr'):
            cells = row.find_elements('css selector', 'td')
            if len(cells) != 3:
                raise CopyStopped('The listing-agent row structure changed.')
            rows.append((cells[1].get_attribute('textContent').strip(), row,
                         self.one('input[type="checkbox"]', cells[0])))
        if len({r[0] for r in rows}) != len(rows):
            raise CopyStopped('Two listing agents have the same displayed name. Match them manually.')
        return rows

    def fill_agents(self, wanted):
        from selenium.webdriver.common.keys import Keys
        names = [a['name'] for a in wanted]
        if len(set(names)) != len(names):
            raise CopyStopped('The source agent names are ambiguous.')
        for name in names:
            if name not in [r[0] for r in self.agent_rows()]:
                self.one('#mui-component-select-listing').send_keys(Keys.SPACE)
                choices = self.wait(lambda: [e for e in self.driver.find_elements('css selector', '[role="option"]')
                                            if e.is_displayed() and e.get_attribute('textContent').strip() == name],
                                    'The original agent is not available in this office.')
                if len(choices) != 1:
                    raise CopyStopped('The agent selector has more than one name match.')
                choices[0].send_keys(Keys.ENTER)
                self.wait(lambda: name in [r[0] for r in self.agent_rows()], 'The agent was not added to the unsaved form.')
        # Add desired agents before removing defaults so the table never has
        # to pass through an empty, potentially different row structure.
        for name, row, _ in self.agent_rows():
            if name not in names:
                buttons = row.find_elements('css selector', 'button')
                if len(buttons) != 1:
                    raise CopyStopped('The default-agent removal control is ambiguous.')
                buttons[0].send_keys(Keys.ENTER)
                self.wait(lambda: name not in [r[0] for r in self.agent_rows()], 'The default agent was not removed from the unsaved form.')
        primary = next(a['name'] for a in wanted if a['primary'])
        name, row, checkbox = next(r for r in self.agent_rows() if r[0] == primary)
        if not checkbox.is_selected():
            checkbox.send_keys(Keys.SPACE)
        actual = sorted((n, c.is_selected()) for n, _, c in self.agent_rows())
        if actual != sorted((a['name'], a['primary']) for a in wanted):
            raise CopyStopped('The primary and secondary agents do not match the source.')

    def fill_room(self, room):
        from selenium.webdriver.common.keys import Keys
        self.guard()
        openers = [e for e in self.driver.find_elements('css selector', 'main span.MuiTypography-caption')
                   if e.is_displayed() and e.get_attribute('textContent') == room['opener']]
        if len(openers) != 1:
            raise CopyStopped('The copied room options cannot be opened unambiguously.')
        self.driver.execute_script('arguments[0].scrollIntoView({block:"center",inline:"center"})', openers[0])
        self.guard()
        openers[0].click()
        def visible_dialogs():
            return [e for e in self.driver.find_elements('css selector', '[role="dialog"]') if e.is_displayed()]
        dialogs = self.wait(visible_dialogs, 'The room options did not open.')
        if len(dialogs) != 1 or self.one('h2', dialogs[0]).get_attribute('textContent') != room['opener']:
            raise CopyStopped('The room dialog does not match the copied room.')
        dialog = dialogs[0]
        fields = dialog.find_elements('css selector', 'input[type="checkbox"],textarea[name="description"]')
        if len(fields) != len(room['fields']):
            raise CopyStopped('The number of room controls differs from the original.')
        # Validate the entire ordering before changing any of this room's fields.
        for target, source in zip(fields, room['fields']):
            if target.get_attribute('name') != source['name'] or target.get_attribute('type') != source['type']:
                raise CopyStopped('The room control order differs from the source.')
            if source['type'] == 'checkbox':
                label = target.find_element('xpath', 'ancestor::label[1]').get_attribute('textContent').strip()
                if label != source['label']:
                    raise CopyStopped('A room checkbox label differs from the source.')
        headings = [e.get_attribute('textContent').strip() for e in dialog.find_elements('css selector', 'h6')]
        if [h for h in headings if re.fullmatch(r'(Bedroom|Bathroom|Kitchen|Parking|Lounge|Dining room|Garage|Braai|Study|Entrance hall) [0-9]+', h)] != room['groups']:
            raise CopyStopped('The room groups differ from the original.')
        for target, source in zip(fields, room['fields']):
            self.guard()
            if source['type'] == 'checkbox':
                if target.is_selected() != source['checked']:
                    target.send_keys(Keys.SPACE)
                if target.is_selected() != source['checked']:
                    raise CopyStopped('A room option was not retained.')
            else:
                self.enter(target, source['value'])
                if target.get_property('value') != source['value']:
                    raise CopyStopped('A room description was not retained.')
        done = [e for e in dialog.find_elements('css selector', 'button')
                if e.get_attribute('textContent').strip() == 'Done']
        if len(done) != 1:
            raise CopyStopped('The room Done button is ambiguous.')
        done[0].send_keys(Keys.ENTER)
        self.wait(lambda: not visible_dialogs(), 'The room dialog did not close.')

    def fill(self, plan):
        if not self.url.endswith('/' + plan['mode']):
            raise CopyStopped('The new form has the wrong listing type.')
        from .general_features import expand_general_features
        expand_general_features(self.driver, guard=self.guard)
        controls = plan['controls']
        combos = sorted((c for c in controls if c['kind'] == 'combo'), key=lambda c: COMBO_ORDER.index(c['key']))
        remaining = [c for c in controls if c['kind'] not in ('combo', 'radio', 'suburb')]
        selected_radios = [c for c in controls if c['kind'] == 'radio' and c['value']]
        for control in combos + [c for c in controls if c['kind'] == 'suburb'] + remaining + selected_radios:
            self.guard()
            self.fill_control(control)
        self.fill_agents(plan['agents'])
        for room in plan.get('rooms', []):
            self.fill_room(room)
        # Later controls may change earlier values. Re-read the whole form
        # after suburb selection and room editing, before any source removal.
        self.verify_controls(controls)
        return {'status': 'particulars_filled_not_saved', 'save_clicked': False,
                'remaining': ['related records']}

    def verify_controls(self, controls):
        for control in controls:
            self.guard()
            key, kind = control['key'], control['kind']
            if kind == 'suburb':
                matches = [e for e in self.driver.find_elements('css selector','input[role="combobox"]')
                           if e.is_displayed() and self.driver.execute_script(
                           'return Array.from(arguments[0].labels||[]).some(e=>e.textContent.replace(/\\s*\\*\\s*$/, "").trim()==="Suburb")', e)]
                if len(matches)!=1 or not suburb_matches(control['value'], matches[0].get_property('value')):
                    raise CopyStopped('The final suburb differs from the reviewed address.')
                continue
            if key == 'bank_mandate':
                groups = [e for e in self.driver.find_elements('css selector','main div.MuiFormControl-root')
                          if any(l.get_attribute('textContent').strip()=='Bank mandate' for l in e.find_elements('xpath','./label'))]
                if len(groups)!=1:
                    raise CopyStopped('The final Bank mandate control is ambiguous.')
                element = self.one('input.MuiSelect-nativeInput', groups[0])
            else:
                selector = f'#{key}' if kind=='count' else f'input[name="{key}"]'
                if kind=='radio':
                    selector += f'[value="{control["code"]}"]'
                element = self.one(selector)
            value = element.is_selected() if kind in ('checkbox','radio') else element.get_property('value')
            if value != control['value']:
                raise CopyStopped('A copied field changed while the form was being prepared: ' + key.replace('_',' ') + '.')
