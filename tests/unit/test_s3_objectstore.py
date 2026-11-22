"""The S3 backend writes the same keys as the local store (verified against an in-process S3
mock, so no credentials or network are needed)."""

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture()
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        boto3.client("s3").create_bucket(Bucket="resgap-lake")
        yield


def test_s3_roundtrip_with_prefix(s3, tmp_path):
    from resgap.objectstore import S3ObjectStore

    store = S3ObjectStore("resgap-lake", prefix="team-x")
    info = store.put_bytes("raw/openfema/X/page-00001.json.gz", b"abc")
    assert info.uri == "s3://resgap-lake/team-x/raw/openfema/X/page-00001.json.gz"
    f = tmp_path / "f.csv"
    f.write_bytes(b"a,b\n")
    store.put_file("raw/cdc_svi/f.csv", f)
    assert store.exists("raw/cdc_svi/f.csv") and not store.exists("raw/nope")
    assert store.get_bytes("raw/openfema/X/page-00001.json.gz") == b"abc"
    with store.open("raw/cdc_svi/f.csv") as fh:
        assert fh.read() == b"a,b\n"
    assert store.list("raw/") == ["raw/cdc_svi/f.csv", "raw/openfema/X/page-00001.json.gz"]
