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
