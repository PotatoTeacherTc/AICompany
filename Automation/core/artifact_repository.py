import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class ArtifactRepository(ABC):
    @abstractmethod
    def save(self, artifact):
        """Save an artifact metadata record."""

    @abstractmethod
    def get(self, artifact_id):
        """Return one artifact metadata record, or None."""

    @abstractmethod
    def list(self):
        """Return all artifact metadata records."""

    @abstractmethod
    def delete(self, artifact_id):
        """Delete metadata only and return whether it existed."""


class InMemoryArtifactRepository(ArtifactRepository):
    def __init__(self, artifacts=None):
        self._artifacts = {
            artifact["artifact_id"]: dict(artifact)
            for artifact in artifacts or []
            if isinstance(artifact, dict) and artifact.get("artifact_id")
        }

    def save(self, artifact):
        self._artifacts[artifact["artifact_id"]] = dict(artifact)

    def get(self, artifact_id):
        artifact = self._artifacts.get(artifact_id)
        return dict(artifact) if artifact else None

    def list(self):
        return [dict(artifact) for artifact in self._artifacts.values()]

    def delete(self, artifact_id):
        return self._artifacts.pop(artifact_id, None) is not None


class FileArtifactRepository(ArtifactRepository):
    def __init__(self, repository_file, storage_root=None):
        self.repository_file = Path(repository_file)
        self.storage_root = Path(storage_root).resolve() if storage_root else None
        self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        self._artifacts = self._load()

    def save(self, artifact):
        value = dict(artifact)
        if self.storage_root is not None and value.get("path"):
            path = Path(value.pop("path")).resolve()
            if self.storage_root != path and self.storage_root not in path.parents:
                raise ValueError("artifact path escaped storage root")
            value["internal_ref"] = path.relative_to(self.storage_root).as_posix()
        self._artifacts[artifact["artifact_id"]] = value
        temporary_file = self.repository_file.with_suffix(
            self.repository_file.suffix + ".tmp"
        )
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(list(self._artifacts.values()), file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, self.repository_file)

    def get(self, artifact_id):
        artifact = self._artifacts.get(artifact_id)
        return self._safe_value(artifact)

    def list(self):
        return [
            value for value in (
                self._safe_value(artifact) for artifact in self._artifacts.values()
            ) if value is not None
        ]

    def delete(self, artifact_id):
        existed = self._artifacts.pop(artifact_id, None) is not None
        if existed:
            self._flush()
        return existed

    def _flush(self):
        temporary_file = self.repository_file.with_suffix(
            self.repository_file.suffix + ".tmp"
        )
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(list(self._artifacts.values()), file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, self.repository_file)

    def _load(self):
        if not self.repository_file.exists():
            return {}
        try:
            with self.repository_file.open("r", encoding="utf-8") as file:
                artifacts = json.load(file)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(artifacts, list):
            return {}
        return {
            artifact["artifact_id"]: dict(artifact)
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("artifact_id")
        }

    def _safe_value(self, artifact):
        if artifact is None:
            return None
        value = dict(artifact)
        if self.storage_root is None:
            return value
        internal_ref = value.get("internal_ref")
        if not isinstance(internal_ref, str):
            return None
        path = (self.storage_root / internal_ref).resolve()
        if self.storage_root != path and self.storage_root not in path.parents:
            return None
        if not path.is_file():
            value["status"] = "MISSING"
        return value
