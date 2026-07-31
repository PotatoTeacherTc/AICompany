import unittest

from fastapi.testclient import TestClient

from application.backend import BackendDependencies, create_backend_app
from core.security import (
    InMemoryRateLimiter,
    SecuritySettings,
    harden_set_cookie,
)


class ProductionSecurityTests(unittest.TestCase):
    def test_production_requires_https_origins_and_strong_non_placeholder_secret(self):
        base = {
            "AICOMPANY_ENV": "production",
            "AICOMPANY_ALLOWED_ORIGINS": "https://app.example.test",
            "AICOMPANY_SIGNING_SECRET": "A9!" * 16,
        }
        value = SecuritySettings.from_environment(base)
        self.assertTrue(value.secure_cookies)
        with self.assertRaisesRegex(ValueError, "production_https_required"):
            SecuritySettings.from_environment(dict(
                base, AICOMPANY_ALLOWED_ORIGINS="http://app.example.test"
            ))
        with self.assertRaisesRegex(ValueError, "invalid_signing_secret"):
            SecuritySettings.from_environment(dict(
                base, AICOMPANY_SIGNING_SECRET="replace-with-secret-value"
            ))

    def test_security_headers_csp_and_hsts_are_applied(self):
        settings = SecuritySettings.from_environment({
            "AICOMPANY_ENV": "production",
            "AICOMPANY_ALLOWED_ORIGINS": "https://app.example.test",
            "AICOMPANY_SIGNING_SECRET": "Z7!" * 16,
        })
        client = TestClient(create_backend_app(BackendDependencies(
            security_settings=settings
        )))
        response = client.get("/health")
        self.assertEqual("nosniff", response.headers["x-content-type-options"])
        self.assertEqual("DENY", response.headers["x-frame-options"])
        self.assertIn("default-src 'self'", response.headers[
            "content-security-policy"
        ])
        self.assertIn("max-age=", response.headers[
            "strict-transport-security"
        ])

    def test_injected_rate_limit_returns_safe_429(self):
        client = TestClient(create_backend_app(BackendDependencies(
            rate_limiter=InMemoryRateLimiter(1, 60),
        )))
        self.assertEqual(200, client.get("/health").status_code)
        response = client.get("/health")
        self.assertEqual(429, response.status_code)
        self.assertEqual("rate_limit_exceeded", response.json()["error"]["code"])
        self.assertNotIn("token", repr(response.json()).lower())
        self.assertIn("content-security-policy", response.headers)

    def test_development_cors_rejects_credentialed_or_external_origins(self):
        client = TestClient(create_backend_app())
        allowed = client.options("/health", headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(
            "http://127.0.0.1:5173",
            allowed.headers.get("access-control-allow-origin"),
        )
        self.assertNotEqual(
            "true", allowed.headers.get("access-control-allow-credentials")
        )
        denied = client.options("/health", headers={
            "Origin": "https://external.example",
            "Access-Control-Request-Method": "GET",
        })
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))

    def test_cookie_hardening_contract(self):
        value = harden_set_cookie("session=value; Path=/")
        self.assertIn("Secure", value)
        self.assertIn("HttpOnly", value)
        self.assertIn("SameSite=Strict", value)


if __name__ == "__main__":
    unittest.main()
