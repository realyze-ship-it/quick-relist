from PySide6.QtGui import QImage, QColor
from quick_relist.upload_review import compare_uploaded_images, next_upload_step


def photos(tmp_path):
    result = []
    for i, color in enumerate(('red', 'blue', 'green')):
        image = QImage(120, 80, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = tmp_path / f'{i}.png'
        assert image.save(str(path))
        result.append({'position': i, 'local_path': str(path)})
    return result


def test_partial_upload_retries_only_the_missing_photo(tmp_path):
    source = photos(tmp_path)
    received = [{**source[0]}, {**source[2], 'position': 1}]
    step = next_upload_step(source, received, upload_finished=True)
    assert step['action'] == 'upload_missing'
    assert step['files'] == [source[1]['local_path']]


def test_unfinished_or_uncertain_upload_never_automatically_retries(tmp_path):
    source = photos(tmp_path)
    assert next_upload_step(source, [], upload_finished=False)['action'] == 'wait'
    assert next_upload_step(source, [], upload_finished=True, outcome_uncertain=True)['action'] == 'review'


def test_matching_count_does_not_hide_duplicates_and_missing_images(tmp_path):
    source = photos(tmp_path)
    received = [{**source[0]}, {**source[0], 'position': 1}, {**source[2]}]
    step = next_upload_step(source, received, upload_finished=True)
    assert step['action'] == 'review'
    assert step['comparison']['missing_positions'] == [1]
    assert step['comparison']['unexpected_positions'] == [1]


def test_all_photos_in_wrong_order_require_reordering_not_uploading_again(tmp_path):
    source = photos(tmp_path)
    received = [{**source[2], 'position': 0}, {**source[1]}, {**source[0], 'position': 2}]
    assert next_upload_step(source, received, upload_finished=True)['action'] == 'reorder'


def test_reencoded_jpeg_is_distinguished_from_a_missing_image(tmp_path):
    source = photos(tmp_path)
    image = QImage(source[0]['local_path'])
    path = tmp_path / 'received.jpg'
    assert image.save(str(path), 'JPEG', 95)
    received = [{'position': 0, 'local_path': str(path)}, source[1], source[2]]
    result = compare_uploaded_images(source, received)
    assert result['all_received']
    assert result['matches'][0]['comparison'] == 'visual_match'


def test_retries_are_bounded(tmp_path):
    source = photos(tmp_path)
    assert next_upload_step(source, [], upload_finished=True, attempts=3)['action'] == 'review'


def test_repeated_validation_reuses_decode_but_checks_current_bytes(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    import pytest
    from quick_relist import upload_review as review
    source = photos(tmp_path)
    for photo in source:
        photo['sha256'] = hashlib.sha256(Path(photo['local_path']).read_bytes()).hexdigest()
    review._fingerprints.clear()
    original_reader = review.QImageReader
    decoded = []
    def reader(*args):
        decoded.append(True)
        return original_reader(*args)
    monkeypatch.setattr(review, 'QImageReader', reader)
    review.validate_saved_photos(source)
    assert review.compare_uploaded_images(source, source)['order_matches']
    assert len(decoded) == len(source)
    Path(source[0]['local_path']).write_bytes(Path(source[1]['local_path']).read_bytes())
    with pytest.raises(review.PhotoReviewNeeded, match='changed'):
        review.validate_saved_photos(source)


def test_visually_ambiguous_nonexact_match_still_requires_review(tmp_path):
    import pytest
    from quick_relist import upload_review as review
    source = photos(tmp_path)
    # Different PNG representations of the same pixels have different digests.
    for pos in (1,2):
        image = QImage(source[0]['local_path'])
        image.setText('fixture', str(pos))
        assert image.save(source[pos]['local_path'])
    with pytest.raises(review.PhotoReviewNeeded, match='too similar'):
        review.compare_uploaded_images(source[:2], [{**source[2], 'position':0}])
    assert review.compare_uploaded_images(source, source)['order_matches']


def test_equal_row_totals_still_require_the_full_pixel_comparison(tmp_path):
    # Red/green have the same sum of channel intensities but very different pixels.
    source = photos(tmp_path)
    received = QImage(120, 80, QImage.Format.Format_RGB32)
    received.fill(QColor(0,255,0))
    path = tmp_path / 'other.png'
    assert received.save(str(path))
    report = compare_uploaded_images(source[:1], [{'position':0, 'local_path':str(path)}])
    assert report['unexpected_positions'] == [0] and not report['order_matches']
