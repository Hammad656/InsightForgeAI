# M6E PostgreSQL connector

Organization administrators can open **Data connectors** from Workspace. Choose an operator-approved profile, test the connection, select an approved table, map the columns, preview up to ten rows, and submit a synchronization. History records the immutable analytics snapshot or reconciliation source, row count, status and sanitized error codes. Disabling or removing a connection preserves existing snapshots and provenance.

## Operator configuration

The connector starts disabled in practice because the profile registry is empty. Configure `PG_CONNECTOR_PROFILES` as a JSON object keyed by organization UUID. The inner map names each approved profile. Each profile supplies `host`, `port`, exactly one pinned numeric `address`, `database`, `username`, `secret_ref`, `ca_file` and an `objects` array of `schema_name` and `table` pairs. Operators edit the profile registry and restart API and workers to change connection parameters. Organization administrators select the profile in the browser.

Set `PG_CONNECTOR_SECRET_DIR` to a protected, read-only directory mounted from your secret manager. Provision one UTF-8 password file per secret reference and mount a trust root for the source. Rotate the remote account password and atomically replace the mounted file. Passwords are read at connection time. API responses omit usernames, passwords, secret references, and resolved addresses. Provide the application services with network egress restricted to approved database addresses and ports, the needed DNS resolver, and existing application dependencies. No deployment network is provisioned by this implementation.

Remote profiles require PostgreSQL TLS `verify-full`, including host verification against the pinned address, and the resolved hostname must return exactly the configured public address. The connection uses libpq's pinned `hostaddr` and disables GSS encryption negotiation. Private, loopback, link-local, multicast, mapped and metadata addresses are denied by default. The loopback test exception requires an explicit profile flag, `PG_CONNECTOR_ALLOW_LOCAL_TEST=true`, and insecure development cookies. Use it only with a disposable source. A test profile with a CA exercises TLS on loopback.

Use a dedicated source database and a least-privilege account without role memberships, database CREATE or TEMP, schema CREATE, object ownership, elevated role attributes, sequence access, or writes. Public grants count. Grant SELECT only to the approved ordinary tables or approved columns plus schema USAGE. Views, foreign and partitioned tables, custom types, and arbitrary SQL are unsupported. Remote source administrators are trusted to manage the PostgreSQL instance, updates, and row policies. Transactions are read-only, with query and lock timeouts and bounded connections per service process.

## Publication

The analytics destination uses the M2 canonical validation and atomic dataset publisher. The reconciliation destination stages a source for the existing M6A workflow. It requires that pipeline's fields, including invoice references or measurement units where applicable. Run reconciliation after sync to evaluate source records. A reconciliation-only sync does not activate a BI dataset.

Extraction reads at most 10,000 rows, 5 MiB, and 1,024 characters per value. The connector rejects an oversized full snapshot. Job keys make retries idempotent, and unchanged analytics content with identical source mapping reuses its existing immutable version. Large production sources should expose a deliberately bounded ordinary staging table with the fields needed for this connector.

## Verification

Use an isolated app database, a separate PostgreSQL source, and Redis. The CI fixture sets `M6E_INTEGRATION=1`, prepares synthetic source data with `python tests/m6e_source.py`, and passes the profile registry to the real Celery worker. The source tests verify trusted and untrusted certificates using temporary CA files. For local reproduction, point `M6E_SOURCE_PORT` to the disposable source port and supply `M6E_TRUSTED_SOURCE_CA`, `M6E_SOURCE_CA`, and `M6E_SECRET_FIXTURE_DIR`. Upgrade the app database with `alembic upgrade head`.
