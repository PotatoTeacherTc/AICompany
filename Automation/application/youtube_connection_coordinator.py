import json
from pathlib import Path
from threading import Lock, Thread

from core.youtube_publishing import GoogleYouTubeOAuthFlow


class YouTubeConnectionCoordinator:
    """Workspace-bound explicit browser OAuth without exposing token material."""

    def __init__(self, connections, client_secret_file, flow=None):
        self.connections = connections
        self.client_secret_file = Path(client_secret_file) if client_secret_file else None
        self.flow = flow or GoogleYouTubeOAuthFlow()
        self._states = {}
        self._lock = Lock()

    def start(self, workspace_id):
        current = self.status(workspace_id)
        if current["status"] in {"CONNECTED", "AUTHORIZATION_PENDING"}:
            return current
        if self.client_secret_file is None or not self.client_secret_file.is_file():
            return self._set(workspace_id, "NOT_CONFIGURED", "CLIENT_CONFIGURATION_REQUIRED")
        with self._lock:
            current = self._states.get(workspace_id)
            if current and current.get("status") == "AUTHORIZATION_PENDING":
                return dict(current)
            self._states[workspace_id] = {
                "component": "youtube", "workspace_id": workspace_id,
                "status": "AUTHORIZATION_PENDING", "safe_error": None,
            }
        Thread(target=self._connect, args=(workspace_id,), daemon=True).start()
        return self.status(workspace_id)

    def status(self, workspace_id):
        connected = next((item for item in self.connections.repository.list(workspace_id)
                          if item.status == "CONNECTED"), None)
        if connected is not None:
            return {
                "component": "youtube", "workspace_id": workspace_id,
                "status": "CONNECTED", "channel_title": connected.safe_channel_title,
                "channel_id": connected.channel_id,
            }
        with self._lock:
            value = self._states.get(workspace_id)
            return dict(value) if value else {
                "component": "youtube", "workspace_id": workspace_id,
                "status": "NOT_CONFIGURED", "safe_error": None,
            }

    def _connect(self, workspace_id):
        try:
            config = json.loads(self.client_secret_file.read_text(encoding="utf-8"))
            token, channel = self.flow.authorize(config, workspace_id, timeout_seconds=900)
            self.connections.connect(
                workspace_id, channel["channel_id"], channel["safe_channel_title"], token
            )
            self._set(workspace_id, "CONNECTED", None)
        except Exception as error:
            self._set(workspace_id, "FAILED", getattr(error, "code", "CONNECTION_FAILED"))

    def _set(self, workspace_id, status, safe_error):
        value = {
            "component": "youtube", "workspace_id": workspace_id,
            "status": status, "safe_error": safe_error,
        }
        with self._lock:
            self._states[workspace_id] = value
        return dict(value)
