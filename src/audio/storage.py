"""Audio storage — upload generated audio and return accessible URLs.

Three implementations:
- LocalStorageProvider: saves to a local directory (development)
- S3StorageProvider: uploads to AWS S3 (production, requires boto3)
- R2StorageProvider: uploads to Cloudflare R2 (production, zero egress fees)

Usage:
    storage = get_storage()
    url = storage.upload(audio_bytes, key="eiffel_tower.mp3")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """Raised when audio storage fails."""


@runtime_checkable
class StorageProvider(Protocol):
    """Interface for audio file storage backends."""

    @property
    def name(self) -> str: ...

    def upload(self, data: bytes, key: str) -> str:
        """Upload audio bytes and return a URL/path to access them."""
        ...

    def exists(self, key: str) -> bool:
        """Check if a file exists at the given key."""
        ...

    def delete(self, key: str) -> bool:
        """Delete a file. Returns True if it existed."""
        ...

    def read(self, url: str) -> bytes:
        """The bytes of a file this provider stored, given the URL ``upload`` returned."""
        ...


class LocalStorageProvider:
    """Store audio files on the local filesystem.

    Files are saved under AUDIO_STORAGE_PATH (default: ./audio_store/).
    URLs are returned as /api/v1/audio/files/{key} for serving via the API.
    """

    def __init__(self) -> None:
        self._base = Path(os.getenv("AUDIO_STORAGE_PATH", "audio_store")).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "local"

    @property
    def base_path(self) -> Path:
        return self._base

    def upload(self, data: bytes, key: str) -> str:
        # Reject null bytes (could bypass path checks on some OSes)
        if "\x00" in key:
            raise StorageError("Invalid storage key: null bytes not allowed")

        filepath = self._base / key
        # Prevent path traversal — resolved path must stay under base
        resolved_base = self._base.resolve()
        resolved_path = filepath.resolve()
        if (
            not str(resolved_path).startswith(str(resolved_base) + os.sep)
            and resolved_path != resolved_base
        ):
            raise StorageError("Invalid storage key: path traversal detected")

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(data)
        return f"/api/v1/audio/files/{key}"

    def exists(self, key: str) -> bool:
        return (self._base / key).exists()

    def read(self, url: str) -> bytes:
        prefix = "/api/v1/audio/files/"
        if not url.startswith(prefix):
            raise StorageError(f"Not a local audio URL: {url!r}")
        filepath = (self._base / url[len(prefix) :]).resolve()
        if not str(filepath).startswith(str(self._base.resolve()) + os.sep):
            raise StorageError("Invalid storage key: path traversal detected")
        try:
            return filepath.read_bytes()
        except OSError as exc:
            raise StorageError(f"Could not read {url!r}: {exc}") from exc

    def delete(self, key: str) -> bool:
        filepath = self._base / key
        if filepath.exists():
            filepath.unlink()
            return True
        return False


class S3StorageProvider:
    """Upload audio files to AWS S3.

    Requires boto3 and AWS credentials (via env vars or IAM role).
    Set AWS_S3_BUCKET env var.
    """

    def __init__(self) -> None:
        self._bucket = os.getenv("AWS_S3_BUCKET")
        if not self._bucket:
            raise StorageError("AWS_S3_BUCKET not set")
        try:
            import boto3

            self._s3 = boto3.client("s3")
        except ImportError as exc:
            raise StorageError("boto3 not installed — run: pip install boto3") from exc

    @property
    def name(self) -> str:
        return "s3"

    def upload(self, data: bytes, key: str) -> str:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType="audio/mpeg",
        )
        return f"s3://{self._bucket}/{key}"

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._s3.exceptions.ClientError:
            return False

    def read(self, url: str) -> bytes:
        prefix = f"s3://{self._bucket}/"
        if not url.startswith(prefix):
            raise StorageError(f"Not an audio URL in bucket {self._bucket!r}: {url!r}")
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=url[len(prefix) :])["Body"].read()
        except Exception as exc:
            raise StorageError(f"Could not read {url!r}: {exc}") from exc

    def delete(self, key: str) -> bool:
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False


class R2StorageProvider:
    """Upload audio to Cloudflare R2 (S3-compatible).

    Returns public URLs for direct client streaming (zero egress fees).
    Requires: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_URL.
    """

    def __init__(self) -> None:
        self._endpoint_url = os.getenv("R2_ENDPOINT_URL")
        self._bucket = os.getenv("R2_BUCKET", "ondoway-audio")
        self._public_url = os.getenv("R2_PUBLIC_URL")
        if not self._endpoint_url:
            raise StorageError("R2_ENDPOINT_URL not set")
        if not self._public_url:
            raise StorageError("R2_PUBLIC_URL not set")
        try:
            import boto3

            self._s3 = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            )
        except ImportError as exc:
            raise StorageError("boto3 not installed — run: pip install boto3") from exc

    @property
    def name(self) -> str:
        return "r2"

    def upload(self, data: bytes, key: str) -> str:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType="audio/mpeg",
        )
        # Return public URL for direct client access (R2 has zero egress fees)
        public_base = self._public_url.rstrip("/")
        return f"{public_base}/{key}"

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def read(self, url: str) -> bytes:
        prefix = self._public_url.rstrip("/") + "/"
        if not url.startswith(prefix):
            raise StorageError(f"Not an audio URL under {prefix!r}: {url!r}")
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=url[len(prefix) :])["Body"].read()
        except Exception as exc:
            raise StorageError(f"Could not read {url!r}: {exc}") from exc

    def delete(self, key: str) -> bool:
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False


def get_storage(name: str | None = None) -> StorageProvider:
    """Return a storage provider by name.

    Falls back to AUDIO_STORAGE env var, then to 'local'.
    """
    storage_name = name or os.getenv("AUDIO_STORAGE", "local")
    if storage_name == "s3":
        return S3StorageProvider()
    if storage_name == "r2":
        return R2StorageProvider()
    if storage_name == "local":
        return LocalStorageProvider()
    raise ValueError(f"Unknown storage provider: '{storage_name}'. Available: local, s3, r2")
