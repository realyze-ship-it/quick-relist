"""Private error locations, excluding browser contents and exception messages."""
from pathlib import Path
import json
import traceback
from uuid import uuid4


def error_locations(exc):
    records, seen = [], set()
    while exc is not None and id(exc) not in seen and len(records) < 5:
        seen.add(id(exc))
        record = {'exception_type': type(exc).__name__,
                  'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                             for f in traceback.extract_tb(exc.__traceback__)[-12:]]}
        for name in ('errno', 'winerror', 'returncode'):
            value = getattr(exc, name, None)
            if type(value) is int:
                record[name] = value
        records.append(record)
        exc = exc.__cause__ or exc.__context__
    return records


def record_error(directory, stage, exc):
    """Best effort only; failure to log must never retry or alter an operation."""
    from .media import _private_directory, _private_file
    try:
        directory = Path(directory)
        if not directory.exists():
            _private_directory(directory)
        if directory.is_symlink():
            return None
        reference = uuid4().hex
        record = {'stage': stage, 'errors': error_locations(exc)}
        with _private_file(directory / ('error-' + reference + '.json')) as stream:
            stream.write(json.dumps(record).encode('utf-8'))
        return reference
    except Exception:
        return None
