"""Object storage abstraction: local filesystem (default, true no-op) or S3
(opt-in via STORAGE_BACKEND=s3), so pipeline artifacts can optionally survive
a container redeploy without a mounted volume."""

import logging
import os
from abc import ABC, abstractmethod

import config

_LOG = logging.getLogger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    def upload_file(self, local_path: str, key: str) -> None: ...

    @abstractmethod
    def download_file(self, key: str, local_path: str) -> bool:
        """True if an object was fetched and written to local_path; False if
        the key does not exist remotely (a normal, non-error outcome)."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def sync_dir_up(
        self, local_dir: str, prefix: str, exclude: set[str] | None = None
    ) -> None:
        """Upload every regular file under local_dir (recursive), keyed by
        f"{prefix}/{relative_path}". `exclude` matches the file's basename."""
        ...

    @abstractmethod
    def sync_dir_down(self, prefix: str, local_dir: str) -> None:
        """Recovery-only: no-op if local_dir already contains any file (local
        disk is trusted once it has anything). Otherwise download every
        object under prefix into local_dir."""
        ...


class LocalBackend(StorageBackend):
    """The default backend. Every method is a true no-op — never imports
    boto3, never touches the network. This is what keeps CLI mode and the
    test suite AWS-free unless S3 is explicitly configured."""

    def upload_file(self, local_path: str, key: str) -> None:
        return None

    def download_file(self, key: str, local_path: str) -> bool:
        return False

    def delete(self, key: str) -> None:
        return None

    def sync_dir_up(
        self, local_dir: str, prefix: str, exclude: set[str] | None = None
    ) -> None:
        return None

    def sync_dir_down(self, prefix: str, local_dir: str) -> None:
        return None


class S3Backend(StorageBackend):
    """Best-effort: every method logs and swallows failures rather than
    raising — a network blip must not break a pipeline run."""

    def __init__(self, bucket: str | None, region: str | None, prefix: str = ""):
        import boto3

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = (
            boto3.client("s3", region_name=region) if region else boto3.client("s3")
        )

    def _key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def upload_file(self, local_path: str, key: str) -> None:
        try:
            self._client.upload_file(local_path, self._bucket, self._key(key))
        except Exception as e:
            _LOG.warning(f"S3 upload failed for {key}: {e}")

    def download_file(self, key: str, local_path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.download_file(self._bucket, self._key(key), local_path)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in ("404", "NoSuchKey"):
                _LOG.warning(f"S3 download failed for {key}: {e}")
            return False
        except Exception as e:
            _LOG.warning(f"S3 download failed for {key}: {e}")
            return False

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(key))
        except Exception as e:
            _LOG.warning(f"S3 delete failed for {key}: {e}")

    def sync_dir_up(
        self, local_dir: str, prefix: str, exclude: set[str] | None = None
    ) -> None:
        exclude = exclude or set()
        prefix = prefix.rstrip("/")
        if not os.path.isdir(local_dir):
            return
        for root, _dirs, files in os.walk(local_dir):
            for filename in files:
                if filename in exclude:
                    continue
                local_path = os.path.join(root, filename)
                rel_path = os.path.relpath(local_path, local_dir)
                self.upload_file(local_path, f"{prefix}/{rel_path}")

    def sync_dir_down(self, prefix: str, local_dir: str) -> None:
        if os.path.isdir(local_dir) and os.listdir(local_dir):
            return
        prefix = prefix.rstrip("/")
        os.makedirs(local_dir, exist_ok=True)
        remote_prefix = self._key(prefix)
        try:
            resp = self._client.list_objects_v2(
                Bucket=self._bucket, Prefix=f"{remote_prefix}/"
            )
        except Exception as e:
            _LOG.warning(f"S3 list failed for prefix {prefix}: {e}")
            return
        for obj in resp.get("Contents", []):
            rel_key = obj["Key"][len(remote_prefix) :].lstrip("/")
            if not rel_key:
                continue
            local_path = os.path.join(local_dir, rel_key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.download_file(f"{prefix}/{rel_key}", local_path)


def get_storage_backend() -> StorageBackend:
    if config.STORAGE_BACKEND == "s3":
        return S3Backend(config.S3_BUCKET, config.S3_REGION, config.S3_PREFIX)
    return LocalBackend()
