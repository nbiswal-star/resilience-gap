import hashlib

import pytest

from resgap.objectstore import LocalObjectStore


def test_put_get_list_roundtrip(tmp_path):
    store = LocalObjectStore(tmp_path / "lake")
    info = store.put_bytes("raw/src/part=1/a.json", b'{"a":1}')
    assert info.sha256 == hashlib.sha256(b'{"a":1}').hexdigest()
    assert store.exists("raw/src/part=1/a.json")
    assert store.get_bytes("raw/src/part=1/a.json") == b'{"a":1}'
    f = tmp_path / "f.csv"
    f.write_bytes(b"x,y\n1,2\n")
    store.put_file("raw/src/part=1/b.csv", f)
    assert store.list("raw/src/") == ["raw/src/part=1/a.json", "raw/src/part=1/b.csv"]
    assert store.uri("raw/src/part=1/b.csv").startswith("file://")


def test_overwrite_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_bytes("k/x", b"1")
    store.put_bytes("k/x", b"2")
    assert store.get_bytes("k/x") == b"2"
    assert [p.name for p in (tmp_path / "k").iterdir()] == ["x"]


@pytest.mark.parametrize("bad", ["", "/abs/key", "a/../b"])
def test_invalid_keys_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        LocalObjectStore(tmp_path).put_bytes(bad, b"x")
