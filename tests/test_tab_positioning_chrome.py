"""Opt-in native helper/script and ordinary-click qualification on localhost.

Not full matched-controller, Hub, Windows, account or live qualification. Uses
only explicitly supplied existing ChromeDriver/Chrome; no download/installation.
Production route validation remains unchanged and rejects every fixture URL.
"""
import unittest

from quick_relist.capture import CaptureSafetyError, parse_editor_url
from quick_relist.tab_positioning import TAB_POSITION_SCRIPT, prepare_native_tab
from tab_positioning_fixture import LABELS, tab_html
import test_editor_recheck_chrome as native_fixture


class TabPositioningChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.EditorIdentityChromeTests.setUpClass.__func__(cls)

    def load(self,url,*,initial_scroll=False):
        self.driver.get(url)
        with self.assertRaises(ValueError): parse_editor_url(url)
        if initial_scroll:
            self.driver.execute_script("window.scrollTo({top:770,behavior:'instant'})")
        self.driver.execute_script(r"""
          const target=document.getElementById('tab-1');
          window.fixtureActions={clicks:0,scrolls:0,point:null,options:null};
          target.addEventListener('click',event=>{
            fixtureActions.clicks++;fixtureActions.point=[event.clientX,event.clientY];
          });
          const nativeScroll=Element.prototype.scrollIntoView;
          Element.prototype.scrollIntoView=function(options){
            if(this!==target||++fixtureActions.scrolls>1)throw new Error('Wrong or repeated scroll');
            fixtureActions.options=options;return nativeScroll.call(this,options);
          };
          const denied=()=>{throw new Error('Unexpected business mutation or private read')};
          HTMLElement.prototype.click=denied;HTMLFormElement.prototype.submit=denied;HTMLFormElement.prototype.requestSubmit=denied;
          for(const name of ['fetch','XMLHttpRequest','WebSocket','open'])window[name]=denied;
          for(const name of ['localStorage','sessionStorage'])Object.defineProperty(window,name,{get:denied});
          Object.defineProperty(document,'cookie',{get:denied,set:denied});
        """)
        return self.tabs()

    def tabs(self):
        return dict(zip(LABELS,[self.driver.find_element('id','tab-'+str(i)) for i in range(4)]))

    def actions(self):
        return self.driver.execute_script('return window.fixtureActions')

    def probe(self,tabs,*,scroll=False):
        before=self.driver.execute_script("return [document.documentElement.outerHTML,location.href,document.querySelector('input').value]")
        result=self.driver.execute_script(TAB_POSITION_SCRIPT,self.driver.current_url,LABELS[0],LABELS[1],tabs,scroll)
        self.assertEqual(before,self.driver.execute_script("return [document.documentElement.outerHTML,location.href,document.querySelector('input').value]"))
        return result

    def prepare(self,tabs,*,recheck=None,cancelled=None):
        return prepare_native_tab(self.driver,source_url=self.driver.current_url,
            source_label=LABELS[0],target_label=LABELS[1],tabs=tabs,
            recheck=recheck or self.tabs,cancelled=cancelled or (lambda:None),phase=lambda _:None)

    def ordinary_click(self,target):
        point=self.driver.execute_script("""
          const r=arguments[0].getClientRects()[0];
          return [Math.floor((Math.max(0,r.x)+Math.min(innerWidth,r.x+r.width))/2),
                  Math.floor((Math.max(0,r.y)+Math.min(innerHeight,r.y+r.height))/2)];
        """,target)
        target.click()
        self.assertEqual(self.actions()['point'],point)
        self.assertEqual(self.actions()['clicks'],1)
        self.assertEqual(self.driver.execute_script("return document.querySelector('input').value"),'unchanged')

    def test_clean_and_inert_inline_ripple_tabs_do_not_scroll(self):
        for child in ['text','inline','ripple']:
            with self.subTest(child=child):
                with native_fixture.serve_synthetic_html(tab_html(child=child)) as url:
                    tabs=self.load(url)
                    self.ordinary_click(self.prepare(tabs))
                    self.assertEqual(self.actions()['scrolls'],0)

    def test_fixed_header_obstruction_is_cleared_by_one_centered_scroll(self):
        html=tab_html(placement='header',overlay='header')
        with native_fixture.serve_synthetic_html(html) as url:
            tabs=self.load(url,initial_scroll=True)
            self.assertEqual(self.probe(tabs),'needs_scroll')
            self.ordinary_click(self.prepare(tabs))
            self.assertEqual(self.actions()['scrolls'],1)
            self.assertEqual(self.actions()['options'],{'behavior':'instant','block':'center','inline':'center'})

    def test_offscreen_and_horizontal_overflow_tabs_are_positioned_once(self):
        for placement in ['below','horizontal']:
            with self.subTest(placement=placement):
                with native_fixture.serve_synthetic_html(tab_html(placement=placement)) as url:
                    tabs=self.load(url)
                    self.assertEqual(self.probe(tabs),'needs_scroll')
                    self.ordinary_click(self.prepare(tabs))
                    self.assertEqual(self.actions()['scrolls'],1)
                    if placement=='horizontal':
                        self.assertGreater(self.driver.execute_script("return document.getElementById('scroller').scrollLeft"),0)

    def test_persistent_overlay_or_fixed_target_stops_without_click(self):
        for placement,overlay in [('clean','full'),('fixed','header')]:
            with self.subTest(placement=placement):
                with native_fixture.serve_synthetic_html(tab_html(placement=placement,overlay=overlay)) as url:
                    tabs=self.load(url)
                    with self.assertRaises(CaptureSafetyError):self.prepare(tabs)
                    self.assertEqual(self.actions()['scrolls'],1)
                    self.assertEqual(self.actions()['clicks'],0)

    def test_native_invalid_dimensions_never_scroll_or_click(self):
        for size in ['tiny','zero']:
            with self.subTest(size=size):
                with native_fixture.serve_synthetic_html(tab_html(size=size)) as url:
                    tabs=self.load(url)
                    self.assertEqual(self.probe(tabs,scroll=True),'invalid')
                    self.assertEqual(self.actions()['scrolls'],0)
                    self.assertEqual(self.actions()['clicks'],0)

    def test_interactive_descendants_cannot_qualify_as_button_hits(self):
        for child in ['interactive','role','nested']:
            with self.subTest(child=child):
                with native_fixture.serve_synthetic_html(tab_html(child=child)) as url:
                    tabs=self.load(url)
                    self.assertEqual(self.probe(tabs,scroll=True),'changed')
                    self.assertEqual(self.actions()['scrolls'],0)
                    self.assertEqual(self.actions()['clicks'],0)

    def test_changed_node_selection_and_overlay_after_scroll_stop(self):
        for fault in ['node','selection','overlay']:
            with self.subTest(fault=fault):
                with native_fixture.serve_synthetic_html(tab_html(placement='below')) as url:
                    tabs=self.load(url)
                    changed=[False]
                    def recheck():
                        if self.actions()['scrolls'] and not changed[0]:
                            changed[0]=True
                            # Fixture-only fault injection. Production never mutates these.
                            if fault=='node':self.driver.execute_script("const e=document.getElementById('tab-1');e.replaceWith(e.cloneNode(true))")
                            if fault=='selection':self.driver.execute_script("document.getElementById('tab-1').setAttribute('aria-selected','true')")
                            if fault=='overlay':self.driver.execute_script("document.getElementById('cover').setAttribute('role','dialog');document.getElementById('cover').style.display='block'")
                        return self.tabs()
                    with self.assertRaises(CaptureSafetyError):self.prepare(tabs,recheck=recheck)
                    self.assertEqual(self.actions()['scrolls'],1)
                    self.assertEqual(self.actions()['clicks'],0)

    def test_native_cancellation_after_scroll_has_no_later_click(self):
        class Cancelled(Exception):pass
        with native_fixture.serve_synthetic_html(tab_html(placement='below')) as url:
            tabs=self.load(url)
            def cancel():
                if self.actions()['scrolls']:raise Cancelled()
            with self.assertRaises(Cancelled):self.prepare(tabs,cancelled=cancel)
            self.assertEqual(self.actions()['scrolls'],1)
            self.assertEqual(self.actions()['clicks'],0)

    def test_stale_native_node_is_not_reacquired_or_retried(self):
        from selenium.common.exceptions import StaleElementReferenceException
        with native_fixture.serve_synthetic_html(tab_html()) as url:
            tabs=self.load(url)
            self.driver.execute_script("const e=document.getElementById('tab-1');e.replaceWith(e.cloneNode(true))")
            with self.assertRaises(StaleElementReferenceException):self.probe(tabs)
            self.assertEqual(self.actions()['scrolls'],0)
            self.assertEqual(self.actions()['clicks'],0)

    def test_late_interception_is_not_atomic_safety_or_retry_permission(self):
        from selenium.common.exceptions import ElementClickInterceptedException
        with native_fixture.serve_synthetic_html(tab_html()) as url:
            tabs=self.load(url)
            target=self.prepare(tabs)
            self.driver.execute_script("document.getElementById('cover').style.cssText='display:block;position:fixed;inset:0;z-index:99'")
            with self.assertRaises(ElementClickInterceptedException):target.click()
            self.assertEqual(self.actions()['scrolls'],0)
            self.assertEqual(self.actions()['clicks'],0)


if __name__=='__main__':unittest.main()
