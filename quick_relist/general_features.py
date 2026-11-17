"""Expand Hub's read-only General Property Features disclosure before copying."""
import time
from .business_fields import GENERAL_FIELDS
from .capture import CaptureReadError
from .visibility import with_displayed


def expand_general_features(driver, *, guard, timeout=15):
    def visible_names():
        guard()
        states = driver.execute_script(with_displayed("""
            const wanted = new Set(arguments[0]);
            return Array.from(document.querySelectorAll('main input[type="checkbox"][name]'))
                .filter(e => wanted.has(e.name)).map(e => {
                    const label = e.closest('label');
                    return [e.name, label ? (displayed(label) ? label.textContent.trim() : null) : false];
                });
        """), list(GENERAL_FIELDS))
        guard()
        seen, names = set(), set()
        for name, label in states:
            if name in seen:
                raise CaptureReadError('A general property feature appears more than once.')
            seen.add(name)
            if label is not None:
                if GENERAL_FIELDS.get(name) != label:
                    raise CaptureReadError('A general property feature label has changed.')
                names.add(name)
        return names
    current = visible_names()
    if current == set(GENERAL_FIELDS):
        return False
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
            return True
        time.sleep(.1)
    raise CaptureReadError('General Property Features did not finish expanding.')
