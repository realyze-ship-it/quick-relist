"""Offline validation of the frozen distribution; never opens Hub or user data."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


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
            assert HubRelistUI is not None
            report.write_text(json.dumps({'passed': True, 'qt': qVersion(),
                'window_created': True, 'driver_manager_bundled': True,
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
