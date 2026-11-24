# M7 predictive analytics

Open **Predictive analytics** from Business analytics. An Analyst or Admin can train a forecast, and members can inspect runs in their organization. Runs pin active dataset versions at submission. A completed run includes the training digest, model and input versions, exact scope, cutoff, holdout metrics and source freshness. The API does not return model artifacts.

## Data and validation

Sales demand is completed units, and the revenue series uses M3 net revenue. Exact product SKU and branch filters support product and branch segments. A category is an explicit list of SKU identifiers submitted with the run because the ingestion contract has no governed category dimension. Compare scope-specific holdouts in run history.

The calendar uses Monday-start weeks in Asia/Karachi. Source sales dates are already business dates. The cutoff week and later weeks are excluded from training, even if the snapshot contains them. PostgreSQL records run and publication timestamps with timezone information. Training uses up to 208 complete calendar weeks. Empty organization weeks are missing. Within an observed week, an empty scoped result can count as zero only after the user confirms the feed is complete. The model uses the latest contiguous segment and abstains if it is too short. Blocking reconciliation stops training or publication. Other quality states are recorded with the run.

## Models and uncertainty

Comparisons include last value, seasonal naive, trailing mean over one season, and a robust seasonal trend model. The last model estimates slope from same-phase changes and a median intercept per phase. Select a 4-, 13-, or 52-week cycle and a 1-, 2-, or 4-week forecast. A run requires twice the seasonal period plus 16 horizon blocks in history.

Sixteen nonoverlapping rolling-origin targets are partitioned chronologically. The first four select by MAE, the next eight calibrate separate absolute-error widths per forecast lead, and the last four measure untouched holdout performance. Every fold fits on its historical prefix. Final inference refits the selected method on the observed series. WAPE divides absolute error by absolute actual volume and is undefined at zero. Bias is forecast minus actual. Reported interval coverage is observed holdout coverage for an 80% target. Small calibration samples, serial dependence and regime changes limit calibration. Intervals can collapse on artificially exact fixtures. Predictions are estimates.

## Advisory outputs and access

Stockout risk requires a demand forecast, exact SKU and branch, observed stock on hand, and supplier lead time within the forecast horizon. Weekly predictions are allocated uniformly to days. Confirmed deliveries can be supplied with dates and quantities. When missing, the scenario includes no inbound stock and says so. Stock on hand excludes reservations. Coverage uses recent observed daily sales velocity. The threshold uses upper lead-time demand before inbound supply. No purchasing action is triggered.

Anomaly flags compare each recent observation to its preceding same-phase history using median absolute deviation with a floor. Sales units, revenue and signed inventory movements are supported. Payment amounts are supported organization-wide. The returns series is unavailable in the current ingestion contract. Flags are review signals and can miss or misclassify data gaps and regime changes.

Celery uses a tenant lease, idempotency key, bounded retry and fresh authorization at publication. Model artifacts have a 30-day validity window and are purged opportunistically during later jobs in that organization. Run summaries remain available for review. M6B reuses completed demand estimates for the exact tenant, sales snapshot, SKU, branch, business cutoff and requested horizon when supplied. It declines incompatible or unavailable forecasts.

## Verification

The synthetic regression fixture has 96 weeks of repeated demand with trend and four seasonal phases. Tests assert exact held-out values and perturb only the holdout to verify it cannot affect model selection or interval calibration. Set M7_INTEGRATION=1 for the live integration test alongside the existing live-worker test flags. Run Alembic through 0010_m7 before starting the worker.
