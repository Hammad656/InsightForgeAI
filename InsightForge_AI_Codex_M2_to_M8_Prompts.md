# InsightForge AI — Codex Implementation Instructions (M2–M8)

**Status:** M1 reported complete by the project owner; this document does not independently verify the repository.  
**Specification:** `SYSTEM_DESIGN.md` (or `docs/SYSTEM_DESIGN.md`), plus `CODEX_HANDOFF.md` and `AGENTS.md`.  
**How to use:** Send the universal rules once, then send only the prompt for the next approved milestone. Do **not** submit M2–M8 as one development request.

## Universal instructions for every Codex milestone

> You are the lead full-stack and AI engineer for InsightForge AI. Read the authoritative System Design Document and repository instructions first. Inspect the current branch, git status, codebase, schema migrations, tests, environment templates, CI, and existing feature boundaries. Keep all working M1 code and follow the modular-monolith architecture (Next.js TypeScript frontend, FastAPI/Python backend, PostgreSQL, Redis/Celery, Docker). Do not initialize a nested Git repository or replace existing architecture without documenting the conflict.
>
> For this task implement **only the milestone explicitly approved below**. First provide a short gap analysis and file-level plan, then implement it. Include SQLAlchemy models and Alembic migrations where required; Pydantic types and OpenAPI contract changes; responsive UI states; unit, integration, and authorization tests; synthetic fixtures; and documentation updates. Run available checks and truthfully report each command's result, failures, unresolved risks, and manual verification steps. Do not claim success without evidence. Never reveal secrets or use real customer financial data. Do not push to the remote, merge, deploy, or create paid resources without my separate authorization. Stop at the milestone gate and wait for the next instruction.

## M2 — Identity, organizations, tenant isolation, and file ingestion (P0)

> **I approve M2 only.** Inspect the completed M1 repository and implement the requirements FR-01 to FR-04 under `SYSTEM_DESIGN.md` Sections 2, 5, 8, 9, and 13.
>
> **Identity:** Register/login/logout/refresh/me APIs. Use password hashing, short-lived authenticated sessions in HttpOnly/Secure/SameSite cookies, refresh rotation/revocation, CSRF protection where relevant, and reasonable brute-force mitigation. Add organization creation, membership/invitations, active organization context, and roles: Admin, Analyst, Viewer. Enforce role and membership authorization in APIs and persistence.
>
> **Tenant boundary:** Add org-scoped tables and migrations; PostgreSQL RLS or equivalent documented DB-enforced tenant isolation on business and app records, plus server-side authorization. Ensure object keys, cache keys, and jobs are tenant-scoped. A user-provided organization ID is never authorization proof. Test with two organizations and multiple roles.
>
> **Ingestion:** Implement protected CSV/XLSX upload to private staging storage with size/type/content validation and resource limits; dataset creation, version metadata and hashes; schema/type/null/duplicate preview; explicit column mapping confirmation; staging validation and asynchronous idempotent imports via Celery; atomic publication/version switching; row-level validation errors and downloadable error manifests. Provide synthetic files and a data dictionary for sales/order lines, product/inventory movements, and invoices/payments. Do not automate cross-source reconciliation yet (M6).
>
> **APIs/UI:** Implement auth, organization/member, upload, preview, import and job-status endpoints specified in SDD Section 9; add login, workspace selection, upload wizard, previews, mapping and job progress pages with loading/error/empty states.
>
> **Acceptance gate:** Tests for valid/invalid CSV and XLSX, file limits and malformed data, duplicate re-import/idempotency, failed import leaving published data intact, mapping errors, expired sessions, permissions, and cross-tenant attempts. Demonstrate org A cannot read/update/import org B's data. Run migrations, API/frontend tests, typecheck/lint and Docker startup. Report exact pass/fail results; stop before M3.

## M3 — Governed metrics and deterministic dashboards (P0)

> **I approve M3 only.** Inspect M2 and implement FR-05 and FR-06 using SDD Sections 4, 7–9, and 13. No LLM-based metric calculation in this phase.
>
> Create the canonical business tables and migrations for branches, customers, products, orders, order items, returns, inventory movements, invoices and payments, with tenant-safe foreign keys and currency-aware DECIMAL values. Preserve historical `cost_at_sale`; use posted/completed records and correctly account for discounts, refunds and partial payments.
>
> Implement a versioned, approved semantic metric catalog (`metric_key`, version, definition, allowed dimensions, dates/timezone, source objects, test fixtures). Required calculations: gross sales, net revenue, COGS, gross profit (not mislabeled net profit), eligible order count, AOV, stock-on-hand, available stock where reservations exist, overdue amount, receivables aging, and comparable-period growth. Explicitly define behavior for unknown costs, zero denominators, returns, and absent data. Business time zone: Asia/Karachi; store timestamps in UTC and use end-exclusive time windows.
>
> Create deterministic, parameterized analytics APIs for overview, sales, inventory and receivables. Build polished Next.js dashboard views with KPI cards, branch/product/category filters, dates, tables, charts, and export-friendly displays. Use synthetic fixture data with independently computed expected values. Include tenant/role authorization, accessibility, pagination, loading/empty/error states, and query performance indexes where justified.
>
> **Acceptance gate:** Exact business fixture totals must match independent expected arithmetic, including refunds, returned cost, partial payments, boundary dates, tenant isolation and no-data cases. Capture tests and docs for metric definitions and API examples. No AI query engine yet; stop before M4.

## M4 — Safe AI analytics and evidence-linked answers (P0)

> **I approve M4 only.** Inspect and preserve M1–M3. Implement FR-07, FR-08, FR-09, FR-10 and the P0 part of FR-12 using SDD Sections 6, 9, 11, and 13.
>
> Build a provider-abstracted LLM adapter with environment-configured credentials, request/token limits, timeouts, tracing and mock provider for CI. Implement a single-domain, supported-business-question pipeline: authenticate and authorize → normalize requested business dates/context → identify approved semantic metric/dimensions → provide only permitted schema context → generate a typed, structured query proposal → validate Pydantic schema and SQLGlot AST/allowed joins, identifiers, functions, and one SELECT-only statement → bind parameters on server → execute via a separate least-privilege, read-only reporting DB role under tenant-enforced PostgreSQL policies/views and resource limits → verify deterministic results → produce chart/table and conservative narrative grounded only in results.
>
> Never trust the LLM to enforce tenancy or SQL safety. Deny arbitrary SQL/DDL/DML, multiple statements, forbidden functions, lateral data access, unsafe CTEs/functions or unapproved schemas. Prefer restricted DSL/query templates where simpler. Include row caps, timeouts, query budget and safe failure modes. Do not put credentials or confidential records in prompts/logs. Protect against prompt injection from file content and questions.
>
> Add `POST /api/v1/ai/queries`, `GET /api/v1/ai/queries/{id}` and scoped history. Return query ID, status, result rows, visualization spec, evidence (approved metric/version, dataset versions, date filters, generated-at, query hash, warnings), and limited usage stats. Build 'Ask AI' screen, result charts, evidence drawer, history, clarification and error UI.
>
> **Acceptance gate:** At least **30 independently verified gold question/result cases** and **15 adversarial/safety cases** executed with recorded outcomes, including cross-org denial and injection attempts. CI must use mock/stub model responses for reliable tests; optional live-provider evaluation is clearly labeled and separately cost-authorized. Report execution accuracy, query rejection rates, latency and failures. Stop before M5.

## M5 — Executive reporting, history, secure download and audit (P0)

> **I approve M5 only.** Inspect current state and implement FR-11 plus remaining P0 history/audit requirements from SDD Sections 7–10 and 13.
>
> Build an on-demand monthly executive PDF report from the **same deterministic approved metrics** used by M3 and evidence produced by M4. Include sales, branch/product comparisons, inventory status, receivables ageing, period and data version, source evidence, KPI definitions, limitations, charts and generated timestamp. AI may write a narrative but must not invent numbers, causality or certainty.
>
> Add reports/templates and report/job metadata, private object storage, asynchronous Celery generation when appropriate, report status/history, authorization checks on report access, short-lived signed download links, secure retention/deletion behavior, and audit events for uploads, metric changes, queries, exports and denied requests. Implement `POST /api/v1/reports`, `GET /api/v1/reports/{id}` and UI for preview/history/download/status.
>
> **Acceptance gate:** Use generated synthetic fixtures to assert numerical equality between dashboard, SQL, evidence and exported PDF. Test missing/failed jobs, access denial between organizations, viewer permissions, expired links, duplicate requests, and sanitized logs. Provide a documented **P0 end-to-end demo** (two organizations → data upload → dashboard → safe AI query → evidence → PDF). Run full CI and stop before M6.

## M6 — Professional release (P1): implement as individually approved sub-milestones

**Do not ask Codex to do all of M6 at once.** Send one of the following prompts at a time.

### M6A — Cross-source reconciliation

> **I approve M6A only.** Add explicit reconciliation for independently imported ERP sales files, inventory spreadsheets and payment CSVs, building on the M2 staging and M3 business schema. Maintain canonical source-ID mappings, normalize branch/customer/product identifiers and units, detect duplicates, missing keys, unmatched/partial payments, mismatched totals, stale snapshots and stock anomalies. Provide human-reviewed mapping resolutions, status/severity, row-level evidence, issue audit trails and publication blocking/acknowledgment rules. Add reconciliation endpoints and UI. Test fixture must seed known duplicates and orphan references; every seeded issue is detected; partial payments reconcile without double counting. Do not implement M6B yet.

### M6B — Cross-domain analytical planning

> **I approve M6B only.** Implement bounded multi-domain decomposition for combined sales, receivables and inventory questions, e.g. 'Compare Lahore sales, show overdue customers and identify low stock.' Build a typed task plan, permission/metric checks per subtask, independent validated query execution using M4, bounded concurrency/cost, result synthesis and evidence per sub-answer. No forecasting claims until M7 is implemented. Clarify ambiguous periods or unsupported causal 'why' claims; use trends/contributing patterns rather than invented causes. Add `POST /api/v1/ai/analyses` and a multi-panel report UI. Evaluate correct decomposition, partial failures, cost limits and tenant boundaries. Do not implement M6C yet.

### M6C — Custom dashboards, scheduling and controlled sharing

> **I approve M6C only.** Implement custom dashboard widgets/layouts referencing only permitted saved metrics/queries, server-checked sharing/permissions, saved filters and schedule-based executive reports via background jobs. Add concurrency-safe, idempotent scheduling and tenant-specific report access. Test sharing denial, job retries, duplicate scheduled execution, timezone boundary behavior and data freshness. Do not implement M6D yet.

### M6D — AI evaluation dashboard

> **I approve M6D only.** Extend the M4 gold set to **at least 150 annotated business question cases** with independently computed expected outputs and security negative cases. Implement recorded evaluation runs, versioned prompts/models/semantic definitions, result correctness, execution success, metric correctness, explanation faithfulness, denied unsafe queries, latency and token/cost measures. Build evaluation dashboard with failure inspection and reproducible CI/offline runs. Do not fabricate scores, and do not require a paid LLM call in every CI run. Do not implement M6E yet.

### M6E — Authorized PostgreSQL connector

> **I approve M6E only.** Build an optional external **read-only PostgreSQL data source** connector with TLS, hostname/port allowlist or secure egress policy, secret references rather than plaintext credential storage, least-privilege accounts, approved schema selection, test connection and query timeouts. Guard against SSRF, internal-network access, credential disclosure, arbitrary SQL and unbounded extraction. Stage imported external records into governed datasets; do not expose external databases directly to model-chosen queries. Include per-org authorization, integration tests with disposable databases and a graceful disable switch. Do not implement P2 integrations yet.

**M6 gate:** End-to-end P1 workflow passes using mismatched sources; cross-domain answers preserve evidence; permissions hold for custom dashboards, scheduled reports and external connectors; evaluation results are recorded. Owner explicitly decides whether to proceed to M7.

## M7 — Predictive ML, risk alerts and optional advanced features (P2)

> **I approve M7 core only.** Implement FR-18 from SDD Sections 7, 8, 11, and 13. Do not start optional MCP/multilingual features unless separately approved.
>
> Build tenant-scoped forecasting for selected product/branch demand using historical synthetic sales. Start with transparent seasonal-naive/moving-average baselines and introduce a justified statsmodels or scikit-learn approach. Use rolling/walk-forward time splits with no future leakage, enough-history checks, forecast horizon configuration, backtest metrics (MAE/WAPE or suitable alternatives), prediction intervals or clearly labeled empirical uncertainty, and model/version metadata. Compare every advanced model with a simple baseline; display limitations when data is sparse.
>
> Calculate stockout risk using available inventory, lead time and demand forecast with explicit assumptions. Add rule-driven anomaly detection on sales/returns/payments with reason codes and reviewable evidence. Create forecasting APIs, job persistence, charts showing observed data versus predictions and uncertainty, and optional non-actionable alerts. Do not automatically issue purchase orders or financial transactions.
>
> **Acceptance gate:** Reproducible time-based backtests, baseline comparison, tested stock calculations, uncertainty display, independent tenant isolation and clear error handling. Report whether forecasts genuinely improve the baseline; never assert improvement without measured evidence. Stop before M8.

**Optional P2 add-ons, each by separate instruction:** (a) what-if simulations with stated assumptions and sensitivity analysis; (b) Roman Urdu/Urdu query handling evaluated against the English benchmark; (c) optional MCP server with allowlisted read-only tools and scope-based permissions; (d) LangGraph orchestration only if the existing planner becomes complex enough to justify it; (e) ERP adapters after permissioned integration and mapping design.

## M8 — Production hardening, staging/demo deployment, documentation (release)

> **I approve M8 release preparation only.** Inspect M1–M7 (including any intentionally deferred P1/P2 features) and implement the release readiness work in SDD Sections 10, 12, 13. Do **not** provision paid AWS resources, connect real financial data, publish secrets, or deploy without my separate explicit approval.
>
> Harden Docker production images, CI build/test/scan pipeline, Alembic migration safety, environment configuration, rate limits, secrets strategy, object storage policy, backups and tested restoration, health/readiness checks, tracing/metrics/alerts, tenant-scoped audit retention, request/cost budgets, worker retries, queue observability and database performance. Provide reproducible infrastructure-as-code or reviewed configuration for AWS ECS/Fargate, RDS, S3, secrets and logs, with a conservative cost estimate and rollout/rollback runbook. Add staging configurations and E2E tests using synthetic datasets.
>
> Produce a deploy checklist and comprehensive README (architecture, development setup, environment reference, migrations, tests, security boundaries, supported features, known limitations, screenshots/demo script). Prepare recruiter demonstration: create two tenants → import sales/inventory/invoices → validate KPIs → ask a supported AI query and display evidence → export PDF → verify denial across tenants → optional P1 reconciliation and P2 forecast demo. Verify CI and, where permitted, backup restoration and load tests. Report blockers honestly.
>
> **Gate:** Do not mark production deployment complete until I separately authorize environment provisioning and you verify a live staging/demo run and restore test. Stop here.

## Milestone sign-off template (send after every Codex task)

Ask Codex to fill in:

| Gate | Evidence required |
|---|---|
| Changed files | Paths and short purpose |
| Migrations | IDs, up/down behavior, tested rollback |
| API changes | Routes, models and OpenAPI updates |
| Tests | Exact commands, counts and pass/fail output |
| Security | Tenant/RBAC negative tests and SQL policy checks where applicable |
| UI | Pages demonstrated and error/loading states |
| Operations | Docker, worker, secrets and CI status |
| Risks | Open issues, skipped tests and external dependencies |
| Manual verification | Copyable commands and user-visible success criteria |
| Next approval | One next milestone only; do not auto-start |

**Reminder:** This file is an execution guide, not permission to skip the SDD. The authoritative requirements and acceptance gates remain in `SYSTEM_DESIGN.md`.
