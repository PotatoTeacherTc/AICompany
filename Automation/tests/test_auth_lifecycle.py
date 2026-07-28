import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from core.access_token_provider import SignedAccessTokenProvider
from core.session_repository import FileSessionRepository, InMemorySessionRepository


class _Unused:
    pass


class AuthenticationLifecycleTests(unittest.TestCase):
    def _services(self, repository=None, now=None):
        users = UserService()
        user = users.create("person@example.com")
        credentials = CredentialService(users)
        credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService(repository=repository, now=now)
        tokens = SignedAccessTokenProvider(
            secret="injected-test-secret",
            expires_in_seconds=60,
            clock=(lambda: now().timestamp()) if now else None,
        )
        login = LoginService(users, credentials, tokens, sessions)
        return users, user, credentials, sessions, login

    def _client(self, users, credentials, sessions, login):
        return TestClient(
            create_app(
                automation_service=_Unused(),
                task_query_service=_Unused(),
                user_service=users,
                credential_service=credentials,
                login_service=login,
                session_service=sessions,
                auth_required=True,
            )
        )

    def test_access_token_validates_schema_type_expiry_and_signature(self):
        current = [100]
        provider = SignedAccessTokenProvider(
            secret="test-secret", expires_in_seconds=10, clock=lambda: current[0]
        )
        token = provider.issue("user-1", "session-1")
        self.assertEqual(
            {"user_id": "user-1", "session_id": "session-1"},
            provider.verify(token),
        )
        self.assertIsNone(provider.verify(token + "tampered"))
        self.assertIsNone(provider.verify("malformed"))
        current[0] = 110
        self.assertIsNone(provider.verify(token))

        encoded, signature = token.split(".")
        payload = json.loads(
            __import__("base64").urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        self.assertNotIn("email", payload)
        self.assertNotIn("password", payload)
        self.assertEqual("access", payload["typ"])
        self.assertEqual(1, payload["ver"])
        self.assertTrue(signature)

    def test_login_failures_are_uniform_and_credential_errors_are_safe(self):
        users, user, credentials, sessions, login = self._services()
        for email, password in (
            ("missing@example.com", "safe-passphrase"),
            ("person@example.com", "wrong-password"),
            (None, None),
        ):
            with self.assertRaisesRegex(ValueError, "^invalid_credentials$"):
                login.login(email, password)

        credentials.password_hasher.verify = lambda *_: (_ for _ in ()).throw(
            RuntimeError("private credential failure")
        )
        with self.assertRaisesRegex(ValueError, "^invalid_credentials$") as raised:
            login.login("person@example.com", "safe-passphrase")
        self.assertNotIn("private", str(raised.exception))

        users.deactivate(user["user_id"])
        with self.assertRaisesRegex(ValueError, "^invalid_credentials$"):
            login.login("person@example.com", "safe-passphrase")
        self.assertEqual([], sessions.list(user["user_id"]))

    def test_refresh_rotates_in_place_and_concurrent_reuse_has_one_winner(self):
        users, user, _, sessions, login = self._services()
        issued = login.login("person@example.com", "safe-passphrase")
        results = []

        def rotate():
            try:
                results.append(login.refresh(issued["refresh_token"]))
            except ValueError:
                results.append(None)

        threads = [threading.Thread(target=rotate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        successful = [result for result in results if result]
        self.assertEqual(1, len(successful))
        self.assertEqual(issued["session_id"], successful[0]["session_id"])
        stored = sessions.repository.get(issued["session_id"])
        self.assertEqual(1, stored["revision"])
        self.assertNotIn(issued["refresh_token"], repr(stored))

    def test_revoke_is_idempotent_and_logout_all_is_user_scoped(self):
        users, user, credentials, sessions, login = self._services()
        first = login.login("person@example.com", "safe-passphrase")
        second = login.login("person@example.com", "safe-passphrase")
        other = users.create("other@example.com")
        credentials.set_password(other["user_id"], "safe-passphrase")
        other_login = login.login("other@example.com", "safe-passphrase")

        self.assertTrue(sessions.revoke(first["session_id"], user["user_id"]))
        self.assertTrue(sessions.revoke(first["session_id"], user["user_id"]))
        self.assertFalse(sessions.revoke(other_login["session_id"], user["user_id"]))
        self.assertEqual(1, sessions.revoke_all(user["user_id"]))
        self.assertIsNone(login.current_user(second["access_token"]))
        self.assertIsNotNone(login.current_user(other_login["access_token"]))

    def test_file_sessions_restore_rotation_revoke_and_ignore_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            users, user, _, sessions, login = self._services(
                FileSessionRepository(path)
            )
            issued = login.login("person@example.com", "safe-passphrase")
            restored = SessionService(FileSessionRepository(path))
            rotated = restored.rotate(issued["refresh_token"])
            self.assertIsNotNone(rotated)
            self.assertIsNone(restored.rotate(issued["refresh_token"]))
            self.assertTrue(restored.revoke(rotated[0]["session_id"], user["user_id"]))
            self.assertFalse(restored.is_active(restored.repository.get(issued["session_id"])))
            self.assertNotIn(issued["refresh_token"], path.read_text(encoding="utf-8"))

            path.write_text('[{"unsupported": true}, "broken"]', encoding="utf-8")
            self.assertEqual({}, FileSessionRepository(path).items)

    def test_api_deactivation_revokes_sessions_and_logout_all_is_safe(self):
        users, user, credentials, sessions, login = self._services()
        client = self._client(users, credentials, sessions, login)
        first = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "safe-passphrase"},
        ).json()
        second = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "safe-passphrase"},
        ).json()
        headers = {"Authorization": "Bearer " + first["access_token"]}
        response = client.post("/auth/logout-all", headers=headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()["revoked_sessions"])
        self.assertEqual(
            401,
            client.post(
                "/auth/refresh", json={"refresh_token": second["refresh_token"]}
            ).status_code,
        )

        active = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "safe-passphrase"},
        ).json()
        headers = {"Authorization": "Bearer " + active["access_token"]}
        self.assertEqual(
            200,
            client.patch(
                "/users/{}/deactivate".format(user["user_id"]), headers=headers
            ).status_code,
        )
        self.assertEqual(
            401,
            client.post(
                "/auth/refresh", json={"refresh_token": active["refresh_token"]}
            ).status_code,
        )


if __name__ == "__main__":
    unittest.main()
