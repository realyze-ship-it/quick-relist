"""Defensive, local-only imports of manually downloaded Hub photo-pack ZIPs.

This is not a Hub download/capture adapter. CRC, file signatures and hashes only
verify the imported bytes, not image decodability, source identity, resolution or
completeness. Archive entry order is retained as evidence, never listing order.
No archive-supplied path is used as an output filename. No values are logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import unicodedata
from uuid import uuid4
import zipfile
import zlib


class MediaImportError(ValueError):
    """Safe to show in the UI: messages never include paths/member contents."""

    def __init__(self, message: str, *, code: str = "invalid_archive"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaLimits:
    max_archive_bytes: int = 256 * 1024 * 1024
    max_members: int = 500
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: int = 100
    max_directory_bytes: int = 2 * 1024 * 1024
    max_member_name_bytes: int = 1024

    def validate(self) -> None:
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise MediaImportError("Media import limits must be positive integers.", code="limits")


_UNRESOLVED = [
    "The archive has not been matched to a verified source listing or account.",
    "Completeness is unverified: compare every original photo and floorplan with Hub.",
    "Original/full-resolution status is unverified; ZIP bytes are preserved unchanged.",
    "Photo versus floorplan classification is unresolved for every image.",
    "Listing display order is unresolved; archive_index records ZIP order only.",
    "Captions, tags, cover selection and other media metadata are unresolved.",
    "The minimum of three listing images and maximum of five floorplans cannot be checked until classification is verified.",
    "Image validation checks signatures and ZIP CRC only, not successful decoding or image dimensions.",
]
_CHUNK = 64 * 1024
_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png"}


def _private_directory(path: Path) -> None:
    """Create one directory with an owner-only inherited ACL on Windows."""
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return
    # POSIX chmod does not secure a Windows directory. Set a protected inheritable
    # ACL at creation time instead. OW is the object's owner-rights SID.
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.ULONG)]
    convert.restype = wintypes.BOOL
    create = kernel.CreateDirectoryW
    create.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    create.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise OSError("Private directory security could not be established.")
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if not create(str(path), ctypes.byref(attributes)):
            error = ctypes.get_last_error()
            if error == 183:
                raise FileExistsError(errno.EEXIST, "Directory already exists.")
            raise OSError("Private directory creation failed.")
    finally:
        free(descriptor)


def _prepare_root(root: Path) -> None:
    # Parent must already exist; avoid silently creating a hierarchy with broad
    # permissions or walking archive-controlled names.
    try:
        _private_directory(root)
    except FileExistsError:
        pass
    details = root.lstat()
    if (stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & 0x400):
        raise MediaImportError("Choose a real private directory for media imports.", code="destination")
    if os.name != "nt" and (details.st_uid != os.getuid() or details.st_mode & 0o077):
        raise MediaImportError("The media directory must be owned by you and accessible only to you.", code="destination")
    # On Windows, each new import is independently created with its own protected
    # DACL even when the pre-existing parent has an inherited/default ACL.


def _private_file(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(descriptor, "wb")


def _copy_archive(source: Path, destination: Path, limits: MediaLimits) -> tuple[int, str]:
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise MediaImportError("Select a regular ZIP file, not a link or special file.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as incoming:
        opened = os.fstat(incoming.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise MediaImportError("The selected archive changed before it could be read.")
        if not 0 < opened.st_size <= limits.max_archive_bytes:
            raise MediaImportError("The archive is empty or exceeds the configured size limit.", code="limits")
        size, digest = 0, hashlib.sha256()
        with _private_file(destination) as outgoing:
            while chunk := incoming.read(_CHUNK):
                size += len(chunk)
                if size > limits.max_archive_bytes:
                    raise MediaImportError("The archive exceeds the configured size limit.", code="limits")
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
        if (size != opened.st_size or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns or after.st_ctime_ns != opened.st_ctime_ns):
            raise MediaImportError("The selected archive changed while it was being read.")
    return size, digest.hexdigest()


def _bounded_directory(path: Path, size: int, limits: MediaLimits) -> None:
    """Bound central-directory allocation before ZipFile builds a member list.

    Deliberately reject ZIP64, split archives, prefixes and trailing data. Normal
    photo packs of these configured sizes do not need any of those features.
    """
    with path.open("rb") as stream:
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise MediaImportError("The selected file is not a supported ZIP archive.")
        end = tail[offset:offset + 22]
        _, disk, start_disk, disk_count, count, directory_size, directory_offset, comment_size = struct.unpack("<4s4H2LH", end)
        absolute_end = size - len(tail) + offset
        if (disk or start_disk or disk_count != count or count == 65535
                or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF
                or offset + 22 + comment_size != len(tail)
                or directory_offset + directory_size != absolute_end):
            raise MediaImportError("Split, ZIP64, prefixed or malformed archives are not supported.")
        if count > limits.max_members or directory_size > limits.max_directory_bytes:
            raise MediaImportError("The archive has too many entries or too much directory metadata.", code="limits")
        if not count:
            raise MediaImportError("The archive contains no files.")
        stream.seek(0)
        if stream.read(4) != b"PK\x03\x04":
            raise MediaImportError("Prefixed or malformed ZIP archives are not supported.")


def _checked_members(archive: zipfile.ZipFile, limits: MediaLimits) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > limits.max_members:
        raise MediaImportError("The archive contains too many entries.", code="limits")
    seen: dict[str, bool] = {}
    total = 0
    for member in members:
        name = member.orig_filename
        if (len(name.encode("utf-8")) > limits.max_member_name_bytes or "\\" in name or ":" in name
                or name.startswith("/") or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise MediaImportError("The archive contains an unsafe entry name.")
        parts = name.rstrip("/").split("/")
        if len(parts) > 32 or any(part in {"", ".", ".."} for part in parts):
            raise MediaImportError("The archive contains an unsafe entry path.")
        key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
        if key in seen:
            raise MediaImportError("The archive contains duplicate or ambiguous entry names.")
        seen[key] = member.is_dir()
        kind = stat.S_IFMT(member.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or member.external_attr & 0x400:
            raise MediaImportError("Links and special files are not allowed in media archives.")
        if ((kind == stat.S_IFDIR and not member.is_dir())
                or (member.is_dir() and kind == stat.S_IFREG)):
            raise MediaImportError("The archive has inconsistent file metadata.")
        if member.flag_bits & 1:
            raise MediaImportError("Encrypted media archives are not supported.")
        if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise MediaImportError("This archive compression method is not supported.")
        if member.file_size > limits.max_file_bytes:
            raise MediaImportError("An archive entry exceeds the configured size limit.", code="limits")
        if member.is_dir() and member.file_size:
            raise MediaImportError("The archive contains a directory with unexpected data.")
        total += member.file_size
        if total > limits.max_total_bytes or member.file_size > max(1, member.compress_size) * limits.max_compression_ratio:
            raise MediaImportError("The archive exceeds the extraction or compression-ratio limit.", code="limits")
    # File/directory conflicts must fail regardless of central-directory order.
    for key in seen:
        parts = key.split("/")
        if any(seen.get("/".join(parts[:index])) is False for index in range(1, len(parts))):
            raise MediaImportError("The archive contains conflicting file and directory paths.")
    return members


def _publish_no_replace(staging: Path, final: Path) -> None:
    """Atomic directory publication without replacing any existing destination."""
    if os.name == "nt":  # Unlike POSIX rename, Windows rename fails if target exists.
        os.rename(staging, final)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        arguments = (-100, os.fsencode(staging), -100, os.fsencode(final), 1)  # RENAME_NOREPLACE
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(staging), os.fsencode(final), 4)  # RENAME_EXCL
    else:
        raise MediaImportError("Atomic, no-overwrite imports are unavailable on this system.", code="destination")
    function.restype = ctypes.c_int
    if function(*arguments):
        raise OSError(ctypes.get_errno(), "Media import could not be published without overwriting.")


def import_media_zip(archive_path: str | Path, destination_root: str | Path, *,
                     limits: MediaLimits | None = None) -> dict:
    """Import a user-selected ZIP into a new private local directory.

    ``destination_root`` must have an existing parent; it is created privately if
    absent. Existing POSIX roots must be owned by the current user with no group
    or other permissions. Never reuses or overwrites an import. Exceptions have
    sanitized messages. Failed imports remove staging data; a process crash may
    leave a private ``.pending-*`` directory, which is never a completed import.

    The JSON-safe result is also saved as manifest.json. ``complete`` is always
    False. ``kind``, ``position`` and ``caption`` remain None. A UI must not turn
    this result into a qualified source snapshot or use it to enable live writes.
    Source ZIP and original entry names may contain personal data: keep the
    entire import private; do not include the manifest in telemetry or logs.
    """
    limits = limits or MediaLimits()
    limits.validate()
    root = Path(os.path.abspath(destination_root))
    staging: Path | None = None
    published = False
    staging_created = False
    try:
        _prepare_root(root)
        import_id = uuid4().hex
        final = root / import_id
        staging = root / (".pending-" + import_id)
        _private_directory(staging)
        staging_created = True
        packed = staging / "source.zip"
        archive_size, archive_hash = _copy_archive(Path(archive_path), packed, limits)
        _bounded_directory(packed, archive_size, limits)
        media, metadata, duplicates = [], [], {}
        extracted_total = 0
        with zipfile.ZipFile(packed) as archive:
            members = _checked_members(archive, limits)
            for index, member in enumerate(members):
                if member.is_dir():
                    continue
                extension = Path(member.filename).suffix.lower()
                image_format = _EXTENSIONS.get(extension)
                output_name = f"media-{index + 1:04d}{extension}" if image_format else None
                outgoing = _private_file(staging / output_name) if output_name else None
                size, digest, prefix = 0, hashlib.sha256(), b""
                try:
                    with archive.open(member, "r") as incoming:
                        while chunk := incoming.read(_CHUNK):
                            size += len(chunk)
                            extracted_total += len(chunk)
                            if size > limits.max_file_bytes or extracted_total > limits.max_total_bytes:
                                raise MediaImportError("Archive extraction exceeded the configured size limit.", code="limits")
                            if len(prefix) < 8:
                                prefix += chunk[:8 - len(prefix)]
                            digest.update(chunk)
                            if outgoing:
                                outgoing.write(chunk)
                    if outgoing:
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                finally:
                    if outgoing:
                        outgoing.close()
                if size != member.file_size:
                    raise MediaImportError("An archive entry failed its declared-size check.")
                if image_format and not ((image_format == "jpeg" and prefix.startswith(b"\xff\xd8\xff"))
                                         or (image_format == "png" and prefix == b"\x89PNG\r\n\x1a\n")):
                    raise MediaImportError("An image entry does not match its claimed JPG/JPEG/PNG format.")
                record = {
                    "original_filename": member.orig_filename, "archive_index": index,
                    "sha256": digest.hexdigest(), "size": size,
                    "archive_metadata": {"date_time": list(member.date_time), "crc32": f"{member.CRC:08x}",
                                         "compressed_size": member.compress_size, "compression_method": member.compress_type},
                }
                if output_name:
                    record.update({"id": f"media-{index + 1:04d}", "local_path": str(final / output_name),
                                   "format": image_format, "kind": None, "position": None, "caption": None,
                                   "original_source_verified": False, "validation": "zip_crc_and_signature_only"})
                    media.append(record)
                    duplicates.setdefault(record["sha256"], []).append(record["id"])
                else:
                    metadata.append(record)
        if not media:
            raise MediaImportError("The archive contains no supported JPG, JPEG or PNG images.")
        unresolved = list(_UNRESOLVED)
        if metadata:
            unresolved.append("Non-image files are retained only in source.zip; their metadata has not been interpreted.")
        duplicate_groups = [ids for ids in duplicates.values() if len(ids) > 1]
        if duplicate_groups:
            unresolved.append("Some images have identical bytes. They are preserved separately; confirm whether the source contains duplicates.")
        result = {"schema_version": 1, "import_id": import_id, "created_at": datetime.now(timezone.utc).isoformat(),
                  "import_dir": str(final), "manifest_path": str(final / "manifest.json"), "complete": False,
                  "archive": {"sha256": archive_hash, "size": archive_size, "local_path": str(final / "source.zip")},
                  "media_manifest": media, "media_count": len(media), "non_image_entries": metadata,
                  "duplicate_content_groups": duplicate_groups, "unresolved": unresolved}
        with _private_file(staging / "manifest.json") as manifest:
            manifest.write(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False).encode("utf-8"))
            manifest.flush()
            os.fsync(manifest.fileno())
        _publish_no_replace(staging, final)
        published = True
        return result
    except MediaImportError:
        raise
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError, zipfile.BadZipFile, UnicodeError, zlib.error, struct.error):
        # Underlying exceptions often embed original names, addresses or paths.
        raise MediaImportError("The ZIP could not be imported safely. Check the archive and private destination, then try again.") from None
    finally:
        if staging_created and staging is not None and not published and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
