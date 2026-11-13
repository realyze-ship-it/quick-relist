"""Synthetic delayed Hub menu; no browser or network access."""
import pytest

from quick_relist.hub_readonly import ReadOnlyHub


class Element:
    def __init__(self, text='', *, visible=True):
        self.text, self.visible = text, visible

    def is_displayed(self):
        return self.visible

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        assert name == 'textContent'
        return self.text


class FilterDriver:
    def __init__(self, *, delay=3, options=('All', 'For Sale', 'To Rent'), retained=True, duplicate_menu=False):
        self.delay, self.retained, self.duplicate_menu = delay, retained, duplicate_menu
        self.opened = self.clicked = False
        self.polls = self.after_click = self.keys = 0
        self.control = Element('For Sale')
        def send_keys(*keys):
            self.keys += 1
            self.opened = True
        self.control.send_keys = send_keys
        self.options = [Element(text) for text in options]
        for option in self.options:
            option.click = self.select
        self.menu = Element()
        self.menu.find_elements = lambda by, value: self.options
        self.group = Element()
        self.group.find_elements = lambda by, value: ([Element('Listing type')] if value == './label' else [self.control])

    def select(self):
        self.clicked = True

    def find_elements(self, by, value):
        if value == 'main .MuiFormControl-root':
            return [self.group]
        assert value == '[role="listbox"]'  # Never select an unbound global All option.
        if not self.opened:
            return []
        self.polls += 1
        if self.polls <= self.delay:
            return []
        if self.clicked:
            self.after_click += 1
            if self.after_click >= 3:
                if self.retained:
                    self.control.text = 'All'
                return []
        return [self.menu, self.menu] if self.duplicate_menu else [self.menu]


def test_waits_for_menu_then_for_selection_to_settle():
    driver = FilterDriver()
    ReadOnlyHub(driver)._select_all_listing_types(timeout=.2, poll_interval=.001)
    assert driver.keys == 1 and driver.clicked
    assert driver.polls > driver.delay and driver.after_click >= 3
    assert driver.control.text == 'All'


def test_already_selected_all_does_not_open_menu():
    driver = FilterDriver()
    driver.control.text = 'All'
    ReadOnlyHub(driver)._select_all_listing_types(timeout=.1, poll_interval=.001)
    assert driver.keys == 0 and not driver.clicked


@pytest.mark.parametrize('options', [('For Sale', 'To Rent'), ()])
def test_missing_all_waits_then_stops_without_click(options):
    driver = FilterDriver(options=options)
    with pytest.raises(ValueError, match='did not finish selecting'):
        ReadOnlyHub(driver)._select_all_listing_types(timeout=.02, poll_interval=.001)
    assert not driver.clicked and driver.polls > 3


def test_duplicate_all_options_stop_without_click():
    driver = FilterDriver(options=('All', 'All'))
    with pytest.raises(ValueError, match='ambiguous'):
        ReadOnlyHub(driver)._select_all_listing_types(timeout=.1, poll_interval=.001)
    assert not driver.clicked


def test_competing_menus_stop_without_click():
    driver = FilterDriver(duplicate_menu=True)
    with pytest.raises(ValueError, match='More than one'):
        ReadOnlyHub(driver)._select_all_listing_types(timeout=.1, poll_interval=.001)
    assert not driver.clicked


def test_click_is_not_enough_when_selection_is_not_retained():
    driver = FilterDriver(retained=False)
    with pytest.raises(ValueError, match='did not finish selecting'):
        ReadOnlyHub(driver)._select_all_listing_types(timeout=.02, poll_interval=.001)
    assert driver.clicked and driver.control.text == 'For Sale'
