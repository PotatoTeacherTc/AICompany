from core.result import PipelineResult
from core.status import PipelineStatus
import time
from providers.content_media import YouTubeUploadRequest, YouTubeUploadResult
from providers.factory import ProviderFactory
from providers.pipeline_utils import provider_error


class ContentOrchestrator:
    """Offline content flow. It coordinates injected pipelines without retries."""

    def __init__(
        self,
        music_pipeline,
        image_pipeline,
        video_pipeline,
        youtube_provider=None,
        youtube_selection=None,
        execution_history=None,
    ):
        selection = youtube_selection
        if selection is None and youtube_provider is None:
            selection = ProviderFactory.youtube_from_environment()
        self.music_pipeline = music_pipeline
        self.image_pipeline = image_pipeline
        self.video_pipeline = video_pipeline
        self.youtube_provider = ProviderFactory.ensure_provider_allowed(
            youtube_provider or selection.provider
        )
        self.youtube_timeout = getattr(selection, "timeout_seconds", 30.0)
        self.execution_history = execution_history

    def run(self, task):
        stages = {}
        artifacts = []
        for name, pipeline, references in (
            ("music", self.music_pipeline, ()),
            ("image", self.image_pipeline, ()),
        ):
            result = pipeline.run(task)
            stages[name] = self._safe_stage(result)
            if result["status"] != PipelineStatus.SUCCESS:
                return self._result(task, stages, artifacts, PipelineStatus.FAILED)
            artifacts.extend(result["artifacts"])

        video = self.video_pipeline.run(task, input_artifacts=artifacts)
        stages["video"] = self._safe_stage(video)
        if video["status"] != PipelineStatus.SUCCESS:
            return self._result(task, stages, artifacts, PipelineStatus.FAILED)
        artifacts.extend(video["artifacts"])

        try:
            video_artifact = video["artifacts"][0]
            started = time.monotonic()
            upload = self.youtube_provider.upload(
                YouTubeUploadRequest(
                    workspace_id=task.workspace_id,
                    mission_id=task.parameters.get("mission_id") or task.id,
                    artifact=video_artifact,
                    title=task.parameters.get("title", "Generated content"),
                    description=task.parameters.get("description", ""),
                    tags=tuple(task.parameters.get("tags", ())),
                    visibility=task.parameters.get("visibility", "private"),
                    timeout_seconds=self.youtube_timeout,
                )
            )
            if time.monotonic() - started > self.youtube_timeout:
                raise TimeoutError()
            if not isinstance(upload, YouTubeUploadResult):
                raise ValueError("youtube provider returned an invalid result")
            stages["youtube"] = {
                "status": PipelineStatus.SUCCESS,
                "provider": upload.provider,
                "upload_id": upload.upload_id,
                "upload_status": upload.status,
                "visibility": upload.visibility,
                "usage": self._usage(upload),
            }
            status = PipelineStatus.SUCCESS
        except Exception as error:
            stages["youtube"] = {
                "status": PipelineStatus.FAILED,
                "error": provider_error(error),
            }
            status = PipelineStatus.FAILED
        return self._result(task, stages, artifacts, status)

    def _result(self, task, stages, artifacts, status):
        result = PipelineResult(
            status,
            "Content End-to-End",
            task,
            "CONTENT",
            data={
                "workspace_id": task.workspace_id,
                "mission_id": task.parameters.get("mission_id") or task.id,
                "stages": stages,
                "task_redacted": True,
            },
            artifacts=artifacts,
            error=None if status == PipelineStatus.SUCCESS else "ContentFlowError",
        ).to_dict()
        result["task"] = "Content generation"
        if self.execution_history is not None:
            try:
                self.execution_history.record_content_stage(task, result, "CONTENT")
            except Exception:
                pass
        return result

    @staticmethod
    def _safe_stage(result):
        data = result.get("data") or {}
        return {
            "status": result.get("status"),
            "provider": data.get("provider"),
            "model": data.get("model"),
            "usage": data.get("provider_usage"),
            "artifact_ids": [
                artifact.get("artifact_id") for artifact in result.get("artifacts", [])
            ],
            "error": result.get("error"),
        }

    @staticmethod
    def _usage(result):
        usage = result.usage
        inputs = getattr(usage, "input_tokens", 0) or 0
        outputs = getattr(usage, "output_tokens", 0) or 0
        return {
            "provider": result.provider,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "total_tokens": getattr(usage, "total_tokens", inputs + outputs) or 0,
            "estimated_cost_usd": getattr(usage, "estimated_cost_usd", 0.0) or 0.0,
        }
