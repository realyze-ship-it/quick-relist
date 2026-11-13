"""Download the observed Hub originals and match a photo pack byte for byte.

Source URLs must come from a guarded media-page observation. No credentials,
browser profiles or guessed image URLs are used. Matching establishes the bytes
and displayed order; it does not assert that unknown media metadata is empty.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .media import _private_directory, _private_file, _prepare_root


class OriginalMediaError(ValueError):
    pass


def observed_originals(media, listing_id):
    """Accept only uniquely identified, consecutively ordered original cards."""
    if not isinstance(listing_id, str) or not re.fullmatch(r'[0-9]+', listing_id):
        raise OriginalMediaError('A verified source listing is required.')
    records = []
    for item in media:
        if not isinstance(item, dict):
            raise OriginalMediaError('The media observation is malformed.')
        if item.get('observed_asset_id') is None:
            continue  # Unrelated page artwork is not a listing image.
        ref = item.get('original_source_ref')
        if (not isinstance(ref, dict) or ref.get('query_omitted') is not False
                or ref.get('fragment_omitted') is not False):
            raise OriginalMediaError('An original image address is missing or incomplete.')
        url = ref.get('url')
        try:
            p = urlsplit(url)
            valid = (isinstance(url, str) and p.scheme == 'https' and p.netloc == 'cdn.remax.co.za'
                     and not p.query and not p.fragment and '?' not in url and '#' not in url
                     and re.fullmatch(r'/listings/' + re.escape(listing_id)
                                      + r'/original/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)', p.path, re.I))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise OriginalMediaError('An image does not belong to the selected listing original folder.')
        ident, position = item.get('observed_asset_id'), item.get('observed_position')
        if (not isinstance(ident, str) or not re.fullmatch(r'[0-9]+', ident)
                or type(position) is not int or position < 0):
            raise OriginalMediaError('An image identity or display position is missing.')
        records.append({'id': ident, 'position': position, 'url': url})
    if not records or len(records) > 500:
        raise OriginalMediaError('No supported original-image inventory was found.')
    records.sort(key=lambda x: x['position'])
    if ([r['position'] for r in records] != list(range(len(records)))
            or len({r['id'] for r in records}) != len(records)
            or len({r['url'] for r in records}) != len(records)):
        raise OriginalMediaError('Image identities or display positions are ambiguous.')
    return records


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OriginalMediaError('The original-image server redirected the request. Review the source.')


def _fetch_original(url, maximum=32 * 1024 * 1024):
    request = Request(url, headers={'User-Agent': 'QuickRelist/0.1', 'Accept': 'image/jpeg,image/png'})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.headers.get_content_type() not in ('image/jpeg', 'image/png'):
                raise OriginalMediaError('The original-image server did not return an image.')
            data = response.read(maximum + 1)
    except OriginalMediaError:
        raise
    except Exception:
        raise OriginalMediaError('An original image could not be downloaded. The listing is unchanged.') from None
    if not data or len(data) > maximum:
        raise OriginalMediaError('An original image is empty or exceeds the size limit.')
    return data


def _dimensions(data):
    from PySide6.QtGui import QImage
    image = QImage.fromData(data)
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise OriginalMediaError('An original image could not be decoded.')
    return image.width(), image.height()


def download_originals(media, listing_id, destination_root, *, fetch=None, cancelled=lambda: False):
    records = observed_originals(media, listing_id)
    root = Path(destination_root)
    _prepare_root(root)
    directory = root / ('originals-' + uuid4().hex)
    _private_directory(directory)
    result = {'schema_version': 'hub-original-media/v1', 'complete': False,
              'listing_id': listing_id, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'status': 'in_progress', 'media_manifest': []}
    get_bytes = fetch or _fetch_original
    try:
        total = 0
        for record in records:
            if cancelled():
                raise OriginalMediaError('Image collection was stopped. Already downloaded files were retained.')
            data = get_bytes(record['url'])
            if not isinstance(data, bytes) or not 0 < len(data) <= 32 * 1024 * 1024:
                raise OriginalMediaError('An original image is empty or exceeds the size limit.')
            total += len(data)
            if total > 512 * 1024 * 1024:
                raise OriginalMediaError('The original images exceed the collection size limit.')
            width, height = _dimensions(data)
            suffix = Path(urlsplit(record['url']).path).suffix.lower()
            path = directory / f"{record['position'] + 1:04d}{suffix}"
            with _private_file(path) as stream:
                stream.write(data)
            result['media_manifest'].append({**record, 'local_path': str(path), 'size': len(data),
                                            'sha256': hashlib.sha256(data).hexdigest(),
                                            'width': width, 'height': height})
        result.update(status='downloaded', media_count=len(records))
        return result
    finally:
        # Preserve progress, including on interruption. Downloading is read-only;
        # no cleanup or retry can remove the source listing or its local files.
        with _private_file(directory / 'manifest.json') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))


def verify_pack_bytes(originals, imported_pack):
    """Match multiplicity, identity and observed display order by actual bytes."""
    if originals.get('status') != 'downloaded' or not originals.get('media_manifest'):
        raise OriginalMediaError('Original image collection is incomplete.')
    source = originals['media_manifest']
    packed = imported_pack.get('media_manifest', [])
    if (len(packed) != len(source) or originals.get('media_count') != len(source)
            or imported_pack.get('media_count') != len(packed) or imported_pack.get('non_image_entries')):
        raise OriginalMediaError('The photo pack and source image counts differ or contain unsupported entries.')
    def key(item):
        path = Path(item['local_path'])
        if path.is_symlink() or not path.is_file():
            raise OriginalMediaError('A saved image is missing or is not a regular file.')
        if not 0 < path.stat().st_size <= 32 * 1024 * 1024:
            raise OriginalMediaError('A saved image is empty or exceeds the size limit.')
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest(), len(data)
        if actual != (item.get('sha256'), item.get('size')):
            raise OriginalMediaError('Saved image bytes changed after collection.')
        return actual
    source_keys = [key(item) for item in source]
    pack_keys = [key(item) for item in packed]
    if Counter(source_keys) != Counter(pack_keys):
        raise OriginalMediaError('The photo pack does not contain exactly the observed original images.')
    matches = defaultdict(list)
    for item, fingerprint in zip(packed, pack_keys):
        matches[fingerprint].append(item)
    verified = []
    for item, fingerprint in zip(source, source_keys):
        candidates = matches[fingerprint]
        # Identical image bytes in multiple archive locations cannot prove which
        # source card is a photo or floorplan. Keep that distinction unresolved.
        kinds = {('photo' if re.fullmatch(r'photos/[0-9]+\.(jpg|jpeg|png)',
                                         c.get('original_filename', ''), re.I) else None)
                 for c in candidates}
        verified.append({**item, 'kind': next(iter(kinds)) if len(kinds) == 1 else None,
                         'pack_matches': [c['id'] for c in candidates], 'caption': None})
    return {'schema_version': 'hub-verified-photo-pack/v1', 'complete': False,
            'listing_id': originals['listing_id'], 'media_count': len(verified),
            'media_manifest': verified, 'source_bytes_verified': True,
            'display_order_observed': True, 'pack_archive_sha256': imported_pack['archive']['sha256'],
            'metadata_verified': False,
            'unresolved': ['Captions and cover must be observed separately.',
                           'Unknown archive folders require photo/floorplan review.']}
