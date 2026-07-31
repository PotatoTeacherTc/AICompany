import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.artifact_service import ArtifactApplicationService
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.quota_service import QuotaApplicationService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.persistence import InMemoryStateRepository
from core.quota import QuotaEngine
from core.usage_engine import UsageEngine


class _Automation:
    def __init__(self, artifact_manager):
        self.artifact_manager = artifact_manager


class _Unused:
    pass


class ArtifactApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = self.root / "storage"
        self.storage.mkdir()
        self.repository_file = self.root / "artifacts.json"
        self.manager = ArtifactManager(
            FileArtifactRepository(self.repository_file, self.storage)
        )
        self.service = ArtifactApplicationService(self.manager)

        self.text_path = self.storage / "safe.txt"
        self.text_path.write_text("safe artifact body", encoding="utf-8")
        self.text = self.manager.register_file(
            self.text_path,
            "TEXT",
            "Text Pipeline",
            workspace_id="workspace-a",
            mission_id="mission-a",
            task_id="task-a",
        )
        json_path = self.storage / "safe.json"
        json_path.write_text(
            '{"title":"safe","secret":"remove","nested":{"api_key":"remove"}}',
            encoding="utf-8",
        )
        self.json = self.manager.register_file(
            json_path,
            "JSON",
            "Text Pipeline",
            workspace_id="workspace-a",
            mission_id="mission-b",
            task_id="task-b",
        )
        other_path = self.storage / "other.txt"
        other_path.write_text("other workspace", encoding="utf-8")
        self.other = self.manager.register_file(
            other_path,
            "TEXT",
            "Text Pipeline",
            workspace_id="workspace-b",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_safe_list_filters_sort_and_pagination(self):
        result = self.service.list(
            "workspace-a", artifact_type="TEXT", mission_id="mission-a"
        )
        self.assertEqual(1, result["total"])
        self.assertEqual(self.text["artifact_id"], result["items"][0]["artifact_id"])
        self.assertNotIn("path", repr(result))
        self.assertNotIn("internal_ref", repr(result))

        paged = self.service.list("workspace-a", limit=1, offset=1)
        self.assertEqual(2, paged["total"])
        self.assertEqual(1, len(paged["items"]))
        with self.assertRaises(ValueError):
            self.service.list("workspace-a", limit=101)
        with self.assertRaises(ValueError):
            self.service.list("../escape")

    def test_content_is_bounded_checksummed_and_json_redacted(self):
        content = self.service.content("workspace-a", self.text["artifact_id"])
        self.assertEqual("safe artifact body", content["content"])
        self.assertEqual(64, len(content["checksum_sha256"]))
        self.assertNotIn(str(self.storage), repr(content))

        structured = self.service.content("workspace-a", self.json["artifact_id"])
        self.assertEqual({"title": "safe", "nested": {}}, structured["content"])
        self.assertNotIn("remove", repr(structured))

    def test_workspace_isolation_missing_and_restart_recovery(self):
        self.assertIsNone(
            self.service.get("workspace-b", self.text["artifact_id"])
        )
        self.text_path.unlink()
        self.assertEqual(
            {"status": "MISSING"},
            self.service.content("workspace-a", self.text["artifact_id"]),
        )
        restarted = ArtifactApplicationService(
            ArtifactManager(
                FileArtifactRepository(self.repository_file, self.storage)
            )
        )
        restored = restarted.get("workspace-a", self.text["artifact_id"])
        self.assertEqual("MISSING", restored["status"])
        self.assertNotIn(str(self.storage), repr(restored))

    def test_archive_restore_are_idempotent_persistent_and_workspace_scoped(self):
        archived = self.service.archive("workspace-a", self.text["artifact_id"])
        again = self.service.archive("workspace-a", self.text["artifact_id"])
        self.assertEqual("ARCHIVED", archived["status"])
        self.assertEqual(archived, again)
        self.assertTrue(self.text_path.is_file())
        self.assertIsNone(self.service.archive("workspace-b", self.text["artifact_id"]))
        self.assertEqual(
            1, self.service.list("workspace-a", status="ARCHIVED")["total"]
        )

        restarted = ArtifactApplicationService(
            ArtifactManager(FileArtifactRepository(self.repository_file, self.storage))
        )
        restored = restarted.restore("workspace-a", self.text["artifact_id"])
        restored_again = restarted.restore("workspace-a", self.text["artifact_id"])
        self.assertEqual("AVAILABLE", restored["status"])
        self.assertEqual(restored, restored_again)
        self.assertEqual("safe artifact body", restarted.content(
            "workspace-a", self.text["artifact_id"]
        )["content"])
        self.assertNotIn(str(self.storage), repr(restored))

    def test_corrupt_and_traversal_metadata_are_ignored(self):
        self.repository_file.write_text(
            '[{"artifact_id":"bad","workspace_id":"workspace-a",'
            '"internal_ref":"../outside.txt"}]',
            encoding="utf-8",
        )
        restarted = ArtifactApplicationService(
            ArtifactManager(
                FileArtifactRepository(self.repository_file, self.storage)
            )
        )
        self.assertEqual([], restarted.list("workspace-a")["items"])

    def test_api_applies_current_workspace_authorization(self):
        users = UserService()
        owner = users.create("owner@example.com")
        outsider = users.create("outsider@example.com")
        member = users.create("member@example.com")
        credentials = CredentialService(users)
        for user in (owner, outsider, member):
            credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService()
        login = LoginService(
            users,
            credentials,
            SignedAccessTokenProvider(secret="injected-test-secret"),
            sessions,
        )
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Artifacts", owner["user_id"])
        memberships.add(workspace["workspace_id"], member["user_id"], "MEMBER")

        owned_path = self.storage / "owned.txt"
        owned_path.write_text("owned", encoding="utf-8")
        owned = self.manager.register_file(
            owned_path,
            "TEXT",
            "Text Pipeline",
            workspace_id=workspace["workspace_id"],
        )
        app = create_app(
            automation_service=_Automation(self.manager),
            task_query_service=_Unused(),
            workspace_service=workspaces,
            user_service=users,
            membership_service=memberships,
            credential_service=credentials,
            login_service=login,
            session_service=sessions,
            quota_service=QuotaApplicationService(
                QuotaEngine(
                    (quota_repository := InMemoryStateRepository()),
                    UsageEngine(quota_repository),
                )
            ),
            auth_required=True,
        )
        client = TestClient(app)

        def headers(email):
            result = client.post(
                "/auth/login",
                json={"email": email, "password": "safe-passphrase"},
            ).json()
            return {"Authorization": "Bearer " + result["access_token"]}

        owner_headers = headers("owner@example.com")
        outsider_headers = headers("outsider@example.com")
        member_headers = headers("member@example.com")
        base = "/workspaces/{}/artifacts".format(workspace["workspace_id"])
        self.assertEqual(401, client.get(base).status_code)
        self.assertEqual(403, client.get(base, headers=outsider_headers).status_code)
        quota_url = "/workspaces/{}/quota".format(workspace["workspace_id"])
        self.assertEqual(200, client.get(quota_url, headers=member_headers).status_code)
        self.assertEqual(
            403,
            client.put(
                quota_url,
                json={"execution_limit": 1},
                headers=member_headers,
            ).status_code,
        )
        configured = client.put(
            quota_url,
            json={"token_limit": 10, "cost_limit": 0, "execution_limit": 1},
            headers=owner_headers,
        )
        self.assertEqual(200, configured.status_code)
        self.assertEqual(10, configured.json()["token_limit"])
        self.assertEqual(
            403,
            client.post(
                base + "/" + owned["artifact_id"] + "/archive",
                headers=member_headers,
            ).status_code,
        )
        listed = client.get(base, headers=owner_headers)
        detail = client.get(base + "/" + owned["artifact_id"], headers=owner_headers)
        content = client.get(
            base + "/" + owned["artifact_id"] + "/content", headers=owner_headers
        )
        self.assertEqual(200, listed.status_code)
        self.assertEqual(200, detail.status_code)
        self.assertEqual("owned", content.json()["content"])
        archived = client.post(
            base + "/" + owned["artifact_id"] + "/archive", headers=owner_headers
        )
        self.assertEqual("ARCHIVED", archived.json()["status"])
        self.assertEqual(
            1,
            client.get(base + "?status=ARCHIVED", headers=member_headers).json()["total"],
        )
        restored_response = client.post(
            base + "/" + owned["artifact_id"] + "/restore", headers=owner_headers
        )
        self.assertEqual("AVAILABLE", restored_response.json()["status"])
        for response in (listed, detail, content):
            self.assertNotIn(str(self.storage), response.text)
            self.assertNotIn(owner_headers["Authorization"], response.text)

        current = workspaces.get(workspace["workspace_id"])
        workspaces.update(
            workspace["workspace_id"],
            status="INACTIVE",
            expected_revision=current["revision"],
        )
        self.assertEqual(403, client.get(base, headers=owner_headers).status_code)


if __name__ == "__main__":
    unittest.main()
