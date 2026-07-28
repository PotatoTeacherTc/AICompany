import time

from core.collaboration_worker import BaseWorker
from core.status import PipelineStatus
from core.worker_context import WorkerContext
from core.worker_result import WorkerResult
from providers.factory import ProviderFactory
from providers.models import ProviderRequest
from providers.pipeline_utils import provider_error


class ProviderWorker(BaseWorker):
    def __init__(
        self,
        name,
        provider=None,
        model=None,
        timeout_seconds=None,
        provider_selection=None,
    ):
        selection = provider_selection
        if selection is None and provider is None:
            selection = ProviderFactory.from_environment()
        super().__init__(name)
        self.provider = provider or selection.provider
        self.model = (
            model
            if model is not None
            else getattr(selection, "default_model", None)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(selection, "timeout_seconds", 30.0)
        )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("worker timeout_seconds must be positive")

    def execute(self, context):
        if not isinstance(context, WorkerContext):
            raise ValueError("context must use the WorkerContext contract")
        started_at = time.monotonic()
        try:
            response = self.provider.generate(
                ProviderRequest(
                    prompt=context.objective,
                    model=self.model,
                    timeout_seconds=self.timeout_seconds,
                    metadata={
                        "mission_id": context.mission_id,
                        "workspace_id": context.workspace_id,
                    },
                )
            )
            if time.monotonic() - started_at > self.timeout_seconds:
                return self._failure(
                    context, PipelineStatus.TIMED_OUT, "ProviderError: TimeoutError"
                )
            return WorkerResult.create(
                PipelineStatus.SUCCESS,
                self.name,
                context,
                data={"output": self._safe_output(response.output_text, context.objective)},
                usage=self._usage(response),
            )
        except TimeoutError:
            return self._failure(
                context, PipelineStatus.TIMED_OUT, "ProviderError: TimeoutError"
            )
        except Exception as error:
            return self._failure(
                context, PipelineStatus.FAILED, provider_error(error)
            )

    def _failure(self, context, status, error):
        return WorkerResult.create(status, self.name, context, error=error)

    def _usage(self, response):
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        return {
            "provider": getattr(response, "provider", self.provider.name),
            "model": getattr(response, "model", self.model),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": getattr(
                usage, "total_tokens", input_tokens + output_tokens
            )
            or 0,
            "estimated_cost_usd": getattr(
                usage, "estimated_cost_usd", 0.0
            )
            or 0.0,
        }

    @staticmethod
    def _safe_output(output, prompt):
        text = output if isinstance(output, str) else ""
        return text.replace(prompt, "[request redacted]")


class ClaudeWorker(ProviderWorker):
    def __init__(self, **options):
        super().__init__("claude", **options)


class GeminiWorker(ProviderWorker):
    def __init__(self, **options):
        super().__init__("gemini", **options)
