"""Packaged entry point; development browser attachment is never enabled."""
import os
from pathlib import Path
import traceback
import sys

os.environ.pop('QUICK_RELIST_DEVELOPMENT', None)
os.environ.pop('QUICK_RELIST_EXISTING_PROFILE', None)

try:
    if len(sys.argv) == 3 and sys.argv[1] == '--check-package':
        from quick_relist.package_check import check_package
        result = check_package(Path(sys.argv[2]))
    else:
        from quick_relist.__main__ import main
        result = main()
except Exception:
    # The windowed bootloader otherwise hides startup diagnostics from the
    # operator. Keep this traceback private, alongside the application's data.
    directory = Path(os.environ.get('LOCALAPPDATA', '.')) / 'QuickRelist' / 'diagnostics'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    raise
raise SystemExit(result)
