# Custom dashboards and scheduled reports

Custom dashboards are available from Business analytics. Analysts and administrators can create dashboards and share them with a current organization member. Owners control sharing and deletion. An Analyst with an editor grant can edit the saved title and layout. A Viewer can refresh a dashboard explicitly shared with them and set it as their personal default. The organization membership check also applies to every refresh. Use the member ID from Team and Settings to grant access.

Widgets use supported metric and dimension combinations from the governed catalog. Each refresh resolves all active or explicitly pinned versions at once and queries those immutable versions. The response includes the version and publication time for each source. Stale indicators use a seven-day publication threshold. Refreshes bypass shared caches and results are private and marked no-store. The builder supports keyboard-operated reordering and sizing. Each dashboard is capped at 12 widgets and grouped results are capped at 25 rows.

Report schedules are available from Reports. One Celery Beat process publishes the dispatcher task every minute. Docker Compose includes the scheduler service. For manual development use:

```sh
celery -A app.jobs.celery_app:celery_app beat --loglevel=INFO
```

Select the business timezone, delivery time, delivery cadence, and prior calendar day, week or month. Weeks begin Monday. During an autumn clock change the first occurrence is used. Spring-forward gaps shift the delivery forward by the gap. Monthly delivery supports days 1 through 28. A restart processes the oldest outstanding occurrence once and advances to the next future slot. Pausing prevents new runs while allowing recovery of an already queued report.

The dispatcher reads only internal due identifiers before entering tenant scope. It records the schedule occurrence, report and next wakeup in one transaction. The occurrence is unique by tenant, schedule and delivery time. Redis or worker interruption can redispatch the same report from its existing durable record. The M5 worker rechecks permissions and reconciliation quality and retains private storage, expiring one-time download grants, and the 30-day retention period. Schedule owners can retrieve execution history. Artifact access follows M5 rules for the report owner and organization administrators. Report details and alerts are delivered in-app.

The dashboard refresh is on demand. Widget preferences and layouts are saved, and result caching and periodic dashboard refresh are not enabled. Dataset scope currently follows organization membership and the selected tenant-owned snapshot. This milestone does not introduce a separate dataset ACL system.
