# SPDX-License-Identifier: MIT
"""Bind existing Invenio authentication, SQLAlchemy transactions and jobs."""

import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
from flask import current_app, g, has_app_context, has_request_context, session
from flask_login import user_logged_in, user_logged_out
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .context import new_capability

_worker_scope = ContextVar("foslib_worker_scope", default=None)


def tenant_job_required(function):
    """Legacy operational jobs must never read an unscoped search index."""

    @wraps(function)
    def guarded(*args, **kwargs):
        if has_app_context() and current_app.config.get("FOSLIB_MULTITENANT_ENABLED"):
            from .service import scalar

            if not scalar("SELECT foslib.current_tenant_id()"):
                raise PermissionError(
                    "This operational job requires an authorised school envelope"
                )
        return function(*args, **kwargs)

    return guarded


def database_uri(value):
    return value.replace("postgresql+psycopg2://", "postgresql://")


def _bind_context(orm_session, transaction, connection):
    if (
        transaction.nested
        or not has_app_context()
        or not current_app.config.get("FOSLIB_MULTITENANT_ENABLED")
    ):
        return
    scope = _worker_scope.get()
    if has_request_context():
        token = session.get("foslib_capability")
        scope = (token, session.get("foslib_tenant")) if token else None
    if not scope:
        return
    # A revoked browser capability must not prevent the user from reaching
    # login/logout. Roll back only activation, leaving no tenant context.
    try:
        if (
            connection.execute(text("SELECT foslib.current_user_id()")).scalar()
            is not None
        ):
            connection.execute(
                text("SELECT foslib.activate_context(:token,CAST(:tenant AS uuid))"),
                {"token": scope[0], "tenant": scope[1]},
            )
            return
        with connection.begin_nested():
            connection.execute(
                text("SELECT foslib.activate_context(:token,CAST(:tenant AS uuid))"),
                {"token": scope[0], "tenant": scope[1]},
            )
    except DBAPIError as error:
        if getattr(error.orig, "pgcode", None) != "28000":
            raise
        if not has_request_context():
            raise PermissionError(
                "Invalid or unauthorised worker tenant context"
            ) from None
        session.pop("foslib_capability", None)
        session.pop("foslib_tenant", None)
        g.foslib_context_rejected = True


def _login(sender, user, **kwargs):
    if not sender.config.get("FOSLIB_MULTITENANT_ENABLED"):
        return
    token, digest = new_capability()
    expires = datetime.now(timezone.utc) + timedelta(hours=11, minutes=59)
    uri = sender.config.get("FOSLIB_AUTH_BROKER_URI")
    if not uri:
        raise RuntimeError("Foslib authentication broker is not configured")
    try:
        with psycopg2.connect(database_uri(uri), connect_timeout=10) as broker:
            with broker.cursor() as cursor:
                cursor.execute(
                    "SELECT foslib.issue_session(%s,%s,%s)",
                    (int(user.id), psycopg2.Binary(digest), expires),
                )
    except psycopg2.errors.InvalidAuthorizationSpecification:
        # Existing accounts retain their identity but receive no school access
        # until an administrator grants an explicit membership.
        session.pop("foslib_capability", None)
        return
    session["foslib_capability"] = token
    session["foslib_csrf"] = secrets.token_hex(32)
    session.pop("foslib_tenant", None)
    # Initial school selection comes only from the database's membership list.
    with psycopg2.connect(
        database_uri(sender.config["SQLALCHEMY_DATABASE_URI"]), connect_timeout=10
    ) as runtime:
        with runtime.cursor() as cursor:
            cursor.execute("SELECT foslib.activate_context(%s,NULL)", (token,))
            cursor.execute("SELECT id FROM foslib.my_schools()")
            school = cursor.fetchone()
            if school:
                session["foslib_tenant"] = str(school[0])


def _logout(sender, user, **kwargs):
    token = session.pop("foslib_capability", None)
    session.pop("foslib_tenant", None)
    session.pop("foslib_csrf", None)
    if token and sender.config.get("FOSLIB_MULTITENANT_ENABLED"):
        import hashlib

        with psycopg2.connect(
            database_uri(sender.config["FOSLIB_AUTH_BROKER_URI"]), connect_timeout=10
        ) as broker:
            with broker.cursor() as cursor:
                cursor.execute(
                    "SELECT foslib.revoke_session(%s)",
                    (psycopg2.Binary(hashlib.sha256(token.encode()).digest()),),
                )


def install_runtime(app):
    if not app.config.get("FOSLIB_MULTITENANT_ENABLED") or app.extensions.get(
        "foslib-runtime"
    ):
        return
    app.extensions["foslib-runtime"] = True
    # HTTP intermediaries may discard headers containing underscores.
    app.config["CSRF_HEADER"] = "X-CSRFToken"
    app.config["SECURITY_LOGIN_USER_TEMPLATE"] = "invenio_app_ils/foslib_login.html"
    app.config["THEME_SITENAME"] = "FOS Library Management System"
    if not event.contains(Session, "after_begin", _bind_context):
        event.listen(Session, "after_begin", _bind_context)
    user_logged_in.connect(_login, sender=app, weak=False)
    user_logged_out.connect(_logout, sender=app, weak=False)
    from invenio_indexer.signals import before_record_index

    before_record_index.connect(_guard_search, sender=app, weak=False)


def _guard_search(sender, json=None, record=None, **kwargs):
    if not sender.config.get("FOSLIB_MULTITENANT_ENABLED") or json is None:
        return
    schema = json.get("$schema", "")
    if not any(
        "/" + kind + "/" in schema
        for kind in ("documents", "series", "vocabularies", "licenses")
    ):
        raise PermissionError(
            "Operational records are searched through the school-scoped database API"
        )
    for field in ("circulation", "items", "stock", "eitems", "internal_locations"):
        json.pop(field, None)


@contextmanager
def worker_context(capability, tenant_id):
    """Require a capability in every scheduled/job envelope, never just a UUID.

    Celery tasks must start with an empty scoped session and remove it on exit.
    The same SQLAlchemy hook validates the worker's active membership on each
    transaction. No web session or untrusted job tenant header is consulted.
    """
    from invenio_db import db

    db.session.remove()
    marker = _worker_scope.set((capability, str(tenant_id)))
    try:
        yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.session.remove()
        _worker_scope.reset(marker)
