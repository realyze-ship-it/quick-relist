"""Only generated synthetic archives; no listing data or network access."""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
from types import SimpleNamespace
import warnings
import zipfile

import pytest

from quick_relist import media
from quick_relist.media import MediaImportError, MediaLimits, import_media_zip


# Deliberately only signatures: the importer must not claim full decoding.
JPEG = b"\xff\xd8\xff\xe0synthetic image bytes\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image bytes"


def make_zip(tmp_path, entries, *, compression=zipfile.ZIP_STORED):
    source = tmp_path / "synthetic.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(source, "w", compression=compression) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    return source


def assert_clean(root):
    assert not root.exists() or not list(root.iterdir())


def test_import_retains_exact_bytes_metadata_and_explicit_unknowns(tmp_path):
    source = make_zip(tmp_path, [("photographs/", b""), ("photographs/02.JPG", JPEG),
                                 ("floorplans/01.png", PNG), ("captions.json", b'{"synthetic":true}')])
    result = import_media_zip(source, tmp_path / "media")
    assert result["complete"] is False
    assert result["media_count"] == 2
    assert Path(result["archive"]["local_path"]).read_bytes() == source.read_bytes()
    assert result["archive"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["archive"]["size"] == source.stat().st_size
    assert json.loads(Path(result["manifest_path"]).read_text()) == result
    for index, (record, payload) in enumerate(zip(result["media_manifest"], [JPEG, PNG]), 1):
        assert Path(record["local_path"]).read_bytes() == payload
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()
        assert record["size"] == len(payload)
        assert record["archive_index"] == index
        assert record["kind"] is None
        assert record["position"] is None
        assert record["caption"] is None
        assert record["original_source_verified"] is False
        assert record["validation"] == "zip_crc_and_signature_only"
        assert record["archive_metadata"]["date_time"]
    assert result["media_manifest"][1]["original_filename"] == "floorplans/01.png"
    assert result["non_image_entries"][0]["original_filename"] == "captions.json"
    assert {path.name for path in Path(result["import_dir"]).iterdir()} == {
        "manifest.json", "source.zip", "media-0002.jpg", "media-0003.png"}
    assert len(result["unresolved"]) >= 9
    assert not list((tmp_path / "media").glob(".pending-*"))
    if os.name != "nt":
        assert stat.S_IMODE(Path(result["import_dir"]).stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "media").stat().st_mode) == 0o700
        for path in Path(result["import_dir"]).iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_importing_twice_never_reuses_files(tmp_path):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    first = import_media_zip(source, tmp_path / "media")
    second = import_media_zip(source, tmp_path / "media")
    assert first["import_dir"] != second["import_dir"]
    assert Path(first["manifest_path"]).exists()
    assert Path(second["manifest_path"]).exists()


def test_identical_images_preserved_and_flagged_not_deduplicated(tmp_path):
    source = make_zip(tmp_path, [("a.jpg", JPEG), ("b.jpg", JPEG)])
    result = import_media_zip(source, tmp_path / "media")
    assert result["media_count"] == 2
    assert result["duplicate_content_groups"] == [["media-0001", "media-0002"]]
    assert result["media_manifest"][0]["local_path"] != result["media_manifest"][1]["local_path"]


@pytest.mark.parametrize("name", ["../outside.jpg", "folder/../../outside.jpg", "/outside.jpg",
                                  "C:/outside.jpg", "folder\\outside.jpg", "image.jpg:stream",
                                  "folder//image.jpg", "./image.jpg", "folder/./image.jpg",
                                  "private\nvalue.jpg", "private\x7fvalue.jpg", "a/" * 33 + "image.jpg"])
def test_rejects_unsafe_names_even_when_image_not_extracted(tmp_path, name):
    source = make_zip(tmp_path, [(name, JPEG)])
    with pytest.raises(MediaImportError, match="unsafe"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("names", [["same.jpg", "same.jpg"], ["SAME.JPG", "same.jpg"],
                                    ["caf\u00e9.jpg", "cafe\u0301.jpg"], ["folder/", "FOLDER/"]])
def test_rejects_duplicate_names_before_extraction(tmp_path, names):
    source = make_zip(tmp_path, [(name, b"" if name.endswith("/") else JPEG) for name in names])
    with pytest.raises(MediaImportError, match="duplicate"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("entries", [[("a", b"data"), ("a/image.jpg", JPEG)],
                                     [("a/image.jpg", JPEG), ("a", b"data")]])
def test_rejects_file_directory_conflicts_in_either_order(tmp_path, entries):
    source = make_zip(tmp_path, entries)
    with pytest.raises(MediaImportError, match="conflicting"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_rejects_symlinks_and_special_members(tmp_path, file_type):
    info = zipfile.ZipInfo("image.jpg")
    info.create_system = 3
    info.external_attr = (file_type | 0o600) << 16
    source = make_zip(tmp_path, [(info, JPEG)])
    with pytest.raises(MediaImportError, match="special"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


def test_rejects_reparse_attribute(tmp_path):
    info = zipfile.ZipInfo("image.jpg")
    info.external_attr = 0x400
    source = make_zip(tmp_path, [(info, JPEG)])
    with pytest.raises(MediaImportError, match="special"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("limits, entries, compression", [
    (MediaLimits(max_archive_bytes=20), [("image.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_members=1), [("a.jpg", JPEG), ("b.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_file_bytes=8), [("image.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_total_bytes=40), [("a.jpg", JPEG), ("b.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_directory_bytes=8), [("image.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_member_name_bytes=3), [("image.jpg", JPEG)], zipfile.ZIP_STORED),
    (MediaLimits(max_compression_ratio=2), [("image.jpg", JPEG + b"x" * 10000)], zipfile.ZIP_DEFLATED),
])
def test_enforces_archive_entry_and_decompression_budgets(tmp_path, limits, entries, compression):
    source = make_zip(tmp_path, entries, compression=compression)
    with pytest.raises(MediaImportError):
        import_media_zip(source, tmp_path / "media", limits=limits)
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_limits_fail_closed(tmp_path, value):
    with pytest.raises(MediaImportError, match="positive integers"):
        import_media_zip(tmp_path / "absent", tmp_path / "media", limits=MediaLimits(max_members=value))
    assert not (tmp_path / "media").exists()


@pytest.mark.parametrize("entries", [[("text.txt", b"hello")], [("image.jpg", PNG)],
                                     [("image.png", JPEG)], [("image.jpg", b"")]])
def test_rejects_unsupported_or_mislabeled_image_packs(tmp_path, entries):
    source = make_zip(tmp_path, entries)
    with pytest.raises(MediaImportError):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


def test_crc_corruption_fails_without_partial_import(tmp_path):
    source = make_zip(tmp_path, [("a.jpg", JPEG), ("b.png", PNG)])
    corrupted = source.read_bytes().replace(b"synthetic image bytes", b"synthetic imago bytes", 1)
    source.write_bytes(corrupted)
    with pytest.raises(MediaImportError):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


def test_private_paths_are_not_in_errors(tmp_path):
    source = tmp_path / "private-address-value.zip"
    source.write_bytes(b"not a ZIP")
    with pytest.raises(MediaImportError) as error:
        import_media_zip(source, tmp_path / "media")
    assert "private-address-value" not in str(error.value)
    assert str(tmp_path) not in str(error.value)
    assert error.value.__suppress_context__ or error.value.__context__ is None


def test_rejects_unsupported_compression(tmp_path):
    source = make_zip(tmp_path, [("image.jpg", JPEG)], compression=zipfile.ZIP_BZIP2)
    with pytest.raises(MediaImportError, match="compression method"):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.parametrize("alteration", ["trailing", "prefix", "split", "zip64", "encrypted", "nul"])
def test_rejects_ambiguous_or_unsupported_zip_structures(tmp_path, alteration):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    data = bytearray(source.read_bytes())
    end = data.rfind(b"PK\x05\x06")
    central = data.find(b"PK\x01\x02")
    if alteration == "trailing":
        data += b"trailing content"
    elif alteration == "prefix":
        data = b"prefix" + data
    elif alteration == "split":
        struct.pack_into("<H", data, end + 4, 1)
    elif alteration == "zip64":
        struct.pack_into("<H", data, end + 10, 65535)
    elif alteration == "encrypted":
        struct.pack_into("<H", data, 6, 1)
        struct.pack_into("<H", data, central + 8, 1)
    elif alteration == "nul":
        data[30:39] = b"ima\x00e.jpg"
        data[central + 46:central + 55] = b"ima\x00e.jpg"
    source.write_bytes(data)
    with pytest.raises(MediaImportError):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


def test_empty_zip_not_imported(tmp_path):
    source = make_zip(tmp_path, [])
    with pytest.raises(MediaImportError):
        import_media_zip(source, tmp_path / "media")
    assert_clean(tmp_path / "media")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and links")
def test_rejects_source_symlink_and_public_or_symlink_destination(tmp_path):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    link = tmp_path / "link.zip"
    link.symlink_to(source)
    with pytest.raises(MediaImportError):
        import_media_zip(link, tmp_path / "media")
    assert_clean(tmp_path / "media")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(MediaImportError, match="accessible only"):
        import_media_zip(source, public)
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "media", target_is_directory=True)
    with pytest.raises(MediaImportError, match="real private"):
        import_media_zip(source, linked)


def test_existing_pending_directory_is_not_removed(tmp_path, monkeypatch):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    pending = root / ".pending-collision"
    pending.mkdir(mode=0o700)
    sentinel = pending / "existing.txt"
    sentinel.write_text("keep")
    monkeypatch.setattr(media, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(MediaImportError):
        import_media_zip(source, root)
    assert sentinel.read_text() == "keep"


def test_existing_final_directory_is_not_overwritten_even_if_empty(tmp_path, monkeypatch):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    existing = root / "collision"
    existing.mkdir(mode=0o700)
    identity = existing.stat().st_ino
    monkeypatch.setattr(media, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(MediaImportError):
        import_media_zip(source, root)
    assert existing.stat().st_ino == identity
    assert list(existing.iterdir()) == []
    assert not list(root.glob(".pending-*"))


def test_failed_publication_cleans_only_new_staging(tmp_path, monkeypatch):
    source = make_zip(tmp_path, [("image.jpg", JPEG)])
    def fail(*_):
        raise OSError("a private path that must not escape")
    monkeypatch.setattr(media, "_publish_no_replace", fail)
    with pytest.raises(MediaImportError) as error:
        import_media_zip(source, tmp_path / "media")
    assert "private path" not in str(error.value)
    assert_clean(tmp_path / "media")


def test_successful_deflate_and_jpeg_suffix_are_supported(tmp_path):
    source = make_zip(tmp_path, [("image.JPEG", JPEG)], compression=zipfile.ZIP_DEFLATED)
    result = import_media_zip(source, tmp_path / "media")
    assert result["media_manifest"][0]["format"] == "jpeg"
    assert result["media_manifest"][0]["local_path"].endswith(".jpeg")
