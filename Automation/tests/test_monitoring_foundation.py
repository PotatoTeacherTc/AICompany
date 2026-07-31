import unittest

from fastapi.testclient import TestClient

from application.backend import (
    BackendDependencies,
    BackendHealthService,
    create_backend_app,
)
from core.operational_metrics import InMemoryOperationalMetrics
from core.structured_logging import InMemoryLogger


class MonitoringFoundationTests(unittest.TestCase):
    def test_request_and_correlation_ids_are_distinct_and_safe(self):
        response = TestClient(create_backend_app()).get(
            "/health",
            headers={
                "X-Request-ID": "request_12345678",
                "X-Correlation-ID": "correlation_12345678",
            },
        )
        self.assertEqual("request_12345678", response.headers["X-Request-ID"])
        self.assertEqual(
            "correlation_12345678", response.headers["X-Correlation-ID"]
        )

    def test_metrics_aggregate_status_duration_and_errors(self):
        metrics = InMemoryOperationalMetrics()
        client = TestClient(create_backend_app(BackendDependencies(
            metrics=metrics,
        )))
        self.assertEqual(200, client.get("/health").status_code)
        self.assertEqual(404, client.get("/missing").status_code)
        value = client.get("/health/metrics").json()["metrics"]
        self.assertGreaterEqual(value["requests_total"], 2)
        self.assertEqual(1, value["status_counts"]["404"])
        self.assertEqual(1, value["error_summary"]["http_404"])
        self.assertGreaterEqual(value["average_duration_ms"], 0)

    def test_health_probe_metrics_are_safe_aggregates(self):
        metrics = InMemoryOperationalMetrics()
        health = BackendHealthService(
            persistence_probe=lambda: True,
            queue_probe=lambda: False,
            metrics=metrics,
        )
        health.snapshot()
        value = metrics.snapshot()
        self.assertEqual("available", value["health"]["persistence"])
        self.assertEqual("unavailable", value["health"]["queue"])
        self.assertNotIn("path", repr(value).lower())

    def test_structured_request_log_has_ids_without_headers_or_body(self):
        logger = InMemoryLogger()
        client = TestClient(create_backend_app(BackendDependencies(
            logger=logger,
        )))
        client.get(
            "/health?prompt=private",
            headers={
                "Authorization": "Bearer secret-value",
                "X-Request-ID": "request_abcdefgh",
            },
        )
        event = logger.events[-1]
        self.assertEqual("HTTP_REQUEST_COMPLETED", event["event_type"])
        self.assertEqual(
            "request_abcdefgh", event["metadata"]["request_id"]
        )
        self.assertNotIn("private", repr(event))
        self.assertNotIn("secret-value", repr(event))

    def test_logger_failure_does_not_change_response_or_metrics(self):
        metrics = InMemoryOperationalMetrics()
        logger = InMemoryLogger(fail_writes=True)
        client = TestClient(create_backend_app(BackendDependencies(
            logger=logger, metrics=metrics,
        )))
        self.assertEqual(200, client.get("/health").status_code)
        self.assertEqual(1, metrics.snapshot()["requests_total"])


if __name__ == "__main__":
    unittest.main()
