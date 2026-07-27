import json
from datetime import datetime

from config.settings import PROJECT_ROOT
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus


class ContentPipeline(BasePipeline):
    """Creates a local, API-free starter project for a YouTube video."""

    def __init__(self, content_root=None):
        super().__init__("Content Pipeline")
        self.content_root = content_root or PROJECT_ROOT / "Content"
        self.content_root.mkdir(parents=True, exist_ok=True)

    def run(self, task):
        try:
            print("Content Pipeline: Creating YouTube content project...")
            created_at = datetime.now().isoformat()
            project_name = f"youtube_project_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            project_path = self.content_root / project_name
            project_path.mkdir()

            options = self._get_options(task)
            content_type = options["content_type"]
            title = f"{options['title_prefix']}: {task.task_text}"
            description = (
                f"This video explores the request: {task.task_text}.\n\n"
                "In this episode, we outline the goal, share practical steps, "
                "and finish with clear next actions for the audience."
            )
            tags = options["tags"]
            content_plan = (
                f"Content Plan: {title}\n\n"
                "1. Hook: Explain why this topic matters.\n"
                "2. Context: Introduce the request and the intended audience.\n"
                "3. Main value: Present three useful, actionable ideas.\n"
                "4. Closing: Summarize and invite the audience to continue learning.\n"
            )
            script = (
                f"[INTRO]\nWelcome! Today we are covering {task.task_text}.\n\n"
                "[MAIN]\nFirst, define the outcome you want. Next, break the work into "
                "small repeatable steps. Finally, review the result and improve it.\n\n"
                "[OUTRO]\nThanks for watching. If this was useful, follow for more "
                "practical automation and content-creation ideas.\n"
            )

            file_contents = {
                "content_plan.txt": content_plan,
                "script.txt": script,
                "title.txt": title + "\n",
                "description.txt": description + "\n",
                "tags.txt": "\n".join(tags) + "\n",
                "review_checklist.txt": (
                    "Content Review Checklist\n\n"
                    "- Confirm the title accurately represents the task.\n"
                    "- Review the script for clarity and completeness.\n"
                    "- Verify description and tags match the intended audience.\n"
                    "- Approve the project before publishing or external generation.\n"
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
                "content_type": content_type,
                "title": title,
                "description": description,
                "tags": tags,
                "script": script,
                "files_created": [str(project_json)] + [str(path) for path in text_files],
                "created_at": created_at,
            }
            project_json.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            files_created = [project_json] + text_files
            if not all(path.is_file() and path.stat().st_size > 0 for path in files_created):
                raise RuntimeError("Content project verification failed")

            return PipelineResult(
                status=PipelineStatus.SUCCESS,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                data=metadata,
            ).to_dict()
        except Exception as error:
            return PipelineResult(
                status=PipelineStatus.FAILED,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                error=str(error),
            ).to_dict()

    @staticmethod
    def _get_options(task):
        parameters = task.parameters
        content_type = parameters.get("content_type", "YouTube video")
        title_prefix = parameters.get("title_prefix", "Getting Started")
        tags = parameters.get(
            "tags", ["AICompany", "YouTube", "automation", "content creation"]
        )

        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("content_type must be a non-empty string")
        if not isinstance(title_prefix, str) or not title_prefix.strip():
            raise ValueError("title_prefix must be a non-empty string")
        if not isinstance(tags, list) or not tags:
            raise ValueError("tags must be a non-empty list")
        if not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise ValueError("tags must contain non-empty strings")

        return {
            "content_type": content_type,
            "title_prefix": title_prefix,
            "tags": list(tags),
        }
