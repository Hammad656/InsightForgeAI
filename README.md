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
