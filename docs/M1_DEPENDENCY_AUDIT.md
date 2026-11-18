# M1 dependency audit

Baseline from the installed frontend lockfile and Python 3.12 test container, before remediation. npm severity is propagated through dependency chains; rows do not imply independent vulnerabilities in each package. `pip-audit` did not provide severity ratings. Versions are the audited installed versions.

## npm baseline

| Package | Installed versions | Severity | npm-recommended fix |
|---|---|---|---|
| @next/eslint-plugin-next | 15.5.29 | high | eslint-config-next 14.2.35 (major change) |
| @typescript-eslint/eslint-plugin | 8.73.0 | high | Update dependency tree |
| @typescript-eslint/parser | 8.73.0 | high | Update dependency tree |
| @typescript-eslint/type-utils | 8.73.0 | high | Update dependency tree |
| @typescript-eslint/typescript-estree | 8.73.0 | high | Update dependency tree |
| @typescript-eslint/utils | 8.73.0 | high | Update dependency tree |
| @vitest/mocker | 3.2.7 | moderate | vitest 4.1.12 (major change) |
| anymatch | 3.1.3 | high | tailwindcss 4.3.5 (major change) |
| chokidar | 3.6.0 | high | tailwindcss 4.3.5 (major change) |
| eslint-config-next | 15.5.29 | high | eslint-config-next 14.2.35 (major change) |
| eslint-import-resolver-typescript | 3.10.1 | high | Update dependency tree |
| fast-glob | 3.3.1, 3.3.3 | high | eslint-config-next 14.2.35 (major change) |
| fdir | 6.5.0 | high | Update dependency tree |
| micromatch | 4.0.8 | high | tailwindcss 4.3.5 (major change) |
| next | 15.5.29 | moderate | next 16.4.6 (major change) |
| picomatch | 2.3.2, 4.0.7 | high | vitest 4.1.12 (major change) |
| postcss | 8.4.31 | high | next 16.4.6 (major change) |
| readdirp | 3.6.0 | high | Update dependency tree |
| sucrase | 3.35.1 | high | Update dependency tree |
| tailwindcss | 3.4.17 | high | tailwindcss 4.3.5 (major change) |
| tinyglobby | 0.2.17 | high | vitest 4.1.12 (major change) |
| tinypool | 1.1.1 | critical | vitest 4.1.12 (major change) |
| vite | 6.4.6 | high | Update dependency tree |
| vitest | 3.2.7 | critical | vitest 4.1.12 (major change) |

## Python baseline

| Package | Installed version | Severity | Advisory and reported fixed version |
|---|---|---|---|
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-1795: 25.3 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-1796: 26.0 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-2875: 26.1 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-2876: 26.1 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-196: 26.1.2 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-3721: 26.2 |
| pip | 25.0.1 | Not supplied by pip-audit | PYSEC-2026-3721: 26.2.0 |
| pytest | 8.3.5 | Not supplied by pip-audit | PYSEC-2026-1845: 9.0.3 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-1941: 0.47.2 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-1942: 0.49.1 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-161: 1.0.1 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-2281: 1.1.0 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-2280: 1.1.0 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-249: 1.3.1 |
| starlette | 0.46.2 | Not supplied by pip-audit | PYSEC-2026-248: 1.3.0 |

## Historical findings after the initial remediation

The initial production-only npm audit reported zero vulnerabilities. The initial full audit reported 20 high-severity package entries through the picomatch advisory. GitHub withdrew the advisory on November 18, 2026. The November 19 rerun reported zero findings in both npm audits. NVD still lists CVE-2026-112366 as high (8.7 from the CNA), and the latest published picomatch version checked was 4.0.7. The installed copies, 2.3.2 and 4.0.7, are development tooling dependencies. Keep glob patterns under repository control and reassess if the advisory status changes or a compatible stable fix becomes available. [GitHub advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) · [NVD record](https://nvd.nist.gov/vuln/detail/CVE-2026-112366)

| Package | Installed versions | Severity | Recommended fix |
|---|---|---|---|
| @next/eslint-plugin-next | 15.5.29 | high | Update affected picomatch dependency when a fix is available |
| @typescript-eslint/eslint-plugin | 8.73.0 | high | Update affected picomatch dependency when a fix is available |
| @typescript-eslint/parser | 8.73.0 | high | Update affected picomatch dependency when a fix is available |
| @typescript-eslint/type-utils | 8.73.0 | high | Update affected picomatch dependency when a fix is available |
| @typescript-eslint/typescript-estree | 8.73.0 | high | Update affected picomatch dependency when a fix is available |
| @typescript-eslint/utils | 8.73.0 | high | Update affected picomatch dependency when a fix is available |
| anymatch | 3.1.3 | high | Update affected picomatch dependency when a fix is available |
| chokidar | 3.6.0 | high | Update affected picomatch dependency when a fix is available |
| eslint-config-next | 15.5.29 | high | Update affected picomatch dependency when a fix is available |
| eslint-import-resolver-typescript | 3.10.1 | high | Update affected picomatch dependency when a fix is available |
| fast-glob | 3.3.1, 3.3.3 | high | Update affected picomatch dependency when a fix is available |
| fdir | 6.5.0 | high | Update affected picomatch dependency when a fix is available |
| micromatch | 4.0.8 | high | Update affected picomatch dependency when a fix is available |
| picomatch | 2.3.2, 4.0.7 | high | Update affected picomatch dependency when a fix is available |
| readdirp | 3.6.0 | high | Update affected picomatch dependency when a fix is available |
| sucrase | 3.35.1 | high | Update affected picomatch dependency when a fix is available |
| tailwindcss | 3.4.17 | high | Update affected picomatch dependency when a fix is available |
| tinyglobby | 0.2.17 | high | Update affected picomatch dependency when a fix is available |
| vite | 6.4.6 | high | Update affected picomatch dependency when a fix is available |
| vitest | 4.1.12 | high | Update affected picomatch dependency when a fix is available |

The Python environment audit reported no known vulnerabilities after upgrading pip to 26.2, pytest to 9.0.3, FastAPI to 0.163.2 and Starlette to 1.3.1. The Next.js PostCSS override to 8.5.33 is an explicitly tested override of Next.js's pinned transitive version and should be revisited when upgrading Next.js.

## November 19 verification

| Package or tool | Version observed | Finding | Action |
|---|---|---|---|
| httpx | 0.28.1 | Starlette TestClient integration deprecated | Switched the backend development pin to httpx2 2.16.2. Both tests passed and this warning disappeared. |
| anyio | 4.16.0 | Starlette 1.3.1 uses a deprecated BlockingPortal alias in tests | One warning remains. Update when a compatible Starlette release removes use of the alias. |
| Node.js / eslint-visitor-keys | 22.7.0 / 5.0.1 | npm issued an engine warning below Node 22.13 | Use the documented Node 22.13+ runtime. The frontend scripts ran with Node 22.23.5. |
| Python / psycopg-binary | 3.14.7 / 3.2.9 | The pinned binary wheel was unavailable for the host Python 3.14 | Verified in the supported Python 3.12.16 container. |
| eslint | 9.27.0 | npm marks the release unsupported | The registry also marks 9.39.5 unsupported. Upgrade when the Next.js configuration supports a maintained ESLint major. |
| whatwg-encoding | 3.1.1 | npm deprecation in the development tree | Upgrade the parent dependency when a compatible release drops the deprecated package. |

Frontend command, from `frontend`:

```powershell
npm.cmd exec --yes --package=node@22.23.5 -c 'node -v && npm ci && npm audit --json && npm audit --omit=dev && npm run lint && npm run typecheck && npm run test && npm run build'
```

Both npm audits reported zero vulnerabilities. Lint, TypeScript, two Vitest tests and the Next.js production build passed. The npm launcher used host Node 22.7.0 for installation and emitted its engine warning.

Compose and backend commands, from the repository root:

```powershell
$composeArgs = @('-p', 'insightforge_m1_verify', '--env-file', '.env.example', '-f', 'docker-compose.yml', '-f', "$env:TEMP\insightforge-m1-check\override.yml")
docker compose @composeArgs config --quiet
docker compose @composeArgs up --build -d db redis api worker
docker compose @composeArgs up --build -d web
docker compose @composeArgs exec -T api alembic upgrade head
docker compose @composeArgs up --build -d
docker compose @composeArgs run --rm --no-deps --user root api sh -ec 'python --version && python -m pip install --quiet --timeout 120 -r requirements-dev.txt pip-audit && python -m pip check && ruff check app tests alembic && python -m pytest && python -m pip_audit'
docker compose @composeArgs ps
docker compose @composeArgs exec -T api alembic current
docker compose @composeArgs exec -T api python -m pip check
docker compose @composeArgs run --rm --no-deps --user root api sh -ec 'python -m pip list --format=freeze > /tmp/m1-runtime-requirements.txt && python -m pip install --quiet --timeout 120 pip-audit && python -m pip_audit --no-deps --disable-pip -r /tmp/m1-runtime-requirements.txt'
docker compose @composeArgs exec -T worker celery -A app.jobs.celery_app:celery_app inspect ping
foreach ($url in @('http://localhost:18000/api/v1/health','http://localhost:18000/api/v1/ready','http://localhost:13000/')) {
    (Invoke-WebRequest -UseBasicParsing $url).StatusCode
}
```

The Compose project started with a fresh database volume. PostgreSQL and Redis became healthy, Alembic reached `0001_foundation (head)`, the worker returned `pong`, and all three HTTP checks returned 200. Backend Ruff and both `pip check` invocations passed. Pytest passed two tests and emitted one upstream AnyIO alias warning. The exact installed runtime package snapshot and the installed development environment both audited with zero known vulnerabilities. GitHub Actions checks were exercised locally. Hosted CI requires a published revision.

## Initial verification commands

From `frontend`, `npm.cmd install`, `npm.cmd audit --omit=dev`, `npm.cmd audit --json`, `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run test`, and `npm.cmd run build` exercised the final dependency tree. The production audit, lint, type check and two tests passed; the full audit reported the 20 findings above.

The isolated API container was updated with `docker exec --user root insightforge_m1_check-api-1 sh -c 'python -m pip install --timeout 120 pip==26.2 fastapi==0.163.2 pytest==9.0.3'` and `docker exec --user root insightforge_m1_check-api-1 python -m pip install --timeout 120 starlette==1.3.1`. `docker exec insightforge_m1_check-api-1 python -m pytest`, `docker exec insightforge_m1_check-api-1 python -m pip check`, and `docker exec insightforge_m1_check-api-1 pip-audit` passed before the container was rebuilt. Backend Ruff had passed in the same Python 3.12 environment. The rebuilt image also passed `python -m pip check`.

The isolated Compose project was rebuilt with `docker compose -p insightforge_m1_check --env-file .env.example -f docker-compose.yml -f "$env:TEMP\insightforge-m1-check\override.yml" up --build -d api worker` and the same command ending in `web`. The override uses synthetic credentials and separate local ports. API health and readiness returned HTTP 200; `alembic current` returned `0001_foundation (head)`, and the Celery worker responded to `inspect ping`.
