import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GatewaySecurityTests(unittest.TestCase):
    def test_http_gateway_bounds_requests_and_overwrites_forwarding(self):
        value = (ROOT / "Automation" / "gateway.conf").read_text(encoding="utf-8")
        for contract in (
            "client_max_body_size 2m", "proxy_connect_timeout 3s",
            "proxy_read_timeout 60s", "X-Forwarded-For $remote_addr",
            "X-Forwarded-Proto $scheme", "X-Content-Type-Options",
            'Cache-Control "no-store"',
        ):
            self.assertIn(contract, value)
        self.assertNotIn("$http_x_forwarded_for", value)

    def test_tls_gateway_uses_modern_protocols_and_external_files(self):
        value = (ROOT / "Automation" / "gateway-tls.conf").read_text(encoding="utf-8")
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3", value)
        self.assertIn("/run/tls/tls.crt", value)
        self.assertIn("return 308 https://", value)
        self.assertNotIn("TLSv1.1", value)
        compose = (ROOT / "docker-compose.tls.yml").read_text(encoding="utf-8")
        self.assertIn("AICOMPANY_TLS_CERT_FILE", compose)
        self.assertIn("AICOMPANY_TLS_KEY_FILE", compose)
        self.assertNotIn("BEGIN PRIVATE KEY", compose)


if __name__ == "__main__": unittest.main()
