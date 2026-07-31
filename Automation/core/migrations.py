"""Small, forward-only PostgreSQL migration runner for shared state."""

import argparse
import os


LATEST_VERSION = 1
MIGRATIONS = (
    (
        1,
        "shared_state",
        """
        CREATE TABLE IF NOT EXISTS aicompany_state (
            kind TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (kind, workspace_id, record_id)
        );
        CREATE INDEX IF NOT EXISTS aicompany_state_workspace_kind_idx
            ON aicompany_state (workspace_id, kind);
        """,
    ),
)


class PostgreSQLMigrationManager:
    """Tracks and applies additive SQL migrations using one DB-API connection."""

    def __init__(self, connection):
        self.connection = connection

    def upgrade(self):
        self._ensure_version_table()
        applied = self._applied_versions()
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    cursor.execute(
                        """
                        INSERT INTO aicompany_schema_migrations (version, name)
                        VALUES (%s, %s)
                        """,
                        (version, name),
                    )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise RuntimeError("database_migration_failed") from None
        return self.current_version()

    def current_version(self):
        self._ensure_version_table()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) FROM aicompany_schema_migrations"
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def status(self):
        try:
            return "current" if self.current_version() == LATEST_VERSION else "outdated"
        except Exception:
            return "unknown"

    def _ensure_version_table(self):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS aicompany_schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise RuntimeError("database_migration_failed") from None

    def _applied_versions(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT version FROM aicompany_schema_migrations")
            return {int(row[0]) for row in cursor.fetchall()}


def connect_postgresql(database_url):
    try:
        import psycopg

        return psycopg.connect(database_url)
    except Exception:
        raise RuntimeError("database_connection_failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="AICompany PostgreSQL migrations")
    parser.add_argument("command", choices=("upgrade", "status"))
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    connection = connect_postgresql(database_url)
    try:
        manager = PostgreSQLMigrationManager(connection)
        value = manager.upgrade() if args.command == "upgrade" else manager.status()
        print(value)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
