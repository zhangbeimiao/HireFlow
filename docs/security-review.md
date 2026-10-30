# Security review

HireFlow treats application authorization, tenant isolation, dependency hygiene, and
container boundaries as separate controls. Passing one layer does not replace the
others.

## Trust boundaries

- Internet-facing traffic terminates at the FastAPI service. Trusted hosts are
  allow-listed and browsers receive restrictive security headers.
- JWTs are signed by a secret supplied at runtime. Tokens require issuer, audience,
  expiry, not-before, issued-at, tenant, role, subject, and unique-token claims.
- Application queries execute through the restricted `hireflow_app` PostgreSQL role.
  Row-Level Security remains the final tenant boundary even if an application filter
  is omitted.
- RabbitMQ, PostgreSQL, Redis, metrics, and management ports bind to loopback by
  default. Production deployments should put them on private networks and expose
  only the API through a TLS-terminating ingress.
- Python and Go application containers run as non-root users.

## Automated controls

The CI security job runs the following checks before Docker integration tests:

| Control | Tool | Fails on |
| --- | --- | --- |
| Python dependency audit | `pip-audit` with OSV | known dependency vulnerability |
| Go dependency audit | `govulncheck` | reachable known vulnerability |
| Repository history scan | Gitleaks | detected secret in Git history or working tree |
| Source and configuration scan | Trivy | high/critical vulnerability with a remediation, or secret |
| Runtime image scan | Trivy | high/critical vulnerability with a remediation in API or Relay image |
| Runtime identity check | Docker Compose | API, consumer, or Relay starts as root |

The security regression suite additionally covers expired and malformed tokens,
role downgrade, tampered tenant claims, unauthenticated requests, cross-tenant
lookups, browser security headers, default-deny CORS behavior, and PostgreSQL RLS.

## Secrets and configuration

`.env.example` contains placeholders only. Copy it to the ignored `.env` file and
replace every placeholder before startup. Compose uses required-variable expansion
for passwords and signing secrets, so an unset secret fails closed. The migration
chain rotates the restricted database role password from runtime configuration.

The repository contains historical *development-only* credential strings in old,
immutable migration context. They are not valid production secrets and a fresh
installation immediately rotates the role password. Real credentials must never be
committed; if one is exposed, revoke it first and then remove it from Git history.

## Production checklist

- Generate independent, high-entropy values for PostgreSQL, RabbitMQ, Grafana, and
  JWT signing; store them in a managed secret service.
- Terminate TLS at the ingress and use TLS/mTLS for service-to-service traffic where
  the network is not fully trusted.
- Keep `HIREFLOW_BIND_ADDRESS` on a private interface and restrict management ports.
- Use a narrow trusted-host allow-list and keep cross-origin access disabled unless a
  specific production front-end origin is required.
- Rotate credentials, rebuild images, and rerun all scanners on a regular cadence.
- Forward structured logs to controlled storage; do not log JWTs, passwords, or
  complete connection strings.

The image gate uses `--ignore-unfixed`: a build cannot remediate a vendor advisory
until its base distribution publishes a patched package. Such findings remain visible
in periodic full reports and must be reassessed when Debian or Alpine releases a fix.
At the 2026-10-31 review, both images had zero fixable high/critical findings; the
Debian-based API image still reported unfixed operating-system advisories, while the
Alpine-based Relay image reported none.

This review documents the controls exercised by the project and is not a third-party
penetration test or a guarantee that future dependency versions are vulnerability-free.
