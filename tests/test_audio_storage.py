"""Unit tests for audio storage providers."""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

from src.audio.storage import (
    LocalStorageProvider,
    R2StorageProvider,
    S3StorageProvider,
    StorageError,
    StorageProvider,
    get_storage,
)

_HAS_BOTO3 = importlib.util.find_spec("boto3") is not None


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
    return LocalStorageProvider()


class TestLocalStorage:
    def test_implements_protocol(self, local_storage):
        assert isinstance(local_storage, StorageProvider)

    def test_name(self, local_storage):
        assert local_storage.name == "local"

    def test_upload_and_exists(self, local_storage):
        url = local_storage.upload(b"audio data", "test.mp3")
        assert "/api/v1/audio/files/test.mp3" in url
        assert local_storage.exists("test.mp3")

    def test_upload_creates_subdirectories(self, local_storage):
        local_storage.upload(b"data", "beats/poi/test.mp3")
        assert local_storage.exists("beats/poi/test.mp3")

    def test_delete(self, local_storage):
        local_storage.upload(b"data", "to_delete.mp3")
        assert local_storage.delete("to_delete.mp3")
        assert not local_storage.exists("to_delete.mp3")

    def test_delete_nonexistent(self, local_storage):
        assert not local_storage.delete("nonexistent.mp3")

    def test_exists_false_for_missing(self, local_storage):
        assert not local_storage.exists("nope.mp3")

    def test_readback(self, local_storage):
        local_storage.upload(b"exact bytes", "readback.mp3")
        data = (local_storage.base_path / "readback.mp3").read_bytes()
        assert data == b"exact bytes"


class TestGetStorage:
    def test_default_is_local(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AUDIO_STORAGE", raising=False)
        monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
        s = get_storage()
        assert s.name == "local"

    def test_explicit_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
        s = get_storage("local")
        assert s.name == "local"

    def test_s3_without_bucket_raises(self, monkeypatch):
        monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
        with pytest.raises(StorageError, match="AWS_S3_BUCKET not set"):
            get_storage("s3")

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown storage provider"):
            get_storage("unknown")


@pytest.mark.skipif(
    not _HAS_BOTO3, reason="boto3 not installed; run `uv sync --extra aws` to enable R2 tests"
)
class TestR2Storage:
    """Tests for R2StorageProvider (Cloudflare R2, S3-compatible)."""

    def test_missing_endpoint_url_raises(self, monkeypatch):
        monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        with pytest.raises(StorageError, match="R2_ENDPOINT_URL not set"):
            R2StorageProvider()

    def test_missing_public_url_raises(self, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.delenv("R2_PUBLIC_URL", raising=False)
        with pytest.raises(StorageError, match="R2_PUBLIC_URL not set"):
            R2StorageProvider()

    @patch("boto3.client")
    def test_upload_calls_put_object(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        monkeypatch.setenv("R2_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        url = provider.upload(b"audio data", "paris/eiffel.mp3")

        mock_s3.put_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="paris/eiffel.mp3",
            Body=b"audio data",
            ContentType="audio/mpeg",
        )
        assert url == "https://audio.ondoway.com/paris/eiffel.mp3"

    @patch("boto3.client")
    def test_upload_returns_public_url(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com/")
        monkeypatch.setenv("R2_BUCKET", "ondoway-audio")
        mock_boto_client.return_value = MagicMock()

        provider = R2StorageProvider()
        url = provider.upload(b"data", "test.mp3")

        # Trailing slash on public URL should be normalized
        assert url == "https://audio.ondoway.com/test.mp3"

    @patch("boto3.client")
    def test_exists_calls_head_object(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        monkeypatch.setenv("R2_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        result = provider.exists("test.mp3")

        mock_s3.head_object.assert_called_once_with(Bucket="my-bucket", Key="test.mp3")
        assert result is True

    @patch("boto3.client")
    def test_exists_returns_false_on_error(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("Not found")
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        assert provider.exists("missing.mp3") is False

    @patch("boto3.client")
    def test_delete_calls_delete_object(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        monkeypatch.setenv("R2_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        result = provider.delete("test.mp3")

        mock_s3.delete_object.assert_called_once_with(Bucket="my-bucket", Key="test.mp3")
        assert result is True

    @patch("boto3.client")
    def test_delete_returns_false_on_error(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_s3 = MagicMock()
        mock_s3.delete_object.side_effect = Exception("Error")
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        assert provider.delete("test.mp3") is False

    @patch("boto3.client")
    def test_implements_protocol(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_boto_client.return_value = MagicMock()

        provider = R2StorageProvider()
        assert isinstance(provider, StorageProvider)

    @patch("boto3.client")
    def test_get_storage_r2(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_boto_client.return_value = MagicMock()

        s = get_storage("r2")
        assert s.name == "r2"
        assert isinstance(s, R2StorageProvider)

    @patch("boto3.client")
    def test_read_downloads_the_key_behind_a_public_url(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com/")
        monkeypatch.setenv("R2_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"stored audio")}
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()
        url = provider.upload(b"stored audio", "paris/eiffel.mp3")

        assert provider.read(url) == b"stored audio"
        mock_s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="paris/eiffel.mp3")

    @patch("boto3.client")
    def test_read_refuses_a_url_from_another_origin(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()

        for foreign in (
            "https://evil.example.com/paris/eiffel.mp3",
            "https://audio.ondoway.com.evil.example.com/paris/eiffel.mp3",
            "/api/v1/audio/files/paris/eiffel.mp3",
        ):
            with pytest.raises(StorageError, match="Not an audio URL under"):
                provider.read(foreign)
        mock_s3.get_object.assert_not_called()

    @patch("boto3.client")
    def test_read_turns_a_download_error_into_storage_error(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
        monkeypatch.setenv("R2_PUBLIC_URL", "https://audio.ondoway.com")
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        mock_boto_client.return_value = mock_s3

        provider = R2StorageProvider()

        with pytest.raises(StorageError, match="Could not read") as caught:
            provider.read("https://audio.ondoway.com/missing.mp3")
        assert "NoSuchKey" in str(caught.value.__cause__)


@pytest.mark.skipif(
    not _HAS_BOTO3, reason="boto3 not installed; run `uv sync --extra aws` to enable S3 tests"
)
class TestS3Read:
    """Reading audio back from AWS S3 by the ``s3://`` URL ``upload`` returned."""

    @patch("boto3.client")
    def test_read_downloads_the_key_behind_an_s3_url(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("AWS_S3_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"stored audio")}
        mock_boto_client.return_value = mock_s3

        provider = S3StorageProvider()
        url = provider.upload(b"stored audio", "paris/eiffel.mp3")

        assert url == "s3://my-bucket/paris/eiffel.mp3"
        assert provider.read(url) == b"stored audio"
        mock_s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="paris/eiffel.mp3")

    @patch("boto3.client")
    def test_read_refuses_a_url_from_another_bucket(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("AWS_S3_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        provider = S3StorageProvider()

        for foreign in (
            "s3://other-bucket/paris/eiffel.mp3",
            "s3://my-bucket-2/paris/eiffel.mp3",
            "https://my-bucket.s3.amazonaws.com/paris/eiffel.mp3",
        ):
            with pytest.raises(StorageError, match="Not an audio URL in bucket"):
                provider.read(foreign)
        mock_s3.get_object.assert_not_called()

    @patch("boto3.client")
    def test_read_turns_a_download_error_into_storage_error(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("AWS_S3_BUCKET", "my-bucket")
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("AccessDenied")
        mock_boto_client.return_value = mock_s3

        provider = S3StorageProvider()

        with pytest.raises(StorageError, match="Could not read") as caught:
            provider.read("s3://my-bucket/missing.mp3")
        assert "AccessDenied" in str(caught.value.__cause__)
