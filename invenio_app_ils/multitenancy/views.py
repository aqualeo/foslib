# SPDX-License-Identifier: MIT
"""Membership-authorised school API; all operational searches use PostgreSQL."""

import json
import secrets
import uuid
from functools import wraps

from flask import Blueprint, abort, current_app, g, jsonify, request, session
from flask_login import current_user
from invenio_db import db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from werkzeug.exceptions import HTTPException

from . import service
from .catalogue import isbn13


def endpoint(function):
    @wraps(function)
    def call(*args, **kwargs):
        if not current_app.config.get("FOSLIB_MULTITENANT_ENABLED"):
            abort(404)
        try:
            if not current_user.is_authenticated:
                abort(401, description="Sign in to your library account")
            # Loading current_user may start the SQLAlchemy transaction; its
            # activation hook validates the opaque capability before any data.
            if getattr(g, "foslib_context_rejected", False) or not session.get(
                "foslib_capability"
            ):
                abort(
                    403,
                    description="No active library membership. Sign in again or contact your platform administrator.",
                )
            if service.scalar("SELECT foslib.current_user_id()") != int(
                current_user.id
            ):
                abort(403)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                supplied = request.headers.get("X-Foslib-CSRF", "")
                expected = session.get("foslib_csrf", "")
                if (
                    not supplied
                    or not expected
                    or not secrets.compare_digest(supplied, expected)
                ):
                    abort(
                        400,
                        description="Session verification failed. Reload the page and try again.",
                    )
            result = function(*args, **kwargs)
            db.session.commit()  # Includes deferred composite FK checks.
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except HTTPException as error:
            db.session.rollback()
            return jsonify(error=error.description), error.code
        except (IntegrityError, DBAPIError) as error:
            db.session.rollback()
            code = getattr(error.orig, "pgcode", None)
            if current_app.testing:
                current_app.logger.error(
                    "Test SQL failure: %s; statement=%s",
                    getattr(getattr(error.orig, "diag", None), "message_primary", code),
                    error.statement,
                )
            if code in ("28000", "42501"):
                return jsonify(error="Access denied for this school"), 403
            if code in ("23503", "23505", "23514", "23502"):
                return (
                    jsonify(
                        error="The operation conflicts with existing records or contains an invalid reference"
                    ),
                    409,
                )
            current_app.logger.error(
                "Foslib database operation failed (SQLSTATE %s)", code
            )
            return jsonify(error="The operation could not be completed"), 503
        except (ValueError, KeyError, TypeError):
            db.session.rollback()
            return jsonify(error="Check the required fields and try again"), 400

    return call


def school_required():
    if not service.scalar("SELECT foslib.current_tenant_id()"):
        abort(403, description="Select an authorised school first")


def admin_required():
    if not service.scalar("SELECT foslib.is_platform_admin()"):
        abort(403, description="Platform administrator access is required")


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, description="A JSON object is required")
    # No operation accepts asserted ownership or user identity.
    if any(key in value for key in ("tenant_id", "user_id", "actor_user_id")):
        abort(
            400,
            description="Ownership is selected through your authorised school membership",
        )
    return value


def create_blueprint(app):
    bp = Blueprint("foslib", __name__, url_prefix="/foslib")

    @bp.route("/me")
    @endpoint
    def me():
        schools = [
            dict(id=str(row.id), slug=row.slug, name=row.name, role=row.role)
            for row in db.session.execute(text("SELECT * FROM foslib.my_schools()"))
        ]
        return dict(
            user={
                "id": current_user.id,
                "email": current_user.email,
                "name": current_user.displayname or current_user.email,
            },
            schools=schools,
            selected_tenant_id=session.get("foslib_tenant"),
            platform_admin=bool(service.scalar("SELECT foslib.is_platform_admin()")),
            csrf_token=session["foslib_csrf"],
        )

    @bp.route("/school", methods=["POST"])
    @endpoint
    def switch_school():
        selected = str(uuid.UUID(request.get_json()["tenant_id"]))
        schools = {
            str(row.id)
            for row in db.session.execute(text("SELECT * FROM foslib.my_schools()"))
        }
        if selected not in schools:
            abort(403, description="You do not have membership in this school")
        session["foslib_tenant"] = selected
        return dict(selected_tenant_id=selected)

    @bp.route("/dashboard")
    @endpoint
    def dashboard():
        stats = {
            "catalogue_count": service.scalar(
                "SELECT count(*) FROM records_metadata WHERE record_kind='document' AND json IS NOT NULL"
            )
        }
        if service.scalar("SELECT foslib.current_tenant_id()"):
            for label, kind, condition in [
                ("copies", "item", "true"),
                ("members", "member", "true"),
                ("active_loans", "loan", "json->>'state'='ITEM_ON_LOAN'"),
                ("pending_holds", "hold", "json->>'status'='pending'"),
                ("unpaid_fines", "fine", "json->>'status'='unpaid'"),
            ]:
                stats[label] = service.scalar(
                    "SELECT count(*) FROM records_metadata WHERE record_kind=:kind AND json IS NOT NULL AND "
                    + condition,
                    kind=kind,
                )
        return stats

    @bp.route("/catalogue")
    @endpoint
    def catalogue():
        q = request.args.get("q", "")[:200]
        page = max(1, min(10000, int(request.args.get("page", 1))))
        where = "record_kind='document' AND json IS NOT NULL AND (:q='' OR json->>'title' ILIKE :search OR CAST(json->'authors' AS text) ILIKE :search OR CAST(json->'identifiers' AS text) ILIKE :search)"
        params = {"q": q, "search": "%" + q + "%", "offset": (page - 1) * 40}
        count = db.session.execute(
            text("SELECT count(*) FROM records_metadata WHERE " + where), params
        ).scalar()
        rows = db.session.execute(
            text(
                "SELECT id,json FROM records_metadata WHERE "
                + where
                + " ORDER BY json->>'title',id LIMIT 40 OFFSET :offset"
            ),
            params,
        )
        return dict(
            items=[service.book_payload(row.id, row.json) for row in rows],
            total=count,
            page=page,
        )

    @bp.route("/catalogue/<record_id>")
    @endpoint
    def book(record_id):
        record = service.get_record("document", record_id)
        copies = [
            r
            for r in service.operational_list("item")
            if r["document_pid"] == record["pid"]
        ]
        return dict(book=service.book_payload(record.id, record), copies=copies)

    for path, kind in [
        ("inventory", "item"),
        ("members", "member"),
        ("loans", "loan"),
        ("reservations", "hold"),
        ("fines", "fine"),
        ("transactions", "event"),
    ]:

        def listing(kind=kind):
            school_required()
            return dict(items=service.operational_list(kind))

        bp.add_url_rule(
            "/" + path,
            endpoint="list_" + path,
            view_func=endpoint(listing),
            methods=["GET"],
        )

    for path, operation in [
        ("inventory", service.add_copy),
        ("members", service.add_member),
        ("loans", service.checkout),
        ("reservations", service.reserve),
        ("fines", service.add_fine),
    ]:

        def create(operation=operation):
            school_required()
            record = operation(body(), int(current_user.id))
            return dict(id=str(record.id), pid=record["pid"])

        bp.add_url_rule(
            "/" + path,
            endpoint="create_" + path,
            view_func=endpoint(create),
            methods=["POST"],
        )

    for path, action, operation in [
        ("loans", "return", service.return_loan),
        ("reservations", "cancel", service.cancel_hold),
        ("fines", "pay", service.pay_fine),
    ]:

        def transition(record_id, operation=operation):
            school_required()
            body()
            record = operation(record_id, int(current_user.id))
            return dict(id=str(record.id), pid=record["pid"])

        bp.add_url_rule(
            "/" + path + "/<record_id>/" + action,
            endpoint=path + "_" + action,
            view_func=endpoint(transition),
            methods=["POST"],
        )

    for path, kind, operation in [
        ("inventory", "item", service.update_copy),
        ("members", "member", service.update_member),
    ]:

        def edit(record_id, kind=kind, operation=operation):
            school_required()
            data = body()
            record = (
                service.archive(kind, record_id, int(current_user.id))
                if request.method == "DELETE"
                else operation(record_id, data, int(current_user.id))
            )
            return dict(id=str(record.id), pid=record["pid"])

        bp.add_url_rule(
            "/" + path + "/<record_id>",
            endpoint="edit_" + path,
            view_func=endpoint(edit),
            methods=["PUT", "DELETE"],
        )

    @bp.route("/admin/schools", methods=["GET", "POST"])
    @endpoint
    def schools():
        admin_required()
        if request.method == "GET":
            return dict(items=service.scalar("SELECT foslib.admin_schools()"))
        data = body()
        identifier = str(uuid.UUID(data["id"])) if data.get("id") else str(uuid.uuid4())
        db.session.execute(
            text(
                "SELECT foslib.admin_set_school(CAST(:id AS uuid),:slug,:name,:active)"
            ),
            dict(
                id=identifier,
                slug=data["slug"],
                name=data["name"],
                active=bool(data.get("active", True)),
            ),
        )
        return dict(id=identifier)

    @bp.route("/admin/memberships", methods=["POST"])
    @endpoint
    def membership():
        admin_required()
        data = request.get_json()
        tenant = str(uuid.UUID(data["tenant_id"]))
        if data.get("role") not in ("librarian", "reader"):
            abort(400)
        db.session.execute(
            text("SELECT foslib.admin_membership(:email,CAST(:tenant AS uuid),:role)"),
            dict(email=data["email"], tenant=tenant, role=data["role"]),
        )
        return dict(saved=True)

    @bp.route("/admin/audit")
    @endpoint
    def audit():
        admin_required()
        return dict(items=service.scalar("SELECT foslib.admin_audit_log()"))

    @bp.route("/admin/catalogue", methods=["POST"])
    @endpoint
    def save_document():
        admin_required()
        data = body()
        canonical = isbn13(data["isbn13"])
        if not canonical:
            abort(400, description="Enter a valid ISBN-13 or ISBN-10")
        if data.get("id"):
            previous = service.get_record("document", data["id"])
            identifier = previous.id
            metadata = dict(previous)
        else:
            identifier = uuid.uuid4()
            metadata = {
                "$schema": "https://"
                + current_app.config["JSONSCHEMAS_HOST"]
                + "/schemas/documents/document-v2.0.0.json",
                "pid": str(identifier),
                "document_type": "BOOK",
                "created_by": {"type": "user_id", "value": str(current_user.id)},
            }
        authors = data["authors"]
        if (
            not isinstance(authors, list)
            or not authors
            or any(not isinstance(a, str) or not a.strip() for a in authors)
        ):
            abort(400, description="Enter at least one author")
        metadata.update(
            title=data["title"].strip(),
            authors=[{"full_name": a.strip()} for a in authors],
            identifiers=[{"scheme": "ISBN", "value": canonical}],
            publication_year=str(data["publication_year"]),
            imprint={"publisher": data.get("publisher", "").strip()},
            abstract=data.get("description", ""),
            edition=data.get("edition", ""),
        )
        if not metadata["title"]:
            abort(400)
        if not metadata["imprint"]["publisher"]:
            metadata.pop("imprint")
        if not metadata["edition"]:
            metadata.pop("edition")
        from invenio_app_ils.documents.api import Document
        from invenio_app_ils.errors import IlsValidationError

        try:
            Document(metadata)._validate()
        except IlsValidationError:
            abort(400, description="Check the bibliographic metadata fields")
        db.session.execute(
            text(
                "SELECT foslib.admin_save_document(CAST(:id AS uuid),CAST(:data AS jsonb))"
            ),
            {"id": str(identifier), "data": json.dumps(metadata)},
        )
        return service.book_payload(identifier, metadata)

    return bp
