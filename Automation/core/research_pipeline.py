import json
from datetime import datetime

from config.settings import PROJECT_ROOT
from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus
from providers.pipeline_utils import get_provider_usage, provider_error


class ResearchPipeline(BasePipeline):
    """Creates a structured local research project without external services."""

    def __init__(self, research_root=None, provider=None, artifact_manager=None):
        super().__init__("Research Pipeline")
        self.research_root = research_root or PROJECT_ROOT / "Research"
        self.provider = provider
        self.artifact_manager = artifact_manager or ArtifactManager()
        self.research_root.mkdir(parents=True, exist_ok=True)

    def run(self, task):
        try:
            print("Research Pipeline: Creating structured research project...")
            created_at = datetime.now().isoformat()
            project_name = f"research_project_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            project_path = self.research_root / project_name
            project_path.mkdir()

            topic = task.task_text
            options = self._get_options(task, topic)
            provider_usage = self._generate_provider_usage(task)
            research_questions = options["research_questions"]
            findings = [
                "This project contains a local research scaffold; no external web or AI API data was collected.",
                f"The requested topic is: {topic}.",
                "The next step is to validate these questions with approved sources or an external research integration.",
            ]
            summary = (
                f"A structured local research project was created for '{topic}'. "
                "It captures research questions, initial findings, and a clear handoff "
                "for future source-backed research."
            )
            source_records = self._get_source_records(task)
            sources = [
                self._format_source_record(source_record)
                for source_record in source_records
            ]

            file_contents = {
                "research_plan.txt": (
                    f"Research Plan: {topic}\n\n"
                    + "\n".join(f"{index}. {question}" for index, question in enumerate(research_questions, 1))
                    + "\n"
                ),
                "findings.txt": "\n".join(f"- {finding}" for finding in findings) + "\n",
                "summary.txt": summary + "\n",
                "sources.txt": "\n".join(f"- {source}" for source in sources) + "\n",
                "review_checklist.txt": (
                    "Research Review Checklist\n\n"
                    "- Confirm the research questions match the requested topic.\n"
                    "- Review findings and distinguish local scaffolding from evidence.\n"
                    "- Verify every source record has an appropriate relevance note.\n"
                    "- Approve sources before making source-backed conclusions.\n"
                ),
            }
            text_files = []
            for filename, content in file_contents.items():
                path = project_path / filename
                path.write_text(content, encoding="utf-8")
                text_files.append(path)

            project_json = project_path / "project.json"
            metadata = {
                "project_name": project_name,
                "project_path": str(project_path),
                "task": task.task_text,
                "research_type": options["research_type"],
                "research_questions": research_questions,
                "findings": findings,
                "summary": summary,
                "sources": sources,
                "source_records": source_records,
                "files_created": [str(project_json)] + [str(path) for path in text_files],
                "created_at": created_at,
            }
            if provider_usage is not None:
                metadata["provider_usage"] = provider_usage
            project_json.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            files_created = [project_json] + text_files
            if not all(path.is_file() and path.stat().st_size > 0 for path in files_created):
                raise RuntimeError("Research project verification failed")

            artifacts = self.artifact_manager.register_files(
                files_created, "TEXT", self.name
            )
            metadata["artifacts"] = artifacts

            return PipelineResult(
                status=PipelineStatus.SUCCESS,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                data=metadata,
                artifacts=artifacts,
            ).to_dict()
        except Exception as error:
            return PipelineResult(
                status=PipelineStatus.FAILED,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                error=provider_error(error) if self.provider is not None else str(error),
            ).to_dict()

    @staticmethod
    def _get_options(task, topic):
        research_type = task.parameters.get("research_type", "Structured local research")
        research_questions = task.parameters.get(
            "research_questions",
            [
                f"What is the current scope of {topic}?",
                f"Which audience needs are most relevant to {topic}?",
                f"What next research steps would validate assumptions about {topic}?",
            ],
        )
        if not isinstance(research_type, str) or not research_type.strip():
            raise ValueError("research_type must be a non-empty string")
        if not isinstance(research_questions, list) or not research_questions:
            raise ValueError("research_questions must be a non-empty list")
        if not all(isinstance(question, str) and question.strip() for question in research_questions):
            raise ValueError("research_questions must contain non-empty strings")
        return {"research_type": research_type, "research_questions": list(research_questions)}

    def _generate_provider_usage(self, task):
        return get_provider_usage(self.provider, task.task_text)

    @staticmethod
    def _get_source_records(task):
        source_records = task.parameters.get("source_records")
        if source_records is None:
            return [
                {
                    "title": "Local-only research run",
                    "url": None,
                    "relevance": "No external sources were accessed in this run.",
                },
                {
                    "title": "Future source guidance",
                    "url": None,
                    "relevance": "Record URL, publication date, and relevance notes when sources are approved.",
                },
            ]

        if not isinstance(source_records, list):
            raise ValueError("source_records must be a list")

        validated_records = []
        for source_record in source_records:
            if not isinstance(source_record, dict):
                raise ValueError("source_records must contain dictionaries")

            title = source_record.get("title")
            url = source_record.get("url")
            relevance = source_record.get("relevance")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("source record title must be a non-empty string")
            if url is not None and (not isinstance(url, str) or not url.strip()):
                raise ValueError("source record url must be a non-empty string or None")
            if not isinstance(relevance, str) or not relevance.strip():
                raise ValueError("source record relevance must be a non-empty string")

            validated_records.append(
                {"title": title, "url": url, "relevance": relevance}
            )
        return validated_records

    @staticmethod
    def _format_source_record(source_record):
        url = source_record["url"] or "No URL (local record)"
        return (
            f"{source_record['title']} | URL: {url} | "
            f"Relevance: {source_record['relevance']}"
        )
