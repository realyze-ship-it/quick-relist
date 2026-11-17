"""Read the selected locality from the saved Hub editor."""
from .form_copy import CopyStopped


def read_selected_suburb(driver, short, guard, wait, *, optional=False):
    from selenium.webdriver.common.keys import Keys
    inputs = [e for e in driver.find_elements('css selector', 'main input[role="combobox"]')
              if e.is_displayed() and driver.execute_script(
                  'return Array.from(arguments[0].labels||[]).some(e=>e.textContent.replace(/\\s*\\*\\s*$/, "").trim()==="Suburb")', e)]
    if optional and len(inputs) != 1:
        return None
    if len(inputs) != 1 or inputs[0].get_property('value') != short or not short:
        raise CopyStopped('The saved suburb control is missing or changed.')
    field = inputs[0]
    guard()
    # Typing the existing name triggers Hub's search while retaining its
    # selected value. aria-selected identifies that value, not keyboard focus.
    try:
        field.send_keys(Keys.CONTROL, 'a', Keys.NULL, short)
        def selected():
            guard()
            owner = field.get_attribute('aria-controls')
            menus = [e for e in driver.find_elements('css selector', '[role="listbox"]')
                     if e.is_displayed() and owner and e.get_attribute('id') == owner]
            if len(menus) != 1:
                return None
            choices = [e for e in menus[0].find_elements('css selector', '[role="option"]')
                       if e.is_displayed() and e.get_attribute('aria-selected') == 'true']
            if len(choices) > 1:
                if optional:
                    return None
                raise CopyStopped('The saved suburb has ambiguous selected options.')
            return choices[0].get_attribute('textContent').strip() if choices else None
        observed = wait(selected, 'Hub did not identify the saved suburb selection.')
    finally:
        guard()
        field.send_keys(Keys.ESCAPE, Keys.TAB)
    if field.get_property('value') != short:
        raise CopyStopped('The suburb search did not return to its saved value.')
    guard()
    return observed
