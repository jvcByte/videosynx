"""Storage backends.

The local backend is exercised properly. The S3 one is checked against a stub
client, because signing is boto3's job -- what matters here is that the key
layout, the presign call and the bucket/endpoint wiring are what we intend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pycoding_worker.storage import (
    DEFAULT_URL_TTL,
    LocalStorage,
    S3Storage,
    from_env,
    video_key,
)


@pytest.fixture
def store(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "artifacts")


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "out.mp4"
    path.write_bytes(b"fake mp4 bytes")
    return path


# ------------------------------------------------------------------ key layout

def test_key_layout_puts_the_tier_first():
    """Free artifacts get expired by a prefix rule, so the tier leads."""
    assert video_key("free", "abc123") == "artifacts/free/abc123.mp4"
    assert video_key("paid", "abc123").startswith("artifacts/paid/")


# ---------------------------------------------------------------------- local

def test_put_then_read_roundtrip(store, video):
    key = video_key("free", "job-1")
    store.put(key, video)
    assert store.exists(key)
    assert store.read(key) == b"fake mp4 bytes"


def test_put_moves_the_file(store, video):
    store.put(video_key("free", "job-1"), video)
    # Uploading is a handoff; leaving the source behind would fill the disk
    # with copies of videos the store already has.
    assert not video.exists()


def test_url_points_at_the_api(store):
    url = store.url(video_key("free", "job-1"))
    assert url.endswith("artifacts/free/job-1.mp4")


def test_delete_is_idempotent(store, video):
    key = video_key("free", "job-1")
    store.put(key, video)
    store.delete(key)
    store.delete(key)
    assert not store.exists(key)


def test_missing_key_reads_as_none(store):
    assert store.read("artifacts/free/nope.mp4") is None
    assert not store.exists("artifacts/free/nope.mp4")


@pytest.mark.parametrize("key", [
    "../escape.mp4",
    "artifacts/../../escape.mp4",
    "/etc/passwd",
])
def test_keys_cannot_escape_the_root(store, key):
    """Keys arrive from a URL, so they are input, not trusted paths."""
    assert store.read(key) is None
    assert not store.exists(key)
    with pytest.raises(ValueError):
        store.put(key, Path("/dev/null"))


# ------------------------------------------------------------------------- s3

class StubS3Client:
    def __init__(self):
        self.uploads: list[tuple] = []
        self.presigned: list[dict] = []
        self.deleted: list[str] = []
        self.heads: list[str] = []

    def upload_file(self, filename, bucket, key):
        self.uploads.append((filename, bucket, key))

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append(
            {"operation": operation, "params": Params, "expires_in": ExpiresIn}
        )
        return f"https://r2.example/{Params['Key']}?sig=abc"

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)

    def head_object(self, Bucket, Key):
        self.heads.append(Key)
        return {}


def s3_with_stub(monkeypatch) -> tuple[S3Storage, StubS3Client]:
    stub = StubS3Client()
    monkeypatch.setattr(
        "boto3.client", lambda *a, **k: stub
    )
    store = S3Storage("my-bucket", endpoint_url="https://acct.r2.cloudflarestorage.com",
                      access_key="key", secret_key="secret")
    return store, stub


def test_s3_uploads_to_the_configured_bucket(monkeypatch, video):
    store, stub = s3_with_stub(monkeypatch)
    store.put("artifacts/free/j1.mp4", video)
    assert stub.uploads == [(str(video), "my-bucket", "artifacts/free/j1.mp4")]


def test_s3_presigns_get_object_with_a_ttl(monkeypatch):
    store, stub = s3_with_stub(monkeypatch)
    url = store.url("artifacts/free/j1.mp4", expires_in=600)
    assert url.startswith("https://r2.example/")
    call = stub.presigned[0]
    assert call["operation"] == "get_object"
    assert call["params"] == {"Bucket": "my-bucket", "Key": "artifacts/free/j1.mp4"}
    assert call["expires_in"] == 600


def test_s3_default_ttl_is_an_hour(monkeypatch):
    store, stub = s3_with_stub(monkeypatch)
    store.url("artifacts/free/j1.mp4")
    assert stub.presigned[0]["expires_in"] == DEFAULT_URL_TTL == 3600


def test_s3_refuses_to_proxy_bytes(monkeypatch):
    """Downloads go straight to the store; the API must not become a relay."""
    store, _ = s3_with_stub(monkeypatch)
    with pytest.raises(NotImplementedError):
        store.read("artifacts/free/j1.mp4")


def test_s3_requires_a_bucket():
    with pytest.raises(ValueError):
        S3Storage("")


# ------------------------------------------------------------------ selection

def test_from_env_prefers_r2_when_configured(tmp_path):
    store = from_env({"R2_BUCKET": "b", "R2_ENDPOINT": "https://x",
                      "PYCODING_ARTIFACTS": str(tmp_path)})
    assert isinstance(store, S3Storage)
    assert store.bucket == "b"


def test_from_env_falls_back_to_local(tmp_path):
    store = from_env({"PYCODING_ARTIFACTS": str(tmp_path / "a")})
    assert isinstance(store, LocalStorage)
    # A checkout with no configuration must still run, and must not look like
    # a deployment.
    assert store.root == tmp_path / "a"
