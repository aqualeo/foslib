# Foslib multi-tenant RLS architecture (proposed)

## Scope
Shared platform-managed bibliographic metadata, separate school inventories and transactions. Demo schools: Alpha and Beta; 1,000 legitimate ISBN-based titles. This is a design, not an implemented or deployed feature.

The incremental control-plane implementation and remaining integration gates
are tracked in [implementation status](multitenancy-implementation.md).

## Core entities
- `tenants(id, slug, name, status)` for schools; platform administration is a separate privileged role.
- `users` and `tenant_memberships(user_id, tenant_id, role, status)`; validate memberships server-side on each tenant switch.
- `bibliographic_records(id, isbn13, title, authors, publisher, edition, publication_year, source, source_id, metadata_json)`: global, read-only for schools, platform-managed. Distinguish editions and ISBN variants.
- `school_copies(id, tenant_id, bibliographic_record_id, barcode, status, location)`, unique `(tenant_id, barcode)`.
- `patrons`, `loans`, `holds`, `fines`, `circulation_events`, `tenant_audit_events`: each has non-null `tenant_id`. Use composite tenant-aware foreign keys to prevent cross-school references.
- Audit and adapt existing InvenioILS tables and APIs; avoid introducing a parallel transaction store bypassing existing code.

## PostgreSQL security
1. Use distinct migration/owner and runtime database roles. Runtime role must not own tables and must not have `BYPASSRLS`.
2. `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on every school-owned table; default deny.
3. Add explicit `USING` and `WITH CHECK` policies for each permitted operation. Global metadata grants SELECT only to school runtime role; platform writes use controlled audited endpoints.
4. Authenticate user, validate active membership server-side, and establish tenant context inside **every transaction** (`SET LOCAL` / `set_config(..., true)`). No trust in a client-supplied tenant ID.
5. A plain custom session GUC is not an adequate security boundary if the runtime SQL role can set arbitrary tenant IDs. Bind context to a validated membership using a secured database function, trusted signed context, or per-tenant roles; test injection and direct SQL access.
6. Connection pooling must never leak context between requests. Explicit transactions for requests, workers, CLI and scheduled tasks.
7. Search/OpenSearch does not inherit PostgreSQL RLS: partition/index or filter holdings by verified tenant, and ensure records returned via search and document APIs respect tenant rules.
8. Platform-admin cross-school access requires explicit audited privilege; tenant switcher only lists authorised memberships.

## Demo dataset
- Import 1,000 genuine, validated ISBN-13 records from a legally reusable source with provenance, deduplication and idempotent import; do not fabricate ISBNs.
- Alpha: 100 physical copies and fictional patrons/transactions.
- Beta: 75 physical copies and fictional patrons/transactions.
- Credentials generated outside Git, safely provisioned in staging.

## Acceptance tests
- Both schools see shared bibliographic titles.
- Alpha cannot read, create, modify or delete Beta copies, patrons, loans, holds, fines, events or audit entries; reciprocal isolation.
- Reject cross-tenant foreign keys, forged tenant IDs, missing context and unauthorised switches.
- Test pooled connection reuse, background jobs, API endpoints, OpenSearch queries and exports.
- Confirm frontend renders catalogue, school-specific inventory, borrowers and circulation, and role-based navigation.
- Migrate staging safely, document rollback, and test the public URL in a browser before announcing completion.

## Implementation sequence
Audit existing schema and routes → migration and RLS → authenticated memberships → tenant-aware circulation/search → catalogue importer and demo seed → frontend school selector → isolation tests → staging deployment.
