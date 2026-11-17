"""Offline validation of the frozen distribution; never opens Hub or user data."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def check_chrome_imports():
    # Selenium resolves these exports with importlib at first use. Merely
    # importing webdriver or checking its manager binary misses absent frozen
    # modules. Exercise the same lazy exports as ReadOnlyHub.open, without
    # constructing a driver or starting a browser.
    from selenium import webdriver
    options = webdriver.ChromeOptions()
    assert options.to_capabilities()['browserName'] == 'chrome'
    assert callable(webdriver.Chrome)
    # Chrome can be newer than Selenium's generated protocol files. Exercise
    # the filesystem-based version fallback as well as its dynamic import.
    from selenium.webdriver.remote import webdriver as remote_webdriver
    remote_webdriver.import_cdp()
    protocol = remote_webdriver.cdp.import_devtools('999')
    assert callable(protocol.browser.DownloadWillBegin)
    assert callable(protocol.browser.DownloadProgress)
    assert callable(protocol.target.get_target_info)
    assert callable(protocol.page.get_frame_tree)
    return True


def check_browser_tools(report: Path) -> int:
    """Check the frozen helper toolchain without starting Chrome or contacting Hub."""
    import subprocess
    import traceback
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chromium.remote_connection import ChromiumRemoteConnection

    result = {'passed': False, 'hub_opened': False, 'browser_opened': False}
    try:
        result['stage'] = 'chrome_imports'
        result['chrome_imports'] = check_chrome_imports()
        result['stage'] = 'locate_browser_driver'
        paths = SeleniumManager().binary_paths(['--browser', 'chrome', '--offline', '--avoid-stats'])
        assert Path(paths['browser_path']).is_file()
        driver = Path(paths['driver_path'])
        assert driver.is_file()
        result['stage'] = 'driver_version'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        completed = subprocess.run([str(driver), '--version'], capture_output=True,
                                   timeout=15, creationflags=flags, check=True)
        assert completed.stdout.startswith(b'ChromeDriver ')
        result['stage'] = 'windowless_driver_process'
        service = Service(executable_path=str(driver), service_args=['--version'])
        try:
            service._start_process(str(driver))
            assert service.process.wait(timeout=15) == 0
        finally:
            service.stop()
        result['stage'] = 'connection_configuration'
        connection = ChromiumRemoteConnection(remote_server_addr='http://localhost:1',
                                               browser_name='chrome', vendor_prefix='goog')
        connection.close()
        result['passed'] = True
    except Exception as exc:
        result['exception_type'] = type(exc).__name__
        result['frames'] = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                            for f in traceback.extract_tb(exc.__traceback__)]
        if isinstance(exc, subprocess.CalledProcessError):
            result['returncode'] = exc.returncode
    report.write_text(json.dumps(result), encoding='utf-8')
    return 0 if result['passed'] else 1


def check_package(report: Path) -> int:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    from .core import Engine, Store
    from .drafts import DraftStore
    from .run_store import RunStore
    from .hub_readonly import ReadOnlyHub
    from .live_relist_ui import HubRelistUI
    from .ui import MainWindow

    application = QApplication([])
    with TemporaryDirectory(prefix='quick-relist-package-check-') as temporary:
        directory = Path(temporary)
        store = Store(directory / 'jobs.sqlite3')
        drafts = DraftStore(directory / 'captures.sqlite3')
        runs = RunStore(directory / 'runs.sqlite3')
        hub = ReadOnlyHub()
        window = None
        try:
            window = MainWindow(engine=Engine(store), store=store, drafts=drafts,
                                runs=runs, hub=hub, media_root=directory / 'media')
            assert window.windowTitle() == 'Quick Relist'
            assert hub.driver is None
            assert SeleniumManager()._get_binary().is_file()
            assert check_chrome_imports()
            assert HubRelistUI is not None
            report.write_text(json.dumps({'passed': True, 'qt': qVersion(),
                'window_created': True, 'driver_manager_bundled': True, 'chrome_imports': True,
                'hub_opened': False, 'user_data_opened': False}), encoding='utf-8')
        finally:
            if window is not None:
                window.close()
                window.deleteLater()
                application.processEvents()
            hub.close()
            runs.close()
            drafts.close()
            store.close()
    return 0
