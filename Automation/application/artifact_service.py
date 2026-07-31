import hashlib
import json
from pathlib import Path
import re

from core.persistence import sanitize_for_read


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ALLOWED_CONTENT_TYPES = {"TEXT", "JSON"}
_MAX_CONTENT_BYTES = 1024 * 1024


class ArtifactApplicationService:
    """Workspace-scoped safe DTO and bounded text-content access."""

    def __init__(self, artifact_manager, entitlement_checker=None):
        self.artifact_manager = artifact_manager
        self.entitlement_checker = entitlement_checker

    def list(
        self,
        workspace_id,
        *,
        artifact_type=None,
        mission_id=None,
        task_id=None,
        status=None,
        limit=50,
        offset=0,
    ):
        self._identifier(workspace_id)
        self._pagination(limit, offset)
        if artifact_type is not None:
            self._identifier(artifact_type)
        if mission_id is not None:
            self._identifier(mission_id)
        if task_id is not None:
            self._identifier(task_id)
        if status is not None:
            self._identifier(status)
        values = [
            artifact
            for artifact in self.artifact_manager.list(workspace_id)
            if (artifact_type is None or artifact.get("artifact_type") == artifact_type)
            and (mission_id is None or artifact.get("mission_id") == mission_id)
            and (task_id is None or artifact.get("task_id") == task_id)
            and (status is None or artifact.get("status") == status)
        ]
        values.sort(
            key=lambda artifact: (
                artifact.get("created_at") or "",
                artifact.get("artifact_id") or "",
            ),
            reverse=True,
        )
        return {
            "items": [self._safe_dto(item) for item in values[offset:offset + limit]],
            "total": len(values),
            "limit": limit,
            "offset": offset,
        }

    def get(self, workspace_id, artifact_id):
        self._identifier(workspace_id)
        self._identifier(artifact_id)
        artifact = self.artifact_manager.get(artifact_id, workspace_id)
        return self._safe_dto(artifact) if artifact else None

    def archive(self, workspace_id, artifact_id):
        if (
            self.entitlement_checker is not None
            and not self.entitlement_checker(
                workspace_id, "artifact_archive_enabled"
            )
        ):
            raise ValueError("entitlement_denied")
        return self._change_status(workspace_id, artifact_id, archive=True)

    def restore(self, workspace_id, artifact_id):
        return self._change_status(workspace_id, artifact_id, archive=False)

    def _change_status(self, workspace_id, artifact_id, *, archive):
        self._identifier(workspace_id)
        self._identifier(artifact_id)
        operation = (
            self.artifact_manager.archive
            if archive
            else self.artifact_manager.restore
        )
        artifact = operation(artifact_id, workspace_id)
        return self._safe_dto(artifact) if artifact else None

    def content(self, workspace_id, artifact_id):
        self._identifier(workspace_id)
        self._identifier(artifact_id)
        artifact = self.artifact_manager.get(artifact_id, workspace_id)
        if artifact is None:
            return None
        if artifact.get("status") == "MISSING":
            return {"status": "MISSING"}
        if artifact.get("artifact_type") not in _ALLOWED_CONTENT_TYPES:
            raise ValueError("unsupported_content_type")
        path = self._resolve_internal_path(artifact)
        try:
            size = path.stat().st_size
            if size > _MAX_CONTENT_BYTES:
                raise ValueError("content_too_large")
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            content = (
                sanitize_for_read(json.loads(text))
                if artifact.get("mime_type") == "application/json"
                or artifact.get("artifact_type") == "JSON"
                else sanitize_for_read(text)
            )
        except UnicodeDecodeError:
            raise ValueError("unsupported_content_encoding")
        except json.JSONDecodeError:
            raise ValueError("corrupted_content")
        except OSError:
            return {"status": "MISSING"}
        return {
            "status": "AVAILABLE",
            "artifact_id": artifact_id,
            "mime_type": artifact.get("mime_type"),
            "size": len(raw),
            "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            "content": content,
        }

    def _resolve_internal_path(self, artifact):
        repository = self.artifact_manager.repository
        storage_root = getattr(repository, "storage_root", None)
        internal_ref = artifact.get("internal_ref")
        if storage_root is None or not isinstance(internal_ref, str):
            raise ValueError("content_unavailable")
        root = Path(storage_root).resolve()
        path = (root / internal_ref).resolve()
        if path == root or root not in path.parents:
            raise ValueError("invalid_artifact_reference")
        return path

    @staticmethod
    def _safe_dto(artifact):
        fields = (
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
        return sanitize_for_read(
            {field: artifact.get(field) for field in fields if field in artifact}
        )

    @staticmethod
    def _identifier(value):
        if (
            not isinstance(value, str)
            or not value
            or not _IDENTIFIER.fullmatch(value)
        ):
            raise ValueError("invalid_identifier")

    @staticmethod
    def _pagination(limit, offset):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise ValueError("invalid_pagination")
