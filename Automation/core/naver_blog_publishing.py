from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
import tempfile
import threading

from core.artifact_manager import ArtifactManager
from core.blog_package import BLOG_PACKAGE_KIND
from core.persistence import StateRepository
from core.status import PipelineStatus
from core.task import Task
from providers.naver_blog import NaverBlogBrowser, NaverBrowserError, NaverDraftInput


NAVER_PUBLICATION_KIND = "naver_blog_publication"
_TERMINAL = {"PUBLISHED"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class NaverPublishingRequest:
    workspace_id: str
    content_project_id: str
    category: str | None = None
    wait_for_publication: bool = False
    timeout_seconds: float = 900


class NaverBlogPublishingAssistant:
    def __init__(self, browser, states, artifacts, history=None, work_root=None, usage=None):
        if not isinstance(browser, NaverBlogBrowser): raise TypeError("browser contract required")
        if not isinstance(states, StateRepository): raise TypeError("state repository required")
        if not isinstance(artifacts, ArtifactManager): raise TypeError("artifact manager required")
        self.browser, self.states, self.artifacts, self.history, self.usage = browser, states, artifacts, history, usage
        self.work_root = Path(work_root or ".naver-publishing-work").resolve(); self.work_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def run(self, request):
        if not isinstance(request, NaverPublishingRequest): return self._failure(None, None, "INVALID_REQUEST")
        try: self._validate(request)
        except Exception: return self._failure(request.workspace_id, request.content_project_id, "INVALID_REQUEST")
        with self._lock:
            current = self.states.get(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id)
            if isinstance(current, dict) and current.get("status") in _TERMINAL:
                if not current.get("receipt_artifact_id"):
                    receipt = self._save_receipt(current); current["receipt_artifact_id"] = receipt["artifact_id"]
                    self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, current)
                self._usage(request); return self._success(current, replay=True)
            try:
                package, article, images = self._inputs(request.workspace_id, request.content_project_id)
                record = {"workspace_id": request.workspace_id, "content_project_id": request.content_project_id,
                          "status": "RUNNING", "blog_package_id": package.get("blog_package_id"),
                          "artifact_ids": [item["artifact_id"] for item in images], "updated_at": _now()}
                self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, record)
                with tempfile.TemporaryDirectory(dir=self.work_root) as temporary:
                    image_paths = self._materialize(images, request.workspace_id, Path(temporary))
                    prepared = self.browser.prepare_draft(NaverDraftInput(
                        package["title"], article, tuple(package.get("tags", ()))[:10],
                        request.category, tuple(str(path) for path in image_paths)), request.timeout_seconds)
                    if prepared.get("status") != "USER_CONFIRM_REQUIRED": raise NaverBrowserError("EDITOR_CHANGED")
                    record.update({"status": "USER_CONFIRM_REQUIRED", "updated_at": _now()})
                    self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, record)
                    if not request.wait_for_publication: return self._result(record)
                    published = self.browser.wait_for_publication(request.timeout_seconds)
                if published.get("status") != "PUBLISHED" or not _published_url(published.get("published_url")):
                    raise NaverBrowserError("PUBLICATION_NOT_CONFIRMED")
                record.update({"status": "PUBLISHED", "published_url": published["published_url"],
                               "published_at": published.get("published_at") or _now(), "updated_at": _now()})
                receipt = self._save_receipt(record); record["receipt_artifact_id"] = receipt["artifact_id"]
                self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, record)
                result = self._success(record); self._history(request, result); self._usage(request); return result
            except Exception as error:
                code = error.code if isinstance(error, NaverBrowserError) else type(error).__name__
                status = ("EDITOR_CHANGED" if code.startswith("EDITOR_CHANGED") else code
                          if code in {"LOGIN_REQUIRED", "SESSION_EXPIRED", "CAPTCHA_REQUIRED", "UPLOAD_FAILED", "PUBLICATION_NOT_CONFIRMED"}
                          else "USER_ACTION_REQUIRED")
                failed = self.states.get(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id) or {}
                failed.update({"workspace_id": request.workspace_id, "content_project_id": request.content_project_id,
                               "status": status, "safe_error": f"NaverPublishingError: {code}", "updated_at": _now()})
                try: self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, failed)
                except Exception: pass
                return self._failure(request.workspace_id, request.content_project_id, code, status)

    def complete_after_confirmation(self, request):
        """Observe the user-clicked publication without preparing or clicking again."""
        if not isinstance(request, NaverPublishingRequest): return self._failure(None, None, "INVALID_REQUEST")
        try: self._validate(request)
        except Exception: return self._failure(request.workspace_id, request.content_project_id, "INVALID_REQUEST")
        with self._lock:
            record = self.states.get(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id)
            if not isinstance(record, dict) or record.get("status") != "USER_CONFIRM_REQUIRED":
                return self._failure(request.workspace_id, request.content_project_id, "PUBLICATION_NOT_CONFIRMED")
            try:
                published = self.browser.wait_for_publication(request.timeout_seconds)
                if published.get("status") != "PUBLISHED" or not _published_url(published.get("published_url")):
                    raise NaverBrowserError("PUBLICATION_NOT_CONFIRMED")
                record.update({"status": "PUBLISHED", "published_url": published["published_url"],
                               "published_at": published.get("published_at") or _now(), "updated_at": _now()})
                receipt = self._save_receipt(record); record["receipt_artifact_id"] = receipt["artifact_id"]
                self.states.save(NAVER_PUBLICATION_KIND, request.content_project_id, request.workspace_id, record)
                result = self._success(record); self._history(request, result); self._usage(request); return result
            except Exception as error:
                code = error.code if isinstance(error, NaverBrowserError) else type(error).__name__
                return self._failure(request.workspace_id, request.content_project_id, code, "PUBLICATION_NOT_CONFIRMED")

    def _inputs(self, workspace, project):
        state = self.states.get(BLOG_PACKAGE_KIND, project, workspace)
        if not isinstance(state, dict) or state.get("status") != "COMPLETED": raise NaverBrowserError("BLOG_PACKAGE_INCOMPLETE")
        values = [self.artifacts.get(value, workspace) for value in state.get("artifact_ids", ())]
        package_meta = next((v for v in values if v and v.get("artifact_type") == "BLOG_PACKAGE"), None)
        article_meta = next((v for v in values if v and v.get("artifact_type") == "BLOG_ARTICLE_MARKDOWN"), None)
        image_manifest = next((v for v in values if v and v.get("artifact_type") == "BLOG_IMAGE_MANIFEST"), None)
        if not package_meta or not article_meta or not image_manifest: raise NaverBrowserError("BLOG_PACKAGE_INCOMPLETE")
        package = json.loads(self.artifacts.storage_adapter.read(workspace, package_meta["artifact_id"]).decode("utf-8"))
        article = self.artifacts.storage_adapter.read(workspace, article_meta["artifact_id"]).decode("utf-8")
        manifest = json.loads(self.artifacts.storage_adapter.read(workspace, image_manifest["artifact_id"]).decode("utf-8"))
        images = [self.artifacts.get(item.get("artifact_id"), workspace) for item in manifest.get("images", ())]
        images = [item for item in images if item and item.get("status") == "AVAILABLE" and item.get("mime_type") in {"image/png", "image/jpeg", "image/webp"}]
        if not images: raise NaverBrowserError("UPLOAD_FAILED")
        return package, article, images[:1]

    def _materialize(self, images, workspace, root):
        paths = []
        for item in images:
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[item["mime_type"]]
            path = root / (item["artifact_id"] + suffix)
            path.write_bytes(self.artifacts.storage_adapter.read(workspace, item["artifact_id"])); paths.append(path)
        return paths

    def _save_receipt(self, record):
        with tempfile.TemporaryDirectory(dir=self.work_root) as temporary:
            path = Path(temporary) / "naver_publication_receipt.json"
            path.write_text(json.dumps({"status": "PUBLISHED", "published_url": record["published_url"],
                "published_at": record["published_at"], "content_project_id": record["content_project_id"]}, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.artifacts.register_file(path, "NAVER_PUBLICATION_RECEIPT", "Naver Blog Publishing Assistant",
                workspace_id=record["workspace_id"], task_id=record["content_project_id"], stage="NAVER_BLOG_PUBLICATION",
                metadata={"content_project_id": record["content_project_id"], "schema_version": "1.0"})

    def _usage(self, request):
        if self.usage is not None:
            self.usage.record_safe(request.workspace_id, "naver-blog-" + request.content_project_id,
                {"provider": "naver-browser", "model": "edge-playwright", "estimated_cost_usd": 0.0},
                mission_id=request.content_project_id, usage_id="naver-blog-" + request.content_project_id)

    @staticmethod
    def _validate(request):
        for value in (request.workspace_id, request.content_project_id):
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value): raise ValueError
        if request.category is not None and (not isinstance(request.category, str) or len(request.category) > 100): raise ValueError
        if not isinstance(request.timeout_seconds, (int, float)) or not 1 <= request.timeout_seconds <= 1800: raise ValueError

    @staticmethod
    def _result(record): return {"status": record["status"], "pipeline": "Naver Blog Publishing Assistant", "data": dict(record), "artifacts": [], "error": None}
    @classmethod
    def _success(cls, record, replay=False):
        value = cls._result(record); value["status"] = PipelineStatus.SUCCESS; value["data"]["idempotent_replay"] = replay; return value
    @staticmethod
    def _failure(workspace, project, code, status="FAILED"):
        return {"status": PipelineStatus.FAILED, "pipeline": "Naver Blog Publishing Assistant",
                "data": {"workspace_id": workspace, "content_project_id": project, "publication_status": status},
                "artifacts": [], "error": f"NaverPublishingError: {code}"}
    def _history(self, request, result):
        if self.history is None: return
        task = Task("Naver blog publication", {"mission_id": request.content_project_id}, workspace_id=request.workspace_id); task.task_type = "NAVER_BLOG_PUBLICATION"
        result["data"]["stages"] = {"NAVER_BLOG_PUBLICATION": "PUBLISHED"}
        try: self.history.record_content_stage(task, result, "NAVER_BLOG_PUBLICATION")
        except Exception: pass


def _published_url(value):
    if not isinstance(value, str): return False
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "blog.naver.com": return False
    if re.fullmatch(r"/[A-Za-z0-9_.-]+/[0-9]+", parsed.path): return True
    query = parse_qs(parsed.query)
    return (parsed.path == "/PostView.naver" and re.fullmatch(r"[A-Za-z0-9_.-]+", (query.get("blogId") or [""])[0]) is not None
            and re.fullmatch(r"[0-9]+", (query.get("logNo") or [""])[0]) is not None)
def _now(): return datetime.now(timezone.utc).isoformat()
