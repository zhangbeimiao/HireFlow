# Production configuration, backup and recovery

## Compose boundaries

- `docker-compose.yml` is the local development baseline and includes Mailpit plus loopback-only ports.
- `docker-compose.test.yml` shortens token/cache lifetimes and makes asynchronous checks converge faster.
- `docker-compose.prod.yml` removes host-published data-plane ports, disables Mailpit unless its profile is explicitly enabled, requires rolling JWT keys and points email at an external SMTP provider.
- `docker-compose.tls.example.yml` shows certificate mounts and PostgreSQL/RabbitMQ TLS wiring. It is an example, not a source of certificates.

Production startup should use an ingress for HTTPS and an external secret manager:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml `
  -f docker-compose.tls.example.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.prod.yml `
  -f docker-compose.tls.example.yml up -d --wait
```

Do not expose PostgreSQL, Redis, RabbitMQ management or Mailpit to the public network. Replace every
example password, restrict certificate/key permissions, and configure certificate rotation outside the image.

## JWT key rotation

`HIREFLOW_JWT_KEYS` is a JSON object from key id to a secret of at least 32 bytes;
`HIREFLOW_JWT_ACTIVE_KEY_ID` selects the signing key. Access tokens carry `kid` and verification accepts
only configured ids.

1. Add the new key while retaining the old key; deploy all API instances.
2. Change `HIREFLOW_JWT_ACTIVE_KEY_ID` to the new id; deploy again.
3. Wait at least the maximum access-token lifetime plus clock skew.
4. Remove the old key and deploy once more.

Refresh tokens are opaque, tenant-prefixed lookup handles whose SHA-256 hashes are stored under RLS.
Every refresh revokes the presented token and returns a replacement in the same family. Replaying a
predecessor revokes every live family member. Browser sessions keep refresh credentials only in a
HttpOnly/SameSite=Strict cookie and require a matching double-submit CSRF header; production Compose
forces the cookie `Secure` flag. JSON-body refresh remains available for non-browser automation clients.

## PostgreSQL backup and restore

Create a compressed logical backup using credentials from the secret manager:

```powershell
docker compose exec -T postgres pg_dump -U hireflow -d hireflow `
  --format=custom --no-owner --file=/tmp/hireflow.dump
docker compose cp postgres:/tmp/hireflow.dump .\backups\hireflow.dump
```

Restore into a newly created database first; never overwrite the only production copy:

```powershell
docker compose exec -T postgres createdb -U hireflow hireflow_restore_check
docker compose cp .\backups\hireflow.dump postgres:/tmp/hireflow.dump
docker compose exec -T postgres pg_restore -U hireflow -d hireflow_restore_check `
  --clean --if-exists --no-owner /tmp/hireflow.dump
```

Run `alembic current`, application smoke tests and row counts against the restored database before a
controlled cutover. Encrypt backups, set retention, and run scheduled restore drills; an untested backup is
not a recovery plan.

## Migration rollback

Before deployment record `alembic current`, take a backup and inspect the next migration. Prefer forward
fixes once a migration has served writes. If a verified migration is reversible and no new-version-only data
must be preserved:

```powershell
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic downgrade -1
```

The repository's `scripts/migration_smoke.ps1` performs `head → base → head` only in a guarded temporary
database. It must not be pointed at the business database.
