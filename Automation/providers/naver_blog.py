from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, quote, urlparse


class NaverBrowserError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(f"NaverBrowserError: {code}")


@dataclass(frozen=True)
class NaverDraftInput:
    title: str; body: str; tags: tuple[str, ...]; category: str | None; image_paths: tuple[str, ...]


class NaverBlogBrowser(ABC):
    @abstractmethod
    def open_login(self, timeout_seconds): pass
    @abstractmethod
    def prepare_draft(self, value, timeout_seconds): pass
    @abstractmethod
    def wait_for_publication(self, timeout_seconds): pass


class FakeNaverBlogBrowser(NaverBlogBrowser):
    def __init__(self, published_url="https://blog.naver.com/fake/1", fail=None): self.published_url, self.fail, self.actions = published_url, fail, []
    def open_login(self, timeout_seconds): self.actions.append("OPEN_LOGIN"); return {"status": "LOGIN_READY"}
    def prepare_draft(self, value, timeout_seconds):
        if not isinstance(value, NaverDraftInput): raise TypeError("draft input required")
        self.actions.extend(("OPEN_EDITOR", "FILL_TITLE", "FILL_BODY", "UPLOAD_IMAGE", "OPEN_PUBLISH_SETTINGS", "FILL_TAGS", "SELECT_CATEGORY"))
        if self.fail: raise NaverBrowserError(self.fail)
        return {"status": "USER_CONFIRM_REQUIRED"}
    def wait_for_publication(self, timeout_seconds):
        self.actions.append("WAIT_FOR_USER_PUBLICATION")
        if self.fail: raise NaverBrowserError(self.fail)
        return {"status": "PUBLISHED", "published_url": self.published_url, "published_at": datetime.now(timezone.utc).isoformat()}


class PlaywrightNaverBlogBrowser(NaverBlogBrowser):
    """Visible persistent Edge assistant. No selector ever clicks Publish."""
    def __init__(self, profile_directory, editor_url="https://blog.naver.com/GoBlogWrite.naver", playwright_factory=None):
        self.profile = Path(profile_directory).resolve(); self.editor_url = editor_url
        if editor_url != "https://blog.naver.com/GoBlogWrite.naver": raise ValueError("unsupported editor endpoint")
        self.factory = playwright_factory; self._playwright = self._context = self._page = self._draft = None
    def _start(self):
        if self._page is not None: return self._page
        self.profile.mkdir(parents=True, exist_ok=True)
        if self.factory is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        else: self._playwright = self.factory().start()
        self._context = self._playwright.chromium.launch_persistent_context(str(self.profile), channel="msedge", headless=False)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page(); return self._page
    def close(self):
        try:
            if self._context: self._context.close()
        finally:
            if self._playwright: self._playwright.stop()
            self._playwright = self._context = self._page = None
    def open_login(self, timeout_seconds):
        page = self._start(); page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if "nidlogin.login" not in page.url: return {"status": "LOGIN_READY"}
            page.wait_for_timeout(1000)
        raise NaverBrowserError("LOGIN_REQUIRED")
    def prepare_draft(self, value, timeout_seconds):
        if not isinstance(value, NaverDraftInput): raise TypeError("draft input required")
        page = self._start(); page.goto(self.editor_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        if "nidlogin" in page.url:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and "nidlogin" in page.url: page.wait_for_timeout(1000)
            if "nidlogin" in page.url: raise NaverBrowserError("LOGIN_REQUIRED")
            page.goto(self.editor_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        parsed = urlparse(page.url)
        if parsed.hostname == "blog.naver.com" and parsed.path.count("/") == 1 and parsed.path != "/":
            blog_id = parsed.path.strip("/")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", blog_id): raise NaverBrowserError("EDITOR_CHANGED")
            page.goto("https://blog.naver.com/PostWriteForm.naver?blogId=" + quote(blog_id), wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000)); page.wait_for_timeout(3000)
        title = self._first(page, (".se-title-text",)); body = self._first(page, (".se-section-text", ".se-text-paragraph"))
        if title is None or body is None: raise NaverBrowserError("EDITOR_CHANGED")
        try: title.click(); page.keyboard.insert_text(value.title)
        except Exception: raise NaverBrowserError("EDITOR_CHANGED_TITLE") from None
        try: body.click(); page.keyboard.insert_text(value.body)
        except Exception: raise NaverBrowserError("EDITOR_CHANGED_BODY") from None
        if value.image_paths:
            button = self._first(page, ("button[data-name='image'].se-image-toolbar-button",))
            if button is None: raise NaverBrowserError("EDITOR_CHANGED")
            try:
                with page.expect_file_chooser(timeout=int(timeout_seconds * 1000)) as chooser: button.click()
                chooser.value.set_files(list(value.image_paths)); page.wait_for_timeout(3000)
            except Exception: raise NaverBrowserError("UPLOAD_FAILED") from None
        self._draft = value
        opener = self._first(page, ("button[class*='publish_btn__']",))
        if opener is None: raise NaverBrowserError("EDITOR_CHANGED")
        try: opener.click(); page.wait_for_timeout(1500)
        except Exception: raise NaverBrowserError("EDITOR_CHANGED_SETTINGS") from None
        self._fill_publish_options(page, required=True)
        return {"status": "USER_CONFIRM_REQUIRED"}
    def wait_for_publication(self, timeout_seconds):
        page = self._start(); deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _published_url(page.url): return {"status": "PUBLISHED", "published_url": page.url, "published_at": datetime.now(timezone.utc).isoformat()}
            page.wait_for_timeout(1000)
        raise NaverBrowserError("PUBLICATION_NOT_CONFIRMED")
    def _fill_publish_options(self, page, required=False):
        if self._draft is None: return
        tag = self._first(page, ("input[class*='tag_input__']",))
        if self._draft.tags and tag is None and required: raise NaverBrowserError("EDITOR_CHANGED")
        if self._draft.tags and tag is not None:
            try:
                for value in self._draft.tags:
                    tag.fill(value); tag.press("Enter")
            except Exception: raise NaverBrowserError("EDITOR_CHANGED_TAGS") from None
        if self._draft.category:
            category = self._first(page, ("div[class*='option_category__']",))
            if category is None: raise NaverBrowserError("EDITOR_CHANGED")
            try: category.click()
            except Exception: raise NaverBrowserError("EDITOR_CHANGED_CATEGORY") from None
            option = page.get_by_text(self._draft.category, exact=True)
            if option.count() != 1: raise NaverBrowserError("EDITOR_CHANGED")
            try: option.click()
            except Exception: raise NaverBrowserError("EDITOR_CHANGED_CATEGORY") from None
    @staticmethod
    def _first(page, selectors):
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count(): return locator
        return None


def _published_url(value):
    if not isinstance(value, str): return False
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "blog.naver.com": return False
    if re.fullmatch(r"/[A-Za-z0-9_.-]+/[0-9]+", parsed.path): return True
    query = parse_qs(parsed.query)
    return (parsed.path == "/PostView.naver" and
            re.fullmatch(r"[A-Za-z0-9_.-]+", (query.get("blogId") or [""])[0]) is not None and
            re.fullmatch(r"[0-9]+", (query.get("logNo") or [""])[0]) is not None)
