"""Synthetic sign-in transitions; no browser, credentials or Hub connection."""
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import InvalidSessionIdException, StaleElementReferenceException

from quick_relist import hub_login


class Element:
    def __init__(self, text='', placeholder=None):
        self.text, self.placeholder = text, placeholder

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, key):
        assert key in ('placeholder', 'aria-label'), 'Never read authentication input values'
        return self.placeholder if key == 'placeholder' else None


class Driver:
    def __init__(self, url='https://hub.remax.co.za/login'):
        self.tabs = {'first': url}
        self.current_window_handle = 'first'
        self.ready = True
        self.avatar = True
        self.duplicate = False
        self.stale = False
        self.closed = False
        self.navigation = []
        self.switch_to = SimpleNamespace(window=self.select, new_window=self.new_window)

    @property
    def current_url(self):
        if self.closed:
            raise InvalidSessionIdException('private browser details')
        return self.tabs[self.current_window_handle]

    @property
    def window_handles(self):
        return list(self.tabs)

    def select(self, handle):
        self.current_window_handle = handle

    def new_window(self, kind):
        assert kind == 'tab'
        self.tabs['new-results'] = 'about:blank'
        self.select('new-results')

    def get(self, url):
        assert url == hub_login.MANAGE_URL
        self.navigation.append(url)
        self.tabs[self.current_window_handle] = url

    def find_elements(self, by, selector):
        if self.stale:
            self.stale = False
            raise StaleElementReferenceException()
        if selector == 'header button img[src*="/agents/"]':
            return [Element()] if self.avatar else []
        if selector == '//input[@placeholder or @aria-label or @id]':
            return [Element(placeholder='Enter fusion ref, listing ID or address')] * (2 if self.duplicate else 1) if self.ready else []
        if selector == '//button':
            return [Element('Get listings')] if self.ready else []
        raise AssertionError('Unexpected browser access: ' + selector)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0, steps=[])
    def sleep(seconds):
        state.now += seconds
        if state.steps:
            state.steps.pop(0)()
    monkeypatch.setattr(hub_login.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(hub_login.time, 'sleep', sleep)
    return state


def test_login_otp_then_dashboard_continues_without_touching_credentials(clock):
    driver = Driver()
    clock.steps = [lambda: driver.tabs.update(first='https://hub.remax.co.za/login/otp'),
                   lambda: driver.tabs.update(first='https://hub.remax.co.za/app/dashboard')]
    progress = []
    hub_login.wait_for_hub_login(driver, timeout=5, progress=progress.append)
    assert driver.navigation == [hub_login.MANAGE_URL]
    assert driver.tabs['first'].endswith('/app/dashboard')
    assert driver.current_url == hub_login.MANAGE_URL
    assert 'automatically' in progress[-1]


@pytest.mark.parametrize('path', ['/app/listings', '/app/listings/manage/active', '/app/listings/manage/archived', '/app/listings/manage/all'])
def test_existing_login_reuses_results_tab_and_preserves_editor(clock, path):
    editor = 'https://hub.remax.co.za/app/listings/edit/123/media'
    driver = Driver(editor)
    driver.tabs['results'] = 'https://hub.remax.co.za' + path
    hub_login.wait_for_hub_login(driver, timeout=5)
    assert driver.navigation == []
    assert driver.tabs['first'] == editor
    assert driver.current_window_handle == 'results'


def test_authentication_and_loaded_search_controls_are_both_required(clock):
    driver = Driver(hub_login.MANAGE_URL)
    driver.avatar = driver.ready = False
    clock.steps = [lambda: setattr(driver, 'avatar', True), lambda: setattr(driver, 'ready', True)]
    hub_login.wait_for_hub_login(driver, timeout=5)
    assert clock.now >= .8


def test_stale_login_render_is_waited_out(clock):
    driver = Driver(hub_login.MANAGE_URL)
    driver.stale = True
    hub_login.wait_for_hub_login(driver, timeout=5)
    assert clock.now == .4


def test_stop_during_otp_does_not_navigate(clock):
    driver = Driver('https://hub.remax.co.za/login/otp')
    with pytest.raises(hub_login.LoginStopped, match='stopped'):
        hub_login.wait_for_hub_login(driver, cancelled=lambda: clock.now > 0, timeout=5)
    assert driver.navigation == []


def test_timeout_keeps_browser_and_saved_work(clock):
    driver = Driver()
    with pytest.raises(hub_login.LoginStopped, match='Resume collection'):
        hub_login.wait_for_hub_login(driver, timeout=.5)
    assert driver.navigation == [] and not driver.closed


def test_default_wait_accepts_late_manual_login_without_an_extra_click(clock):
    driver = Driver()
    def late_sign_in():
        clock.now += 3600
        driver.tabs['first'] = hub_login.MANAGE_URL
    clock.steps = [late_sign_in]
    hub_login.wait_for_hub_login(driver)
    assert clock.now > 3600 and driver.current_url == hub_login.MANAGE_URL
    assert driver.navigation == []


@pytest.mark.parametrize('url', ['https://example.com/app/listings', 'http://hub.remax.co.za/app/listings',
                                'https://hub.remax.co.za.evil.test/app/listings',
                                'https://user@hub.remax.co.za/app/listings',
                                'https://hub.remax.co.za:444/app/listings'])
def test_untrusted_page_never_continues(clock, url):
    driver = Driver(url)
    with pytest.raises(hub_login.LoginStopped, match='official Hub'):
        hub_login.wait_for_hub_login(driver, timeout=5)
    assert driver.navigation == []


def test_closed_browser_has_a_private_safe_error(clock):
    driver = Driver()
    driver.closed = True
    with pytest.raises(hub_login.LoginStopped, match='connection closed') as exc:
        hub_login.wait_for_hub_login(driver, timeout=5)
    assert 'private browser details' not in str(exc.value)


def test_ambiguous_results_tabs_are_not_chosen(clock):
    driver = Driver(hub_login.MANAGE_URL)
    driver.tabs['duplicate'] = hub_login.MANAGE_URL
    with pytest.raises(hub_login.LoginStopped, match='More than one'):
        hub_login.wait_for_hub_login(driver, timeout=5)
    assert driver.current_window_handle == 'first'
    assert driver.navigation == []


def test_ambiguous_search_controls_are_not_used(clock):
    driver = Driver(hub_login.MANAGE_URL)
    driver.duplicate = True
    with pytest.raises(hub_login.LoginStopped, match='ambiguous'):
        hub_login.wait_for_hub_login(driver, timeout=5)
