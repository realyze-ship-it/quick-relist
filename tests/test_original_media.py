import hashlib
from pathlib import Path
import zipfile

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from quick_relist.media import import_media_zip
from quick_relist.original_media import (OriginalMediaError, download_originals,
                                        observed_originals, verify_pack_bytes)


def picture(color):
    image = QImage(4, 3, QImage.Format.Format_RGB32)
    image.fill(color)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(buffer.data())


def inventory(count=2):
    return [{'observed_asset_id': str(100 + i), 'observed_position': i,
             'original_source_ref': {'url': f'https://cdn.remax.co.za/listings/123/original/image-{i}.png',
                                     'query_omitted': False, 'fragment_omitted': False}}
            for i in range(count)]


def imported(tmp_path, data):
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        for i, value in enumerate(data):
            output.writestr(f'photos/{i+1:02d}.png', value)
    return import_media_zip(archive, tmp_path / 'packs')


def originals(tmp_path, data):
    lookup = {record['original_source_ref']['url']: value
              for record, value in zip(inventory(len(data)), data)}
    return download_originals(inventory(len(data)), '123', tmp_path / 'originals', fetch=lookup.__getitem__)


def test_archive_order_does_not_replace_observed_display_order(tmp_path):
    red, blue = picture(0xffff0000), picture(0xff0000ff)
    result = verify_pack_bytes(originals(tmp_path, [red, blue]), imported(tmp_path, [blue, red]))
    assert result['source_bytes_verified'] is True
    assert [item['sha256'] for item in result['media_manifest']] == [hashlib.sha256(x).hexdigest() for x in (red, blue)]
    assert [item['pack_matches'] for item in result['media_manifest']] == [['media-0002'], ['media-0001']]
    assert all((item['width'], item['height'], item['kind']) == (4, 3, 'photo') for item in result['media_manifest'])
    assert result['metadata_verified'] is False


def test_count_is_insufficient_when_photo_bytes_differ(tmp_path):
    source = originals(tmp_path, [picture(0xffff0000)])
    with pytest.raises(OriginalMediaError, match='exactly'):
        verify_pack_bytes(source, imported(tmp_path, [picture(0xff0000ff)]))


def test_changed_local_bytes_and_missing_images_are_rejected(tmp_path):
    red = picture(0xffff0000)
    source = originals(tmp_path, [red, red])
    with pytest.raises(OriginalMediaError, match='counts'):
        verify_pack_bytes(source, imported(tmp_path, [red]))
    pack = imported(tmp_path, [red, red])
    Path(pack['media_manifest'][0]['local_path']).write_bytes(picture(0xff0000ff))
    with pytest.raises(OriginalMediaError, match='changed'):
        verify_pack_bytes(source, pack)


@pytest.mark.parametrize('url', ['https://cdn.remax.co.za/listings/999/original/image-0.png',
                               'https://example.com/image.png',
                               'https://cdn.remax.co.za/listings/123/original/image-0.png?token=private'])
def test_wrong_listing_host_or_tokenized_source_is_rejected(url):
    items = inventory(1)
    items[0]['original_source_ref']['url'] = url
    with pytest.raises(OriginalMediaError):
        observed_originals(items, '123')


def test_interruption_preserves_already_downloaded_files(tmp_path):
    red = picture(0xffff0000)
    calls = []
    def fetch(url):
        calls.append(url)
        return red
    with pytest.raises(OriginalMediaError, match='stopped'):
        download_originals(inventory(), '123', tmp_path / 'originals', fetch=fetch,
                           cancelled=lambda: len(calls) == 1)
    assert len(list((tmp_path / 'originals').glob('*/0001.png'))) == 1
    assert len(list((tmp_path / 'originals').glob('*/manifest.json'))) == 1
