import json
import os
from pathlib import Path
from urllib.parse import urlparse

from core.persistence import (
    InMemoryStateRepository,
    JsonStateRepository,
    StateRepository,
    _payload,
    _record,
)


class PostgreSQLStateRepository(StateRepository):
    """DB-API adapter; schema ownership and migrations remain external."""

    def __init__(self, connection):
        self.connection = connection

    def save(self, kind, record_id, workspace_id, payload):
        record = _record(kind, record_id, workspace_id, payload)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO aicompany_state
                    (kind, record_id, workspace_id, schema_version, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (kind, record_id) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    schema_version = EXCLUDED.schema_version,
                    payload = EXCLUDED.payload
                """,
                (
                    kind, record_id, workspace_id,
                    record["schema_version"], json.dumps(record["payload"]),
                ),
            )
        self.connection.commit()
        return dict(record)

    def get(self, kind, record_id, workspace_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT workspace_id, schema_version, payload
                FROM aicompany_state WHERE kind = %s AND record_id = %s
                """,
                (kind, record_id),
            )
            row = cursor.fetchone()
        return _payload(_postgres_record(kind, record_id, row), workspace_id)

    def list(self, kind, workspace_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT record_id, schema_version, payload
                FROM aicompany_state
                WHERE kind = %s AND workspace_id = %s
                """,
                (kind, workspace_id),
            )
            rows = cursor.fetchall()
        return [
            _payload(
                _postgres_record(kind, row[0], (workspace_id, row[1], row[2])),
                workspace_id,
            )
            for row in rows
        ]

    def health(self):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
            return {"ok": bool(row and row[0] == 1)}
        except Exception:
            return {"ok": False}

    def close(self):
        try:
            self.connection.close()
        except Exception:
            return None


class RedisStateRepository(StateRepository):
    """Redis client adapter using Workspace-qualified keys and JSON values."""

    def __init__(self, client, namespace="aicompany"):
        self.client = client
        self.namespace = namespace

    def save(self, kind, record_id, workspace_id, payload):
        record = _record(kind, record_id, workspace_id, payload)
        self.client.set(
            self._key(kind, record_id),
            json.dumps(record, ensure_ascii=False),
        )
        return dict(record)

    def get(self, kind, record_id, workspace_id):
        return _payload(self._decode(
            self.client.get(self._key(kind, record_id))
        ), workspace_id)

    def list(self, kind, workspace_id):
        values = []
        pattern = self._key(kind, "*")
        for key in self.client.scan_iter(match=pattern):
            record = self._decode(self.client.get(key))
            value = _payload(record, workspace_id)
            if value is not None:
                values.append(value)
        return values

    def health(self):
        try:
            return {"ok": bool(self.client.ping())}
        except Exception:
            return {"ok": False}

    def close(self):
        try:
            self.client.close()
        except Exception:
            return None

    def _key(self, kind, record_id):
        return f"{self.namespace}:state:{kind}:{record_id}"

    @staticmethod
    def _decode(value):
        try:
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return None


class RepositoryFactory:
    @staticmethod
    def create_state(config, *, postgres_connection=None, redis_client=None):
        config.validate()
        adapter = config.adapter
        if adapter == "memory":
            return InMemoryStateRepository()
        if adapter == "json":
            return JsonStateRepository(config.state_file)
        if adapter == "postgres":
            if postgres_connection is None:
                raise ValueError("postgres_connection_required")
            return PostgreSQLStateRepository(postgres_connection)
        if adapter == "redis":
            if redis_client is None:
                raise ValueError("redis_client_required")
            return RedisStateRepository(redis_client)
        raise ValueError("unsupported_repository_adapter")


class InfrastructureConfig:
    def __init__(
        self, adapter="memory", state_file=None,
        database_url=None, redis_url=None,
    ):
        self.adapter = str(adapter).lower()
        self.state_file = state_file
        self.database_url = database_url
        self.redis_url = redis_url

    @classmethod
    def from_environment(cls, environment=None):
        values = environment or os.environ
        return cls(
            values.get("AICOMPANY_REPOSITORY_ADAPTER", "memory"),
            values.get("AICOMPANY_STATE_FILE"),
            values.get("DATABASE_URL"),
            values.get("REDIS_URL"),
        ).validate()

    def validate(self):
        if self.adapter not in {"memory", "json", "postgres", "redis"}:
            raise ValueError("unsupported_repository_adapter")
        if self.adapter == "json" and not self.state_file:
            raise ValueError("state_file_required")
        if self.adapter == "postgres":
            _url(self.database_url, {"postgres", "postgresql"})
        if self.adapter == "redis":
            _url(self.redis_url, {"redis", "rediss"})
        return self


class InfrastructureResources:
    """Owns injected resources and closes them exactly once."""

    def __init__(self, *resources):
        self.resources = resources
        self.closed = False

    def health(self):
        checks = []
        for resource in self.resources:
            probe = getattr(resource, "health", None)
            checks.append(bool(probe and probe().get("ok")))
        return {"ok": all(checks)}

    def close(self):
        if self.closed:
            return
        for resource in reversed(self.resources):
            close = getattr(resource, "close", None)
            if close:
                close()
        self.closed = True


def _url(value, schemes):
    if not isinstance(value, str):
        raise ValueError("infrastructure_url_required")
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.hostname:
        raise ValueError("invalid_infrastructure_url")


def _postgres_record(kind, record_id, row):
    if not row:
        return None
    workspace_id, schema_version, payload = row
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return {
        "kind": kind,
        "record_id": record_id,
        "workspace_id": workspace_id,
        "schema_version": schema_version,
        "payload": payload,
    }
