"""Synthetic search views: archived sources and active replacements."""
from types import SimpleNamespace

import pytest
from quick_relist.hub_readonly import ReadOnlyHub, is_manage_url

ROOT = 'https://hub.remax.co.za'


class Tab:
    def __init__(self, driver, label):
        self.driver, self.label, self.clicks = driver, label, 0
    def is_displayed(self): return True
    def is_enabled(self): return True
    def get_attribute(self, name):
        return {'textContent':self.label, 'aria-selected':str(self.driver.selected == self.label).lower()}[name]
    def click(self):
        self.clicks += 1
        self.driver.current_url = self.driver.redirect or ROOT + '/app/listings/manage/' + self.label.lower()
        if self.driver.selects: self.driver.selected = self.label
        if self.driver.extra_tab: self.driver.window_handles.append('unexpected')


class Driver:
    def __init__(self, path='/app/listings'):
        self.current_url = ROOT + path
        self.current_window_handle = 'results'
        self.window_handles = ['results']
        self.selected = 'Active'
        self.redirect = None
        self.selects = True
        self.extra_tab = False
        self.tabs = [Tab(self, 'Active'), Tab(self, 'Archived')]
    def find_elements(self, by, query):
        assert query == 'main [role="tablist"] [role="tab"]'
        return self.tabs


def test_source_archive_search_returns_to_active_before_replacement_search():
    driver = Driver()
    hub = ReadOnlyHub(driver)
    hub._select_listing_view(archived=True, timeout=.05, poll_interval=.001)
    assert driver.current_url == ROOT + '/app/listings/manage/archived'
    assert driver.tabs[1].clicks == 1
    hub._select_listing_view(archived=False, timeout=.05, poll_interval=.001)
    assert driver.current_url == ROOT + '/app/listings/manage/active'
    assert driver.tabs[0].clicks == 1
    hub._select_listing_view(archived=False)
    assert driver.tabs[0].clicks == 1


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'not_selected', 'wrong_view', 'wrong_origin', 'tab_changed'])
def test_archive_view_must_settle_in_the_exact_search_context(problem):
    driver = Driver()
    if problem == 'missing': driver.tabs = driver.tabs[:1]
    if problem == 'duplicate': driver.tabs.append(Tab(driver, 'Archived'))
    if problem == 'not_selected': driver.selects = False
    if problem == 'wrong_view': driver.redirect = ROOT + '/app/listings/manage/all'
    if problem == 'wrong_origin': driver.redirect = 'https://example.test/app/listings/manage/archived'
    if problem == 'tab_changed': driver.extra_tab = True
    with pytest.raises(ValueError):
        ReadOnlyHub(driver)._select_listing_view(archived=True, timeout=.02, poll_interval=.001)


@pytest.mark.parametrize('path', ['/app/listings', '/app/listings/manage/active', '/app/listings/manage/archived', '/app/listings/manage/all'])
def test_only_observed_manage_routes_are_recognized(path):
    assert is_manage_url(ROOT + path)
    assert not is_manage_url(ROOT + path + '?unexpected=1')
    assert not is_manage_url(ROOT + path + '#unexpected')
    assert not is_manage_url('http://hub.remax.co.za' + path)
    assert not is_manage_url('https://user@hub.remax.co.za' + path)
    assert not is_manage_url('https://hub.remax.co.za.example.test' + path)


def test_archive_status_check_requests_archived_search(monkeypatch, tmp_path):
    from quick_relist import live_relist_ui as module
    observed = []
    hub = SimpleNamespace(driver=SimpleNamespace(refresh=lambda:None), search_reference=lambda ref, **kw: observed.append((ref,kw)))
    ui = module.HubRelistUI(hub,tmp_path,clipboard=lambda *a:None)
    ui.wait = lambda *args: True
    class SearchObserved(Exception): pass
    def stop_after_search(*args): raise SearchObserved()
    monkeypatch.setattr(module, '_wait_search', stop_after_search)
    with pytest.raises(SearchObserved):
        ui._check_source_archive({'source_reference':'RXCU1000','source_listing_id':'123'},archived=True)
    assert observed == [('RXCU1000',{'all_listing_types':True,'archived':True})] * 2
