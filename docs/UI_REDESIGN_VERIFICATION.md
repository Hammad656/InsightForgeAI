# Glassmorphism redesign verification

The redesign uses the existing Next.js frontend and FastAPI contracts. Organization selection is shared across client navigation. The shell verifies sessions with `/auth/me`, and backend authorization continues to govern data and mutations.

## Local browser coverage

Headless Microsoft Edge exercised the production Docker build at 1440 × 1000, 820 × 1000, and 390 × 844. The walkthrough opened login, registration, data sources, business overview, sales, inventory, receivables, Ask AI, multi-domain analysis, custom dashboards, executive reports, schedules, reconciliation, AI evaluation, connectors, predictive analytics, and team settings. Each route passed the document overflow check at all three sizes.

The browser registered a synthetic Admin, published sales, inventory, and payment CSV snapshots with Celery, verified October net revenue of PKR 1,775.00, generated an executive report, and downloaded the authorized PDF. It also checked sign-out, unauthenticated route redirection, sign-in, keyboard focus, mobile navigation, and Ask AI with the provider disabled. Advanced job execution and permission cases were exercised by the backend integration suite. A complete assistive-technology audit and live-model browser responses remain unverified.

A follow-up browser run verified organization switching across navigation, accepted a real Viewer invitation, confirmed upload access returned 403 for a Viewer and 404 for another organization, and rendered a 96-week synthetic forecast with prediction intervals, chronological holdout, stockout advisory, and anomaly panels. The populated forecast also fit at laptop width.

Representative foreground/background contrast measurements were 15.37:1 for primary text, 5.83:1 for muted text on a raised surface, 6.48:1 for white on the violet button, and 9.32:1 for cyan on raised navy.

## Largest Contentful Paint

Measured on localhost using production Docker, headless Edge, a 1440 × 1000 viewport, authenticated synthetic data, and disabled browser cache. There was no network or CPU throttling.

| Route | Samples (ms) | Median (ms) |
| --- | --- | ---: |
| Business overview | 460, 396, 436 | 436 |
| Data sources | 404, 472, 472 | 472 |

The M8 release preparation report recorded a 5,756 ms LCP p95 across 34 observations. The route mix, sample size, and statistic differ from this local check. These results establish a local reference for the redesigned routes. The cause of the earlier p95 and a comparable performance change remain unverified.

## Automated checks

- `npm.cmd run lint`, `npm.cmd run typecheck`, and `npm.cmd run test` passed. Vitest ran seven tests.
- `docker compose build web` passed, including `npm run build`.
- `python -m pytest -q -p no:cacheprovider` passed all 137 tests using a disposable PostgreSQL database, Redis, a separate PostgreSQL connector fixture, and a live Celery worker. Pytest emitted one existing Starlette/AnyIO deprecation warning.
- `alembic upgrade head` reached `0011_dataset_lifecycle` in the disposable database.

## Frontend changes

| Area | Files |
| --- | --- |
| Theme and navigation | `frontend/app/globals.css`, `frontend/app/layout.tsx`, `frontend/components/app-frame.tsx`, `frontend/components/ui/glass.tsx`, `frontend/components/m6c-workspace.tsx` |
| Authentication | `frontend/components/auth-form.tsx`, `frontend/components/session-entry.tsx` |
| Data sources and team | `frontend/app/workspace/page.tsx`, `frontend/app/workspace/team/page.tsx` |
| Overview and business analytics | `frontend/app/analytics/page.tsx`, `frontend/app/analytics/sales/page.tsx`, `frontend/app/analytics/inventory/page.tsx`, `frontend/app/analytics/receivables/page.tsx` |
| AI workspaces | `frontend/app/ask/page.tsx`, `frontend/app/analyses/page.tsx`, `frontend/components/analysis-result.tsx`, `frontend/app/evaluations/page.tsx` |
| Reports and operations | `frontend/app/reports/page.tsx`, `frontend/app/schedules/page.tsx`, `frontend/app/reconciliation/page.tsx`, `frontend/app/connectors/page.tsx`, `frontend/app/predictive/page.tsx` |

The custom dashboard builder receives the shared controls and application shell through `m6c-workspace.tsx`. Existing local authentication and dataset lifecycle changes were preserved.

## Final dataset and UI verification

The final local integration run passed 137 backend tests and seven frontend tests. Ruff, CI-configured mypy, pip check, ESLint, TypeScript, and Compose configuration passed. A new disposable database migrated through `0011_dataset_lifecycle`, and the existing local database reported the same revision. The release gate workflows match the checked-out baseline.

The production browser walkthrough deleted an unused synthetic upload, inferred dataset types and column mappings, archived and restored a snapshot, and published a replacement while keeping the previous version visible in history. Backend checks covered historical Ask AI evidence and frozen PDF sources, import cancellation and late delivery, quota recovery, and Admin, Analyst, Viewer, and cross-organization boundaries.

The browser verified governed overview values against the imported fixtures, including PKR 1,775.00 in net revenue, PKR 855.00 in gross profit, 20 units on hand, and PKR 475.00 outstanding. The walkthrough checked 17 screens at widths of 1440, 820, and 390 pixels and captured 36 desktop and mobile screenshots, including the populated forecast. Verification fixes scope the notification disclosure to the selected organization and apply the shared composer theme to Multi-domain Analysis.

The final production build passed. A focused retest confirmed both fixes in Edge, along with authenticated root redirection, mobile navigation focus restoration on Escape, Viewer restrictions, and cross-tenant denial.
