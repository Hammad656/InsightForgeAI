# InsightForge AI — Codex repository handoff

**Start here.** The authoritative architecture, data model, feature scope, security decisions, API contracts, and milestone gates are in [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md). Treat all listed features as **planned**, not as existing code.

## Owner's current instruction

**Planning only.** Do not create implementation files, change the repository, push commits, deploy, or purchase cloud services until the owner explicitly instructs you to implement a milestone.

## Required first response after opening the GitHub repository

1. Inspect `git status`, branch, repository tree, README, existing `AGENTS.md`, manifests, framework versions, migrations, existing APIs, tests, Docker and workflows.
2. Summarize what exists and what is missing relative to the SDD. Separate **reusable**, **needs modification**, **new**, **risky or conflicting**.
3. Identify existing naming, data models, auth, package choices, deployment expectations, and any secrets exposure *without printing secrets*.
4. Propose an M0 gap report and M1 implementation backlog with file-level changes, acceptance tests, ADRs and risks.
5. Ask for explicit approval before implementing **M1**. Do not proceed to M2–M8 without a new instruction.

## Binding architecture decisions

- Next.js + TypeScript frontend; FastAPI Python backend; PostgreSQL authoritative store.
- Modular monolith plus Celery worker, not microservices.
- Synthetic wholesale/retail business data, single base currency and Asia/Karachi time zone initially.
- P0: authentication, tenant isolation, CSV/XLSX import, governed metrics, dashboards, safe text-to-SQL, evidence, PDF, 30+ gold tests and 15+ safety tests.
- P1: reconciliation across multiple sources, multi-domain query planner, custom dashboards, eval dashboard, read-only PostgreSQL connector, scheduling.
- P2: forecasting with uncertainty, anomaly/stockout models, what-if simulation, multilingual and optional MCP/agentic orchestration.
- No generated SQL executes without a trusted policy engine and a least-privilege read-only reporting DB role. All tenant boundaries enforced independently of LLM behavior.
- Existing repository choices may require adaptation: report conflicts before changing these decisions.

## Quality / completion rules

For each authorized milestone: write tests and migrations, run static checks and automated tests, report exact results (including failures), update docs, and ensure no tenant or business data is leaked into logs. Never claim a feature works if it has not been tested. Avoid hardcoded API keys, direct production DB access, broad tool permissions, and hidden schema changes.

## Suggested prompt to paste into Codex

> Read `SYSTEM_DESIGN.md` and `CODEX_HANDOFF.md`. Inspect this GitHub repository and produce a non-destructive M0 repo audit, feature gap analysis, relevant architecture conflicts, prioritized backlog, and an M1 file-level plan with tests and acceptance criteria. **Do not modify code yet**. Do not invent completed features. Wait for my explicit approval before implementation.

## First implementation request (only after approval)

> Implement M1 only, following the approved plan and the SDD. Preserve existing project structure when reasonable; add tests, migrations and environment templates; run checks; report changes and unresolved issues. Do not deploy or push without separate permission.
