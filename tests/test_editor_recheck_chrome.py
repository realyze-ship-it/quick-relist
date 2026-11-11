"""Opt-in native DOM qualification of the exact read-only identity scripts.

Uses generated localhost HTML, a disposable Chrome profile and an explicitly
provided existing driver. Never visits Hub or relaxes production origin guards.
This qualifies script DOM/visibility semantics only, not the whole controller,
real Hub timing, the Windows app, ownership, fidelity or live relisting.

Run with QUICK_RELIST_CHROME_TESTS=1 and QUICK_RELIST_CHROMEDRIVER set to an
existing executable. QUICK_RELIST_CHROME_BINARY may select an existing Chrome.
No installation or automatic driver download is performed. Without opt-in,
the entire class skips before creating a server, profile or browser.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from quick_relist.capture import parse_editor_url
from quick_relist.hub_readonly import IDENTITY_SCRIPT, SOURCE_CONTEXT_SCRIPT


@contextmanager
def serve_synthetic_html(html):
    path = "/quick-relist-editor-script/" + secrets.token_hex(24)
    data = ("<!doctype html><meta charset='utf-8'><title>Synthetic script fixture</title>" + html).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{path}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class EditorIdentityChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("QUICK_RELIST_CHROME_TESTS") != "1":
            raise unittest.SkipTest("Native editor-script Chrome qualification not requested")
        driver_path = Path(os.environ.get("QUICK_RELIST_CHROMEDRIVER", ""))
        if not driver_path.is_file():
            raise unittest.SkipTest("Existing chromedriver required; no download attempted")
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            raise unittest.SkipTest("Selenium is not installed; no installation attempted") from None
        profile = TemporaryDirectory(prefix="quick-relist-editor-synthetic-")
        cls.addClassCleanup(profile.cleanup)
        options = Options()
        for argument in ("--headless=new", "--window-size=1400,1200", "--disable-background-networking",
                         "--no-first-run", "--no-default-browser-check", "--user-data-dir=" + profile.name):
            options.add_argument(argument)
        if os.environ.get("QUICK_RELIST_CHROME_BINARY"):
            options.binary_location = os.environ["QUICK_RELIST_CHROME_BINARY"]
        cls.driver = webdriver.Chrome(service=Service(str(driver_path.resolve())), options=options)
        cls.addClassCleanup(cls.driver.quit)
        cls.driver.set_page_load_timeout(10)
        cls.driver.set_script_timeout(5)

    def checked_script(self, script, *args):
        # Synthetic harness only: fail if a production read tries to mutate,
        # reach the network, or inspect browser storage/cookies.
        self.driver.execute_script("""
          const denied = () => { throw new Error('Unexpected side effect or hidden-state read'); };
          for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'open']) window[name] = denied;
          for (const name of ['localStorage', 'sessionStorage']) Object.defineProperty(window, name, {get:denied, configurable:true});
          Object.defineProperty(document, 'cookie', {get:denied, set:denied, configurable:true});
          HTMLElement.prototype.click = denied;
          HTMLFormElement.prototype.submit = denied;
          HTMLFormElement.prototype.requestSubmit = denied;
        """)
        before = self.driver.execute_script("return [document.documentElement.outerHTML, location.href];")
        result = self.driver.execute_script(script, *args)
        self.assertEqual(self.driver.execute_script("return [document.documentElement.outerHTML, location.href];"), before)
        return result

    def source_read(self, *, wrapper="", reference="RXCU-12345", extra=""):
        html = f"<section {wrapper}><div role='row' id='source-row'><p>Ref: {reference}</p></div>{extra}</section>"
        with serve_synthetic_html(html) as url:
            self.driver.get(url)
            root = self.driver.find_element("tag name", "html")
            row = self.driver.find_element("id", "source-row")
            return self.checked_script(SOURCE_CONTEXT_SCRIPT, root, row, "RXCU12345")

    def identity_read(self, html):
        with serve_synthetic_html(html) as url:
            self.driver.get(url)
            # Executing scripts directly must not weaken controller route checks.
            with self.assertRaises(ValueError):
                parse_editor_url(url)
            return self.checked_script(IDENTITY_SCRIPT, "RXCU12345")

    def test_source_ancestor_visibility_and_menu_hiding(self):
        for wrapper, expected in (("", True), ("aria-hidden='true'", True),
                                  ("style='opacity:0'", False), ("hidden", False),
                                  ("style='display:none'", False), ("style='visibility:hidden'", False),
                                  ("aria-hidden='true' style='opacity:0'", False)):
            with self.subTest(wrapper=wrapper):
                self.assertIs(self.source_read(wrapper=wrapper), expected)

    def test_source_reference_and_duplicate_rows_fail_closed(self):
        self.assertIs(self.source_read(reference="RXCU-99999"), False)
        self.assertIs(self.source_read(extra="<div role='row'><p>Ref: RXCU-12345</p></div>"), False)
        self.assertIs(self.source_read(extra="<div role='row' hidden><p>Ref: RXCU-12345</p></div>"), True)

    def test_exact_heading_semantics_and_loading_counts(self):
        matching = "<h2>Edit listing - <span>RXCU-12345</span> (Synthetic)</h2><main><h4>Edit listing - RXCU12345 (Synthetic)</h4></main>"
        matched = {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        self.assertEqual(self.identity_read(matching), matched)
        self.assertEqual(self.identity_read("<main>Loading synthetic fixture</main>"),
                         {"outer_count": 0, "inner_count": 0, "outer_matches": False, "inner_matches": False})
        wrong = self.identity_read(matching.replace("<span>RXCU-12345</span>", "RXCU-99999"))
        self.assertEqual(wrong, {**matched, "outer_matches": False})
        duplicate = self.identity_read(matching + "<h2>Edit listing - RXCU-12345 (Synthetic)</h2>")
        self.assertEqual(duplicate, {**matched, "outer_count": 2, "outer_matches": False})

    def test_heading_ancestor_hiding_is_not_the_source_menu_exception(self):
        headings = "<h2>Edit listing - RXCU-12345 (Synthetic)</h2><main><h4>Edit listing - RXCU-12345 (Synthetic)</h4></main>"
        for wrapper in ("aria-hidden='true'", "style='opacity:0'", "style='visibility:hidden'", "hidden"):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(self.identity_read(f"<section {wrapper}>{headings}</section>"),
                                 {"outer_count": 0, "inner_count": 0, "outer_matches": False, "inner_matches": False})

    def test_hidden_heading_descendant_cannot_supply_a_matching_reference(self):
        html = "<h2><span hidden>Edit listing - RXCU-12345 (Synthetic)</span>Edit listing - RXCU-99999 (Synthetic)</h2>"
        html += "<main><h4><span aria-hidden='true'>Edit listing - RXCU-12345 (Synthetic)</span>Edit listing - RXCU-99999 (Synthetic)</h4></main>"
        self.assertEqual(self.identity_read(html),
                         {"outer_count": 1, "inner_count": 1, "outer_matches": False, "inner_matches": False})


if __name__ == "__main__":
    unittest.main()
