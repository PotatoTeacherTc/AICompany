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


class FileArtifactRepository(ArtifactRepository):
    def __init__(self, repository_file):
        self.repository_file = Path(repository_file)
        self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        self._artifacts = self._load()

    def save(self, artifact):
        self._artifacts[artifact["artifact_id"]] = dict(artifact)
        temporary_file = self.repository_file.with_suffix(
            self.repository_file.suffix + ".tmp"
        )
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(list(self._artifacts.values()), file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, self.repository_file)

    def get(self, artifact_id):
        artifact = self._artifacts.get(artifact_id)
        return dict(artifact) if artifact else None

    def list(self):
        return [dict(artifact) for artifact in self._artifacts.values()]

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
