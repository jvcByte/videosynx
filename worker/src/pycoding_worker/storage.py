"""Where finished videos live.

Cloudflare R2, spoken to over the S3 API. R2 specifically because egress is
free: a 5-minute 1080p video is 50-150 MB and every view is a download, so
egress on a metered store is the line item that grows with success.

The local backend exists so the whole loop -- submit, render, upload, watch --
runs and is tested without a bucket. It is not a fallback: a deployed server
uses one or the other, chosen by configuration, and the API refuses to serve
files directly when it is not the local backend.

Keys are laid out ``artifacts/<tier>/<job_id>.mp4`` so a lifecycle rule can
expire the free tier on a prefix. Retention is per-tier policy, and doing it
in the bucket means it holds even if this service is down.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

VIDEO_KEY = "artifacts/{tier}/{job_id}.mp4"
DEFAULT_URL_TTL = 3600


def video_key(tier: str, job_id: str, suffix: str = ".mp4") -> str:
    return VIDEO_KEY.format(tier=tier, job_id=job_id).replace(".mp4", suffix)


class Storage(Protocol):
    def put(self, key: str, path: Path) -> None: ...
    def url(self, key: str, *, expires_in: int = DEFAULT_URL_TTL) -> str: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def read(self, key: str) -> bytes | None: ...


def _url_secret() -> bytes:
    """Key for signing local URLs.

    Per-process by default: a development server's links are not meant to
    outlive it. Set PYCODING_URL_SECRET when the links must survive a restart.
    """
    import os

    return (os.environ.get("PYCODING_URL_SECRET") or secrets.token_hex(16)).encode()


@dataclass
class LocalStorage:
    """Files on disk, served by the API itself. Development and tests.

    URLs are signed and time-limited, the same shape as a presigned S3 URL.
    This is not decoration: a ``<video src=...>`` fetch cannot carry an
    Authorization header, so an unsigned local URL is simply unplayable in a
    browser -- which is exactly the bug that made a served video 401 while the
    API happily handed out its address.
    """

    root: Path
    base_url: str = "/artifacts"
    ttl: int = DEFAULT_URL_TTL
    secret: bytes | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.secret is None:
            self.secret = _url_secret()

    def sign(self, key: str, expires: int) -> str:
        return hmac.new(
            self.secret or b"", f"{key}:{expires}".encode(), hashlib.sha256
        ).hexdigest()

    def verify(self, key: str, expires: int | None, signature: str | None,
               *, now: int | None = None) -> bool:
        if not expires or not signature:
            return False
        current = int(time.time()) if now is None else now
        if expires < current:
            return False
        return hmac.compare_digest(self.sign(key, expires), signature)

    def _path(self, key: str) -> Path:
        # Resolve and confirm containment: a key arrives from a URL, and
        # "../../etc/passwd" must not become a file read.
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ValueError(f"key escapes the storage root: {key!r}")
        return candidate

    def put(self, key: str, path: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(path).replace(target)

    def url(self, key: str, *, expires_in: int = DEFAULT_URL_TTL) -> str:
        expires = int(time.time()) + expires_in
        signature = self.sign(key, expires)
        return f"{self.base_url}/{key}?expires={expires}&sig={signature}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def read(self, key: str) -> bytes | None:
        try:
            path = self._path(key)
        except ValueError:
            return None
        return path.read_bytes() if path.is_file() else None


class S3Storage:
    """R2 (or any S3-compatible store) with presigned downloads."""

    def __init__(self, bucket: str, *, endpoint_url: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None,
                 region: str = "auto"):
        if not bucket:
            raise ValueError("a bucket name is required")
        import boto3

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put(self, key: str, path: Path) -> None:
        self._client.upload_file(str(path), self.bucket, key)

    def url(self, key: str, *, expires_in: int = DEFAULT_URL_TTL) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def read(self, key: str) -> bytes | None:
        # Downloads go straight to the viewer over a presigned URL; the API
        # never proxies video bytes through itself.
        raise NotImplementedError("presigned URLs serve the bytes directly")


def from_env(environ: dict | None = None) -> Storage:
    """Pick a backend from configuration.

    R2 when a bucket is configured, local otherwise, so a checkout runs with
    no setup and a deployment cannot accidentally look like one.
    """
    import os

    env = environ if environ is not None else os.environ
    bucket = env.get("R2_BUCKET", "")
    if bucket:
        return S3Storage(
            bucket,
            endpoint_url=env.get("R2_ENDPOINT") or None,
            access_key=env.get("R2_ACCESS_KEY_ID") or None,
            secret_key=env.get("R2_SECRET_ACCESS_KEY") or None,
            region=env.get("R2_REGION", "auto"),
        )
    return LocalStorage(Path(env.get("PYCODING_ARTIFACTS", "/tmp/pycoding/artifacts")))
