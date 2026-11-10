import os
import sqlite3
import pytest
from quick_relist.drafts import DraftStore


def test_partial_observations_and_tentative_addresses_survive_restart(tmp_path):
    path = tmp_path / "data" / "drafts.sqlite3"
    store = DraftStore(path)
    field = {"name": "street_number", "value": "1", "checked": None}
    cid = store.save_capture("job-one", {"complete": False, "pages": [{"fields": [field]}]})
    original = {"pages/0/fields/0": field}
    edited = {"pages/0/fields/0": {**field, "value": "2"}}
    aid = store.save_address(cid, original, edited)
    store.close()
    store = DraftStore(path)
    assert store.get_capture(cid)["capture"]["complete"] is False
    address = store.get_address(aid)
    assert address["original"] == original
    assert address["edited"]["pages/0/fields/0"]["value"] == "2"
    assert address["approved"] is False
    assert store.list_captures("job-other") == []
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    store.close()


def test_cannot_pass_partial_store_as_verified_snapshot():
    store = DraftStore(":memory:")
    with pytest.raises(ValueError): store.save_capture("job", {"complete": True})
    with pytest.raises(ValueError): store.save_capture("job", {})
    with pytest.raises(ValueError): store.save_capture("", {"complete": False})
    store.close()


def test_fields_must_be_preserved_and_history_is_immutable():
    store = DraftStore(":memory:")
    field = {"name": "street", "value": "x", "checked": None}
    cid = store.save_capture("job", {"complete": False, "pages": [{"fields": [field]}]})
    with pytest.raises(ValueError): store.save_address(cid, {"a": "x"}, {"b": "y"})
    with pytest.raises(ValueError): store.save_address(cid, {"pages/0/fields/0": {**field, "value": "forged"}}, {"pages/0/fields/0": field})
    with pytest.raises(ValueError): store.save_address(cid, {"pages/0/fields/0": field}, {"pages/0/fields/0": {**field, "name": "forged"}})
    aid = store.save_address(cid, {"pages/0/fields/0": field}, {"pages/0/fields/0": {**field, "value": "y"}})
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE captures SET observation_json='{}' WHERE id=?", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM addresses WHERE id=?", (aid,))
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink semantics")
def test_existing_world_readable_file_is_restricted(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "drafts.sqlite3"
    path.touch(mode=0o644)
    os.chmod(path, 0o644)
    store = DraftStore(path)
    assert path.stat().st_mode & 0o077 == 0
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink semantics")
def test_symlink_and_shared_directory_are_rejected(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    target = directory / "target"
    target.touch()
    link = directory / "drafts.sqlite3"
    link.symlink_to(target)
    with pytest.raises(ValueError): DraftStore(link)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    os.chmod(public, 0o755)
    with pytest.raises(ValueError): DraftStore(public / "drafts.sqlite3")


def test_replace_cannot_overwrite_history_or_coerce_bool_and_int():
    store = DraftStore(":memory:")
    field = {"name": "show", "value": "", "checked": False, "dom_order": 0}
    cid = store.save_capture("job", {"complete": False, "pages": [{"fields": [field]}]})
    altered = {**field, "checked": 0, "dom_order": False}
    with pytest.raises(ValueError):
        store.save_address(cid, {"pages/0/fields/0": altered}, {"pages/0/fields/0": altered})
    aid = store.save_address(cid, {"pages/0/fields/0": field}, {"pages/0/fields/0": {**field, "checked": True}})
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT OR REPLACE INTO captures SELECT * FROM captures WHERE id=?", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT OR REPLACE INTO addresses SELECT * FROM addresses WHERE id=?", (aid,))
    store.close()
