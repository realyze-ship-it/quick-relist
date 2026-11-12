"""Expand Hub's read-only General Property Features disclosure before copying."""
import time
from .business_fields import GENERAL_FIELDS
from .capture import CaptureReadError


def expand_general_features(driver, *, guard, timeout=15):
    def visible_names():
        guard()
        names = set()
        for name, label in GENERAL_FIELDS.items():
            fields = driver.find_elements('css selector', f'main input[type="checkbox"][name="{name}"]')
            if not fields:
                continue
            if len(fields) != 1:
                raise CaptureReadError('A general property feature appears more than once.')
            proxy = fields[0].find_element('xpath', 'ancestor::label[1]')
            if proxy.is_displayed():
                if proxy.get_attribute('textContent').strip() != label:
                    raise CaptureReadError('A general property feature label has changed.')
                names.add(name)
        return names
    current = visible_names()
    if current == set(GENERAL_FIELDS):
        return
    if current:
        raise CaptureReadError('Only part of General Property Features is visible. Review Hub before continuing.')
    headings = [e for e in driver.find_elements('css selector', 'main h6')
                if e.is_displayed() and e.get_attribute('textContent').strip() == 'General Property Features']
    if len(headings) != 1:
        raise CaptureReadError('General Property Features could not be located.')
    buttons = headings[0].find_elements('css selector', 'button[aria-label="add"]')
    if len(buttons) != 1:
        raise CaptureReadError('The feature expansion control is missing or ambiguous.')
    from selenium.webdriver.common.keys import Keys
    guard()
    buttons[0].send_keys(Keys.ENTER)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if visible_names() == set(GENERAL_FIELDS):
            return
        time.sleep(.1)
    raise CaptureReadError('General Property Features did not finish expanding.')
