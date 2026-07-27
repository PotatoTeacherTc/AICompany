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
    )

    def __init__(self, repository=None):
        self.repository = repository or InMemoryArtifactRepository()

    def register_file(
        self,
        file_path,
        artifact_type,
        producer_pipeline,
        workspace_id=None,
    ):
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError("Artifact file does not exist")
        if not isinstance(artifact_type, str) or not artifact_type:
            raise ValueError("artifact_type must be a non-empty string")
        if not isinstance(producer_pipeline, str) or not producer_pipeline:
            raise ValueError("producer_pipeline must be a non-empty string")

        artifact = {
            "artifact_id": uuid.uuid4().hex,
            "artifact_type": artifact_type,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "filename": path.name,
            "size": path.stat().st_size,
            "created_at": datetime.now().isoformat(),
            "producer_pipeline": producer_pipeline,
            "workspace_id": workspace_id,
            "path": str(path),
        }
        self.repository.save(artifact)
        return artifact

    def get(self, artifact_id):
        return self.repository.get(artifact_id)

    def list(self):
        return self.repository.list()

    def register_files(self, file_paths, artifact_type, producer_pipeline):
        return [
            self.register_file(path, artifact_type, producer_pipeline)
            for path in file_paths
        ]
