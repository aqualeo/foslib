# SPDX-License-Identifier: MIT
"""Validate edition identity, checksums and fail-before-write input handling."""

import json

import pytest

from invenio_app_ils.multitenancy.catalogue import (
    isbn13,
    normalise_edition,
    read_catalogue,
)
from invenio_app_ils.multitenancy.catalogue_cli import document_metadata, plan_import


@pytest.mark.parametrize(
    "value,expected",
    [
        ("9780140328721", "9780140328721"),
        ("0-14-032872-6", "9780140328721"),
        ("0-8044-2957-X", "9780804429573"),
        ("9780140328722", None),
        ("0140328725", None),
        ("9780000000000", None),
        ("1234567890128", None),
        (None, None),
        ("DROP TABLE books", None),
    ],
)
def test_isbn_checksum(value, expected):
    assert isbn13(value) == expected


def edition():
    return {
        "title": "Matilda",
        "authors": [{"name": "Roald Dahl"}],
        "publishers": [{"name": "Puffin"}],
        "publish_date": "1988",
        "identifiers": {"isbn_10": ["0140328726"], "openlibrary": ["OL123M"]},
    }


def test_normalisation_preserves_edition():
    row = normalise_edition("9780140328721", edition(), "2026-11-12T00:00:00Z")
    assert row["publication_year"] == 1988
    assert row["authors"] == ["Roald Dahl"]
    assert row["source_id"] == "/books/OL123M"
    assert row["cover_url"] is None
    assert row["metadata_license"] == "CC0-1.0"


def test_wrong_edition_is_not_merged():
    assert normalise_edition("9780804429573", edition(), "now") is None


def test_snapshot_duplicate_rejected(tmp_path):
    row = normalise_edition("9780140328721", edition(), "now")
    path = tmp_path / "books.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        read_catalogue(path, minimum=2)


def test_incomplete_snapshot_rejected(tmp_path):
    path = tmp_path / "books.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="at least 1000"):
        read_catalogue(path)


def test_import_plan_is_idempotent_and_preserves_edits():
    row = normalise_edition("9780140328721", edition(), "now")
    data = document_metadata(row)
    assert plan_import([row], []) == ([row], [])
    data["title"] = "Librarian's corrected title"
    assert plan_import([row], [("existing-id", data)]) == ([], ["existing-id"])
    assert data["title"] == "Librarian's corrected title"


def test_legacy_isbn10_is_recognised():
    row = normalise_edition("9780140328721", edition(), "now")
    existing = {"identifiers": [{"scheme": "ISBN", "value": "0140328726"}]}
    assert plan_import([row], [("legacy", existing)]) == ([], ["legacy"])


def test_ambiguous_existing_metadata_fails_before_import():
    row = normalise_edition("9780140328721", edition(), "now")
    data = document_metadata(row)
    with pytest.raises(ValueError, match="Conflicting"):
        plan_import([row], [("first", data), ("second", data)])


def test_committed_snapshot_has_1000_distinct_editions():
    from pathlib import Path

    path = Path(__file__).parents[2] / "data/catalogue/openlibrary-1000.jsonl"
    rows = read_catalogue(path)
    assert len(rows) == 1000
    assert all(
        row["source_url"].startswith("https://openlibrary.org/books/OL") for row in rows
    )
