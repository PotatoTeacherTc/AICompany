from datetime import datetime
import mimetypes
from pathlib import Path
import uuid

from core.artifact_repository import InMemoryArtifactRepository


class ArtifactManager:
    METADATA_FIELDS = (
        "artifact_id",
        "artifact_type",
        "mime_type",
        "filename",
        "size",
        "created_at",
        "producer_pipeline",
        "workspace_id",
        "mission_id",
        "task_id",
        "stage",
        "status",
        "updated_at",
    )

    def __init__(self, repository=None, storage_adapter=None):
        self.repository = repository or InMemoryArtifactRepository()
        self.storage_adapter = storage_adapter

    def register_file(
        self,
        file_path,
        artifact_type,
        producer_pipeline,
        workspace_id=None,
        mission_id=None,
        task_id=None,
        stage=None,
        status="AVAILABLE",
        metadata=None,
    ):
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError("Artifact file does not exist")
        if not isinstance(artifact_type, str) or not artifact_type:
            raise ValueError("artifact_type must be a non-empty string")
        if not isinstance(producer_pipeline, str) or not producer_pipeline:
            raise ValueError("producer_pipeline must be a non-empty string")
        metadata = self._metadata(metadata)

        if self.storage_adapter is not None:
            artifact = self.storage_adapter.store(
                workspace_id or "default", uuid.uuid4().hex, path.name,
                path.read_bytes(), artifact_type=artifact_type,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                mission_id=mission_id, task_id=task_id,
                stage=stage or producer_pipeline, producer_pipeline=producer_pipeline,
            )
            if metadata:
                artifact["metadata"] = metadata
                self.repository.save(artifact)
            return artifact
        created_at = datetime.now().isoformat()
        artifact = {
            "artifact_id": uuid.uuid4().hex,
            "artifact_type": artifact_type,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "filename": path.name,
            "size": path.stat().st_size,
            "created_at": created_at,
            "producer_pipeline": producer_pipeline,
            "workspace_id": workspace_id or "default",
            "mission_id": mission_id,
            "task_id": task_id,
            "stage": stage or producer_pipeline,
            "status": status,
            "updated_at": created_at,
            "path": str(path),
        }
        if metadata:
            artifact["metadata"] = metadata
        self.repository.save(artifact)
        return artifact

    @staticmethod
    def _metadata(value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("artifact metadata must be a dictionary")
        allowed = {
            "provider", "model", "prompt_version", "source_request_id",
            "schema_version", "extension", "detected_format", "duration_seconds",
            "audio_codec", "sample_rate", "channels", "checksum_sha256",
            "imported_at", "content_project_id", "music_project_id",
            "source_audio_artifact_id", "prompt_hash", "workflow_version",
            "purpose", "width", "height", "seed", "steps", "guidance",
            "blog_package_id", "source_brief_artifact_id",
            "image_manifest_artifact_id",
        }
        if set(value) - allowed:
            raise ValueError("artifact metadata contains unsupported fields")
        clean = {}
        for key, item in value.items():
            if item is not None:
                if (
                    not isinstance(item, (str, int, float))
                    or isinstance(item, bool)
                    or (isinstance(item, str) and (not item.strip() or len(item) > 128))
                    or (isinstance(item, (int, float)) and item < 0)
                ):
                    raise ValueError("artifact metadata value is invalid")
                clean[key] = item.strip() if isinstance(item, str) else item
        return clean

    def get(self, artifact_id, workspace_id=None):
        artifact = self.repository.get(artifact_id, workspace_id)
        if artifact and self.storage_adapter is not None:
            key = artifact.get("internal_ref")
            if not isinstance(key, str) or not self.storage_adapter.storage.exists(key):
                artifact["status"] = "MISSING"
        return artifact if artifact and (workspace_id is None or artifact.get("workspace_id", "default") == workspace_id) else None

    def list(self, workspace_id=None):
        artifacts = self.repository.list(workspace_id)
        return artifacts if workspace_id is None else [artifact for artifact in artifacts if artifact.get("workspace_id", "default") == workspace_id]

    def find(self, workspace_id, mission_id=None, artifact_id=None):
        values = self.list(workspace_id)
        return [
            artifact for artifact in values
            if (mission_id is None or artifact.get("mission_id") == mission_id)
            and (artifact_id is None or artifact.get("artifact_id") == artifact_id)
        ]

    def delete_metadata(self, artifact_id, workspace_id):
        artifact = self.get(artifact_id, workspace_id)
        if artifact is None:
            return False
        return self.repository.delete(artifact_id, workspace_id)

    def discard_managed_artifact(self, artifact_id, workspace_id):
        """Remove only an Artifact copy managed by the configured storage adapter."""
        if self.storage_adapter is None:
            return False
        return self.storage_adapter.discard(workspace_id, artifact_id)

    def archive(self, artifact_id, workspace_id):
        return self._transition(artifact_id, workspace_id, "ARCHIVED")

    def restore(self, artifact_id, workspace_id):
        return self._transition(artifact_id, workspace_id, "AVAILABLE")

    def _transition(self, artifact_id, workspace_id, target_status):
        artifact = self.get(artifact_id, workspace_id)
        if artifact is None:
            return None
        current_status = artifact.get("status", "AVAILABLE")
        if current_status == "MISSING":
            raise ValueError("artifact_missing")
        if current_status == target_status:
            return artifact
        artifact["status"] = target_status
        artifact["updated_at"] = datetime.now().isoformat()
        self.repository.save(artifact)
        return self.get(artifact_id, workspace_id)

    def register_files(
        self,
        file_paths,
        artifact_type,
        producer_pipeline,
        workspace_id=None,
        mission_id=None,
        task_id=None,
        stage=None,
    ):
        return [
            self.register_file(
                path,
                artifact_type,
                producer_pipeline,
                workspace_id=workspace_id,
                mission_id=mission_id,
                task_id=task_id,
                stage=stage,
            )
            for path in file_paths
        ]
