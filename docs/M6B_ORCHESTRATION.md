# M6B implementation plan

Baseline: CI-verified M6A commit `5b29d0376ea13e299b2bfe4bd05deaa80ac0626a`.

1. Store one author-scoped analysis and its progress under forced PostgreSQL RLS. Submit through the existing Celery broker, with an idempotency key, cancellable state and a bounded execution lease.
2. Request a single typed plan from the M4 provider. Validate dependency cycles, domains, metrics, dates and dimensions independently. The server chooses source versions from a single authorized snapshot. Forecast tasks return an unavailable status.
3. Reuse M3 expressions and M4's reporting role, AST policy and binding. Recheck membership before each task and before publishing progress. Execute at most two tasks concurrently within a fixed deadline and retry only transient connection errors.
4. Persist domain results, provenance and sanitized audit events. Synthesize the executive summary from verified values and supported conclusions. Attach M6A reconciliation context separately from financial calculations.
5. Add an Ask AI navigation entry and a responsive analysis workspace with progress, cancellation, history and per-task evidence. Verify the pipeline with synthetic fixtures and a mocked provider.

The typed coordinator fits the fixed four-task graph and existing broker. LangGraph would add another execution and state layer without improving this workflow.

## Runtime contract

- `POST /api/v1/ai/analyses` takes `organization_id`, `request_key`, `question` and optional `dataset_ids`. The key is unique to an author and organization. Resubmitting a queued request with the same payload can redispatch it after broker failure.
- `GET /api/v1/ai/analyses`, `GET /api/v1/ai/analyses/{id}`, and `POST /api/v1/ai/analyses/{id}/cancel` take `organization_id` in the query string. Analysts and administrators can submit, read and cancel their own analyses. Other authors' records return 404, including for administrators in the same organization. Role and membership are checked again before task execution and result persistence.
- Active source versions and organization quality context are captured at submission. An optional set of active dataset IDs narrows the snapshot scope. Even if a newer version is published during execution, every task uses its original immutable source.
- The provider makes one structured plan with the existing 600 output-token cap. Plans above 6,000 reported total tokens are rejected. Submission is limited to six new plans per user per minute. Task SQL has a combined ceiling of 100,000 PostgreSQL planner cost units per task, at most 25 returned groups, a two-second statement ceiling, and a 40-second run deadline. Planner units are estimates rather than currency.
- Cancellation is cooperative. It stops new SQL and new result publication. An in-flight SQL statement can finish within its statement timeout. A running job that loses its worker expires instead of repeating its provider call. Only transient connection or serialization failures are retried once per task.
- Executive synthesis uses verified task results and rule-based recommendations. A sales calendar comparison requires a complete month and queries the immediately preceding calendar month. Forecasts remain unavailable. Historical quality and task evidence are snapshots of the execution context.

Apply `alembic upgrade head` through `0006_m6b`. Select **Multi-domain analysis** from Ask AI. The live-worker tests set `M6B_INTEGRATION=1` and `M6B_LIVE_WORKER=1` alongside existing integration settings. Start the worker with `tests` on `PYTHONPATH` and `celery -A m6b_worker:celery_app worker --pool=solo` to inject the test-only provider. The deployment entrypoint continues to use the configured M4 provider.
