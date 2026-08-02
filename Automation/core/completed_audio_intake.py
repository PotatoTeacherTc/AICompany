from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import subprocess

from core.artifact_manager import ArtifactManager
from core.persistence import StateRepository
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task


SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_AUDIO_NAME = re.compile(r"^[^/\\:]+$")
MUSIC_AUDIO_LINK_KIND = "music_audio_link"


class AudioIntakeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"AudioIntakeError: {code}")


@dataclass(frozen=True)
class AudioProbeResult:
    detected_format: str
    duration_seconds: float
    audio_codec: str
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True)
class AudioInputMetadata:
    source_filename: str
    extension: str
    mime_type: str
    file_size_bytes: int
    detected_format: str
    duration_seconds: float
    audio_codec: str
    sample_rate: int | None
    channels: int | None
    checksum_sha256: str
    imported_at: str


@dataclass(frozen=True)
class MusicProjectAudioLink:
    project_id: str
    workspace_id: str
    audio_artifact_id: str
    source_filename: str
    detected_format: str
    duration_seconds: float
    checksum_sha256: str
    linked_at: str
    status: str
    next_action: str

    def to_dict(self):
        return asdict(self)


class AudioInputLocator:
    """Resolves names only inside one Workspace's dedicated input boundary."""

    def __init__(self, input_root):
        self.root = Path(input_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def workspace_directory(self, workspace_id, create=False):
        _identifier(workspace_id, "workspace_id")
        directory = (self.root / workspace_id / "music").resolve()
        if self.root != directory and self.root not in directory.parents:
            raise AudioIntakeError("ACCESS_DENIED")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def locate(self, workspace_id, audio_name):
        directory = self.workspace_directory(workspace_id)
        name = _audio_name(audio_name)
        requested = Path(name)
        if requested.suffix and requested.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise AudioIntakeError("UNSUPPORTED_FORMAT")
        try:
            candidates = [path for path in directory.iterdir() if path.is_file()]
        except (FileNotFoundError, OSError):
            raise AudioIntakeError("NOT_FOUND") from None
        matches = []
        for candidate in candidates:
            if candidate.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                continue
            matches_name = (
                candidate.name.casefold() == requested.name.casefold()
                if requested.suffix
                else candidate.stem.casefold() == requested.name.casefold()
            )
            if matches_name:
                self._inside(directory, candidate)
                matches.append(candidate)
        if not matches:
            raise AudioIntakeError("NOT_FOUND")
        if len(matches) > 1:
            raise AudioIntakeError("AMBIGUOUS_INPUT")
        return matches[0]

    @staticmethod
    def _inside(directory, candidate):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise AudioIntakeError("ACCESS_DENIED") from None
        if candidate.is_symlink() or (directory != resolved and directory not in resolved.parents):
            raise AudioIntakeError("ACCESS_DENIED")


class FfprobeMediaProbe:
    def __init__(self, executable="ffprobe", timeout_seconds=10, runner=None):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.runner = runner or subprocess.run
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("probe timeout must be positive")

    def probe(self, path):
        try:
            completed = self.runner(
                [self.executable, "-v", "error", "-show_entries",
                 "format=format_name,duration:stream=codec_type,codec_name,sample_rate,channels",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AudioIntakeError("PROBE_TIMEOUT") from None
        except (FileNotFoundError, OSError):
            raise AudioIntakeError("PROBE_UNAVAILABLE") from None
        if completed.returncode != 0:
            raise AudioIntakeError("CORRUPT_AUDIO")
        try:
            value = json.loads(completed.stdout)
            stream = next(
                item for item in value.get("streams", [])
                if isinstance(item, dict) and item.get("codec_type") == "audio"
            )
            format_value = value["format"]
            duration = float(format_value["duration"])
            detected = str(format_value["format_name"]).split(",")[0]
            codec = str(stream["codec_name"])
            sample_rate = _optional_positive_int(stream.get("sample_rate"))
            channels = _optional_positive_int(stream.get("channels"))
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
            raise AudioIntakeError("CORRUPT_AUDIO") from None
        if duration <= 0 or not detected or not codec:
            raise AudioIntakeError("CORRUPT_AUDIO")
        return AudioProbeResult(detected, duration, codec, sample_rate, channels)


class AudioInputValidator:
    def __init__(self, probe=None, maximum_bytes=250 * 1024 * 1024):
        self.probe = probe or FfprobeMediaProbe()
        self.maximum_bytes = maximum_bytes
        if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
            raise ValueError("maximum audio size must be positive")

    def validate(self, path):
        path = Path(path)
        try:
            if path.is_symlink() or not path.is_file():
                raise AudioIntakeError("ACCESS_DENIED")
            size = path.stat().st_size
            extension = path.suffix.lower()
            if extension not in SUPPORTED_AUDIO_EXTENSIONS:
                raise AudioIntakeError("UNSUPPORTED_FORMAT")
            if size == 0:
                raise AudioIntakeError("EMPTY_FILE")
            if size > self.maximum_bytes:
                raise AudioIntakeError("FILE_TOO_LARGE")
            with path.open("rb") as stream:
                signature = stream.read(16)
            if not _valid_signature(extension, signature):
                raise AudioIntakeError("FORMAT_MISMATCH")
            probe = self.probe.probe(path)
            if not _format_matches(extension, probe.detected_format):
                raise AudioIntakeError("FORMAT_MISMATCH")
            checksum = _checksum(path)
        except AudioIntakeError:
            raise
        except OSError:
            raise AudioIntakeError("READ_FAILED") from None
        return AudioInputMetadata(
            source_filename=path.name,
            extension=extension,
            mime_type=mimetypes.guess_type(path.name)[0] or "audio/octet-stream",
            file_size_bytes=size,
            detected_format=probe.detected_format,
            duration_seconds=round(probe.duration_seconds, 3),
            audio_codec=probe.audio_codec,
            sample_rate=probe.sample_rate,
            channels=probe.channels,
            checksum_sha256=checksum,
            imported_at=datetime.now(timezone.utc).isoformat(),
        )


class MusicProjectAudioLinkService:
    def __init__(self, locator, validator, artifact_manager, state_repository,
                 execution_history, logger=None):
        if not isinstance(locator, AudioInputLocator):
            raise TypeError("locator must use AudioInputLocator")
        if not isinstance(validator, AudioInputValidator):
            raise TypeError("validator must use AudioInputValidator")
        if not isinstance(artifact_manager, ArtifactManager):
            raise TypeError("artifact_manager must use ArtifactManager")
        if not isinstance(state_repository, StateRepository):
            raise TypeError("state_repository must implement StateRepository")
        self.locator = locator
        self.validator = validator
        self.artifacts = artifact_manager
        self.states = state_repository
        self.history = execution_history
        self.logger = logger

    def import_audio(self, workspace_id, project_id, audio_name, correlation_id=None):
        task = _task(workspace_id, project_id)
        previous_status = PipelineStatus.WAITING_FOR_INPUT
        try:
            _identifier(workspace_id, "workspace_id")
            _identifier(project_id, "project_id")
            if correlation_id is not None:
                _identifier(correlation_id, "correlation_id")
            self._project_waiting(workspace_id, project_id)
            existing_link = self.states.get(MUSIC_AUDIO_LINK_KIND, project_id, workspace_id)
            if existing_link is not None:
                previous_status = existing_link.get("status", PipelineStatus.INPUT_READY)
                raise AudioIntakeError("AUDIO_ALREADY_LINKED")
            safe_log(self.logger, "AUDIO_INPUT_DISCOVERY_STARTED", "MusicProjectAudioLinkService",
                     workspace_id=workspace_id, mission_id=project_id,
                     execution_id=correlation_id, status=previous_status)
            source = self.locator.locate(workspace_id, audio_name)
            before = _checksum(source)
            metadata = self.validator.validate(source)
            if before != metadata.checksum_sha256:
                raise AudioIntakeError("SOURCE_CHANGED")
            if self._checksum_exists(workspace_id, metadata.checksum_sha256):
                raise AudioIntakeError("DUPLICATE_AUDIO")
            artifact = self.artifacts.register_file(
                source, "MUSIC_SOURCE_AUDIO", "Completed Audio Intake",
                workspace_id=workspace_id, mission_id=project_id,
                task_id=project_id, stage="AUDIO_INPUT",
                metadata={
                    "source_request_id": project_id,
                    "schema_version": "1.0",
                    "extension": metadata.extension,
                    "detected_format": metadata.detected_format,
                    "duration_seconds": metadata.duration_seconds,
                    "audio_codec": metadata.audio_codec,
                    "sample_rate": metadata.sample_rate,
                    "channels": metadata.channels,
                    "checksum_sha256": metadata.checksum_sha256,
                    "imported_at": metadata.imported_at,
                },
            )
            if _checksum(source) != before:
                self.artifacts.delete_metadata(artifact["artifact_id"], workspace_id)
                raise AudioIntakeError("SOURCE_CHANGED")
            linked_at = datetime.now(timezone.utc).isoformat()
            link = MusicProjectAudioLink(
                project_id, workspace_id, artifact["artifact_id"],
                metadata.source_filename, metadata.detected_format,
                metadata.duration_seconds, metadata.checksum_sha256, linked_at,
                PipelineStatus.INPUT_READY,
                "Use this audio Artifact as the input for the approved @4 content brief.",
            )
            try:
                self.states.save(MUSIC_AUDIO_LINK_KIND, project_id, workspace_id, link.to_dict())
            except Exception:
                self.artifacts.discard_managed_artifact(
                    artifact["artifact_id"], workspace_id
                )
                raise AudioIntakeError("PROJECT_LINK_FAILED") from None
            result = self._result(task, link, artifact, previous_status)
            task.mark_input_ready(result)
            self._record(task, result, correlation_id)
            safe_log(self.logger, "AUDIO_INPUT_LINKED", "MusicProjectAudioLinkService",
                     workspace_id=workspace_id, mission_id=project_id,
                     execution_id=correlation_id, status=PipelineStatus.INPUT_READY,
                     metadata={"artifact_id": artifact["artifact_id"]})
            return result
        except Exception as error:
            code = error.code if isinstance(error, AudioIntakeError) else type(error).__name__
            result = PipelineResult(
                PipelineStatus.FAILED, "Completed Audio Intake", "Audio import",
                "MUSIC_AUDIO_INPUT",
                data={"workspace_id": workspace_id if isinstance(workspace_id, str) else None,
                      "mission_id": project_id if isinstance(project_id, str) else None,
                      "previous_status": previous_status, "current_status": previous_status,
                      "task_redacted": True},
                error=f"AudioIntakeError: {code}",
            ).to_dict()
            result["task_id"] = project_id if isinstance(project_id, str) else None
            task.fail(result)
            self._record(task, result, correlation_id)
            safe_log(self.logger, "AUDIO_INPUT_FAILED", "MusicProjectAudioLinkService",
                     level=LogLevel.ERROR,
                     workspace_id=workspace_id if isinstance(workspace_id, str) else None,
                     mission_id=project_id if isinstance(project_id, str) else None,
                     execution_id=correlation_id, status=previous_status,
                     error=f"AudioIntakeError: {code}")
            return result

    def get_link(self, workspace_id, project_id):
        _identifier(workspace_id, "workspace_id")
        _identifier(project_id, "project_id")
        value = self.states.get(MUSIC_AUDIO_LINK_KIND, project_id, workspace_id)
        if not isinstance(value, dict):
            return None
        try:
            return MusicProjectAudioLink(**value)
        except TypeError:
            return None

    def _project_waiting(self, workspace_id, project_id):
        project_artifacts = self.artifacts.find(workspace_id, mission_id=project_id)
        if not any(item.get("artifact_type") == "MUSIC_PLAN" for item in project_artifacts):
            other = self.artifacts.find(None, mission_id=project_id)
            if other:
                raise AudioIntakeError("WORKSPACE_MISMATCH")
            raise AudioIntakeError("PROJECT_NOT_FOUND")
        try:
            records = self.history.query(workspace_id=workspace_id)
        except Exception:
            raise AudioIntakeError("HISTORY_UNAVAILABLE") from None
        record = next((item for item in records if item.get("task_id") == f"{project_id}:MUSIC_PLAN"), None)
        if record is None or record.get("status") != PipelineStatus.WAITING_FOR_INPUT:
            raise AudioIntakeError("PROJECT_NOT_WAITING")

    def _checksum_exists(self, workspace_id, checksum):
        return any(
            artifact.get("artifact_type") == "MUSIC_SOURCE_AUDIO"
            and artifact.get("metadata", {}).get("checksum_sha256") == checksum
            for artifact in self.artifacts.list(workspace_id)
        )

    @staticmethod
    def _result(task, link, artifact, previous_status):
        result = PipelineResult(
            PipelineStatus.INPUT_READY, "Completed Audio Intake", "Audio import",
            "MUSIC_AUDIO_INPUT",
            data={
                "workspace_id": link.workspace_id, "mission_id": link.project_id,
                "project_id": link.project_id,
                "audio_artifact_id": link.audio_artifact_id,
                "source_filename": link.source_filename,
                "detected_format": link.detected_format,
                "duration_seconds": link.duration_seconds,
                "checksum_sha256": link.checksum_sha256,
                "linked_at": link.linked_at, "previous_status": previous_status,
                "current_status": link.status, "next_action": link.next_action,
                "provider_usage": None, "task_redacted": True,
            },
            artifacts=[_safe_artifact(artifact)],
        ).to_dict()
        result["task_id"] = task.id
        return result

    def _record(self, task, result, correlation_id):
        if self.history is None:
            return
        data = result.get("data") or {}
        data["stages"] = {
            "discovery": "SUCCESS" if result.get("status") == PipelineStatus.INPUT_READY else "FAILED",
            "validation": "SUCCESS" if result.get("status") == PipelineStatus.INPUT_READY else "FAILED",
            "artifact": "SUCCESS" if result.get("artifacts") else "NOT_RECORDED",
            "project_link": data.get("current_status"),
            "correlation_id": correlation_id,
        }
        try:
            self.history.record_content_stage(task, result, "MUSIC_AUDIO_INPUT")
        except Exception:
            pass


def _task(workspace_id, project_id):
    task = Task("Completed audio intake", {"mission_id": project_id}, workspace_id=workspace_id or "invalid")
    task.id = (
        f"{project_id}:{workspace_id}"
        if isinstance(project_id, str) and project_id
        and isinstance(workspace_id, str) and workspace_id
        else "invalid"
    )
    task.task_type = "MUSIC_AUDIO_INPUT"
    return task


def _audio_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 255
            or not _AUDIO_NAME.fullmatch(value.strip())
            or value.strip() in {".", ".."} or Path(value.strip()).is_absolute()):
        raise AudioIntakeError("ACCESS_DENIED")
    return value.strip()


def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise AudioIntakeError("WORKSPACE_MISMATCH" if name == "workspace_id" else "INVALID_IDENTIFIER")


def _valid_signature(extension, value):
    if extension == ".wav":
        return len(value) >= 12 and value[:4] == b"RIFF" and value[8:12] == b"WAVE"
    if extension == ".flac":
        return value.startswith(b"fLaC")
    if extension == ".m4a":
        return len(value) >= 8 and value[4:8] == b"ftyp"
    if extension == ".mp3":
        return value.startswith(b"ID3") or (len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0)
    return False


def _format_matches(extension, detected):
    names = {
        ".wav": {"wav"}, ".flac": {"flac"},
        ".mp3": {"mp3"}, ".m4a": {"mov", "mp4", "m4a", "3gp"},
    }
    return detected.lower() in names[extension]


def _checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _optional_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _safe_artifact(artifact):
    return {key: artifact[key] for key in (
        "artifact_id", "artifact_type", "mime_type", "filename", "size",
        "created_at", "producer_pipeline", "workspace_id", "mission_id",
        "task_id", "stage", "status", "internal_ref", "metadata",
    ) if key in artifact}
