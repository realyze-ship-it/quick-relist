"""Wait for manual Hub sign-in, then prepare the read-only listing search.

Only authenticated page markers and search controls are inspected. Passwords,
OTP inputs, cookies and tokens are never read or supplied by this module.
"""
from urllib.parse import urlsplit
import time
from .hub_readonly import is_manage_url


MANAGE_URL = 'https://hub.remax.co.za/app/listings'


class LoginStopped(RuntimeError):
    pass


def _hub_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'hub.remax.co.za'
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise LoginStopped('The browser is no longer on the official Hub site. Return to Hub, then resume collection.')
    return parsed


def _manage_tab(driver):
    """Reuse one results tab, preserving any already-open listing editors."""
    original = driver.current_window_handle
    matches = []
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if is_manage_url(driver.current_url):
            matches.append(handle)
    if len(matches) > 1:
        driver.switch_to.window(original)
        raise LoginStopped('More than one Manage Listings tab is open. Keep one, then resume collection.')
    if matches:
        driver.switch_to.window(matches[0])
    else:
        driver.switch_to.window(original)
        # Do not navigate away from a captured editor or discard its bindings.
        driver.switch_to.new_window('tab')
        from selenium.common.exceptions import TimeoutException
        try:
            driver.get(MANAGE_URL)
        except TimeoutException:
            pass  # Check the loaded page below; never repeat this navigation.


def wait_for_hub_login(driver, *, cancelled=lambda: False, progress=lambda message: None,
                       timeout=None, poll_interval=.4):
    """Continue once login/OTP and Hub's listing controls have finished loading."""
    from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
    from selenium.webdriver.common.by import By
    if driver is None or not .01 <= poll_interval <= 2 or (timeout is not None and not .05 <= timeout <= 1800):
        raise ValueError('Invalid Hub login wait')
    progress('Sign in and enter your OTP in the RE/MAX Chrome window. Collection will continue automatically.')
    # Manual login is not a failed browser action when the user steps away.
    # Keep waiting until sign-in or Stop; bounded waits remain available to callers.
    deadline = time.monotonic() + timeout if timeout is not None else None
    prepared = False
    while deadline is None or time.monotonic() < deadline:
        if cancelled():
            raise LoginStopped('Collection stopped while waiting for sign-in. Your saved batch is ready to resume.')
        try:
            url = driver.current_url
            parsed = _hub_url(url)
            signed_in = parsed.path.startswith('/app/') and any(
                el.is_displayed() for el in driver.find_elements(
                    By.CSS_SELECTOR, 'header button img[src*="/agents/"]'))
            if signed_in and driver.current_url == url:
                if not prepared:
                    if cancelled():
                        raise LoginStopped('Collection stopped. Your saved batch is ready to resume.')
                    _manage_tab(driver)
                    prepared = True
                    progress('Signed in. Waiting for Hub’s listing search…')
                    continue
                if is_manage_url(url):
                    prompts = {'Search fusion ref, listing ID or address', 'Enter fusion ref, listing ID or address'}
                    inputs = [el for el in driver.find_elements(By.XPATH, '//input[@placeholder or @aria-label or @id]')
                              if el.is_displayed() and el.is_enabled() and any(
                                  el.get_attribute(key) in prompts for key in ('placeholder', 'aria-label'))]
                    buttons = [el for el in driver.find_elements(By.XPATH, '//button')
                               if el.is_displayed() and el.is_enabled() and el.text.strip() == 'Get listings']
                    if len(inputs) > 1 or len(buttons) > 1:
                        raise LoginStopped('Hub’s listing search is ambiguous. Check the page before resuming.')
                    if len(inputs) == len(buttons) == 1 and driver.current_url == url:
                        if cancelled():
                            raise LoginStopped('Collection stopped. Your saved batch is ready to resume.')
                        progress('Signed in. Collecting your saved batch automatically…')
                        return
        except StaleElementReferenceException:
            pass  # Hub is still rendering after login or navigation.
        except WebDriverException:
            raise LoginStopped('The Hub browser connection closed. Reopen Quick Relist and resume your saved batch.') from None
        time.sleep(poll_interval)
    raise LoginStopped('Sign-in is still incomplete. Finish your login and OTP, then choose Resume collection. Your saved batch is intact.')
