"""Start the local Quick Relist desktop workspace."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def data_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "QuickRelist"


def main():
    from .core import Engine, Store
    from .hub_readonly import ReadOnlyHub
    from .ui import launch

    directory = data_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = Store(directory / "quick-relist.sqlite3")
    hub = ReadOnlyHub()
    try:
        return launch(engine=Engine(store), store=store, hub=hub)
    finally:
        try:
            hub.close()
        finally:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
