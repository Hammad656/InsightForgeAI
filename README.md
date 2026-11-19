# InsightForge AI

InsightForge AI provides a Next.js workspace, a FastAPI service, PostgreSQL, Redis and a Celery worker. M2 adds account sessions, organization roles and versioned CSV/XLSX snapshot imports. The overview retains the M1 foundation cards.

## Requirements

Docker Desktop with Compose, or Node.js 22.13 or newer in the 22.x line, Python 3.12, PostgreSQL 16 and Redis 7 for host development. Commands below use PowerShell; on Unix use `cp`, `source .venv/bin/activate` and omit `.exe`/`.cmd` suffixes.

## Docker Compose

1. Copy `.env.example` to `.env` and `backend/.env.example` to `backend/.env`. Replace both example passwords with unique local values. Use URL-safe passwords in database URLs, or percent-encode special characters. Set the same passwords in both files. For Compose set the database hosts in both backend URLs to `db` and the Redis host to `redis`.
2. Run `docker compose up --build -d`.
3. Apply the schema migrations: `docker compose exec api alembic upgrade head`.
4. Open http://localhost:3000, http://localhost:8000/api/v1/health and http://localhost:8000/api/v1/ready. API docs are at http://localhost:8000/docs.
5. Stop with `docker compose down`. The named database volume persists. PostgreSQL initialization scripts only run on a fresh volume; changing the passwords in `.env` does not rotate existing database credentials.

The owner account initializes the database and runs migrations. The API and worker use a separate non-owner login with no superuser, database creation, role creation or RLS bypass privileges; M2 grants access to the identity and ingestion tables and enables forced row-level security on tenant-owned records. Redis and PostgreSQL are bound to loopback for local use. This Compose setup is development-only.

## Host development

Start PostgreSQL and Redis with `docker compose up -d db redis` after configuring the root `.env`. Copy `backend/.env.example` to `backend/.env`, populate passwords, and retain `localhost` hosts. Then:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

In a second terminal, from `backend`, activate the environment and run `celery -A app.jobs.celery_app:celery_app worker --loglevel=INFO` (Celery workers are intended for Linux/containers). In another terminal:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd run dev
```

The browser uses `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`); it must be a public API URL, never a secret. Set it in the root `.env` before building with Compose, or in `frontend/.env.local` for host development. Next.js embeds it at build time; rebuild the web image to change it. API CORS origins are configured with the JSON array `CORS_ORIGINS` in the backend environment.

## Checks

```powershell
cd backend
python -m pytest
python -m ruff check app tests alembic
python -m mypy --ignore-missing-imports app
alembic upgrade head
alembic current
cd ../frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
cd ..
docker compose config --quiet
```

`GET /api/v1/health` is liveness only. `GET /api/v1/ready` checks a database connection and Redis ping and returns 503 without internal error details if either is unavailable. Readiness does not establish that migrations have been applied. Alembic uses `MIGRATION_DATABASE_URL`, separate from the service's `DATABASE_URL`; never supply the owner URL to frontend code. No secrets or real customer data belong in environment examples, source, tests or logs.

## M2 workflow

1. Open **Open workspace** and create an account. Create an organization to become its first Admin.
2. To add a teammate, create an invitation for their email and share the code privately. The recipient signs in and accepts the code. Invitations expire after 48 hours. Admins can update roles and remove members. Each organization must retain an Admin.
3. Download a synthetic sample from the upload panel. Upload a UTF-8 CSV or a single-sheet XLSX with up to 10,000 rows, 64 columns and 5 MiB. Review the preview, select the dataset type, and confirm the column mapping.
4. Open the import in history to check the result or download its validation report. Each successful import creates an immutable snapshot. The latest successful submission for that domain becomes active. Repeating the same content and mapping returns the existing import. A failed import leaves the active version intact.

The sample headers define the required fields. Use ISO dates, PKR for sales and payments, decimal amounts with up to four fractional digits, positive sales quantities and signed nonzero inventory movements. The first field in each sample is the unique row identifier within the snapshot.

M2 stores original upload bytes in tenant-scoped PostgreSQL staging with a limit of 100 uploads or 100 MiB per organization. This local implementation keeps staging private without requiring a separate storage service. Accounts use 15-minute access tokens and rotating refresh tokens with a seven-day absolute session lifetime. Set `COOKIE_SECURE=true` for HTTPS environments. The provided backend example sets it to false for local HTTP. Use `localhost` consistently for the web and API hosts so browser cookie rules work.

Enable the PostgreSQL/Redis integration tests with `M2_INTEGRATION=1` against an isolated, migrated database. Set `M2_LIVE_WORKER=1` and start Celery with `--pool=solo` to exercise Redis broker delivery. Run the suite with the restricted `insightforge_app` database credentials and `COOKIE_SECURE=false`. The suite creates synthetic accounts, organizations and imports.

## M3 business analytics

Open **Business analytics** from the workspace. Admins, Analysts, and Viewers can read dashboards for their own organizations. The overview and detail reports show the published source version and timestamp. All calculations run against the selected organization's immutable active snapshots. Replacing a snapshot changes future report requests without changing older snapshot rows.

The tenant-scoped endpoints are `GET /api/v1/organizations/{org_id}/metrics` and `GET /api/v1/organizations/{org_id}/analytics/{report}`. Use `overview`, `sales`, `inventory`, or `receivables` as the report. Numeric business values are serialized as decimal strings to preserve precision. Snapshot metadata is returned per domain. A domain without a published source has null metrics.

| Report | Policy and filters |
| --- | --- |
| Sales | `start` inclusive, `end` exclusive, optional exact `branch`, `sku`, and `customer` IDs. Only completed PKR lines count. Order count uses distinct order IDs. COGS uses captured unit cost. Net revenue deducts line discounts. Returns, credits, taxes and shipping are outside the current sales input. Growth uses the preceding equal-length period. A zero prior baseline or zero orders makes the corresponding ratio unavailable. Ratios round to four decimal places. |
| Inventory | `as_of` inclusive for on-hand stock. Movement counts also respect `start` and `end`. Filter by exact `branch` and `sku`. Include opening balances in the source as movements. The `threshold` defaults to 5 units per observed SKU and branch. Available stock is unavailable because the snapshot has no reservations. |
| Receivables | `as_of` inclusive and optional exact `customer`. Invoice face value is counted once and only payments dated through the as-of date reduce it. Due today is current. Overdue balances use 1–30, 31–60, 61–90 and 91+ day buckets. The payments snapshot excludes credit adjustments and any invoices without a source row, including standalone unpaid invoices. These balances cover the imported invoices only. |

The period defaults to the current calendar month in Asia/Karachi. The as-of date defaults to the earlier of today and the last day in the selected period. To reproduce the shipped sample, select October 1 through November 1, 2026 with October 31 as the as-of date. Expected totals include PKR 1,775 net revenue, PKR 855 gross profit, 20 units on hand, and PKR 475 outstanding and overdue.

Run `python -m pytest -v` with the integration flags above to include the deterministic KPI fixtures and tenant authorization checks. The M3 fixture checks cover exact decimal arithmetic, date and ageing boundaries, source publication changes, and role-scoped reads.

## M4 Ask AI

Open **Ask AI** from Business analytics. Questions support one governed metric at a time. Supported groupings include sales by day, SKU, branch or customer, stock on hand by SKU or branch, and receivables ageing. Filters use exact source IDs. Published snapshot IDs can be supplied as `dataset_ids` to constrain a request to an eligible active snapshot. Unsupported questions and unavailable data are reported explicitly.

The API uses `POST /api/v1/ai/queries` to submit a question with `organization_id` and `question`. Use `GET /api/v1/ai/queries?organization_id=…` for private history or `GET /api/v1/ai/queries/{query_id}?organization_id=…` for one saved result. History is scoped to both the organization and the original author. There is no shared result cache or export endpoint.

Apply `alembic upgrade head` with a migration account authorized to provision the reporting roles. Migration `0003_m4` creates `insightforge_reporting` with login disabled. As the database owner, enable its login, set a password interactively with psql's `\password insightforge_reporting`, and grant it `CONNECT` on the application database if public database access is revoked. Supply that login through `REPORTING_DATABASE_URL` in the API environment. Keep the operational and reporting credentials distinct.

AI is disabled by default. Enable OpenAI explicitly with `AI_PROVIDER=openai`, a server-only `OPENAI_API_KEY`, and a compatible Structured Outputs model in `OPENAI_MODEL`. The provider receives the submitted question and approved public metric definitions. Keep confidential details out of questions. The explanation call receives result-shape flags. The application inserts verified values and labels into the selected explanation format locally. The model supplies typed intent and has no SQL execution authority.

Run `python -m pytest -v` with `M4_INTEGRATION=1`, `REPORTING_DATABASE_URL`, and the M2 integration configuration to exercise the full suite. The M4 gold cases use mocked provider responses and independently calculated fixture totals. They verify the typed-intent-to-result path, explanation grounding, and usage reporting. Live-model interpretation quality requires a separately authorized evaluation.

## M5 executive reports

Apply `alembic upgrade head` through `0004_m5` before starting the API and worker. Open **Reports** from Business analytics or Ask AI, then choose a reporting month and optional filters. Admins and analysts can generate reports. An administrator can read reports within their organization, and other members can read reports they authored while they retain membership. M5 does not include report sharing.

`POST /api/v1/reports` accepts the organization ID, a client-generated UUID `request_key`, a first-of-month `start` and following first-of-month exclusive `end`, plus optional filters and a completed Ask AI query ID. Reuse the same request key for a retry of the same submission. Ask AI references must belong to the requester and use a pinned snapshot. History and individual report endpoints require `organization_id`. The report ID also identifies the durable background job. If the broker is unavailable or a worker lease expires, use **Retry** from history.

The PDF pins active dataset versions at submission and uses M3 values for the selected and preceding equal-length periods. Coverage and limitations appear in the evidence appendix. In optional AI mode, the provider chooses a focus from recommendations already supported by the verified results. The application supplies all text and numerical values. The report falls back to the same verified observations when the provider is disabled or unavailable.

Downloads require a current session, organization membership, report access, and a five-minute, single-use grant. The grant is sent in a request body and stored only as a hash. The API verifies the artifact's size and SHA-256 before returning it. Reports expire after 30 days. Local generation removes expired files in bounded batches for active organizations. Report metadata remains in history after expiry.

The default `REPORT_STORAGE=local` backend uses `REPORT_DIRECTORY`. The API and worker must use the same private path. Compose supplies a shared private volume and mounts it read-only in the API. For an S3-compatible deployment, configure `REPORT_STORAGE=s3`, `REPORT_S3_BUCKET`, and optionally an HTTPS `REPORT_S3_ENDPOINT`. Use a private bucket with public access blocked, encryption enabled, a scoped runtime identity for the `reports/` prefix, and a 30-day lifecycle rule. For idle local organizations, remove expired artifacts as part of storage maintenance.

Audit records contain fixed event types, timestamps, actor and organization IDs when known, resource IDs, and correlation IDs. The runtime role can append events. Migration `0004_m5` records the static semantic catalog baseline. Future catalog revisions should record their publication in the corresponding migration.

Run `python -m pytest -v` with M2 and M4 integration flags plus `M5_INTEGRATION=1`, `M5_LIVE_WORKER=1`, and a disposable `REPORT_DIRECTORY`. Use a live Celery worker for broker verification and leave `AI_PROVIDER=disabled` during acceptance.
