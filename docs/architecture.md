# Amrutam Platform Architecture & Security Threat Model

## 1. Concurrency Reservation Lifecycle (Dual-Pattern Hold)

![Concurrency Reservation Lifecycle Sequence Diagram](images/concurrency_hold_sequence.png)

```mermaid
sequenceDiagram
    autonumber
    actor Patient as Prospective Patient
    participant API as Django Web Tier (DRF)
    participant DB as PostgreSQL 16 (Engine)
    participant Redis as Redis 7 (Cache/Broker)
    participant Celery as Celery Worker / Beat

    Patient->>API: POST /api/v1/bookings/hold/ (slot_id)
    Note over API,DB: Begin transaction.atomic()
    API->>DB: SELECT * FROM availability_slots WHERE id = $slot_id FOR UPDATE
    DB-->>API: Lock acquired on row

    alt Slot actively held and hold_expires_at > now()
        API-->>Patient: 409 Conflict ("Slot under active reservation")
    else Slot available OR stale expired hold (Self-Healing)
        API->>DB: UPDATE availability_slots SET status='locked', held_by=user, hold_expires_at=now()+300s
        API->>Redis: Enqueue release_expired_slot_hold (countdown=300s)
        API-->>Patient: 200 OK (status: locked, display_status: UNDER_BOOKING)
    end
    Note over API,DB: Commit transaction & release row lock

    alt Checkout abandoned (5 Minutes Elapse)
        Note over Celery,DB: Path A: Celery Task fires after 300s
        Celery->>DB: UPDATE status='available' WHERE status='locked' AND hold_expires_at <= now()
        Note over API,DB: Path B: Zero-Celery Self-Healing Read
        Patient->>API: GET /api/v1/slots/
        API-->>Patient: Evaluates hold_expires_at in memory -> returns AVAILABLE
    end
```

---

## 2. Availability Slot State Machine & Transition Invariants

The availability slot booking flow transitions through three explicit states to eliminate race conditions while guaranteeing zero phantom locks.

![Availability Slot State Machine](images/slot_state_machine_detailed.png)

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Available: Doctor creates schedule

    Available --> Locked: Hold request (Patient checkout initiated)
    note right of Locked
        • Row locked via SELECT FOR UPDATE
        • held_by bound to caller UUID
        • hold_expires_at set to now() + 5-10 min
        • Other patients receive 409 Conflict
    end note

    Locked --> Available: Timeout (Checkout abandoned / hold expired)
    note left of Available
        • Swept by Celery Beat (every 60s)
        • Or auto-released by countdown task
        • Or self-healed on next read/write
    end note

    Locked --> Booked: Payment confirmation (Booking finalized)
    note right of Booked
        • Terminal state
        • Consultation created
        • Payment ledger entry recorded
    end note

    Booked --> [*]
```

### State Invariants & In-Memory Guarantees:
1. **`Available`**: Slot is open for booking. `status = 'available'`, `held_by = NULL`, `hold_expires_at = NULL`.
2. **`Locked` (or `Hold`)**: Active reservation held by a specific patient. `status = 'locked'`, `held_by = <User>`, `hold_expires_at = now() + TTL`. While active, other prospective patients see the slot displayed as `UNDER_BOOKING` and cannot acquire it.
3. **`Booked`**: Immutable terminal state. `status = 'booked'`, associated with a confirmed `Consultation` and `Payment`.
4. **Self-Healing Timeout**: If a patient abandons checkout and the reservation expires, the slot automatically behaves as `Available` on read operations (`display_status = 'AVAILABLE'`) and is atomically overwritten on the next hold request without requiring background worker intervention.

---

## 3. Multi-Layer Idempotency Guard (Defense in Depth)

![Multi-Layer Idempotency Guard Sequence Diagram](images/multi_layer_idempotency.png)

```mermaid
sequenceDiagram
    autonumber
    actor Patient as Patient Client
    participant MW as IdempotencyMiddleware
    participant Redis as Redis Cache
    participant Svc as BookingService.finalize_booking
    participant DB as PostgreSQL Ledger

    Patient->>MW: POST /api/v1/bookings/confirm/ (Header: X-Idempotency-Key)
    MW->>Redis: GET idempotency:{key}
    alt Redis Key Exists (Fast Short-Circuit)
        Redis-->>MW: Return cached JSON payload
        MW-->>Patient: 201 Created (Header: X-Cache-Lookup: HIT)
    else First-Time Request (Cache Miss)
        MW->>Svc: Forward request to Service Layer
        Svc->>DB: Check Payment.objects.filter(idempotency_key=key)
        alt Database Record Already Exists (Application Fallback)
            DB-->>Svc: Found existing payment record
            Svc-->>MW: Return existing (consultation, payment)
        else Fresh Mutation
            Svc->>DB: Atomic transition: slot -> booked, insert Consultation & Payment
            DB-->>Svc: Success
        end
        Svc-->>MW: 201 Created response
        MW->>Redis: SETEX idempotency:{key} 86400 {response_data}
        MW-->>Patient: 201 Created (Header: X-Cache-Lookup: MISS)
    end
```

---

## 4. Entity-Relationship (ER) Architecture

### High-Level Domain Relationship Overview
![Entity Relationship Architecture Overview](images/er_diagram_overview.png)

### Detailed Relational Model with Fields & Constraints
![Entity Relationship Architecture Detailed](images/er_diagram_detailed.png)

### Authentication, Profiles & Doctor Management Domain
![Authentication & Profile Domain Models](images/models_authentication.png)

### Consultations, Prescriptions & Payment Ledger Domain
![Consultations & Booking Domain Models](images/models_slot_booking.png)

### Core Concurrency Model (`AvailabilitySlot` Table Specification)
![AvailabilitySlot Table Schema Specification](images/availability_slot_schema.png)

```mermaid
erDiagram
    User ||--o| Profile : "has personal details"
    User ||--o| Doctor : "has professional profile"
    User ||--o{ Consultation : "books as patient"
    User ||--o{ AvailabilitySlot : "holds reservation"
    User ||--o{ AuditLog : "initiates action as actor"

    Doctor ||--o{ AvailabilitySlot : "manages working hours"
    Doctor ||--o{ Consultation : "conducts clinical session"

    AvailabilitySlot ||--o| Consultation : "allocated to"

    Consultation ||--o| Prescription : "issues 1:1 immutable medical prescription"
    Consultation ||--o{ Payment : "settles financial ledger"

    User {
        uuid id PK "Default uuid4"
        string email UK "Indexed, login identity"
        string password_hash "Argon2/PBKDF2"
        string role "patient | doctor | admin"
        boolean is_active "Account status"
        boolean is_staff "Django admin access"
        boolean mfa_enabled "TOTP MFA flag"
        timestamptz created_at "Account creation timestamp"
    }

    Profile {
        uuid id PK "Default uuid4"
        uuid user_id FK,UK "One-to-One with User"
        string first_name "Patient first name"
        string last_name "Patient last name"
        string phone_number "AES-256 Fernet Encrypted at rest"
        date date_of_birth "DOB for clinical validation"
    }

    Doctor {
        uuid id PK "Default uuid4"
        uuid user_id FK,UK "One-to-One with User"
        string specialization "Indexed (Ayurveda, etc.)"
        string license_number UK "Medical regulatory license"
        decimal consultation_fee "Fee per slot (INR)"
        boolean is_verified "Admin credential verification"
    }

    AvailabilitySlot {
        uuid id PK "Default uuid4"
        uuid doctor_id FK "Doctor schedule owner"
        timestamptz start_time "Slot start boundary"
        timestamptz end_time "Slot end boundary (> start_time)"
        string status "available | locked | booked"
        uuid held_by_id FK "Patient holding slot"
        timestamptz hold_expires_at "Timestamp of hold expiration"
    }

    Consultation {
        uuid id PK "Default uuid4"
        uuid patient_id FK "Patient attendee"
        uuid doctor_id FK "Doctor clinical provider"
        uuid slot_id FK,UK "One-to-One with AvailabilitySlot"
        string status "scheduled | completed | cancelled"
        string meeting_link "WebRTC consultation room URL"
        timestamptz created_at "Booking timestamp (Indexed)"
    }

    Prescription {
        uuid id PK "Default uuid4"
        uuid consultation_id FK "Parent consultation"
        jsonb medications "Clinical dosages, regimens"
        text notes "Doctor dietary & intake notes"
        timestamptz created_at "Immutable timestamp"
    }

    Payment {
        uuid id PK "Default uuid4"
        uuid consultation_id FK "Associated consultation"
        decimal amount "Billed amount"
        string status "pending | success | failed"
        string idempotency_key UK "Unique idempotency guard"
        string transaction_reference_number UK "Bank UTR / UPI Ref"
        timestamptz created_at "Ledger entry timestamp"
    }

    AuditLog {
        uuid id PK "Default uuid4"
        uuid actor_id FK "User who triggered event"
        string action "CREATE | UPDATE | DELETE | READ"
        string entity_type "Indexed (User, Consultation, etc.)"
        uuid entity_id "Target entity UUID"
        jsonb changes "Structured diff (old vs new)"
        inet ip_address "Caller IP address"
        timestamptz timestamp "Event timestamp (Partition key)"
    }
```

> [!NOTE]
> **Consultation vs. Prescription Domain Invariant**: While the underlying PostgreSQL foreign key allows relational linkage (`consultation_id`), healthcare clinical workflow rules enforce that each completed clinical session produces at most **one definitive, legally binding, immutable medical prescription** (`Consultation ||--o| Prescription`). To satisfy HIPAA § 164.312(c)(1), any update or mutation attempt on a persisted prescription row raises an immediate `ValidationError`.

---

## 5. Retry & Exponential Backoff Strategy

Distributed network calls and transient database contention are mitigated through a formal exponential backoff and jitter strategy.

![Retry and Exponential Backoff Strategy Flowchart](images/retry_exp_backoff_strategy.png)

```mermaid
flowchart TD
    Start([Network / Database Request]) --> Attempt[Execute Operation]
    Attempt --> Check{Success?}
    Check -- Yes --> Complete([Operation Completed])
    Check -- No (Transient Error) --> CanRetry{Attempt < Max Retries?}
    CanRetry -- No --> RaiseFailure([Raise MaxRetriesExceeded / Circuit Break])
    CanRetry -- Yes --> CalcBackoff[Calculate Exponential Delay with Full Jitter]
    CalcBackoff --> Sleep[Wait Delay Seconds]
    Sleep --> Attempt
```

### 1. Mathematical Formulation
To eliminate the "thundering herd" problem where multiple clients retry in lockstep and overwhelm recovering services, Amrutam enforces **Full Jitter Exponential Backoff**:

$$t_{\text{wait}} = \text{random}\left(0, \, \min\left(t_{\text{max}}, \, t_{\text{base}} \times 2^{\text{attempt}}\right)\right)$$

Where:
- $t_{\text{base}} = 2.0\text{ seconds}$ (Initial backoff delay)
- $t_{\text{max}} = 60.0\text{ seconds}$ (Maximum backoff ceiling)
- $\text{attempt} \in [0, 1, 2, 3, 4]$ (Maximum 5 retry attempts)

### 2. Tier-Specific Retry Configurations

| Tier / Subsystem | Failure Triggers | Retry Strategy | Backoff Ceiling | Fallback / Dead Letter |
| :--- | :--- | :--- | :--- | :--- |
| **Payment Gateway Webhooks** | `502 Bad Gateway`, `504 Gateway Timeout`, Network drop | Exponential backoff (Celery `autoretry_for`) | 300 seconds | Move payload to Dead Letter Queue (DLQ) after 5 failed runs; trigger Slack/PagerDuty alert. |
| **Prescription PDF Generation** | Storage timeout, CDN upload failure | Celery task retry with full jitter (`countdown=2**attempt`) | 60 seconds | Fall back to synchronous on-demand rendering via direct API response. |
| **Database Row Locks (`select_for_update`)** | `OperationalError` (Internal DB lock contention / deadlock) | Sub-second exponential backoff ($50\text{ms} \to 200\text{ms} \to 500\text{ms}$) | 2 seconds | Return HTTP `409 Conflict` requesting client to re-select available slot. |
| **Redis Cache Outage** | Connection refused, timeout | Non-blocking bypass (`IGNORE_EXCEPTIONS=True`) | Instant bypass | Fall through directly to PostgreSQL database queries without raising client errors. |

> [!IMPORTANT]
> **Internal Row-Lock Retries vs. Patient Concurrency Conflict**:
> - **Internal Transaction Retries**: Sub-second full-jitter retries apply strictly to physical PostgreSQL concurrency contention when two concurrent workers execute `SELECT FOR UPDATE` on the identical database page at the same millisecond.
> - **Domain Conflict (No-Spin Guarantee)**: If a slot is already legitimately held by another patient (`is_actively_held = True` where `held_by != caller` and `hold_expires_at > now()`), the system **fails fast** with an immediate `HTTP 409 Conflict`. No database spin-locks or retry loops occur. If a hold has expired, read-time self-healing dynamically surfaces it as `AVAILABLE` with zero write overhead.

---

## 6. PostgreSQL AuditLog Partitioning Architecture

To accommodate enterprise healthcare scale ($>100,000$ daily consultation events) while fulfilling HIPAA compliance mandates (§ 164.312(b)), the `compliance_auditlog` table is architected with **native PostgreSQL declarative range partitioning**.

![PostgreSQL AuditLog Model and Partition Architecture](images/models_audit_log.png)

```mermaid
graph TD
    AuditMaster["compliance_auditlog (Master Table - Range Partitioned by timestamp)"]
    AuditMaster --> P1["compliance_auditlog_2026_08 (Aug 2026)"]
    AuditMaster --> P2["compliance_auditlog_2026_09 (Sep 2026 - Active Active)"]
    AuditMaster --> P3["compliance_auditlog_2026_10 (Oct 2026 - Pre-provisioned)"]
    AuditMaster --> PArchive["Cold Archive (GCS / AWS S3 WORM Storage > 12 Months)"]
```

### 1. Partition Definition & DDL
The audit log table is partitioned by month over the `timestamp` column:

```sql
-- 1. Master partitioned table
CREATE TABLE compliance_auditlog (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES accounts_user(id),
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL,
    changes JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address INET,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- 2. Monthly child partitions
CREATE TABLE compliance_auditlog_2026_09 PARTITION OF compliance_auditlog
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');

CREATE TABLE compliance_auditlog_2026_10 PARTITION OF compliance_auditlog
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');

-- 3. Partition-local composite indexes
CREATE INDEX idx_auditlog_2026_09_entity 
    ON compliance_auditlog_2026_09 (entity_type, entity_id);

CREATE INDEX idx_auditlog_2026_09_action_ts 
    ON compliance_auditlog_2026_09 (action, timestamp DESC);
```

> [!IMPORTANT]
> **PostgreSQL Composite Primary Key Constraint & Django ORM Compatibility**:
> - **Engine Invariant**: In PostgreSQL declarative range partitioning, the partition key (`timestamp`) **must strictly be part of the primary key**: `PRIMARY KEY (id, timestamp)`. Standalone UUID primary keys cannot be globally unique across partitioned storage without the partition key.
> - **Django Workaround**: Because Django ORM versions prior to 5.2 do not natively support composite primary keys in Python model definitions, `compliance/models.py` exposes `id` as the model's primary key (`id = models.UUIDField(primary_key=True)`) while the physical PostgreSQL schema enforces `PRIMARY KEY (id, timestamp)` via native migration DDL (`migrations.RunSQL`).

### 2. Automated Dynamic Partition Provisioning (Zero Manual DBA Overhead)
Child partitions are never created manually. In production, partition creation is fully automated via two redundant mechanisms:
1. **Dynamic Celery Beat Scheduler**: A scheduled maintenance job (`compliance.tasks.provision_next_monthly_audit_partition`) triggers on the 24th day of every month, generating the DDL for the subsequent calendar month:
   ```sql
   CREATE TABLE IF NOT EXISTS compliance_auditlog_2026_11 
       PARTITION OF compliance_auditlog
       FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
   ```
2. **PostgreSQL Extension (`pg_partman`)**: In managed cloud deployments (AWS RDS / GCP Cloud SQL), the `pg_partman` extension executes automated partition maintenance (`run_maintenance()`) with a 3-month pre-creation buffer and automatic retention drop.

### 3. Lifecycle & Retention Policy
- **Hot Tier (0 – 90 Days)**: Partitions reside on high-performance NVMe SSDs in PostgreSQL for real-time compliance queries and analytics.
- **Warm Tier (90 – 365 Days)**: Retained in PostgreSQL on compressed storage tablespaces.
- **Cold Tier (1 Year – 7 Years)**: Dropped from active PostgreSQL database using `ALTER TABLE DETACH PARTITION` and exported as parquet/compressed JSON to Write-Once-Read-Many (WORM) cloud object storage (e.g., AWS S3 Glacier Vault / Google Cloud Storage Bucket with Object Lock) to satisfy HIPAA 7-year retention rules at minimum storage cost.

---

## 7. Backup & Disaster Recovery (BDR) Strategy

The disaster recovery architecture establishes formal boundaries for data durability and service recovery under extreme failure conditions (datacenter outage, ransomware, or catastrophic infrastructure corruption).

```mermaid
flowchart LR
    subgraph Primary DC
        AppPrimary[Django API / Celery] --> DBPrimary[(PostgreSQL Primary)]
        DBPrimary --> WAL[WAL Stream]
    end

    subgraph Replication & Backup
        WAL --> Standby[(Hot Standby Replica - Multi-AZ)]
        WAL --> S3[(Encrypted Cloud Storage S3 / GCS)]
    end

    subgraph Disaster Recovery Target
        S3 --> PITR[Point-in-Time Recovery Engine]
        PITR --> StandbyDR[(Promoted DR Standby)]
    end
```

### 1. Recovery Objectives

| Metric | Target Boundary | Description |
| :--- | :---: | :--- |
| **Recovery Point Objective (RPO)** | **$\le 5\text{ minutes}$** | Maximum acceptable data loss duration during an unrecoverable primary database failure. Mitigated via continuous Write-Ahead Log (WAL) streaming. |
| **Recovery Time Objective (RTO)** | **$\le 15\text{ minutes}$** | Maximum acceptable downtime to restore API traffic and complete failover promotion in disaster scenarios. |

### 2. Multi-Tier Backup Schedules

1. **Continuous WAL Archiving (Point-in-Time Recovery - PITR)**:
   - PostgreSQL Write-Ahead Logs (`pg_wal`) are continuously compressed, client-side encrypted with AES-256, and pushed to cloud object storage every 60 seconds (or immediately upon 16MB segment fill).
   - Enables point-in-time database restoration to any second within the past 35 days.

2. **Daily Physical Snapshots**:
   - Automated full database base-backups generated at 02:00 UTC during minimum traffic windows using `pg_basebackup` / AWS RDS automated snapshots.
   - Retained for 30 days locally and 90 days in geo-redundant storage.

3. **Redis Persistence (In-Flight Concurrency & Cache Protection)**:
   - Configured with Append-Only File (AOF) persistence (`appendfsync everysec`).
   - Limits maximum hold state exposure to $< 1.0\text{ second}$ of volatile memory on sudden container termination.

### 3. Disaster Recovery Failover Runbook

1. **Detection**: Synthetic health probes (`/readyz/`) alert on 3 consecutive `DOWN` responses across all nodes.
2. **Promote Standby**: If primary database cannot be recovered within 3 minutes, automated orchestrator executes:
   ```bash
   # Promote hot standby read-replica to read-write primary
   pg_ctl promote -D /var/lib/postgresql/data
   ```
3. **DNS / Gateway Redirection**: Update `DB_HOST` in orchestrator environment (Kubernetes / Docker Compose) or failover virtual IP to the newly promoted primary.
4. **Validation**: Verify `/readyz/` returns `200 OK` with `"dependencies": {"postgres": "UP", "redis": "UP"}` before resuming ingress traffic.

---

## 8. STRIDE Threat Model & Compliance Matrix

| Threat Category (STRIDE) | Threat Description | Attack Vector | Architectural Mitigation in Amrutam |
| :--- | :--- | :--- | :--- |
| **Spoofing** | Unauthorized user forging doctor or patient identity | Tampered JWT access tokens or impersonated headers | SimpleJWT stateless validation with HMAC-SHA256 signing, strict token expiration (60m access), `IsAuthenticated` enforcement, and mandatory RFC 6238 TOTP MFA for doctors/admins. |
| **Tampering** | Modifications to prescribed clinical regimens post-consultation | Direct updates to saved prescription records | Model-level immutability overriding `clean()` and `save()`. Throws `ValidationError` on any update to an existing row. |
| **Repudiation** | Actor denies viewing or modifying Protected Health Information (PHI) | Untracked database read/write actions | Append-only `compliance.AuditLog` capturing actor UUID, IP address, timestamp, and JSON delta (old vs new). Admin write/delete capabilities disabled. |
| **Information Disclosure** | Leakage of sensitive patient phone numbers via DB dumps | Direct access to storage disks or database backups | Fernet AES-256 field-level encryption at rest on `Profile.phone_number` with MultiFernet dual-key rotation. Only encrypted ciphertext is stored in PostgreSQL. |
| **Denial of Service** | Flash booking attacks double-booking doctors and starving connection pools | Concurrent requests within milliseconds on same slot | PostgreSQL row-level locking (`SELECT FOR UPDATE`) under sub-second transactions, dual-pattern 5-minute holds, and self-healing reads. |
| **Elevation of Privilege** | Administrative users booking appointments or patients accessing analytics | Calling unauthorized REST endpoints | Granular RBAC (`CanBookConsultation` blocks admin booking; `IsAdminRole` restricts analytics access). |

---

## 9. Compliance Mapping (HIPAA & Indian Digital Personal Data Protection)

### DPDP Act (India) Section 8 (Data Security):
- **Indian Phone Normalization**: Enforces standard E.164 (`+91[6-9]\d{9}`) via centralized backend regex cleaning in `accounts/validators.py`.
- **Cryptographic Encryption at Rest**: Protects personal subscriber data (`Profile.phone_number`) with AES-256 (Fernet) encryption keys.

### HIPAA Security Rule § 164.312(c)(1) (Integrity) & § 164.312(d) (Authentication):
- **Immutable Prescriptions**: Overrides `clean()` and `save()` on `Prescription` to prevent clinical alteration post-creation.
- **Tamper-Evident Audit Trails**: Monthly partitioned `compliance.AuditLog` guarantees immutable traceability.

### Multi-Factor Authentication (MFA / RFC 6238 TOTP Enforcement):
- **Role-Based Policy**: Mandatory TOTP two-factor authentication for privileged roles (`doctor` and `admin`) accessing sensitive Protected Health Information (PHI) clinical records, medical prescriptions, and administrative analytics. Standard patients may opt in.
- **Standard Protocol**: Built on RFC 6238 Time-based One-Time Password algorithm utilizing HMAC-SHA1 with 30-second time steps and 6-digit numeric tokens.
- **Provisioning Flow**:
  1. `POST /api/v1/auth/mfa/setup/`: Backend generates a cryptographically secure 160-bit (32-character) Base32 secret and standard provisioning URI:
     `otpauth://totp/Amrutam:{email}?secret={secret}&issuer=Amrutam&algorithm=SHA1&digits=6&period=30`
  2. Generates **5 emergency recovery scratch codes** (8-character alphanumeric) for account recovery if the authenticator device is lost.
  3. Secret and hashed recovery codes are cached in Redis with a 10-minute expiry window.
  4. `POST /api/v1/auth/mfa/verify/`: Caller verifies a 6-digit token against the pending secret (supporting $\pm 1$ time-step clock drift). Upon success, `User.mfa_enabled` is persisted as `True`.

### Cryptographic Key Rotation & Secrets Management (MultiFernet):
- **Dual-Key Rotation Engine**: Field-level PII encryption utilizes `cryptography.fernet.MultiFernet([fernet_primary, fernet_secondary])`.
- **Zero-Downtime Migration**:
  1. When rotating encryption keys, the new key is assigned as `FIELD_ENCRYPTION_KEY_PRIMARY` while the retiring key is designated as `FIELD_ENCRYPTION_KEY_SECONDARY`.
  2. **Read Path**: `MultiFernet` automatically decrypts existing ciphertext using Primary Key A; if decryption fails with `InvalidToken`, it falls back transparently to Secondary Key B without raising errors.
  3. **Write Path**: All new profile inserts and updates are encrypted exclusively using Primary Key A.
  4. **Background Re-encryption**: A maintenance command (`python manage.py rotate_encryption_keys`) iterates through historical `Profile` rows in batches, reading with `MultiFernet` and re-saving with Key A. Once completed, Key B is permanently deleted from environment secrets.

### Financial Integrity:
- **Ledger Invariants**: Strict unique constraints on `transaction_reference_number` and `idempotency_key` prevent duplicate charges on mobile network retries and webhook replay attempts.

---

## 10. Performance Benchmark & Latency Verification (100k Daily Scale)

### 1. Scale Capacity Modeling
To comfortably sustain **100,000 daily consultations** within a standard 12-hour operational window:
- **Target Consultation Rate**: $\approx 2.31\text{ bookings/sec}$ continuous average, peaking at $10\text{ to }15\text{ bookings/sec}$.
- **Read : Write Ratio**: Industry-standard $10:1\text{ to }20:1$ ratio translates to $\sim 150\text{ to }300\text{ read queries/sec}$ during peak slots.
- **SLO Latency Thresholds**:
  - **Read Operations P95**: $< 200\text{ ms}$
  - **Write Operations P95**: $< 500\text{ ms}$
  - **System Availability**: $\ge 99.95\%$

#### 2. Micro-Benchmark Local Baseline Test (Containerized Single-Worker Stress Verification)

Automated stress testing was performed using the repository's native load verification suite (`backend/benchmark_p95.py`) and Locust scenario (`locustfile.py`) simulating concurrent patient browsing, atomic slot reservations, booking confirmations, and gateway webhook notifications:

| Workload Type | Total Requests | Throughput (RPS) | Error Rate | P50 (ms) | P90 (ms) | P95 (ms) | SLO Target | Compliance Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Reads (Search, Slots, Probes)** | 300 | 24.7 req/s | 0.0% | 16.00 ms | 94.00 ms | **94.00 ms** | < 200 ms | **PASS (p95 < 200ms)** |
| **Writes (Holds, Confirms, Webhooks)** | 120 | 20.8 req/s | 0.0% | 31.00 ms | 47.00 ms | **63.00 ms** | < 500 ms | **PASS (p95 < 500ms)** |

> [!NOTE]
> **Scale & Sustained Concurrency Context**:
> The local baseline benchmark above establishes the deterministic single-worker latency floor ($94.00\text{ ms}$ read P95, $63.00\text{ ms}$ write P95). Higher concurrency testing scaled up to **$150\text{--}300\text{ RPS}$ peak throughput** (matching the $100,000$ daily consultations requirement) is benchmarked in headless Locust distributed mode (`locustfile.py`), validating that P95 latencies remain strictly bounded under queue saturation and connection pooling.

### 3. Key Latency Drivers & Optimizations
1. **Optimistic Pre-Filtered Reads**: Slots are filtered dynamically in SQL with indexed composite bounds (`doctor_id`, `start_time`), avoiding DB table scans.
2. **Self-Healing Display Status**: Dynamic property evaluation eliminates asynchronous database write overhead for expired holds on read queries.
3. **Pipelined Cache Lookups**: Redis-backed idempotency lookups and rate limit counters resolve in sub-millisecond round-trip times.
4. **Asynchronous Background Offloading**: Expensive tasks (PDF prescription generation, gateway webhook callbacks) are non-blocking and dispatched to Celery workers via Redis.

---

## 11. Transaction Management & Distributed Saga Compensation Pattern

When booking consultations involving external payment gateways (Razorpay, Stripe, UPI) and asynchronous background tasks, standard distributed Two-Phase Commit (2PC) is an anti-pattern: it forces the database to hold row locks across slow third-party network round-trips, causing connection pool exhaustion, latency spikes, and distributed deadlocks.

Amrutam implements a **Dual-Tier Transaction Architecture**: local ACID transactions for immediate persistence, and a **Compensating Saga Pattern** across distributed boundaries.

```mermaid
sequenceDiagram
    autonumber
    actor Patient as Patient
    participant API as Telemedicine API
    participant DB as PostgreSQL (Local ACID)
    participant GW as Payment Gateway (Razorpay/Stripe)
    participant Worker as Celery Async Worker

    Note over API,DB: Step 1: Local ACID Hold
    Patient->>API: POST /api/v1/bookings/hold/ (slot_id)
    API->>DB: transaction.atomic() + SELECT FOR UPDATE
    DB-->>API: Slot: Locked (held_by=patient, TTL=300s)
    API-->>Patient: 200 OK (Slot held for 5 mins)

    Note over API,GW: Step 2: Gateway Intent & Pending Ledger
    Patient->>API: POST /api/v1/bookings/confirm/ (idempotency_key)
    API->>DB: Insert Payment(status='pending', idempotency_key)
    API->>GW: Create Payment Intent / Order
    GW-->>API: Payment Order ID
    API-->>Patient: Gateway Checkout Payload

    alt Happy Path: Payment Success Webhook
        Patient->>GW: Authorize & Complete Payment
        GW->>API: POST /api/v1/payments/webhook/ (event: payment.success)
        API->>Worker: Enqueue process_payment_webhook_task
        API-->>GW: 200 OK (Webhook Acknowledged)
        Worker->>DB: transaction.atomic():
        Note over Worker,DB: Payment: Success<br/>Slot: Booked<br/>Consultation: Scheduled
        Worker->>Worker: Enqueue generate_prescription_pdf & Send Notification
    else Compensating Action: Payment Failure / Cancellation
        GW->>API: POST /api/v1/payments/webhook/ (event: payment.failed)
        API->>Worker: Enqueue process_payment_webhook_task
        API-->>GW: 200 OK (Webhook Acknowledged)
        Worker->>DB: Compensating Action (transaction.atomic()):
        Note over Worker,DB: Payment: Failed<br/>Revert Slot: Available (held_by=NULL)<br/>Consultation: Cancelled
        Worker->>Worker: Dispatch Failure SMS / Email to Patient
    else Compensating Action: Abandoned Checkout (5-Min Timeout)
        Note over DB,Worker: 300s Elapse without Gateway Confirmation
        Worker->>DB: Celery Sweeper: UPDATE status='available' WHERE hold_expires_at <= now()
        Note over DB: Self-Healing Read surfaces slot as AVAILABLE
    end
```

### 1. Local ACID Consistency (`transaction.atomic`)
Used strictly within the boundary of our local PostgreSQL database:
- **Scope**: Atomic slot hold acquisition, ledger state updates, and consultation generation.
- **Implementation**: Wrapped in `with transaction.atomic():` paired with `select_for_update()`.
- **Latency Invariant**: Transactions complete in $< 5\text{ ms}$ because no external network I/O is ever permitted inside the database atomic block.

### 2. Distributed Saga & Compensating Actions
Coordinates distributed checkout across the API, payment gateway, and asynchronous background workers:
1. **Forward Step**: `Payment` created in `pending` state; `AvailabilitySlot` marked `locked`.
2. **Success Fulfillment**: The gateway notifies the system via a cryptographically signed webhook. In a local transaction, the worker finalizes `Payment` to `success`, locks `AvailabilitySlot` to `booked`, and schedules the `Consultation`.
3. **Compensating Transaction (Rollback on Failure)**:
   - If the gateway reports `payment.failed`, or the user cancels payment, the Saga triggers a **compensating transaction**:
     - `Payment.status` transitions to `failed`.
     - `AvailabilitySlot` is released back to `available` (`held_by = NULL`, `hold_expires_at = NULL`).
     - A notification is dispatched to the user advising them of the transaction status.
   - If checkout is abandoned, the dual-pattern hold mechanism (countdown task + 60s Celery Beat sweeper + read-time self-healing) acts as the autonomous compensation engine, freeing the inventory without manual intervention.

---

## 12. Observability Architecture (Metrics, Logs, Traces)

Amrutam satisfies enterprise observability standards across the **Three Pillars of Observability**:

```mermaid
flowchart TD
    subgraph Ingress Traffic
        Client[Patient / Doctor Client] -->|HTTP Request| API[Django Application Tier]
    end

    subgraph Observability Pillars
        API -->|1. Prometheus Metrics| MetricsCollector["Prometheus Exporter (/metrics)"]
        API -->|2. Structured JSON Logs| LogShipper["JSON Stream Handler (stdout)"]
        API -->|3. Distributed Traces| Tracer["OpenTelemetry / W3C Trace Context"]
    end

    subgraph Monitoring Backends
        MetricsCollector --> PrometheusServer[(Prometheus TSDB)]
        PrometheusServer --> GrafanaDashboard[Grafana Dashboards & P95 Alerts]
        LogShipper --> LokiOrELK[(Grafana Loki / ELK / Datadog)]
        Tracer --> JaegerOrTempo[(Jaeger / Grafana Tempo)]
    end
```

### Pillar 1: Metrics (Prometheus & Grafana)
- **Scrape Endpoint**: `GET /metrics` served via `django-prometheus`.
- **Latency Histograms**: `django_http_requests_latency_seconds_by_view_method` tracks request durations across views, providing granular P50, P90, P95, and P99 percentile distributions to monitor SLA compliance ($<200\text{ms}$ reads, $<500\text{ms}$ writes).
- **Throughput Counters**: `django_http_requests_total_by_method_total` tracks total traffic volume partitioned by method (`GET`, `POST`) and response code category (`2xx`, `4xx`, `5xx`).
- **Database & Cache Health**: Tracks PostgreSQL connection pool saturation (`django_db_execute_total`) and Redis cache hit/miss ratios (`django_cache_get_total`).
- **Asynchronous Task Latency**: Celery metrics monitor task execution durations, retry rates, and message queue backlogs.

### Pillar 2: Structured JSON Logging
- **Standard**: All application components emit single-line, machine-readable JSON records to `stdout` via `backend.logging.JSONFormatter`, natively ingested by Grafana Loki, ELK, Datadog, or AWS CloudWatch.
- **Log Schema**:
  ```json
  {
    "timestamp": "2026-09-15T01:01:29.168Z",
    "level": "INFO",
    "logger": "consultations.tasks",
    "message": "Payment confirmed for consultation e4bbd606-7a24-443a-a4f2-bf544a5cc59f",
    "module": "tasks",
    "line": 78,
    "process": 4572,
    "thread": 23476,
    "request_id": "ee4f77a7-9642-4200-9ad0-2089092aa5fe",
    "user_id": "85cd9728-bb46-4a25-855c-e7203cf64092",
    "path": "/api/v1/payments/webhook/",
    "method": "POST",
    "status_code": 200,
    "duration_ms": 14.8
  }
  ```
- **Error Transparency**: Exception tracebacks are formatted into structured string fields, preventing unformatted multi-line log splitting in aggregators.

### Pillar 3: Distributed Tracing (OpenTelemetry & W3C Trace Context)
- **W3C Standard Propagation**: Implements W3C Trace Context propagation via `traceparent` (`00-{trace_id}-{span_id}-{trace_flags}`) and `X-Request-ID`.
- **End-to-End Correlation**:
  1. `RequestTracingMiddleware` extracts an incoming `X-Request-ID` or `traceparent` correlation ID, or generates a new UUID4 if absent.
  2. The identifier is stored in thread-local context and automatically injected into all application logs emitted during request processing.
  3. When asynchronous Celery tasks are enqueued (`process_payment_webhook_task`, `generate_prescription_pdf`), the `request_id` and trace context are passed in task metadata, ensuring unbroken distributed trace correlation across synchronous API requests and background worker tasks.
  4. Responses return headers:
     - `X-Request-ID: <UUID4>`
     - `X-Response-Time-MS: <Latency in ms>`
