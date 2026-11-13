"""Opt-in native fractional state/position script checks on synthetic localhost.

No full-controller, Hub, Windows or live qualification. The unchanged origin
parser must reject this fixture. Uses only the explicitly supplied existing
ChromeDriver/Chrome and disposable profile, never installs/downloads either.
"""
from dataclasses import asdict
from html import escape
import unittest

from quick_relist.capture import CaptureSafetyError, parse_editor_url
from quick_relist.room_capture import ROOM_OPTIONS, ROOM_POSITION_SCRIPT, ROOM_STATE_SCRIPT
from room_positioning_fixture import positioning_html
import test_editor_recheck_chrome as native_fixture
import test_room_positioning_chrome as position_fixture

BATHROOM = ROOM_OPTIONS[1]
RAW = '08.3750'  # Synthetic raw text, not real listing data or a group count.
OBSERVATION = {'format': 'positive_nonintegral_decimal', 'raw': RAW}


def bathroom_html(*, placement='clean', raw=RAW):
    return positioning_html(placement=placement).replace('bedroom', 'bathroom').replace(
        'Bedroom', 'Bathroom').replace('value="2"', 'value="' + escape(raw, quote=True) + '"')


class FractionalBathroomChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The reused fixture skips before any server/profile/browser setup
        # unless the explicit opt-in and existing driver are both present.
        native_fixture.EditorIdentityChromeTests.setUpClass.__func__(cls)

    install_harness = position_fixture.RoomPositioningChromeTests.install_harness
    actions = position_fixture.RoomPositioningChromeTests.actions

    def state(self):
        return self.driver.execute_script(ROOM_STATE_SCRIPT, asdict(BATHROOM))

    def load_fixture(self, url):
        self.driver.get(url)
        with self.assertRaises(CaptureSafetyError):
            parse_editor_url(url)
        self.install_harness()
        feature = self.state()['feature']
        self.assertTrue(feature['shape_ok'])
        self.assertIsNone(feature['count'])
        self.assertEqual(feature['count_observation'], OBSERVATION)
        # No derived integer or expected dialog cardinality is serialized.
        self.assertEqual(set(feature), {'opener_count', 'count_count', 'shape_ok',
                                      'count', 'element', 'count_observation'})
        return feature['element']

    def probe(self, target, *, scroll=False, expected=None):
        signature = "return [document.documentElement.outerHTML,location.href,document.querySelector('input').value]"
        before = self.driver.execute_script(signature)
        status = self.driver.execute_script(ROOM_POSITION_SCRIPT, asdict(BATHROOM), target,
                                            OBSERVATION if expected is None else expected, scroll)
        self.assertEqual(self.driver.execute_script(signature), before)
        return status

    def recheck(self, target):
        state = self.state()
        self.assertTrue(state['ready'])
        self.assertFalse(state['busy'])
        self.assertEqual(state['overlays'], [])
        self.assertEqual(state['feature']['element'], target)
        self.assertIsNone(state['feature']['count'])
        self.assertEqual(state['feature']['count_observation'], OBSERVATION)
        return target

    def test_exact_raw_serialization_and_clear_one_ordinary_click(self):
        with native_fixture.serve_synthetic_html(bathroom_html()) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'clear')
            self.assertEqual(self.probe(self.recheck(target)), 'clear')
            target.click()  # Native WebDriver dispatch only, never JS click.
            self.assertEqual(self.actions()['clicks'], 1)
            self.assertEqual(self.actions()['scrolls'], 0)
            self.assertEqual(self.driver.execute_script("return document.querySelector('input').value"), RAW)

    def test_typed_observation_survives_one_centered_scroll_and_recheck(self):
        with native_fixture.serve_synthetic_html(bathroom_html(placement='below')) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'needs_scroll')
            self.assertEqual(self.probe(self.recheck(target), scroll=True), 'scrolled')
            self.assertEqual(self.probe(self.recheck(target)), 'clear')
            target.click()
            self.assertEqual(self.actions()['clicks'], 1)
            self.assertEqual(self.actions()['scrolls'], 1)
            self.assertEqual(self.actions()['options'], {'behavior': 'instant', 'block': 'center', 'inline': 'center'})
            self.assertEqual(self.driver.execute_script("return document.querySelector('input').value"), RAW)

    def test_changed_or_unsupported_raw_stops_before_click_or_scroll(self):
        with native_fixture.serve_synthetic_html(bathroom_html(placement='below')) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'needs_scroll')
            for raw in ['8.3750', '08.375', '08.6250', '8.000', '8e-1', '8,375', ' 8.375']:
                with self.subTest(raw=raw):
                    # Fixture-only fault injection, never a production action.
                    self.driver.execute_script("document.querySelector('input').value=arguments[0]", raw)
                    feature = self.state()['feature']
                    self.assertIsNone(feature['count'])
                    if raw in ('8.000', '8e-1', '8,375', ' 8.375'):
                        self.assertNotIn('count_observation', feature)
                    self.assertEqual(self.probe(target, scroll=True), 'changed')
                    self.assertEqual(self.actions()['clicks'], 0)
                    self.assertEqual(self.actions()['scrolls'], 0)

    def test_post_scroll_raw_or_expected_format_change_never_clicks(self):
        with native_fixture.serve_synthetic_html(bathroom_html(placement='below')) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target, scroll=True), 'scrolled')
            self.assertEqual(self.probe(target, expected={'format': 'integer', 'raw': RAW}), 'changed')
            self.driver.execute_script("document.querySelector('input').value='8.3750'")
            self.assertEqual(self.probe(target), 'changed')
            self.assertEqual(self.actions()['clicks'], 0)
            self.assertEqual(self.actions()['scrolls'], 1)


if __name__ == '__main__':
    unittest.main()
