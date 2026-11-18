# InsightForge AI

M1 provides a Next.js application shell, a FastAPI service, PostgreSQL, Redis, a Celery worker foundation and Alembic migrations. The dashboard contains placeholders, not business results. Authentication, tenant isolation, imports, analytics and AI are future milestones; do not use this foundation for customer data or expose it publicly.

## Requirements

Docker Desktop with Compose, or Node.js 22.13 or newer in the 22.x line, Python 3.12, PostgreSQL 16 and Redis 7 for host development. Commands below use PowerShell; on Unix use `cp`, `source .venv/bin/activate` and omit `.exe`/`.cmd` suffixes.

## Docker Compose

1. Copy `.env.example` to `.env` and `backend/.env.example` to `backend/.env`. Replace both example passwords with unique local values. Use URL-safe passwords in database URLs, or percent-encode special characters. Set the same passwords in both files. For Compose set the database hosts in both backend URLs to `db` and the Redis host to `redis`.
2. Run `docker compose up --build -d`.
3. Apply the initial migration: `docker compose exec api alembic upgrade head`.
4. Open http://localhost:3000, http://localhost:8000/api/v1/health and http://localhost:8000/api/v1/ready. API docs are at http://localhost:8000/docs.
5. Stop with `docker compose down`. The named database volume persists. PostgreSQL initialization scripts only run on a fresh volume; changing the passwords in `.env` does not rotate existing database credentials.

The owner account initializes the database and runs migrations. The API and worker use a separate non-owner login with no superuser, database creation, role creation or RLS bypass privileges; it currently needs only database connection for readiness. Future migrations must grant narrowly scoped object privileges and enforce tenant isolation before any business data is stored. Redis and PostgreSQL are bound to loopback for local use. This Compose setup is development-only.

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
