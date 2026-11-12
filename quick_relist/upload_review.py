"""Compare Hub's received photos and plan bounded retries of missing images.

Hub can re-encode JPEGs on upload, so byte equality alone is insufficient for
the destination. Visual matches retain that qualification in the report.
This module performs no uploads, deletions, browser actions or publication.
"""
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader


class PhotoReviewNeeded(ValueError):
    pass


def _fingerprint(item):
    path = Path(item['local_path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise PhotoReviewNeeded('A photo file is unavailable or exceeds the supported size.')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if item.get('sha256') and digest != item['sha256']:
        raise PhotoReviewNeeded('A saved photo changed after collection.')
    reader = QImageReader(str(path))
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 50_000_000:
        raise PhotoReviewNeeded('A photo has unsupported dimensions.')
    image = reader.read()
    if image.isNull():
        raise PhotoReviewNeeded('A photo could not be decoded for comparison.')
    thumbnail = image.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
    return {'sha256': digest, 'size': (image.width(), image.height()),
            'pixels': bytes(thumbnail.constBits())}


def compare_uploaded_images(source, received, *, maximum_difference=0.5):
    """Match every received image to one source occurrence, preserving counts."""
    if len(source) > 500 or len(received) > 500 or not source:
        raise PhotoReviewNeeded('The photo count is outside the supported range.')
    for items in (source, received):
        if [x['position'] for x in items] != list(range(len(items))):
            raise PhotoReviewNeeded('Photo positions are missing, repeated or out of order.')
    originals = [_fingerprint(x) for x in source]
    uploads = [_fingerprint(x) for x in received]
    candidates = []
    for upload in uploads:
        possible = []
        for index, original in enumerate(originals):
            if upload['size'] != original['size']:
                continue
            exact = upload['sha256'] == original['sha256']
            difference = 0.0 if exact else sum(abs(a - b) for a, b in zip(upload['pixels'], original['pixels'])) / len(upload['pixels'])
            if difference <= maximum_difference:
                possible.append((index, exact, difference))
        # Multiple *different* source images can be too similar for this
        # comparison. Do not guess which one is missing and upload duplicates.
        exact_matches = [p for p in possible if p[1]]
        if exact_matches:
            possible = exact_matches
        distinct = {originals[i]['sha256'] for i, _, _ in possible}
        if len(distinct) > 1:
            raise PhotoReviewNeeded('Two source photos are too similar to match reliably. Review this upload before retrying.')
        candidates.append(possible)
    used, matches, unexpected = set(), [], []
    for position, possible in enumerate(candidates):
        available = [p for p in possible if p[0] not in used]
        if not available:
            unexpected.append(position)
            continue
        index, exact, difference = min(available, key=lambda p: (not p[1], p[2], p[0]))
        used.add(index)
        matches.append({'received_position': position, 'source_position': index,
                        'comparison': 'exact_bytes' if exact else 'visual_match',
                        'mean_pixel_difference': round(difference, 6)})
    missing = [i for i in range(len(source)) if i not in used]
    return {'matches': matches, 'missing_positions': missing, 'unexpected_positions': unexpected,
            'all_received': not missing and not unexpected,
            'order_matches': not missing and not unexpected and all(m['received_position'] == m['source_position'] for m in matches)}


def next_upload_step(source, received, *, upload_finished, outcome_uncertain=False, attempts=1, max_attempts=3):
    """A stable thumbnail count alone is not an upload-completion signal."""
    if outcome_uncertain:
        return {'action': 'review', 'files': [], 'reason': 'The last upload outcome is uncertain; no retry was sent.'}
    if upload_finished is not True:
        return {'action': 'wait', 'files': [], 'reason': 'Wait for Hub to finish the current upload.'}
    comparison = compare_uploaded_images(source, received)
    if comparison['unexpected_positions']:
        return {'action': 'review', 'files': [], 'comparison': comparison,
                'reason': 'Hub contains extra or unmatched photos. No images were removed.'}
    if comparison['all_received']:
        return {'action': 'done' if comparison['order_matches'] else 'reorder', 'files': [], 'comparison': comparison}
    if attempts >= max_attempts:
        return {'action': 'review', 'files': [], 'comparison': comparison,
                'reason': 'Hub still has missing photos after the allowed attempts.'}
    return {'action': 'upload_missing', 'files': [source[i]['local_path'] for i in comparison['missing_positions']],
            'source_positions': comparison['missing_positions'], 'comparison': comparison}
