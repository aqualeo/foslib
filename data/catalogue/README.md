# Open Library edition snapshot

`openlibrary-1000.jsonl` contains 1,000 distinct ISBN-based editions downloaded
from Open Library on 2026-11-12. Bibliographic metadata is supplied under CC0:
https://openlibrary.org/developers/licensing. The repository's MIT licence
applies to the importer code; the snapshot retains its source's CC0 provenance.
Cover artwork and excerpts are not included.

Each line records a validated ISBN-13 and its edition aliases, title, authors,
publisher(s), publication date/year when supplied, subjects, Open Library
edition ID/URL, retrieval timestamp and metadata licence. No work-level title,
author, publisher or date has been substituted for edition metadata.

The source omits a publication year for 16 editions and publishers for 11.
Language, edition statement, physical format and description are represented as
unknown because the Books API response did not establish them. The importer
does not invent these facts. All 1,000 editions have named authors. ISBN-10
aliases are converted to ISBN-13 only after validating their checksum.

`manifest.json` records the SHA-256 digest and data-quality counts. This is a
stable snapshot, so application tests and demo seeding do not depend on the live
public API. Open Library is community maintained; validation establishes ISBN
checksum/edition consistency, not independent publisher verification of every
metadata field.

To regenerate, from the repository root:

```sh
python -m invenio_app_ils.multitenancy.catalogue data/catalogue/openlibrary-1000.jsonl
```

The downloader paces requests, retries bounded transient errors and only
replaces the destination once the complete snapshot passes validation.
Regeneration can select different editions as the source/search ranking changes;
review and regenerate the manifest before committing a replacement.

In a configured ILS environment, validate and preview the import:

```sh
ils foslib-catalogue validate data/catalogue/openlibrary-1000.jsonl
ils foslib-catalogue import data/catalogue/openlibrary-1000.jsonl
```

Only an authorised platform/migration execution context should commit it:

```sh
ils foslib-catalogue import data/catalogue/openlibrary-1000.jsonl --apply
ils foslib-catalogue index
```

Import uses the existing `Document`/PID APIs, validates their schema and commits
the new editions atomically. A PostgreSQL advisory transaction lock serialises
importer runs. ISBN aliases and provenance identify existing records. Reruns
skip matches and preserve librarian edits. Conflicting existing identities abort
before insertion. Existing records are never deleted or overwritten. This lock
does not impose a database-wide ISBN uniqueness constraint on unrelated writers;
the shared catalogue ownership/index migration still needs to enforce that
invariant for all writers.

Indexing is a separate retryable operation through the existing Invenio indexer
queue. Verify worker health, queue drain and actual search results after import;
a committed database import alone does not prove a searchable catalogue.

The test suite imports the entire snapshot into a temporary database through
real Invenio models, reruns the import and verifies edits survive. The snapshot
has **not yet been imported into Railway staging**.
