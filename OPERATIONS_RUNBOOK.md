# Single-host Production-like Operations

This runbook covers the Mission 141 single-host validation boundary. It is not
Cloud HA or a Production Ready declaration.

## External inputs

Keep all values outside Git. Create files for a strong application signing
value, PostgreSQL password, full PostgreSQL URL, full authenticated Redis URL,
Redis ACL, and TLS certificate/private key. Point the documented
`AICOMPANY_*_FILE` variables at those files. Keep `ALLOW_PAID_PROVIDER=False`.

Validate the merged configuration before startup:

```powershell
docker compose -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.production.yml config --quiet
```

## Startup and scaling

```powershell
docker compose -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.production.yml build
docker compose -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.production.yml up -d --scale backend=2 --scale worker=2
docker compose -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.production.yml ps
```

The one-shot `migration` service applies the existing additive migration before
Backend and Worker instances start.
API readiness is `https://localhost:8443/ready`; the Frontend is
`https://localhost:8444`. Stop with `docker compose ... down` without `-v`.

## Backup and restore

Quiesce submissions before a coordinated backup. Use `pg_dump` against the
PostgreSQL service and archive the `aicompany_backend_state` volume, which
contains Local Artifact bytes. Store both outputs together outside Git with
restricted permissions and checksums. Never place credentials in filenames or
command output.

Restore only into an empty verification database and empty Artifact volume.
Apply the SQL dump, extract the Artifact archive, start the stack, wait for
readiness, and verify Workspace-scoped Job/History/Artifact reads before any
production cutover. Restore never deletes or overwrites an existing target.

## Failure response

PostgreSQL or Redis loss makes readiness fail. Worker heartbeats expire when
Workers stop. Named volumes retain state. After a dependency restart, restart
Backend and Worker processes so their clients reconnect, then wait for
readiness before reopening submissions. This does not provide automatic
dependency reconnection, host, volume, Redis, or PostgreSQL high availability;
move to managed/clustered infrastructure only through a separately approved
Mission.
