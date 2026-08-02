import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import wave
import subprocess

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.completed_audio_intake import (
    AudioInputLocator, AudioInputValidator, AudioIntakeError, AudioProbeResult,
    FfprobeMediaProbe, MusicProjectAudioLinkService,
)
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.music_planning import MusicPlanningRequest, MusicPlanningService
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository, StateRepository
from core.status import PipelineStatus
from main import run_music_import, run_music_plan
from providers.text import FakeTextProvider


SIGNATURES = {
    ".mp3": b"ID3\x04\x00\x00\x00\x00\x00\x08fake-audio",
    ".wav": b"RIFF\x24\x00\x00\x00WAVEfake-audio",
    ".flac": b"fLaCfake-audio-data",
    ".m4a": b"\x00\x00\x00\x18ftypM4A fake-audio",
}
FORMATS = {".mp3": "mp3", ".wav": "wav", ".flac": "flac", ".m4a": "mov"}


class FakeProbe:
    def __init__(self, duration=12.5, error=None):
        self.duration = duration
        self.error = error

    def probe(self, path):
        if self.error:
            raise self.error
        return AudioProbeResult(
            FORMATS[Path(path).suffix.lower()], self.duration,
            "fake-codec", 44100, 2,
        )


class CompletedAudioIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "artifacts"
        self.state = self.root / "state"
        self.repository_file = self.state / "artifact-metadata.json"
        self.history_file = self.state / "execution-history.json"
        self.project_state_file = self.state / "music-project-state.json"
        self.repository = FileArtifactRepository(self.repository_file, self.storage)
        self.history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(self.history_file))
        self.planning_artifacts = ArtifactManager(self.repository)
        self.project_id = "project-a"
        planning = MusicPlanningService(
            self.storage, provider=FakeTextProvider(),
            artifact_manager=self.planning_artifacts,
            execution_history=self.history,
        ).run(MusicPlanningRequest(
            "workspace-a", "private music request", request_id=self.project_id
        ))
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, planning["status"])
        self.locator = AudioInputLocator(self.root / "inputs")

    def tearDown(self):
        self.temp.cleanup()

    def service(self, probe=None, artifacts=None, states=None, history=None):
        repository = FileArtifactRepository(self.repository_file, self.storage)
        manager = artifacts or ArtifactManager(
            repository,
            storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(self.storage), repository),
        )
        return MusicProjectAudioLinkService(
            self.locator, AudioInputValidator(probe=probe or FakeProbe()),
            manager, states or JsonStateRepository(self.project_state_file),
            self.history if history is None else history,
        )

    def audio(self, name, extension, workspace="workspace-a", content=None):
        directory = self.locator.workspace_directory(workspace, create=True)
        path = directory / f"{name}{extension}"
        path.write_bytes(content if content is not None else SIGNATURES[extension])
        return path

    def test_supported_formats_are_located_validated_and_extension_is_optional(self):
        for extension in (".mp3", ".wav", ".flac", ".m4a"):
            path = self.audio(f"song{extension[1:]}", extension)
            located = self.locator.locate("workspace-a", path.stem)
            metadata = AudioInputValidator(FakeProbe()).validate(located)
            self.assertEqual(extension, metadata.extension)
            self.assertEqual(12.5, metadata.duration_seconds)
            self.assertEqual(64, len(metadata.checksum_sha256))

    def test_missing_ambiguous_and_unsupported_names_are_safe(self):
        with self.assertRaisesRegex(AudioIntakeError, "NOT_FOUND"):
            self.locator.locate("workspace-a", "missing")
        self.audio("duplicate", ".mp3")
        self.audio("duplicate", ".wav")
        with self.assertRaisesRegex(AudioIntakeError, "AMBIGUOUS_INPUT"):
            self.locator.locate("workspace-a", "duplicate")
        with self.assertRaisesRegex(AudioIntakeError, "UNSUPPORTED_FORMAT"):
            self.locator.locate("workspace-a", "track.exe")

    def test_empty_corrupt_disguised_and_oversized_files_are_rejected(self):
        empty = self.audio("empty", ".wav", content=b"")
        corrupt = self.audio("corrupt", ".wav", content=SIGNATURES[".wav"])
        disguised = self.audio("disguised", ".mp3", content=SIGNATURES[".wav"])
        oversized = self.audio("large", ".flac", content=SIGNATURES[".flac"] + b"x" * 100)
        with self.assertRaisesRegex(AudioIntakeError, "EMPTY_FILE"):
            AudioInputValidator(FakeProbe()).validate(empty)
        with self.assertRaisesRegex(AudioIntakeError, "CORRUPT_AUDIO"):
            AudioInputValidator(FakeProbe(error=AudioIntakeError("CORRUPT_AUDIO"))).validate(corrupt)
        with self.assertRaisesRegex(AudioIntakeError, "FORMAT_MISMATCH"):
            AudioInputValidator(FakeProbe()).validate(disguised)
        with self.assertRaisesRegex(AudioIntakeError, "FILE_TOO_LARGE"):
            AudioInputValidator(FakeProbe(), maximum_bytes=20).validate(oversized)

    def test_traversal_absolute_unc_and_workspace_isolation_are_blocked(self):
        self.audio("private", ".mp3", workspace="workspace-b")
        for value in ("../private", r"C:\private", r"\\server\share\audio.mp3"):
            with self.assertRaisesRegex(AudioIntakeError, "ACCESS_DENIED"):
                self.locator.locate("workspace-a", value)
        with self.assertRaisesRegex(AudioIntakeError, "NOT_FOUND"):
            self.locator.locate("workspace-a", "private")

    def test_symlink_escape_is_blocked_when_supported(self):
        outside = self.root / "outside.mp3"
        outside.write_bytes(SIGNATURES[".mp3"])
        directory = self.locator.workspace_directory("workspace-a", create=True)
        link = directory / "linked.mp3"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(AudioIntakeError, "ACCESS_DENIED"):
            self.locator.locate("workspace-a", "linked")

    def test_success_imports_artifact_preserves_source_and_transitions_state(self):
        source = self.audio("finished", ".mp3")
        before = source.read_bytes()
        result = self.service().import_audio("workspace-a", self.project_id, "finished")
        self.assertEqual(PipelineStatus.INPUT_READY, result["status"])
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, result["data"]["previous_status"])
        self.assertEqual(PipelineStatus.INPUT_READY, result["data"]["current_status"])
        self.assertEqual(before, source.read_bytes())
        artifact = self.service().artifacts.get(result["data"]["audio_artifact_id"], "workspace-a")
        self.assertEqual("MUSIC_SOURCE_AUDIO", artifact["artifact_type"])
        self.assertNotIn("path", artifact)
        self.assertEqual(hashlib.sha256(before).hexdigest(), artifact["metadata"]["checksum_sha256"])

    def test_restart_recovers_link_and_artifact_metadata(self):
        self.audio("restart", ".flac")
        result = self.service().import_audio("workspace-a", self.project_id, "restart")
        restarted = self.service()
        link = restarted.get_link("workspace-a", self.project_id)
        artifact = restarted.artifacts.get(result["data"]["audio_artifact_id"], "workspace-a")
        self.assertEqual(PipelineStatus.INPUT_READY, link.status)
        self.assertEqual(result["data"]["audio_artifact_id"], artifact["artifact_id"])
        self.assertNotIn(str(self.root), repr(link))
        self.assertNotIn(str(self.root), repr(artifact))

    def test_duplicate_project_link_is_blocked_and_state_remains_ready(self):
        self.audio("first", ".m4a")
        self.audio("second", ".mp3")
        first = self.service().import_audio("workspace-a", self.project_id, "first")
        second = self.service().import_audio("workspace-a", self.project_id, "second")
        self.assertEqual(PipelineStatus.INPUT_READY, first["status"])
        self.assertEqual(PipelineStatus.FAILED, second["status"])
        self.assertEqual("AudioIntakeError: AUDIO_ALREADY_LINKED", second["error"])
        self.assertEqual(PipelineStatus.INPUT_READY, second["data"]["current_status"])
        self.assertEqual(PipelineStatus.INPUT_READY, self.service().get_link("workspace-a", self.project_id).status)

    def test_workspace_mismatch_and_validation_failure_keep_waiting(self):
        self.audio("bad", ".wav", content=b"bad")
        mismatch = self.service().import_audio("workspace-b", self.project_id, "bad")
        failed = self.service().import_audio("workspace-a", self.project_id, "bad")
        self.assertEqual("AudioIntakeError: WORKSPACE_MISMATCH", mismatch["error"])
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, failed["data"]["current_status"])
        self.assertIsNone(self.service().get_link("workspace-a", self.project_id))

    def test_history_records_success_and_failure_without_paths_or_request(self):
        self.audio("history", ".wav")
        success = self.service().import_audio("workspace-a", self.project_id, "history")
        failure = self.service().import_audio("workspace-b", self.project_id, "missing")
        self.assertEqual(PipelineStatus.INPUT_READY, success["status"])
        self.assertEqual(PipelineStatus.FAILED, failure["status"])
        records_a = self.history.query(workspace_id="workspace-a")
        records_b = self.history.query(workspace_id="workspace-b")
        self.assertTrue(any(item["task_type"] == "MUSIC_AUDIO_INPUT" for item in records_a))
        self.assertTrue(any(item["task_type"] == "MUSIC_AUDIO_INPUT" for item in records_b))
        combined = repr(records_a + records_b)
        self.assertNotIn(str(self.root), combined)
        self.assertNotIn("private music request", combined)

    def test_probe_timeout_unavailable_and_malformed_output_are_safe(self):
        class Completed:
            returncode = 0
            stdout = "not-json"

        path = self.audio("probe", ".mp3")
        malformed = FfprobeMediaProbe(runner=lambda *_args, **_kwargs: Completed())
        with self.assertRaisesRegex(AudioIntakeError, "CORRUPT_AUDIO"):
            malformed.probe(path)
        unavailable = FfprobeMediaProbe(executable="missing-ffprobe-command")
        with self.assertRaisesRegex(AudioIntakeError, "PROBE_UNAVAILABLE"):
            unavailable.probe(path)
        timed_out = FfprobeMediaProbe(runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("ffprobe", 1)
        ))
        with self.assertRaisesRegex(AudioIntakeError, "PROBE_TIMEOUT"):
            timed_out.probe(path)

    def test_duplicate_checksum_is_rejected_across_projects_in_same_workspace(self):
        second_project = "project-b"
        planned = MusicPlanningService(
            self.storage, provider=FakeTextProvider(),
            artifact_manager=self.planning_artifacts,
            execution_history=self.history,
        ).run(MusicPlanningRequest(
            "workspace-a", "another private request", request_id=second_project
        ))
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, planned["status"])
        first = self.audio("checksum-one", ".mp3")
        second = self.audio("checksum-two", ".mp3", content=first.read_bytes())
        self.assertEqual(
            PipelineStatus.INPUT_READY,
            self.service().import_audio("workspace-a", self.project_id, first.stem)["status"],
        )
        duplicate = self.service().import_audio(
            "workspace-a", second_project, second.stem
        )
        self.assertEqual("AudioIntakeError: DUPLICATE_AUDIO", duplicate["error"])
        self.assertIsNone(self.service().get_link("workspace-a", second_project))

    def test_artifact_and_history_failures_do_not_create_false_ready_state(self):
        class FailingArtifacts(ArtifactManager):
            def register_file(self, *_args, **_kwargs):
                raise OSError("private storage path")

        class FailingHistory:
            def query(self, workspace_id=None):
                return self.records

            def record_content_stage(self, *_args):
                raise OSError("private history path")

        self.audio("artifact-failure", ".mp3")
        repository = FileArtifactRepository(self.repository_file, self.storage)
        failed_manager = FailingArtifacts(repository)
        failed = self.service(artifacts=failed_manager).import_audio(
            "workspace-a", self.project_id, "artifact-failure"
        )
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertIsNone(self.service().get_link("workspace-a", self.project_id))
        self.assertNotIn("private storage path", repr(failed))

        self.audio("history-failure", ".wav")
        history = FailingHistory()
        history.records = self.history.query(workspace_id="workspace-a")
        success = self.service(history=history).import_audio(
            "workspace-a", self.project_id, "history-failure"
        )
        self.assertEqual(PipelineStatus.INPUT_READY, success["status"])

    def test_state_transition_failure_remains_waiting_and_removes_metadata(self):
        class FailingState(StateRepository):
            def save(self, *_args):
                raise OSError("private state path")

            def get(self, *_args):
                return None

            def list(self, *_args):
                return []

        self.audio("state-failure", ".flac")
        repository = FileArtifactRepository(self.repository_file, self.storage)
        manager = ArtifactManager(
            repository,
            storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(self.storage), repository),
        )
        failed = self.service(artifacts=manager, states=FailingState()).import_audio(
            "workspace-a", self.project_id, "state-failure"
        )
        self.assertEqual("AudioIntakeError: PROJECT_LINK_FAILED", failed["error"])
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, failed["data"]["current_status"])
        self.assertFalse(any(
            item.get("artifact_type") == "MUSIC_SOURCE_AUDIO"
            for item in manager.list("workspace-a")
        ))
        self.assertFalse(any(
            path.name == "state-failure.flac"
            for path in self.storage.rglob("*") if path.is_file()
        ))
        self.assertNotIn("private state path", repr(failed))

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is not installed")
    def test_real_generated_wav_is_probed_with_duration(self):
        path = self.locator.workspace_directory("workspace-a", create=True) / "real.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 8000)
        metadata = AudioInputValidator(FfprobeMediaProbe()).validate(path)
        self.assertGreater(metadata.duration_seconds, 0)
        self.assertEqual("pcm_s16le", metadata.audio_codec)
        self.assertEqual(8000, metadata.sample_rate)

    def test_cli_composition_reuses_music_plan_state_and_safe_output(self):
        cli_root = self.root / "cli"
        planned = run_music_plan(
            "private CLI request", "workspace-cli", cli_root, environment={}
        )
        project_id = planned["data"]["mission_id"]
        locator = AudioInputLocator(cli_root / "inputs")
        (locator.workspace_directory("workspace-cli", create=True) / "song.mp3").write_bytes(SIGNATURES[".mp3"])
        imported = run_music_import(
            "workspace-cli", project_id, "song", cli_root, probe=FakeProbe()
        )
        self.assertEqual(PipelineStatus.INPUT_READY, imported["status"])
        self.assertNotIn("private CLI request", repr(imported))
        self.assertNotIn(str(cli_root), repr(imported))


if __name__ == "__main__":
    unittest.main()
