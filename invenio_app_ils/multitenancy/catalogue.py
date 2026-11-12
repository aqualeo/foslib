# SPDX-License-Identifier: MIT
"""Edition-specific Open Library metadata ingestion, without work-level merging.

Metadata: https://openlibrary.org/developers/licensing (CC0).
Images are deliberately excluded because metadata licensing does not confer
rights to the cover artwork. Each retained row identifies its source edition.
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def isbn13(value):
    """Validate ISBN-10/13 and return its canonical ISBN-13, or None."""
    value = re.sub(r"[\s-]", "", str(value)).upper()
    if re.fullmatch(r"[0-9]{9}[0-9X]", value):
        digits = [int(x) if x != "X" else 10 for x in value]
        if sum((10 - i) * x for i, x in enumerate(digits)) % 11:
            return None
        value = "978" + value[:9]
        value += str(
            (-sum(int(x) * (1 if i % 2 == 0 else 3) for i, x in enumerate(value))) % 10
        )
    if not re.fullmatch(r"97[89][0-9]{10}", value):
        return None
    return (
        value
        if sum(int(x) * (1 if i % 2 == 0 else 3) for i, x in enumerate(value)) % 10 == 0
        else None
    )


def normalise_edition(requested_isbn, book, retrieved_at):
    """Require the exact requested ISBN on the returned edition.

    Search hits only supply candidate ISBNs. Title/author/publisher/year always
    come from the edition API response, never aggregated work search metadata.
    """
    wanted = isbn13(requested_isbn)
    identifiers = book.get("identifiers", {})
    edition_isbns = {
        isbn13(x)
        for x in identifiers.get("isbn_13", []) + identifiers.get("isbn_10", [])
    } - {None}
    source_ids = identifiers.get("openlibrary", [])
    authors = [
        a["name"].strip() for a in book.get("authors", []) if a.get("name", "").strip()
    ]
    title = book.get("title", "").strip()
    if (
        not wanted
        or wanted not in edition_isbns
        or not title
        or not authors
        or not source_ids
    ):
        return None
    edition = source_ids[0]
    if not re.fullmatch(r"OL[0-9]+M", edition):
        return None
    year = re.search(r"\b([12][0-9]{3})\b", book.get("publish_date", ""))
    return {
        "isbn13": wanted,
        "isbn_aliases": sorted(edition_isbns),
        "title": title,
        "authors": list(dict.fromkeys(authors)),
        "publishers": [p["name"] for p in book.get("publishers", []) if p.get("name")],
        "publication_year": int(year[1]) if year else None,
        "publication_date": book.get("publish_date"),
        "subjects": list(
            dict.fromkeys(s["name"] for s in book.get("subjects", []) if s.get("name"))
        ),
        "languages": [],
        "edition": None,
        "format": None,
        "description": None,
        "cover_url": None,
        "source": "Open Library",
        "source_id": "/books/" + edition,
        "source_url": "https://openlibrary.org/books/" + edition,
        "metadata_license": "CC0-1.0",
        "retrieved_at": retrieved_at,
    }


def read_catalogue(path, minimum=1000):
    """Validate an entire snapshot before any database changes."""
    rows = [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]
    seen_isbns, seen_editions = set(), set()
    for row in rows:
        canonical = isbn13(row.get("isbn13"))
        if canonical != row.get("isbn13") or not canonical:
            raise ValueError("Invalid canonical ISBN")
        aliases = row.get("isbn_aliases", [])
        if canonical not in aliases or any(isbn13(x) != x for x in aliases):
            raise ValueError("Invalid edition ISBN aliases")
        if seen_isbns.intersection(aliases) or row["source_id"] in seen_editions:
            raise ValueError("Duplicate ISBN or edition")
        if (
            not row.get("title")
            or not row.get("authors")
            or row.get("metadata_license") != "CC0-1.0"
        ):
            raise ValueError("Missing bibliographic metadata or licence")
        seen_isbns.update(aliases)
        seen_editions.add(row["source_id"])
    if len(rows) < minimum:
        raise ValueError(f"Expected at least {minimum} editions; found {len(rows)}")
    return rows


class OpenLibraryClient:
    """Bounded requests with a descriptive User-Agent and conservative pacing."""

    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.last_request = 0

    def get(self, path, params):
        url = "https://openlibrary.org" + path + "?" + urlencode(params)
        assert urlparse(url).hostname == "openlibrary.org"
        for attempt in range(4):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with urlopen(
                    Request(url, headers={"User-Agent": self.user_agent}), timeout=45
                ) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                time.sleep(
                    min(30, max(2**attempt, int(error.headers.get("Retry-After", "1"))))
                )
            except URLError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)


def download_catalogue(
    destination,
    count=1000,
    user_agent="Foslib catalogue research (github.com/aqualeo/foslib)",
):
    """Download genuine editions; atomically publish only a complete snapshot."""
    client = OpenLibraryClient(user_agent)
    retrieved = datetime.now(timezone.utc).isoformat()
    rows, seen_isbns, seen_editions = [], set(), set()
    queries = [
        "subject:juvenile_fiction",
        "subject:science",
        "subject:history",
        "subject:fiction",
    ]
    for page in range(1, 16):
        for query in queries:
            search = client.get(
                "/search.json",
                {"q": query, "fields": "isbn", "limit": 100, "page": page},
            )
            candidates = []
            for work in search.get("docs", []):
                valid = sorted(
                    {isbn13(x) for x in work.get("isbn", [])} - {None} - seen_isbns
                )
                if valid:
                    candidates.append(valid[0])
            candidates = list(dict.fromkeys(candidates))
            if not candidates:
                continue
            books = client.get(
                "/api/books",
                {
                    "bibkeys": ",".join("ISBN:" + x for x in candidates),
                    "format": "json",
                    "jscmd": "data",
                },
            )
            for candidate in candidates:
                row = normalise_edition(
                    candidate, books.get("ISBN:" + candidate, {}), retrieved
                )
                if (
                    row
                    and not seen_isbns.intersection(row["isbn_aliases"])
                    and row["source_id"] not in seen_editions
                ):
                    rows.append(row)
                    seen_isbns.update(row["isbn_aliases"])
                    seen_editions.add(row["source_id"])
                    if len(rows) == count:
                        break
            print(f"Validated {len(rows)}/{count} distinct ISBN editions", flush=True)
            if len(rows) >= count:
                target = Path(destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".tmp")
                temporary.write_text(
                    "".join(
                        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                        for row in rows
                    )
                )
                read_catalogue(temporary, minimum=count)
                os.replace(temporary, target)
                return
    raise RuntimeError(
        f"Only {len(rows)} verified editions found; no snapshot published"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, default=1000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    download_catalogue(args.destination, args.count)


if __name__ == "__main__":
    main()
