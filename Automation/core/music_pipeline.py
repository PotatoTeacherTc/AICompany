from datetime import datetime

from config.settings import PROJECT_ROOT
from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus
from providers.pipeline_utils import get_provider_usage, provider_error


class MusicPipeline(BasePipeline):
    def __init__(self, music_root=None, provider=None, artifact_manager=None):
        super().__init__("Music Pipeline")
        self.music_root = music_root or PROJECT_ROOT / "Music"
        self.provider = provider
        self.artifact_manager = artifact_manager or ArtifactManager()
        self.music_root.mkdir(parents=True, exist_ok=True)

    def run(self, task):
        try:
            print("Music Pipeline: Starting music creation...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            project_name = f"music_project_{timestamp}"
            project_path = self.music_root / project_name
            project_path.mkdir(exist_ok=True)

            song_structure = ["INTRO", "VERSE 1", "PRE-CHORUS", "CHORUS", "VERSE 2", "CHORUS", "BRIDGE", "FINAL CHORUS", "OUTRO"]
            metadata = {
                "project_name": project_name,
                "request": task.task_text,
                "genre": "AI Generated Music",
                "bpm": 100,
                "key": "C Major",
                "structure": song_structure,
                "created_at": datetime.now().isoformat(),
            }
            provider_usage = self._generate_provider_usage(task)
            if provider_usage is not None:
                metadata["provider_usage"] = provider_usage
            structure_file = project_path / "song_structure.txt"
            structure_file.write_text(
                "AICompany Music Project\n=======================\n\n"
                f"Project: {project_name}\nRequest: {task.task_text}\n\nSong Structure:\n"
                + "\n".join(f"{index}. {section}" for index, section in enumerate(song_structure, 1))
                + "\n",
                encoding="utf-8",
            )
            metadata_file = project_path / "metadata.txt"
            metadata_file.write_text(
                "\n".join(f"{key}: {value}" for key, value in metadata.items()) + "\n",
                encoding="utf-8",
            )
            files_created = [structure_file, metadata_file]
            if not all(path.exists() for path in files_created):
                raise RuntimeError("Music project verification failed")

            artifacts = self.artifact_manager.register_files(
                files_created, "TEXT", self.name
            )

            return PipelineResult(
                status=PipelineStatus.SUCCESS,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                data={"project_name": project_name, "project_path": str(project_path), "files_created": [str(path) for path in files_created], "metadata": metadata, "provider_usage": provider_usage, "artifacts": artifacts},
                artifacts=artifacts,
            ).to_dict()
        except Exception as error:
            return PipelineResult(PipelineStatus.FAILED, self.name, task, task.task_type, error=provider_error(error)).to_dict()

    def _generate_provider_usage(self, task):
        return get_provider_usage(self.provider, task.task_text)
