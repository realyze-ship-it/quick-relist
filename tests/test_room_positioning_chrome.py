"""Opt-in native script/ordinary-click qualification on generated localhost HTML.

Not full-controller, real Hub, Windows or live qualification. Production origin
checks stay unchanged and reject this fixture. Uses only an explicitly provided
existing ChromeDriver/Chrome, no installation or automatic driver download.
"""
from dataclasses import asdict
import unittest

from quick_relist.capture import parse_editor_url
from quick_relist.room_capture import ROOM_OPTIONS, ROOM_POSITION_SCRIPT, ROOM_STATE_SCRIPT
import test_editor_recheck_chrome as native_fixture
from room_positioning_fixture import positioning_html


class RoomPositioningChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the existing explicit opt-in / driver-path / disposable-profile
        # setup, not controller origin exceptions or a second CI browser job.
        native_fixture.EditorIdentityChromeTests.setUpClass.__func__(cls)

    def install_harness(self):
        self.driver.execute_script(r"""
          const target=document.getElementById('opener');
          window.fixtureActions={clicks:0,scrolls:0,point:null,options:null};
          target.addEventListener('click', event => {
            fixtureActions.clicks++;
            fixtureActions.point=[event.clientX,event.clientY];
          });
          const nativeScroll=Element.prototype.scrollIntoView;
          Element.prototype.scrollIntoView=function(options) {
            if(this !== target || ++fixtureActions.scrolls > 1) throw new Error('Repeated or wrong scroll');
            fixtureActions.options=options;
            return nativeScroll.call(this,options);
          };
          const denied=()=>{throw new Error('Unexpected business mutation or private read')};
          HTMLElement.prototype.click=denied;
          HTMLFormElement.prototype.submit=denied;
          HTMLFormElement.prototype.requestSubmit=denied;
          for(const name of ['fetch','XMLHttpRequest','WebSocket','open']) window[name]=denied;
          for(const name of ['localStorage','sessionStorage']) Object.defineProperty(window,name,{get:denied});
          Object.defineProperty(document,'cookie',{get:denied,set:denied});
        """)

    def state(self):
        return self.driver.execute_script(ROOM_STATE_SCRIPT, asdict(ROOM_OPTIONS[0]))

    def probe(self, target, *, scroll=False):
        before = self.driver.execute_script("return [document.documentElement.outerHTML,location.href,document.querySelector('input').value]")
        result = self.driver.execute_script(ROOM_POSITION_SCRIPT, asdict(ROOM_OPTIONS[0]), target, 2, scroll)
        self.assertEqual(self.driver.execute_script("return [document.documentElement.outerHTML,location.href,document.querySelector('input').value]"), before)
        return result

    def actions(self):
        return self.driver.execute_script('return window.fixtureActions')

    def load_fixture(self, url, *, qualified=True):
        self.driver.get(url)
        with self.assertRaises(ValueError):
            parse_editor_url(url)
        self.install_harness()
        feature = self.state()['feature']
        self.assertIs(feature['shape_ok'], qualified)
        if qualified:
            self.assertEqual(feature['count'], 2)
            return feature['element']
        return self.driver.find_element('id', 'opener')

    def recheck(self, original):
        state = self.state()
        self.assertTrue(state['ready'])
        self.assertFalse(state['busy'])
        self.assertEqual(state['overlays'], [])
        feature = state['feature']
        self.assertTrue(feature['shape_ok'])
        self.assertEqual(feature['count'], 2)
        self.assertEqual(feature['element'], original)
        return feature['element']

    def ordinary_click(self, target):
        # Independent expected point computed from actual native first rect.
        point = self.driver.execute_script("""
          const r=arguments[0].getClientRects()[0];
          return [Math.floor((Math.max(0,r.x)+Math.min(innerWidth,r.x+r.width))/2),
                  Math.floor((Math.max(0,r.y)+Math.min(innerHeight,r.y+r.height))/2)];
        """, target)
        target.click()
        self.assertEqual(self.actions()['point'], point)
        self.assertEqual(self.actions()['clicks'], 1)
        self.assertEqual(self.driver.execute_script("return document.querySelector('input').value"), '2')

    def test_clean_point_uses_one_ordinary_click_without_scroll(self):
        with native_fixture.serve_synthetic_html(positioning_html()) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'clear')
            target = self.recheck(target)
            self.assertEqual(self.probe(target), 'clear')
            self.ordinary_click(target)
            self.assertEqual(self.actions()['scrolls'], 0)

    def test_transparent_fixed_help_footprint_moves_clear_with_one_scroll(self):
        with native_fixture.serve_synthetic_html(positioning_html(placement='bottom', overlay='help')) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'needs_scroll')
            self.assertEqual(self.probe(self.recheck(target), scroll=True), 'scrolled')
            target = self.recheck(target)
            self.assertEqual(self.probe(target), 'clear')
            self.ordinary_click(target)
            self.assertEqual(self.actions()['scrolls'], 1)
            self.assertEqual(self.actions()['options'], {'behavior':'instant','block':'center','inline':'center'})

    def test_below_view_target_is_scrolled_once_then_rechecked(self):
        with native_fixture.serve_synthetic_html(positioning_html(placement='below')) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'needs_scroll')
            self.assertEqual(self.probe(self.recheck(target), scroll=True), 'scrolled')
            self.assertEqual(self.probe(self.recheck(target)), 'clear')
            self.ordinary_click(target)
            self.assertEqual(self.actions()['scrolls'], 1)

    def test_persistent_iframe_or_fixed_target_never_receives_click(self):
        for placement, overlay in [('bottom','full'),('fixed','help')]:
            with self.subTest(placement=placement,overlay=overlay):
                with native_fixture.serve_synthetic_html(positioning_html(placement=placement,overlay=overlay)) as url:
                    target = self.load_fixture(url)
                    self.assertEqual(self.probe(target), 'needs_scroll')
                    self.assertEqual(self.probe(self.recheck(target), scroll=True), 'scrolled')
                    self.assertEqual(self.probe(self.recheck(target)), 'needs_scroll')
                    self.assertEqual(self.actions()['clicks'], 0)
                    self.assertEqual(self.actions()['scrolls'], 1)

    def test_native_tiny_and_zero_rectangles_never_scroll_or_click(self):
        for size in ['tiny','zero']:
            with self.subTest(size=size):
                with native_fixture.serve_synthetic_html(positioning_html(size=size)) as url:
                    target = self.load_fixture(url)
                    self.assertEqual(self.probe(target,scroll=True), 'invalid')
                    self.assertEqual(self.actions()['scrolls'], 0)
                    self.assertEqual(self.actions()['clicks'], 0)

    def test_all_element_descendants_invalidate_the_leaf_caption(self):
        for child in ['span','button','empty_button','empty_span','nested_empty_button']:
            with self.subTest(child=child):
                with native_fixture.serve_synthetic_html(positioning_html(child=child)) as url:
                    target = self.load_fixture(url, qualified=False)
                    self.assertEqual(self.probe(target, scroll=True), 'changed')
                    self.assertEqual(self.actions()['clicks'], 0)
                    self.assertEqual(self.actions()['scrolls'], 0)

    def test_observed_three_text_nodes_preserve_leaf_caption_behavior(self):
        with native_fixture.serve_synthetic_html(positioning_html()) as url:
            target = self.load_fixture(url)
            self.driver.execute_script("arguments[0].replaceChildren(document.createTextNode('Set '),document.createTextNode('bedrooms'),document.createTextNode(' options'))", target)
            self.assertEqual(self.probe(self.recheck(target)), 'clear')
            self.ordinary_click(target)
            self.assertEqual(self.actions()['scrolls'], 0)

    def test_changed_node_or_count_after_scroll_is_not_reused(self):
        from selenium.common.exceptions import StaleElementReferenceException
        for change in ['count','node']:
            with self.subTest(change=change):
                with native_fixture.serve_synthetic_html(positioning_html(placement='below')) as url:
                    target = self.load_fixture(url)
                    self.assertEqual(self.probe(target,scroll=True), 'scrolled')
                    # Fixture-only fault injection, never a production workaround.
                    if change == 'count':
                        self.driver.execute_script("document.querySelector('input').value='3'")
                        self.assertEqual(self.probe(target), 'changed')
                    else:
                        self.driver.execute_script("const e=document.getElementById('opener');e.replaceWith(e.cloneNode(true))")
                        with self.assertRaises(StaleElementReferenceException): self.probe(target)
                    self.assertEqual(self.actions()['clicks'], 0)
                    self.assertEqual(self.actions()['scrolls'], 1)

    def test_hit_test_does_not_promise_atomic_click_safety(self):
        from selenium.common.exceptions import ElementClickInterceptedException
        with native_fixture.serve_synthetic_html(positioning_html()) as url:
            target = self.load_fixture(url)
            self.assertEqual(self.probe(target), 'clear')
            # A later obstruction can still intercept the ordinary click.
            self.driver.execute_script("document.getElementById('help').style.cssText='display:block;position:fixed;inset:0;width:100vw;height:100vh;opacity:0;z-index:2147483000'")
            with self.assertRaises(ElementClickInterceptedException): target.click()
            self.assertEqual(self.actions()['clicks'], 0)
            self.assertEqual(self.actions()['scrolls'], 0)
            # Do not retry or treat the typed exception as proof of no real click.


if __name__ == '__main__':
    unittest.main()
