# SPDX-License-Identifier: MIT
"""Non-destructive catalogue import using existing Invenio Document records."""

import click
from flask.cli import with_appcontext

from .catalogue import isbn13, read_catalogue


def document_metadata(row):
    """Map edition metadata to the existing ILS document JSON schema."""
    data = {
        "title": row["title"],
        "authors": [{"full_name": name} for name in row["authors"]],
        "document_type": "BOOK",
        "publication_year": (
            str(row["publication_year"])
            if row["publication_year"]
            else "[date not supplied]"
        ),
        "identifiers": [
            {"scheme": "ISBN", "value": value} for value in row["isbn_aliases"]
        ],
        "source": "Open Library: " + row["source_id"],
        "created_by": {"type": "import", "value": "foslib-openlibrary"},
        "extensions": {
            "foslib_" + key: row[key]
            for key in ("source_id", "source_url", "metadata_license", "retrieved_at")
        },
    }
    if row["publishers"]:
        data["imprint"] = {"publisher": "; ".join(row["publishers"])}
    if row["publication_date"]:
        data.setdefault("imprint", {})["date"] = row["publication_date"]
    if row["subjects"]:
        data["subjects"] = [
            {"scheme": "Open Library", "value": s}
            for s in dict.fromkeys(row["subjects"])
        ]
    if row.get("edition"):
        data["edition"] = row["edition"]
    if row.get("description"):
        data["abstract"] = row["description"]
    return data


def plan_import(rows, existing):
    """Compute inserts without overwriting existing catalogue edits.

    Index all ISBN aliases as well as edition provenance. Ambiguous existing
    edition identities fail before any new record is created.
    """
    by_isbn, by_source = {}, {}
    for record_id, data in existing:
        for identifier in data.get("identifiers", []):
            if identifier.get("scheme", "").upper() == "ISBN":
                canonical = isbn13(identifier.get("value"))
                if canonical:
                    by_isbn.setdefault(canonical, set()).add(str(record_id))
        if data.get("source"):
            by_source.setdefault(data["source"], set()).add(str(record_id))
    inserts, skipped = [], []
    for row in rows:
        matches = set(by_source.get("Open Library: " + row["source_id"], set()))
        for alias in row["isbn_aliases"]:
            matches.update(by_isbn.get(alias, set()))
        if len(matches) > 1:
            raise ValueError(
                "Conflicting existing records for edition " + row["source_id"]
            )
        if matches:
            skipped.append(next(iter(matches)))
        else:
            inserts.append(row)
    return inserts, skipped


@click.group()
def catalogue():
    """Validate/import the shared catalogue; never reset existing data."""


@catalogue.command("validate")
@click.argument("snapshot", type=click.Path(exists=True, dir_okay=False))
def validate(snapshot):
    """Validate checksums, edition identity, duplicates and provenance."""
    try:
        rows = read_catalogue(snapshot)
    except (ValueError, KeyError, TypeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Validated {len(rows)} distinct ISBN editions")


@catalogue.command("import")
@click.argument("snapshot", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--apply", is_flag=True, help="Commit new records; default is a read-only plan."
)
@with_appcontext
def import_catalogue(snapshot, apply):
    """Import with an owner/migration credential, never a school runtime role.

    Search indexing is deliberately a separate, retryable operation after the
    database commit. No metadata is deleted or overwritten on reruns.
    """
    from invenio_db import db
    from invenio_records.models import RecordMetadata
    from sqlalchemy import text

    from invenio_app_ils.documents.api import Document, document_pid_minter

    try:
        rows = read_catalogue(snapshot)
        # Serialize importer invocations, including preflight identity checks.
        db.session.execute(text("SELECT pg_advisory_xact_lock(413506, 1000)"))
        existing = (
            db.session.query(RecordMetadata.id, RecordMetadata.json)
            .filter(RecordMetadata.json["$schema"].as_string().like("%/documents/%"))
            .yield_per(500)
        )
        inserts, skipped = plan_import(rows, existing)
        click.echo(f"Plan: {len(inserts)} new editions; {len(skipped)} already present")
        if not apply:
            db.session.rollback()
            return
        for row in inserts:
            data = document_metadata(row)
            # PID minting requires the record UUID but Document validation
            # requires pid, so generate UUID and mint before Document.create.
            import uuid

            record_id = uuid.uuid4()
            document_pid_minter(record_id, data)
            record = Document.create(data, id_=record_id)
            record.commit()
        db.session.commit()
        click.echo(
            f"Committed {len(inserts)} new Document records; existing records preserved"
        )
    except Exception:
        db.session.rollback()
        raise


@catalogue.command("index")
@with_appcontext
def index_catalogue():
    """Queue all imported edition UUIDs using Invenio's existing indexer."""
    from invenio_indexer.api import RecordIndexer
    from invenio_records.models import RecordMetadata

    rows = RecordMetadata.query.filter(
        RecordMetadata.json["source"].as_string().like("Open Library: /books/OL%")
    ).with_entities(RecordMetadata.id)
    identifiers = [str(row.id) for row in rows]
    RecordIndexer().bulk_index(identifiers)
    click.echo(
        f"Queued {len(identifiers)} records; verify worker processing and search counts"
    )
