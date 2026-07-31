from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path, PurePosixPath


class StorageProvider(ABC):
    @abstractmethod
    def put(self, key, content):
        pass

    @abstractmethod
    def get(self, key):
        pass

    @abstractmethod
    def exists(self, key):
        pass

    def health(self):
        return {"ok": True, "backend": self.__class__.__name__}


class LocalStorageProvider(StorageProvider):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key, content):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_bytes(content))
        return _reference(key)

    def get(self, key):
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def exists(self, key):
        return self._path(key).is_file()

    def _path(self, key):
        safe = _key(key)
        path = (self.root / safe).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("storage_path_escape")
        return path


class FakeS3StorageProvider(StorageProvider):
    """In-memory S3-shaped provider; performs no network operation."""

    def __init__(self, bucket="fake-artifacts"):
        if not isinstance(bucket, str) or not bucket:
            raise ValueError("invalid_bucket")
        self.bucket = bucket
        self.objects = {}

    def put(self, key, content):
        safe = _key(key)
        self.objects[safe] = _bytes(content)
        return f"s3-fake://{self.bucket}/{safe}"

    def get(self, key):
        return self.objects.get(_key(key))

    def exists(self, key):
        return _key(key) in self.objects


class SignedUrlService:
    """Provider-neutral, opaque access-reference contract."""

    def __init__(self, signing_key, clock=None):
        if not isinstance(signing_key, bytes) or len(signing_key) < 16:
            raise ValueError("invalid_storage_signing_key")
        self.signing_key = signing_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, workspace_id, storage_key, expires_in=300):
        if not isinstance(expires_in, int) or not 1 <= expires_in <= 3600:
            raise ValueError("invalid_expiration")
        safe = _key(storage_key)
        expires_at = self.clock() + timedelta(seconds=expires_in)
        timestamp = int(expires_at.timestamp())
        message = f"{workspace_id}:{safe}:{timestamp}".encode()
        signature = hmac.new(
            self.signing_key, message, hashlib.sha256
        ).hexdigest()
        return {
            "url": f"storage://{safe}?expires={timestamp}&signature={signature}",
            "expires_at": expires_at.isoformat(),
        }


class ArtifactStorageAdapter:
    """Stores bytes and persists only safe Artifact metadata."""

    def __init__(self, storage, artifact_repository):
        self.storage = storage
        self.artifacts = artifact_repository

    def store(
        self, workspace_id, artifact_id, filename, content,
        *, artifact_type="BINARY", mime_type="application/octet-stream",
        mission_id=None, task_id=None, stage=None, producer_pipeline=None,
    ):
        for value in (workspace_id, artifact_id, filename):
            if not isinstance(value, str) or not value:
                raise ValueError("invalid_artifact_storage_request")
        safe_name = Path(filename).name
        if safe_name != filename or safe_name in {".", ".."}:
            raise ValueError("invalid_filename")
        key = f"{workspace_id}/{artifact_id}/{safe_name}"
        data = _bytes(content)
        self.storage.put(key, data)
        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "filename": safe_name,
            "artifact_type": artifact_type,
            "mime_type": mime_type,
            "size": len(data),
            "mission_id": mission_id,
            "task_id": task_id,
            "stage": stage,
            "producer_pipeline": producer_pipeline,
            "status": "AVAILABLE",
            "created_at": now,
            "updated_at": now,
            "internal_ref": key,
        }
        self.artifacts.save(metadata)
        return dict(metadata)

    def read(self, workspace_id, artifact_id):
        artifact = self.artifacts.get(artifact_id, workspace_id)
        if artifact is None or artifact.get("workspace_id") != workspace_id:
            return None
        key = artifact.get("internal_ref")
        return self.storage.get(key) if isinstance(key, str) else None

    def health(self):
        try:
            value = self.storage.health()
            return value if isinstance(value, dict) else {"ok": bool(value)}
        except Exception:
            return {"ok": False}


class StorageFactory:
    @staticmethod
    def create(provider, *, root=None, bucket=None):
        name = str(provider).lower()
        if name == "local":
            if root is None:
                raise ValueError("storage_root_required")
            return LocalStorageProvider(root)
        if name == "fake_s3":
            return FakeS3StorageProvider(bucket or "fake-artifacts")
        raise ValueError("unsupported_storage_provider")


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_storage_key")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
        raise ValueError("storage_path_escape")
    return path.as_posix()


def _reference(key):
    return f"local-storage://{_key(key)}"


def _bytes(value):
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        return value
    raise TypeError("storage_content_must_be_bytes")
