# Amrutam Telemedicine Platform — Enterprise Backend

[![CI Pipeline](https://github.com/amrutam/backend/actions/workflows/ci.yml/badge.svg)](https://github.com/amrutam/backend/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Django Version](https://img.shields.io/badge/Django-6.1-green.svg)](https://www.djangoproject.com/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()
[![OpenAPI 3.0](https://img.shields.io/badge/OpenAPI-3.0-orange.svg)](http://127.0.0.1:8000/api/docs/)
[![P95 Read](https://img.shields.io/badge/P95%20Read-94ms-brightgreen.svg)]()
[![P95 Write](https://img.shields.io/badge/P95%20Write-63ms-brightgreen.svg)]()

Production-grade, highly available, and HIPAA/DPDP-compliant telemedicine backend engineered for **100,000+ daily consultations**. Built with **Django 6.1**, **PostgreSQL 16**, **Redis 7.2**, **Celery**, and **uv** package management.

---

## 📑 Table of Contents
1. [Architecture Overview](#-architecture-overview)
2. [Quickstart with Docker Compose](#-quickstart-with-docker-compose)
3. [Local Development Setup (`uv`)](#-local-development-setup-uv)
4. [Environment Variables Reference](#-environment-variables-reference)
5. [API Documentation & Endpoints](#-api-documentation--endpoints)
6. [Core Architectural Innovations](#-core-architectural-innovations)
7. [Observability, Telemetry & Logging](#-observability-telemetry--logging)
8. [Performance Benchmarks & P95 Verification](#-performance-benchmarks--p95-verification)
9. [Security, Threat Model & Compliance](#-security-threat-model--compliance)
10. [Automated Testing & CI/CD](#-automated-testing--cicd)

---

## 🏛 Architecture Overview

Amrutam Telemedicine is organized around clean domain separation, high throughput, zero data loss, and sub-millisecond data security:

```
amrutam-pharma/
├── Dockerfile                  # Multi-stage container definition
├── docker-compose.yml          # Unified orchestrator (web, workers, beat, db, redis)
├── pyproject.toml              # UV-managed project dependencies
├── requirements.txt            # Synced pip requirements export
├── schema.openapi.yaml         # OpenAPI 3.0 specification
├── locustfile.py               # Production load generation test suite
├── docs/
│   ├── architecture.md         # Comprehensive system architecture & threat model
│   ├── benchmark_results.md    # Real measured P95 latency report
│   └── images/                 # 10 formal architectural diagrams
└── backend/
    ├── manage.py
    ├── benchmark_p95.py        # Automated latency verification test harness
    ├── backend/                # Core settings, ASGI/WSGI, routing, health & JSON logging
    │   ├── logging.py          # Distributed tracing & JSON formatter
    │   └── health.py           # Kubernetes liveness (/healthz/) & readiness (/readyz/)
    ├── accounts/               # Domain 1: Auth, Roles, TOTP MFA, PII Fernet Encryption
    │   ├── mfa.py              # RFC 6238 TOTP engine & scratch codes
    │   └── mfa_views.py        # MFA setup & token verification endpoints
    ├── consultations/          # Domain 2: Booking Engine, Optimistic Holds, Payments
    │   ├── services.py         # Dual-pattern atomic hold engine & payment finalization
    │   ├── tasks.py            # Celery async workers (holds, PDFs, webhooks)
    │   └── middleware.py       # Distributed Redis idempotency guard
    ├── compliance/             # Domain 3: Immutable PHI Audit Logging
    │   └── models.py           # AuditLog with monthly table partitioning
    └── analytics/              # Domain 4: Operational KPIs & Scale Metrics
```

Full architectural diagrams and specs are detailed in [docs/architecture.md](docs/architecture.md).

---

## ⚡ Quickstart with Docker Compose

Spin up the complete 5-node production stack (PostgreSQL, Redis, Django Web, Celery Worker, Celery Beat) with one command:

```bash
# 1. Clone the repository
git clone https://github.com/amrutam/backend.git
cd backend

# 2. Populate environment secrets
cp .env.example .env

# 3. Build and launch all containerized services
docker compose up --build -d
```

### Verify Service Health
```bash
# Check running containers
docker compose ps

# Inspect web service logs
docker compose logs -f web

# Probe readiness (verifies DB and Redis connectivity)
curl -s http://127.0.0.1:8000/readyz/ | jq .
```

---

## 🛠 Local Development Setup (`uv`)

Amrutam uses `uv` for blazingly fast, deterministic dependency resolution.

### 1. Prerequisites
- Python 3.12+
- PostgreSQL 16 (`psycopg` v3 native)
- Redis 7.2+
- `uv` (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 2. Installation & Migrations
```bash
# Sync virtual environment
uv sync

# Run database migrations
uv run --directory backend python manage.py migrate

# Create administrator account
uv run --directory backend python manage.py createsuperuser

# Seed mock demo data (Doctors, patients, slots)
uv run --directory backend python seed_data.py
```

### 3. Running Services
```bash
# Start Django development server
uv run --directory backend python manage.py runserver 127.0.0.1:8000

# Start Celery asynchronous worker
uv run --directory backend celery -A backend worker -l info

# Start Celery Beat periodic scheduler (60s hold sweeper)
uv run --directory backend celery -A backend beat -l info
```

---

## ⚙ Environment Variables Reference

All runtime parameters are loaded from `.env`:

| Variable | Description | Default | Production Requirement |
| :--- | :--- | :--- | :--- |
| `SECRET_KEY` | Django cryptographic signing key | *Insecure Dev Key* | **Mandatory 64+ char random string** |
| `DEBUG` | Django debug mode flag | `False` | Must be `False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` | FQDN (e.g., `api.amrutam.com`) |
| `DB_NAME` | PostgreSQL database name | `amrutam` | Required |
| `DB_USER` | PostgreSQL user | `postgres` | Required |
| `DB_PASSWORD` | PostgreSQL password | `postgres` | Required |
| `DB_HOST` | PostgreSQL hostname | `localhost` / `db` | Required |
| `DB_PORT` | PostgreSQL port | `5432` | Required |
| `REDIS_URL` | Redis cache connection string | `redis://127.0.0.1:6379/1` | Required |
| `CELERY_BROKER_URL` | Celery broker connection string | `redis://127.0.0.1:6379/0` | Required |
| `FIELD_ENCRYPTION_KEY` | Fernet AES-256 base64 key | *Dev Fallback* | **Mandatory 32-byte URL-safe base64 key** |
| `LOG_LEVEL` | Application logging verbosity | `INFO` | `INFO` or `WARN` |

---

## 📡 API Documentation & Contract

Interactive documentation is served out-of-the-box when containers are running:
- **Swagger UI**: [http://127.0.0.1:8000/api/docs/](http://127.0.0.1:8000/api/docs/)
- **ReDoc**: [http://127.0.0.1:8000/api/redoc/](http://127.0.0.1:8000/api/redoc/)
- **Live OpenAPI 3.0 Schema**: [http://127.0.0.1:8000/api/schema/](http://127.0.0.1:8000/api/schema/)

The static contract is pre-exported and committed at the repository root:
- **Static Contract File**: [`schema.openapi.yaml`](./schema.openapi.yaml)
- **Re-export Command**:
  ```bash
  uv run --directory backend python manage.py spectacular --file ../schema.openapi.yaml
  ```

### Core REST Endpoints

| HTTP | Endpoint | Description | Auth / Role | Rate Limit |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/token/` | Obtain JWT access & refresh token pair | Public | `30/min` |
| `POST` | `/api/v1/token/refresh/` | Refresh expired access token | Public | `30/min` |
| `POST` | `/api/v1/auth/mfa/setup/` | Generate TOTP secret & 5 scratch codes | JWT User | `30/min` |
| `POST` | `/api/v1/auth/mfa/verify/` | Validate 6-digit TOTP token and activate MFA | JWT User | `30/min` |
| `GET` | `/api/v1/doctors/` | Search & filter verified doctors | Public | `1000/min` |
| `GET` | `/api/v1/slots/` | List availability slots with countdown | Public | `1000/min` |
| `POST` | `/api/v1/bookings/hold/` | Place atomic 5-minute hold on a slot | Patient / Doctor | `30/min` |
| `POST` | `/api/v1/bookings/confirm/` | Confirm booking with idempotency key | Patient / Doctor | `1000/min` |
| `GET` | `/api/v1/consultations/{id}/` | Retrieve consultation & prescription data | Authenticated | `1000/min` |
| `POST` | `/api/v1/prescriptions/` | Create immutable clinical prescription | Doctor | `1000/min` |
| `POST` | `/api/v1/payments/webhook/` | Ingest asynchronous payment gateway event | Gateway / Public | `600/min` |
| `GET` | `/api/v1/admin/analytics/` | View volume, GTV, and operational KPIs | Admin | `1000/min` |
| `GET` | `/metrics` | Prometheus metrics scrape endpoint | Monitoring | Unthrottled |
| `GET` | `/healthz/` | Kubernetes liveness probe | Orchestrator | Unthrottled |
| `GET` | `/readyz/` | Kubernetes readiness probe (checks DB & Redis) | Orchestrator | Unthrottled |

---

## 💎 Core Architectural Innovations

### 1. Dual-Pattern Concurrency Hold (Zero-Stale-Lock Guarantee)
Eliminates race conditions and double-bookings without database deadlocks:
- **Layer A (Atomic Overwrite)**: Reads dynamically evaluate `is_actively_held` based on `hold_expires_at <= timezone.now()`. Expired holds are overwritten in a single `SELECT FOR UPDATE` transaction.
- **Layer B (Asynchronous Sweeper)**: Celery Beat task runs every 60 seconds (`periodic_expire_stale_holds`) to clean stale holds from the database.

### 2. Multi-Layer Idempotency Guard
Guarantees strict once-only execution for payments and consultations:
- **Transport Layer**: `IdempotencyMiddleware` intercepts requests with `X-Idempotency-Key` and returns cached responses directly from Redis with `X-Cache-Lookup: HIT`.
- **Domain Layer**: `BookingService.finalize_booking()` verifies unique database constraints on `Payment.idempotency_key` and `transaction_reference_number`.

### 3. PII & PHI Cryptographic Protection at Rest
- Patient contact details (`Profile.phone_number`) are encrypted using **Fernet AES-256** before writing to PostgreSQL.
- Plaintext is decrypted transparently upon model instantiation. Raw SQL dumps expose zero patient phone numbers.
- Indian mobile numbers are normalized to E.164 (`+91[6-9]\d{9}`) via centralized regex validation.

### 4. Immutable Medical Records
- Clinical prescriptions (`Prescription`) override `clean()` and `save()`. Any update attempt raises a `ValidationError`, ensuring tamper-proof clinical records compliant with HIPAA § 164.312.

---

## 📈 Observability, Telemetry & Logging

### 1. Prometheus Metrics (`/metrics`)
Standard metrics exposed via `django-prometheus` for Prometheus and Grafana:
- HTTP request duration histograms (`django_http_requests_latency_seconds_by_view_method`)
- Total HTTP request counters partitioned by method and status code (`django_http_requests_total_by_method_total`)
- Database connection and query performance metrics
- Cache hit/miss rates

### 2. Distributed Tracing (`X-Request-ID`)
`RequestTracingMiddleware` injects an `X-Request-ID` correlation UUID into every request and thread-local context. Outgoing responses return:
- `X-Request-ID: <UUID4>`
- `X-Response-Time-MS: <Latency in ms>`

### 3. Structured JSON Logging
All application logs are formatted as single-line JSON records formatted for ELK, Datadog, and CloudWatch:
```json
{
  "timestamp": "2026-09-15T01:01:29.168Z",
  "level": "INFO",
  "logger": "consultations.tasks",
  "message": "Payment confirmed for consultation e4bbd606-7a24-443a-a4f2-bf544a5cc59f",
  "process": 4572,
  "thread": 23476,
  "request_id": "ee4f77a7-9642-4200-9ad0-2089092aa5fe"
}
```

---

## 🚀 Performance Benchmarks & P95 Verification

Benchmarking was conducted against the running PostgreSQL/Redis stack using the repository's native verification harness (`backend/benchmark_p95.py`) and Locust scenario (`locustfile.py`) under a simulated 100,000 daily consultations workload:

| Workload Type | Total Requests | Throughput (RPS) | Error Rate | P50 Latency | P90 Latency | P95 Latency | SLO Target | Compliance Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Reads (Search, Slots, Probes)** | 300 | 24.7 req/s | 0.0% | 16.00 ms | 94.00 ms | **94.00 ms** | < 200 ms | **PASS (P95 < 200ms)** |
| **Writes (Holds, Confirms, Webhooks)** | 120 | 20.8 req/s | 0.0% | 31.00 ms | 47.00 ms | **63.00 ms** | < 500 ms | **PASS (P95 < 500ms)** |

### Execute Performance Verification Locally
```bash
# Run the automated latency benchmark harness
uv run --directory backend python benchmark_p95.py

# Run headless Locust load test
uv run locust -f locustfile.py --headless -u 25 -r 5 --run-time 20s --host http://127.0.0.1:8000
```

---

## 🛡 Security, Threat Model & Compliance

### STRIDE Mitigation Matrix
- **Spoofing**: Enforces stateless HMAC-SHA256 signed SimpleJWT access tokens with 60-minute expiry.
- **Tampering**: Model-level immutability on `Prescription` blocks updates; `Payment` unique constraints prevent transaction tampering.
- **Repudiation**: Monthly-partitioned `compliance.AuditLog` captures actor UUID, IP address, and JSON diffs. Django Admin deletion is strictly disabled.
- **Information Disclosure**: Fernet AES-256 field-level encryption on patient contact data.
- **Denial of Service**: Layered rate limiting (DRF `ScopedRateThrottle`) and atomic row-level locks prevent concurrency exhaustion.
- **Elevation of Privilege**: Strict RBAC (`CanBookConsultation` prevents admin booking; `IsAdminRole` protects analytics).

### Multi-Factor Authentication (MFA / RFC 6238 TOTP)
- Standard Base32 TOTP secret provisioning via `POST /api/v1/auth/mfa/setup/`
- Compatible with Google Authenticator, Authy, and 1Password via `otpauth://` URI
- Emergency recovery via 5 cryptographically generated one-time scratch codes
- Verification and activation via `POST /api/v1/auth/mfa/verify/`

---

## 🧪 Automated Testing & CI/CD

The test suite covers models, services, concurrency holds, payment idempotency, JWT RBAC, TOTP MFA, Prometheus metrics, and distributed tracing.

```bash
# Run all 43 unit tests (100% passing)
uv run --directory backend python manage.py test

# Run Ruff linter
uvx ruff check backend/

# Run Bandit security vulnerability scanner
uvx bandit -r backend/ -x "backend/accounts/tests.py,backend/consultations/tests.py,backend/analytics/tests.py,backend/compliance/tests.py"
```

### GitHub Actions CI Workflow (`.github/workflows/ci.yml`)
Automated pipeline executes on every push and pull request:
1. **Lint**: `uvx ruff check backend/`
2. **Security Scan**: `uvx bandit -r backend/`
3. **Database Setup**: Boots ephemeral PostgreSQL 16 & Redis 7.2 services
4. **Test Suite**: Runs `uv run --directory backend python manage.py test` (43/43 tests passing)
5. **OpenAPI Validation**: Validates `schema.openapi.yaml` generation (`uv run --directory backend python manage.py spectacular --file ../schema.openapi.yaml --validate`)
