# SPDX-License-Identifier: MIT
"""School circulation commands over the existing Invenio record store.

PostgreSQL RLS determines visibility. Locks and database uniqueness constraints
protect checkout/payment races; OpenSearch is not a transaction authority.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from flask import abort
from invenio_circulation.api import Loan
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier
from invenio_records.api import Record
from sqlalchemy import text

from invenio_app_ils.circulation.api import ils_circulation_loan_pid_minter
from invenio_app_ils.documents.api import Document
from invenio_app_ils.items.api import Item, item_pid_minter
from invenio_app_ils.records.api import IlsRecord

CUSTOM = {}
for _kind, _pid_type in {
    "member": "fmem",
    "hold": "fhold",
    "fine": "ffine",
    "payment": "fpay",
    "event": "fevent",
}.items():
    CUSTOM[_kind] = type(
        _kind.title(),
        (IlsRecord,),
        {"_schema": f"foslib/{_kind}-v1.json", "_pid_type": _pid_type},
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def scalar(sql, **params):
    return db.session.execute(text(sql), params).scalar()


def require_writer():
    if not scalar("SELECT foslib.can_write()"):
        abort(403, description="A librarian role is required")


def get_record(kind, record_id=None, pid=None, lock=False):
    params = {"kind": kind}
    clause = "record_pid=:pid" if pid is not None else "id=CAST(:id AS uuid)"
    if pid is not None:
        params["pid"] = str(pid)
    else:
        try:
            params["id"] = str(uuid.UUID(str(record_id)))
        except ValueError:
            abort(404)
    row = db.session.execute(
        text(
            "SELECT id FROM records_metadata WHERE record_kind=:kind AND json IS NOT NULL AND "
            + clause
            + (" FOR UPDATE" if lock else "")
        ),
        params,
    ).first()
    if row is None:
        abort(404, description="Record not found")
    cls = {"item": Item, "loan": Loan, "document": Document}.get(
        kind, CUSTOM.get(kind, Record)
    )
    return cls.get_record(row.id)


def create_custom(kind, data):
    cls = CUSTOM[kind]
    identifier = uuid.uuid4()
    data = dict(data, pid=str(identifier))
    PersistentIdentifier.create(
        pid_type=cls._pid_type,
        pid_value=data["pid"],
        object_type="rec",
        object_uuid=identifier,
    )
    # create() already validates and stages the versioned metadata. Do not
    # issue a second UPDATE for immutable event/payment records.
    record = cls.create(data, id_=identifier)
    return record


def event(action, actor, **refs):
    details = refs.pop("details", {})
    return create_custom(
        "event",
        dict(
            action=action,
            actor_user_id=int(actor),
            created_at=now(),
            details=details,
            **refs,
        ),
    )


def add_member(data, actor):
    require_writer()
    record = create_custom(
        "member",
        {
            "name": data["name"].strip(),
            "code": data["code"].strip(),
            "year_group": data.get("year_group", "").strip(),
            "active": True,
        },
    )
    event("member.created", actor, patron_pid=record["pid"])
    return record


def add_copy(data, actor):
    require_writer()
    book = get_record("document", data["book_id"])
    shelf = db.session.execute(
        text(
            "SELECT json->>'pid' FROM records_metadata WHERE record_kind='internal_location' AND json IS NOT NULL ORDER BY created LIMIT 1"
        )
    ).scalar()
    if not shelf:
        abort(409, description="Create a school shelf before registering copies")
    identifier = uuid.uuid4()
    metadata = {
        "document_pid": book["pid"],
        "internal_location_pid": shelf,
        "barcode": data["barcode"].strip(),
        "status": "CAN_CIRCULATE",
        "medium": "PAPER",
        "circulation_restriction": "NO_RESTRICTION",
        "created_by": {"type": "user_id", "value": str(actor)},
    }
    item_pid_minter(identifier, metadata)
    record = Item.create(metadata, id_=identifier)
    record.commit()
    event("copy.registered", actor, copy_pid=record["pid"], document_pid=book["pid"])
    return record


def update_member(record_id, data, actor):
    require_writer()
    record = get_record("member", record_id, lock=True)
    for key in ("name", "code", "year_group"):
        if key in data:
            record[key] = str(data[key]).strip()
    if "active" in data:
        if not isinstance(data["active"], bool):
            abort(400)
        record["active"] = data["active"]
    record.commit()
    event("member.updated", actor, patron_pid=record["pid"])
    return record


def update_copy(record_id, data, actor):
    require_writer()
    record = get_record("item", record_id, lock=True)
    if "barcode" in data:
        record["barcode"] = str(data["barcode"]).strip()
    if "status" in data:
        if data["status"] not in Item.STATUSES:
            abort(400)
        record["status"] = data["status"]
    record.commit()
    event("copy.updated", actor, copy_pid=record["pid"])
    return record


def archive(kind, record_id, actor):
    require_writer()
    record = get_record(kind, record_id, lock=True)
    column = "copy_pid" if kind == "item" else "member_pid"
    if scalar(
        "SELECT EXISTS(SELECT FROM records_metadata WHERE "
        + column
        + "=:pid AND record_kind IN ('loan','hold','fine') AND json IS NOT NULL)",
        pid=record["pid"],
    ):
        abort(
            409,
            description="This record has circulation history. Mark it inactive instead of removing it.",
        )
    # Use Invenio's versioned metadata model, retaining the identity tombstone.
    # The legacy delete signals enqueue unscoped search jobs, so the scoped
    # command records its own event and leaves operational indexing closed.
    record.model.is_deleted = True
    event(
        kind + ".archived",
        actor,
        **{"copy_pid" if kind == "item" else "patron_pid": record["pid"]},
    )
    return record


def checkout(data, actor):
    require_writer()
    copy = get_record("item", data["copy_id"], lock=True)
    member = get_record("member", data["member_id"])
    if not member["active"]:
        abort(409, description="Member is inactive")
    if copy["status"] != "CAN_CIRCULATE":
        abort(409, description="This copy cannot circulate")
    if scalar(
        "SELECT EXISTS(SELECT FROM records_metadata WHERE record_kind='loan' AND copy_pid=:pid AND json->>'state'='ITEM_ON_LOAN')",
        pid=copy["pid"],
    ):
        abort(409, description="This copy is already on loan")
    location = scalar(
        "SELECT location_pid FROM records_metadata WHERE record_kind='internal_location' AND record_pid=:pid",
        pid=copy["internal_location_pid"],
    )
    identifier = uuid.uuid4()
    due = (
        date.fromisoformat(data["due_date"])
        if data.get("due_date")
        else date.today() + timedelta(days=14)
    )
    if not date.today() <= due <= date.today() + timedelta(days=366):
        abort(400, description="Choose a due date within the next year")
    metadata = {
        "patron_pid": member["pid"],
        "document_pid": copy["document_pid"],
        "item_pid": {"type": "pitmid", "value": copy["pid"]},
        "state": "ITEM_ON_LOAN",
        "start_date": date.today().isoformat(),
        "end_date": due.isoformat(),
        "transaction_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "transaction_location_pid": location,
        "transaction_user_pid": str(actor),
        "trigger": "checkout",
        "extension_count": 0,
    }
    ils_circulation_loan_pid_minter(identifier, metadata)
    loan = Loan.create(metadata, id_=identifier)
    loan.commit()
    for row in db.session.execute(
        text(
            "SELECT id FROM records_metadata WHERE record_kind='hold' AND member_pid=:member AND document_pid=:book AND json->>'status'='pending' FOR UPDATE"
        ),
        {"member": member["pid"], "book": copy["document_pid"]},
    ):
        hold = CUSTOM["hold"].get_record(row.id)
        hold["status"] = "fulfilled"
        hold.commit()
    event(
        "loan.checkout",
        actor,
        loan_pid=loan["pid"],
        copy_pid=copy["pid"],
        patron_pid=member["pid"],
        document_pid=copy["document_pid"],
    )
    return loan


def return_loan(record_id, actor):
    require_writer()
    loan = get_record("loan", record_id, lock=True)
    if loan["state"] != "ITEM_ON_LOAN":
        abort(409, description="This loan has already been returned")
    loan.update(
        state="ITEM_RETURNED",
        trigger="checkin",
        transaction_date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        transaction_user_pid=str(actor),
    )
    loan.commit()
    event(
        "loan.return",
        actor,
        loan_pid=loan["pid"],
        copy_pid=loan["item_pid"]["value"],
        patron_pid=loan["patron_pid"],
        document_pid=loan["document_pid"],
    )
    return loan


def reserve(data, actor):
    require_writer()
    book = get_record("document", data["book_id"])
    member = get_record("member", data["member_id"])
    if not member["active"]:
        abort(409, description="Member is inactive")
    hold = create_custom(
        "hold",
        dict(
            document_pid=book["pid"],
            patron_pid=member["pid"],
            status="pending",
            created_at=now(),
        ),
    )
    event("hold.created", actor, patron_pid=member["pid"], document_pid=book["pid"])
    return hold


def cancel_hold(record_id, actor):
    require_writer()
    hold = get_record("hold", record_id, lock=True)
    if hold["status"] != "pending":
        abort(409, description="This reservation is no longer pending")
    hold["status"] = "cancelled"
    hold.commit()
    event(
        "hold.cancelled",
        actor,
        patron_pid=hold["patron_pid"],
        document_pid=hold["document_pid"],
    )
    return hold


def add_fine(data, actor):
    require_writer()
    loan = get_record("loan", data["loan_id"])
    amount = int(data["amount_cents"])
    if not 1 <= amount <= 10000000:
        abort(400, description="Enter a positive fine amount")
    fine = create_custom(
        "fine",
        dict(
            loan_pid=loan["pid"],
            patron_pid=loan["patron_pid"],
            amount_cents=amount,
            paid_cents=0,
            currency="USD",
            reason=data["reason"].strip(),
            status="unpaid",
            created_at=now(),
        ),
    )
    event(
        "fine.created",
        actor,
        loan_pid=loan["pid"],
        patron_pid=loan["patron_pid"],
        fine_pid=fine["pid"],
        details={"amount_cents": amount},
    )
    return fine


def pay_fine(record_id, actor):
    require_writer()
    fine = get_record("fine", record_id, lock=True)
    amount = fine["amount_cents"] - fine["paid_cents"]
    if amount <= 0:
        abort(409, description="This fine is already paid")
    payment = create_custom(
        "payment",
        dict(
            fine_pid=fine["pid"],
            amount_cents=amount,
            created_at=now(),
            actor_user_id=int(actor),
        ),
    )
    fine["paid_cents"] = fine["amount_cents"]
    fine["status"] = "paid"
    fine.commit()
    event(
        "fine.paid",
        actor,
        fine_pid=fine["pid"],
        patron_pid=fine["patron_pid"],
        details={"amount_cents": amount},
    )
    return payment


def operational_list(kind):
    rows = db.session.execute(
        text(
            """SELECT r.id,r.json,d.json->>'title' AS book_title,m.json->>'name' AS member_name,
      EXISTS(SELECT FROM records_metadata l WHERE l.record_kind='loan' AND l.copy_pid=r.record_pid AND l.json->>'state'='ITEM_ON_LOAN') AS checked_out
      FROM records_metadata r LEFT JOIN records_metadata d ON d.record_kind='document' AND d.record_pid=r.document_pid
      LEFT JOIN records_metadata m ON m.record_kind='member' AND m.record_pid=r.member_pid
      WHERE r.record_kind=:kind AND r.json IS NOT NULL ORDER BY r.created DESC LIMIT 500"""
        ),
        {"kind": kind},
    )
    return [
        dict(
            id=str(row.id),
            **{k: v for k, v in row.json.items() if k != "$schema"},
            book_title=row.book_title,
            member_name=row.member_name,
            checked_out=row.checked_out,
        )
        for row in rows
    ]


def book_payload(identifier, data):
    # Never expose resolver refs, embedded holdings or circulation extensions
    # from the legacy search representation of a shared bibliographic record.
    return {
        "id": str(identifier),
        "pid": data["pid"],
        "title": data["title"],
        "authors": [a["full_name"] for a in data.get("authors", [])],
        "publication_year": data.get("publication_year"),
        "publisher": data.get("imprint", {}).get("publisher", ""),
        "isbn": [
            i["value"]
            for i in data.get("identifiers", [])
            if i.get("scheme", "").upper() == "ISBN"
        ],
        "subjects": [s["value"] for s in data.get("subjects", [])],
        "description": data.get("abstract", ""),
        "edition": data.get("edition", ""),
        "source": data.get("source", ""),
        "provenance": {
            k: v
            for k, v in data.get("extensions", {}).items()
            if k.startswith("foslib_")
        },
    }
