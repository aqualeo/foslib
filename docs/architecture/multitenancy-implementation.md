# Multi-tenant implementation status and security contract

This branch is an incremental implementation. It does **not** yet satisfy the
multi-tenant staging acceptance criteria. No live database has been migrated and
no school accounts have been provisioned by this change. Existing ILS routes
remain unchanged. Do not advertise tenant isolation until the integration work
below is complete and tested.

## Implemented control plane

`invenio_app_ils/multitenancy/sql/001_security.sql` creates separate NOLOGIN
owner, runtime and authentication-broker group roles. Private tables hold
principals, schools, memberships, hashed authentication capabilities, transaction
contexts and administrative audit events. RLS is enabled and forced, with an
explicit owner policy on these control tables; runtime has no private-table
privileges. All SECURITY DEFINER functions pin `search_path`, qualify tables,
revoke PUBLIC execution, and use bound SQL parameters. No function executes
client-supplied SQL or sets roles based on client input.

The authentication broker can mint and revoke sessions, but cannot read or write
school operational data. The runtime cannot mint a capability. A separate login
role must be provisioned for each group; **the runtime login must never be a
member of the broker or owner role, and must never be a superuser**. Merely
connecting as an owner and issuing `SET ROLE foslib_runtime` is insufficient:
PostgreSQL checks `SET ROLE` against the session user's memberships.

After existing Invenio login succeeds, the application generates a 256-bit
opaque capability, stores its SHA-256 digest through the broker connection, and
retains the secret only in its server-side session. The broker must derive the
user ID from authenticated `current_user`, never from a request body. This
capability issuance is a trusted authentication boundary; SQL injection confined
to the runtime connection cannot access the separate broker connection or its
credentials. Process compromise or broker credential theft is outside that
boundary and requires revoking capabilities and rotating credentials.

Each request/job starts an explicit transaction and activates a capability plus
the selected school. The database verifies the session, principal, active
membership and school status. Context lives in an inaccessible table keyed by
backend PID and the full transaction ID (`xid8`). A transaction can activate only
once. There is no trusted GUC and no cross-transaction context inheritance.
Policies recheck expiry/revocation and active membership at statement boundaries
under READ COMMITTED. Use READ COMMITTED for runtime connections; a deliberately
long REPEATABLE READ transaction retains its old membership snapshot until it
ends. Bound transaction duration and revoke connections for urgent termination.

Platform principals do not acquire automatic school access. The explicit
`admin_set_school` function checks platform privilege and appends an audit event
in the same transaction. Successful, committed changes always retain their
audit event. Rejected or rolled-back actions require structured application
security logs; PostgreSQL cannot preserve an audit insert in a rolled-back
transaction. Runtime cannot edit/delete audit rows.

`tenant_transaction` is a psycopg 3 adapter and rejects nested activation. The
existing Invenio SQLAlchemy/psycopg2 request integration is **not yet wired**.
Worker tests exercise this adapter as a database contract; they do not prove
that existing Celery tasks propagate context.

Expired sessions and stale backend context rows need a privileged maintenance
job. Delete context rows referencing expired/revoked sessions before deleting
those sessions. Never grant this cleanup role to runtime. Context rows cannot
authorise later transactions even before cleanup.

## Existing-model audit

* `IlsRecord` derives from upstream `invenio_records.api.Record`, storing
  documents, items, locations and requests in `records_metadata` JSONB, with
  version history. School ownership must protect both current and historical
  rows. A blanket tenant policy would incorrectly hide shared documents.
* `Document` and `Item` already implement ISBN metadata, inventory validation,
  PID resolution and references. Preserve these APIs; do not create a parallel
  circulation store to bypass them.
* Loans use upstream `invenio_circulation.api.Loan`, transitions and search APIs.
  Inspect and migrate the installed model, including transition history.
* Patrons wrap Invenio authentication accounts and profiles and are indexed in
  OpenSearch. A global user-login table cannot simply be hidden by tenant RLS:
  login occurs before a school is selected. School-patron associations and the
  patron APIs/index require independent ownership checks.
* Existing `ItemSearch`, `LoansSearch`, patron search, statistics, resolvers,
  exports and document metadata extensions can expose operational information
  through OpenSearch. PostgreSQL RLS alone cannot secure these routes.

## Remaining integration gates

1. Inventory the live schema and assign existing operational rows explicitly;
   do not guess Alpha/Beta ownership or drop pre-existing records.
2. Add non-null school ownership and composite foreign keys to existing
   operational models and history, plus tenant query indexes and forced RLS.
3. Bind authenticated requests and every worker/job to the restricted runtime
   role; integrate broker issuance/logout and authorised school selection.
4. Prevent cross-school search hits, aggregations, embedded holdings, resolvers,
   exports and direct document reads; reindex safely.
5. Implement missing school member, hold, fine and audit functionality through
   existing ILS services; build role-based screens and seed fictional schools.
6. Run real HTTP, Celery and browser acceptance tests against both schools,
   not just the isolated database-contract fixtures.
7. Verify on PostgreSQL 18 (staging version), review backup/restore and rollout,
   then deploy app and worker from the reviewed implementation commit.

## Local security tests

Create a virtual environment and install `pytest`, `psycopg[binary]` and
`pgserver==0.1.4`, Flask and Click. These are development tools; no new runtime dependency has
been added. `pgserver` supplies an isolated PostgreSQL 16 instance and does not
connect to staging. Run:

```sh
python -m pytest --confcutdir=tests/multitenancy tests/multitenancy -q -o addopts=''
```

The test inventory/catalogue/loan tables are contract fixtures, not application
models. Tests exercise reciprocal RLS, write denial, composite FKs, token/GUC
forgery, membership switching, capability revocation, reader/worker permissions,
transaction/pool reuse, privilege escalation, and explicit audited admin access.
Passing them does not claim the unchanged production ILS tables are isolated.

The full existing application dependencies enable an additional integration
test that imports all 1,000 editions through actual Document/PID APIs and checks
that a second import preserves edits and creates no duplicates. A dedicated CI
workflow runs these tests against PostgreSQL 18. Local `pgserver` uses version
16, so local results alone do not establish PostgreSQL 18 validation. CI accepts
only a dedicated disposable `*_test` database via `FOSLIB_TEST_POSTGRES_DSN`.

## Shared catalogue implementation

The committed [Open Library snapshot](../../data/catalogue/README.md) supplies
1,000 genuine, checksum-valid editions with explicit CC0 provenance. The
repeatable downloader keeps work-level search data out of edition metadata.
The new `ils foslib-catalogue` commands validate, preview, atomically import
through the existing Document/PID APIs and separately queue search indexing.
The Docker image includes the snapshot. No live staging import has occurred.

## Migration and rollback review

Run `001_security.sql` inside one explicit transaction using a dedicated
migration credential with CREATE ROLE and SET ROLE privileges. It intentionally
fails if its schemas already exist, so accidental reapplication cannot silently
alter live policy. Use a migration ledger before staging rollout. It refuses
pre-existing privileged group roles or unsafe role memberships.

Provision login-role passwords separately through the secret manager, never in
SQL files, command arguments or Git. Do not change Railway's existing database,
volumes, networking or connection references. The current staging Postgres is
private and has no public TCP proxy; use an authorised in-network execution path.

Before rollout, take and verify a restorable backup, inspect schema/row counts,
and review the ownership migration. This initial additive migration does not
alter existing ILS tables. If it fails inside the migration transaction, roll
back the transaction. After a committed rollout, disable its callers first and
prefer a forward fix. Removing `foslib`/`foslib_private` would delete newly
created memberships, sessions and audit records, so no destructive rollback is
automatically provided. Export/retain those records and review dependencies
before any approved removal. Do not use `ils setup`, which drops live data.

## Staging baseline observed on 2026-11-12

Railway reports `foslib-app` online and Postgres, Redis, RabbitMQ and OpenSearch
online. `foslib-worker` was crashed. Its log identifies RabbitMQ
`PRECONDITION_FAILED`: a delivery remained unacknowledged for 1,800,000 ms.
This identifies the immediate failure, not the underlying slow/stuck task.
Inspect task identity, duration, retries and downstream waits before changing
timeouts or restarting queued work. No infrastructure settings were changed.

The last received tasks before that timeout were
`closures.tasks.extend_active_loans_location_closure`. Its producer,
`notify_location_updated`, schedules an ETA at the configured daily time,
potentially almost 24 hours ahead. Celery can hold ETA deliveries unacknowledged
until they become due, so this is a likely explanation for the broker's
30-minute acknowledgement timeout even without a running task being stuck.
Inspect the queued task ETAs in-network to confirm. A durable due-job dispatcher
would avoid long broker-held ETAs; merely restarting the worker may repeat the
failure. Any temporary broker-timeout change must preserve existing broker
settings and be reviewed against the maximum ETA and task duration.

Chromium also loaded the public homepage with HTTP 200, visible catalogue
content and no JavaScript page errors. It showed the earlier fictional Alpha
and Beta sample titles. This was a baseline check, not an authenticated
multi-tenant acceptance test. Railway MCP access is available, but CLI access
for in-network execution requires a separate sign-in; its secure device flow
was requested. The database remains private with no public TCP proxy.
