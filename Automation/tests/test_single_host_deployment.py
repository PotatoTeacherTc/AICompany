import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SingleHostDeploymentTests(unittest.TestCase):
    def test_production_overlay_has_external_secrets_and_bounded_operations(self):
        value = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
        for contract in (
            "AICOMPANY_SIGNING_SECRET_FILE", "AICOMPANY_DATABASE_URL_FILE",
            "AICOMPANY_REDIS_URL_FILE", "AICOMPANY_POSTGRES_PASSWORD_FILE",
            "AICOMPANY_REDIS_ACL_FILE", "restart: unless-stopped",
            "max-size", "mem_limit", "cpus", 'ALLOW_PAID_PROVIDER: "False"',
            "migration:", "condition: service_completed_successfully",
        ):
            self.assertIn(contract, value)
        self.assertNotIn("local-development-only", value)

    def test_tls_frontend_and_runbook_boundaries(self):
        gateway = (ROOT / "Automation" / "gateway-tls.conf").read_text(encoding="utf-8")
        self.assertIn("listen 8444 ssl", gateway)
        self.assertIn("set $frontend http://frontend:80", gateway)
        runbook = (ROOT / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
        for contract in ("pg_dump", "empty verification database", "without `-v`", "not provide automatic"):
            self.assertIn(contract, runbook)


if __name__ == "__main__": unittest.main()
