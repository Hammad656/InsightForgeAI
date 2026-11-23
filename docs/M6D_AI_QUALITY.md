# M6D AI Quality benchmark

Open **AI Quality** from Ask AI. Analysts and administrators can run, examine, compare and export evaluations for their organization. Administrators can pin one completed baseline per suite. A retry keeps the original run identifier. The worker leases each run, publishes results atomically and rechecks the requester's role before publication.

## Held-out suite

`retail-holdout-v1` has 162 cases. Its ledger differs from the M4 fixtures, which remain in CI unchanged. The manifest pairs each question with a distinct metric or decision and reporting scope. It covers 80 sales cases, 24 inventory cases, 40 receivables cases, 6 cross-domain plans and 12 clarification, refusal or policy cases. The independent reference code uses Decimal arithmetic over signed movements and invoice ledgers, rather than production SQL. It checks exact outputs across date cutoffs, missing costs, zero denominators, repeat order lines, partial payments, unpaid invoices and ageing boundaries. Dates use the Asia/Karachi business calendar; the source contract stores business dates without timestamps.

The offline provider replays annotated intents and emphasis choices. The successful execution score includes real PostgreSQL analytical SQL and the existing M4/M6B reporting policy. It does not validate live question interpretation. The tenant-specific runtime fixture tests database isolation with two synthetic organizations. These fixtures have no memberships or usable user login. Application tests separately verify role and tenant authorization.

Only outcomes, classifications, benchmark metadata, timing and token summaries are persisted per case. Exports exclude prompt text, raw SQL, response text and result rows. Benchmark datasets are synthetic and evaluation jobs never read the requesting organization's business data. Suite version and ledger hash are recorded for reproducibility.

## Running the benchmark

From the backend, configure the application and reporting database roles as in the existing local development setup. Upgrade with `alembic upgrade head`. The following command runs the offline suite and writes only aggregate scores and failing case IDs:

```sh
python -m app.evaluation
```

The CI integration test sets `M6D_INTEGRATION=1` while a Celery worker is running. No provider key is needed. The UI reports nearest-rank p50/p95 for the observed application path, separate applicable-case denominators and recorded token counts. Mocked token counts are zero and cost estimates are unavailable for offline runs.

Live mode is disabled by default and is only available via the API. It requires operator authorization to configure `EVALUATION_LIVE_ENABLED=true`, the existing OpenAI provider, a model and key, and `EVALUATION_COST_CEILING_PER_MILLION_TOKENS` set to a conservative USD rate for that model. An administrator must also send `live: true` and `approve_provider_cost: true`. This mode runs a fixed six-case subset and has no automatic job retry after starting. The estimate uses reported total tokens multiplied by the configured blended ceiling rate. It is not billing data and excludes requests whose usage was not returned. Baseline comparisons pair only matching fixture cases and show the paired count.
