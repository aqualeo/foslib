# Foslib multi-tenant implementation

## Current scope

The implementation keeps Invenio's existing `records_metadata`, version history,
PID store, Document, Item and Loan APIs. It adds verified school ownership,
school member/hold/fine/payment/event schemas, authentication binding, a school
API and the FOS-themed interface. Staging activation and live browser acceptance
are tracked in the pull request; code availability alone is not acceptance.

The user interface is branded **FOS Library Management System**, using the teal,
marigold and warm neutral palette from Aqualeo/FriendsOfSchool. The earlier React
ILS remains available when `FOSLIB_MULTITENANT_ENABLED` is disabled.

## Database authority

`001_security.sql` creates separate NOLOGIN owner, runtime and authentication
broker group roles. Login roles are provisioned separately with secret passwords.
Runtime must not own tables, be a superuser, have BYPASSRLS, or inherit the owner
or broker role. Connecting as a superuser and merely SET ROLE is not the runtime
security boundary: the deployed connection uses a genuinely restricted login.

Only the authentication broker can mint/revoke opaque 256-bit capabilities,
after successful existing Invenio authentication. Only capability digests are
stored in PostgreSQL; the browser capability remains in the existing server-side
Redis session. The principal ID comes from authenticated `current_user`, never
from client JSON. School selection is checked against active memberships.

Context is stored in an inaccessible table keyed by backend PID and full xid8.
It cannot be forged through SET/configuration variables. Every SQLAlchemy root
transaction revalidates the capability and membership, including internal
SQLAlchemy-Continuum sessions. Identical context re-entry is permitted; changing
identity or school within a transaction is rejected. Connection reuse cannot
carry authorisation into a new transaction. Use READ COMMITTED and bound request
and job durations; urgent revocation of a long snapshot also requires terminating
that connection. Principal, school, membership and session status are rechecked
at statement boundaries under READ COMMITTED.

All SECURITY DEFINER functions pin search_path, qualify tables and revoke PUBLIC
execution. SQL inputs are bound parameters. The trust boundary assumes runtime
SQL injection, not compromise of the application process and its separate broker
credentials. Rotate credentials and revoke sessions after process compromise.

## Existing records and references

`002_records.sql` adds ownership and typed identity/reference columns to the
existing metadata table. Shared bibliographic rows have null ownership; every
operational row must have non-null ownership, enforced by a CHECK constraint.
Triggers classify schemas, set ownership from verified context, prohibit changing
identity/ownership and retain identity on soft deletion.

Composite foreign keys prevent school copies, members, loans, locations, holds,
fines and payments from referencing another school's operational records. Shared
book references remain global. Tenant indexes support school lists; unique
indexes protect per-school barcodes/member codes, active copy loans and pending
holds. Database copy locks and constraints protect checkout races; fine locks
protect duplicate payments. Payments and circulation events are append-only.

Metadata, history, PID, transaction and notification tables use enabled, forced
RLS and explicit read/write policies. Runtime cannot physically delete records or
history. History may be coalesced by Continuum in its creating transaction;
committed payloads cannot be rewritten. Existing metadata rows and their history
are retained. The explicitly named Alpha/Beta legacy locations are assigned by
PID; unclassified rows are protected under an inactive legacy quarantine tenant.
Any other unassigned operational rows cause migration failure rather than guessed
ownership. No database reset or independent school databases are used.

Authentication accounts remain global identities; school students are scoped
library-member records, not automatically created login accounts. This separates
student data from staff authentication and retains the existing account model
for future FOSSIS/OIDC identity linking. Members have an optional external_ref.
Runtime can read authentication identities and update login/session activity but
cannot change password hashes, global roles or platform privilege. Platform
privilege is stored separately from ordinary Invenio backoffice roles.

## Platform administration and catalogue

`003_catalogue_admin.sql` provides explicitly authorised functions for shared
metadata, schools, memberships and audit access. A platform principal does not
implicitly bypass school RLS. Successful changes and their audit events commit
atomically. Runtime cannot alter private audit rows. Rejected/rolled-back requests
need application security logging; database inserts cannot survive rollback.

The CC0 [catalogue snapshot](../../data/catalogue/README.md) contains 1,000 genuine,
checksum-valid Open Library editions and source/edition provenance. Work-level
search metadata is never substituted for edition metadata. The existing
Document/PID APIs perform imports; a database ISBN alias registry rejects
conflicting editions across all writers. Reruns preserve existing edits. Cover
artwork is excluded. Sixteen source editions omit year and eleven omit publisher;
language, format, edition statements and descriptions remain unknown when the
source does not establish them. No fabricated values are substituted.

## Search and workers

Operational search and catalogue display use PostgreSQL, applying RLS to lists,
counts, joins and record reads. Shared catalogue responses whitelist bibliography
and exclude legacy resolver references/embedded circulation information.

OpenSearch stays private. In tenant mode the gateway denies the legacy search,
resolver, statistics and export routes because their cached aggregates are not
protected by PostgreSQL RLS. Operational indexing is rejected; shared indexing
strips operational fields. The original features are preserved in legacy mode,
but need scoped adapters before their old routes can be reopened in tenant mode.

The SQLAlchemy context adapter also binds job envelopes. `recount_school` is a
scoped Celery job; enqueue through `queue_recount` to redact capability arguments.
An untrusted tenant UUID is insufficient. Legacy closure, circulation,
notification and patron indexing jobs fail before unscoped search unless they
have verified context. Expired capabilities deny access. Scheduling infrastructure
must issue fresh, bounded capabilities for each school/job; this change does not
make legacy unscoped notification schedules safe by granting bypass privileges.

The earlier worker crash was a RabbitMQ 30-minute acknowledgement timeout. Its
received closure tasks can have almost 24-hour ETAs; Celery holds ETA deliveries
unacknowledged. Inspect actual queues and policy before changing settings. A
queue-specific acknowledgement window can preserve existing ETA jobs; a durable
due-job dispatcher is the longer-term improvement. Restarting alone can repeat
the failure.

## Rollout and rollback

The additive bootstrap requires a privileged migration connection, maintenance
mode and secrets supplied through `FOSLIB_BOOTSTRAP_SECRETS`. Passwords are never
source constants. It provisions restricted login roles, records migrations in a
private ledger, imports the catalogue and seeds each school once. Default school
accounts are Admin@alpha.com and Admin@beta.com, with the password supplied by
the user outside Git. Platform credentials are separate. No credentials appear
on the public sign-in page.

Take a backup before migration. The pre-rollout staging backup is retained inside
the existing PostgreSQL volume under
`/var/lib/postgresql/data/foslib-backups/before-tenant-20261112.dump`; pg_restore
parsed its table of contents and payload without errors. No database contents
were exported to this workspace. Backup restoration to a live database is not
automated or authorised by this change.

Deploy the reviewed branch with maintenance enabled, run the additive bootstrap,
then switch app and worker to the restricted runtime connection and enable tenant
mode. The app alone receives the broker connection. Migration credentials remain
on the database service. Remove bootstrap secrets from the application before
opening it. Preserve existing service references, domains, volumes and unrelated
secrets. Never run `ils setup`, which drops live data and indices.

A failed migration rolls back its transaction. After committed circulation data
exists, prefer a forward fix. Maintenance can close public access while a fix is
prepared. Removing ownership columns or restoring a backup would affect new
records and requires separate review/approval; no destructive downgrade is run.

## Verification

The dedicated CI workflow uses PostgreSQL 18. Local isolated PostgreSQL tests
cover direct reciprocal RLS, forged contexts, composite foreign keys, role
escalation, revocation, pooled connections, real metadata/history/PID protection,
actual HTTP circulation and CRUD, immutable audit/history, worker isolation,
search guards and the gateway. A full integration test imports all 1,000 books,
applies the real migrations, seeds exactly 100/75 copies and 25/20 fictional
students, then reruns the seed without duplicates.

Run with existing application dependencies and development tools pytest,
psycopg[binary] and pgserver==0.1.4:

```sh
python -m pytest --confcutdir=tests/multitenancy tests/multitenancy -q -o addopts=''
```

Browser acceptance must still exercise the deployed login, catalogue, inventory,
issue/return, reservations, fines and reciprocal isolation with both school
accounts. An HTTP 200 or successful build is not sufficient.
