import tempfile
import unittest
from pathlib import Path

from core.production_config import resolve_secret_files, validate_production_configuration
from core.security import SecuritySettings


def production_values():
    return {
        "AICOMPANY_ENV": "production",
        "ALLOW_PAID_PROVIDER": "False",
        "AICOMPANY_REPOSITORY_ADAPTER": "postgresql",
        "AICOMPANY_QUEUE_BACKEND": "redis",
        "AICOMPANY_ARTIFACT_STORAGE": "local",
        "DATABASE_URL": "postgresql://app:strong-password@postgres/app",
        "REDIS_URL": "redis://:strong-password@redis:6379/0",
        "AICOMPANY_SIGNING_SECRET": "a-valid-signing-value-with-32-characters",
        "AICOMPANY_ALLOWED_ORIGINS": "https://app.invalid",
    }


class ProductionConfigurationTests(unittest.TestCase):
    def test_valid_production_and_local_compatibility(self):
        values = validate_production_configuration(production_values())
        self.assertEqual("production", values["AICOMPANY_ENV"])
        self.assertTrue(SecuritySettings.from_environment(values).secure_cookies)
        self.assertEqual({"AICOMPANY_ENV": "test"}, validate_production_configuration({"AICOMPANY_ENV": "test"}))

    def test_missing_insecure_and_paid_configuration_rejected(self):
        cases = []
        for key in ("DATABASE_URL", "REDIS_URL", "AICOMPANY_SIGNING_SECRET"):
            value = production_values(); value.pop(key); cases.append(value)
        insecure = production_values(); insecure["DATABASE_URL"] = "postgresql://app:local-development-only@postgres/app"; cases.append(insecure)
        paid = production_values(); paid["ALLOW_PAID_PROVIDER"] = "True"; cases.append(paid)
        for value in cases:
            with self.subTest(value=set(value)):
                with self.assertRaises(ValueError): validate_production_configuration(value)

    def test_file_secret_loading_rotation_and_safe_errors(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "signing"; path.write_text("first-safe-value", encoding="utf-8")
            values = resolve_secret_files({"AICOMPANY_SIGNING_SECRET_FILE": str(path)})
            self.assertEqual("first-safe-value", values["AICOMPANY_SIGNING_SECRET"])
            path.write_text("second-safe-value", encoding="utf-8")
            rotated = resolve_secret_files({"AICOMPANY_SIGNING_SECRET_FILE": str(path)})
            self.assertEqual("second-safe-value", rotated["AICOMPANY_SIGNING_SECRET"])
            with self.assertRaisesRegex(ValueError, "duplicate_secret_source"):
                resolve_secret_files({"AICOMPANY_SIGNING_SECRET": "direct", "AICOMPANY_SIGNING_SECRET_FILE": str(path)})
            try: resolve_secret_files({"DATABASE_URL_FILE": str(Path(root) / "private-name")})
            except ValueError as error: self.assertNotIn(root, str(error))


if __name__ == "__main__": unittest.main()
