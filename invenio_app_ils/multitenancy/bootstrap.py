# SPDX-License-Identifier: MIT
"""Initial, additive staging bootstrap from an authorised migration connection.

Secrets come only from FOSLIB_BOOTSTRAP_SECRETS in the deployment environment.
Run behind maintenance mode. This command never drops tables, indices or data.
"""

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from flask_security.utils import hash_password
from invenio_accounts.models import User
from invenio_db import db
from psycopg2 import sql
from sqlalchemy import text

from . import service
from .catalogue_cli import catalogue

SCHOOLS = {
    "alpha": (
        "11111111-1111-4111-8111-111111111111",
        "Alpha School",
        "Admin@alpha.com",
        100,
        25,
    ),
    "beta": (
        "22222222-2222-4222-8222-222222222222",
        "Beta School",
        "Admin@beta.com",
        75,
        20,
    ),
}


def _user(email, password, name, active=True):
    email = email.strip().lower()
    user = User.query.filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, active=active, user_profile={"full_name": name})
        db.session.add(user)
    user.password = hash_password(password)
    user.active = active
    user.confirmed_at = datetime.now(timezone.utc)
    user.user_profile = dict(user.user_profile or {}, full_name=name)
    db.session.commit()
    return user.id


def _migration(name):
    if db.session.execute(
        text(
            "SELECT EXISTS(SELECT FROM foslib_private.migrations WHERE version=:name)"
        ),
        {"name": name},
    ).scalar():
        return
    db.session.execute(text((Path(__file__).parent / "sql" / name).read_text()))
    db.session.execute(
        text("INSERT INTO foslib_private.migrations(version) VALUES(:name)"),
        {"name": name},
    )
    db.session.commit()
    print("Applied", name, flush=True)


def _roles(config):
    connection = db.engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            for role, group, key in [
                ("foslib_app_login", "foslib_runtime", "runtime_password"),
                ("foslib_broker_login", "foslib_auth_broker", "broker_password"),
                ("foslib_migration_login", "foslib_owner", "migration_password"),
            ]:
                cursor.execute(
                    "SELECT rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname=%s",
                    (role,),
                )
                existing = cursor.fetchone()
                if existing and any(existing):
                    raise RuntimeError("Unsafe existing application role")
                if existing:
                    cursor.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD %s").format(
                            sql.Identifier(role)
                        ),
                        (config[key],),
                    )
                else:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD %s IN ROLE {}"
                        ).format(sql.Identifier(role), sql.Identifier(group)),
                        (config[key],),
                    )
            cursor.execute(
                "SELECT pg_has_role('foslib_app_login','foslib_owner','MEMBER') OR pg_has_role('foslib_app_login','foslib_auth_broker','MEMBER')"
            )
            if cursor.fetchone()[0]:
                raise RuntimeError("Runtime role has unsafe membership")
        connection.commit()
    finally:
        connection.close()


def _seed_school(slug, uid):
    tenant, name, email, copies_target, members_target = SCHOOLS[slug]
    if service.scalar(
        "SELECT EXISTS(SELECT FROM foslib_private.demo_seeds WHERE tenant_id=CAST(:tenant AS uuid))",
        tenant=tenant,
    ):
        db.session.rollback()
        print("Preserved existing demo seed for", name, flush=True)
        return
    token = secrets.token_hex(32)
    db.session.execute(
        text("SELECT foslib.issue_session(:uid,:digest,:expires)"),
        {
            "uid": uid,
            "digest": hashlib.sha256(token.encode()).digest(),
            "expires": datetime.now(timezone.utc) + timedelta(hours=11, minutes=59),
        },
    )
    db.session.commit()
    try:
        db.session.execute(text("SET LOCAL ROLE foslib_runtime"))
        db.session.execute(
            text("SELECT foslib.activate_context(:token,CAST(:tenant AS uuid))"),
            {"token": token, "tenant": tenant},
        )
        from invenio_app_ils.internal_locations.api import (
            InternalLocation,
            internal_location_pid_minter,
        )
        from invenio_app_ils.locations.api import Location, location_pid_minter

        location = service.scalar(
            "SELECT record_pid FROM records_metadata WHERE record_kind='location' AND json IS NOT NULL ORDER BY created LIMIT 1"
        )
        if not location:
            identifier = uuid.uuid4()
            data = {
                "name": name + " Library",
                "opening_weekdays": [
                    {"weekday": weekday, "is_open": index < 5}
                    for index, weekday in enumerate(
                        [
                            "Monday",
                            "Tuesday",
                            "Wednesday",
                            "Thursday",
                            "Friday",
                            "Saturday",
                            "Sunday",
                        ]
                    )
                ],
            }
            location_pid_minter(identifier, data)
            record = Location.create(data, id_=identifier)
            location = record["pid"]
        if not service.scalar(
            "SELECT count(*) FROM records_metadata WHERE record_kind='internal_location' AND json IS NOT NULL"
        ):
            identifier = uuid.uuid4()
            data = {
                "name": "Main collection",
                "location_pid": location,
                "restricted": False,
            }
            internal_location_pid_minter(identifier, data)
            InternalLocation.create(data, id_=identifier)
        books = list(
            db.session.execute(
                text(
                    "SELECT id FROM records_metadata WHERE record_kind='document' AND json->>'source' LIKE 'Open Library: /books/OL%' ORDER BY created,id LIMIT 150"
                )
            )
        )
        if len(books) < 100:
            raise RuntimeError("Shared catalogue is not ready")
        members = [
            service.add_member(
                {
                    "name": f"{name.split()[0]} Student {i+1:03d}",
                    "code": f"{slug.upper()}-STUDENT-{i+1:03d}",
                    "year_group": f"Year {i%6+1}",
                },
                uid,
            )
            for i in range(members_target)
        ]
        copies = [
            service.add_copy(
                {
                    "book_id": str(books[i % len(books)].id),
                    "barcode": f"{slug.upper()}-{i+1:04d}",
                },
                uid,
            )
            for i in range(copies_target)
        ]
        loans = []
        active_count, returned_count, offset = (
            (8, 4, 0) if slug == "alpha" else (6, 3, 10)
        )
        for i in range(active_count + returned_count):
            loan = service.checkout(
                {
                    "copy_id": str(copies[offset + i].id),
                    "member_id": str(members[i].id),
                },
                uid,
            )
            loans.append(loan)
            if i >= active_count:
                service.return_loan(str(loan.id), uid)
        for i in range(4 if slug == "alpha" else 3):
            service.reserve(
                {"book_id": str(books[30 + i].id), "member_id": str(members[i].id)}, uid
            )
        for i in range(2):
            fine = service.add_fine(
                {
                    "loan_id": str(loans[-1 - i].id),
                    "amount_cents": 150 + 100 * i,
                    "reason": "Fictional demonstration handling charge",
                },
                uid,
            )
            if i == 1:
                service.pay_fine(str(fine.id), uid)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    db.session.execute(
        text(
            "INSERT INTO foslib_private.demo_seeds(tenant_id) VALUES(CAST(:tenant AS uuid))"
        ),
        {"tenant": tenant},
    )
    db.session.commit()
    print(
        "Seeded",
        name,
        "with",
        copies_target,
        "copies and",
        members_target,
        "fictional members",
        flush=True,
    )


def bootstrap():
    if current_app.config.get("FOSLIB_MULTITENANT_ENABLED"):
        raise RuntimeError(
            "Initial bootstrap requires maintenance mode and the migration connection"
        )
    if not current_app.config.get("FOSLIB_MAINTENANCE"):
        raise RuntimeError("Enable maintenance mode before initial staging bootstrap")
    if not service.scalar("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
        raise RuntimeError(
            "Initial role provisioning requires the authorised database owner"
        )
    config = json.loads(os.environ["FOSLIB_BOOTSTRAP_SECRETS"])
    users = {
        slug: _user(values[2], config["school_password"], values[1] + " Library Admin")
        for slug, values in SCHOOLS.items()
    }
    platform = _user(
        "Admin@foslib.example",
        config["platform_password"],
        "FOS Platform Administrator",
    )
    if not service.scalar("SELECT to_regnamespace('foslib_private') IS NOT NULL"):
        db.session.execute(
            text((Path(__file__).parent / "sql/001_security.sql").read_text())
        )
        db.session.execute(
            text(
                "CREATE TABLE foslib_private.migrations(version text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        db.session.execute(
            text("ALTER TABLE foslib_private.migrations OWNER TO foslib_owner")
        )
        db.session.execute(
            text(
                "ALTER TABLE foslib_private.migrations ENABLE ROW LEVEL SECURITY; ALTER TABLE foslib_private.migrations FORCE ROW LEVEL SECURITY; CREATE POLICY owner_access ON foslib_private.migrations TO foslib_owner USING(true) WITH CHECK(true)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO foslib_private.migrations(version) VALUES('001_security.sql')"
            )
        )
        db.session.commit()
    for slug, (tenant, name, _, _, _) in SCHOOLS.items():
        db.session.execute(
            text(
                "INSERT INTO foslib_private.tenants(id,slug,name) VALUES(CAST(:id AS uuid),:slug,:name) ON CONFLICT(id) DO NOTHING"
            ),
            {"id": tenant, "slug": slug, "name": name},
        )
        db.session.execute(
            text(
                "INSERT INTO foslib_private.principals(user_id,platform_admin) VALUES(:id,false) ON CONFLICT(user_id) DO UPDATE SET active=true,platform_admin=false"
            ),
            {"id": users[slug]},
        )
        db.session.execute(
            text(
                "INSERT INTO foslib_private.memberships(tenant_id,user_id,role) VALUES(CAST(:tenant AS uuid),:uid,'librarian') ON CONFLICT(tenant_id,user_id) DO UPDATE SET role='librarian',active=true"
            ),
            {"tenant": tenant, "uid": users[slug]},
        )
    db.session.execute(
        text(
            "INSERT INTO foslib_private.principals(user_id,platform_admin) VALUES(:id,true) ON CONFLICT(user_id) DO UPDATE SET active=true,platform_admin=true"
        ),
        {"id": platform},
    )
    db.session.commit()
    _migration("002_records.sql")
    _migration("003_catalogue_admin.sql")
    _roles(config)
    snapshot = Path("data/catalogue/openlibrary-1000.jsonl")
    result = current_app.test_cli_runner().invoke(
        catalogue, ["import", str(snapshot), "--apply"]
    )
    if result.exception:
        raise result.exception
    print(result.output.strip(), flush=True)
    if (
        service.scalar(
            "SELECT count(*) FROM records_metadata WHERE record_kind='document' AND json->>'source' LIKE 'Open Library: /books/OL%'"
        )
        < 1000
    ):
        raise RuntimeError(
            "Catalogue must contain at least 1,000 genuine imported editions"
        )
    db.session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS foslib_private.demo_seeds(tenant_id uuid PRIMARY KEY REFERENCES foslib_private.tenants(id),seeded_at timestamptz NOT NULL DEFAULT now())"
        )
    )
    db.session.execute(
        text("ALTER TABLE foslib_private.demo_seeds OWNER TO foslib_owner")
    )
    db.session.commit()
    if not service.scalar(
        "SELECT relrowsecurity FROM pg_class WHERE oid='foslib_private.demo_seeds'::regclass"
    ):
        db.session.execute(
            text(
                "ALTER TABLE foslib_private.demo_seeds ENABLE ROW LEVEL SECURITY; ALTER TABLE foslib_private.demo_seeds FORCE ROW LEVEL SECURITY; CREATE POLICY owner_access ON foslib_private.demo_seeds TO foslib_owner USING(true) WITH CHECK(true)"
            )
        )
        db.session.commit()
    for slug, uid in users.items():
        _seed_school(slug, uid)
    print(
        "Additive staging bootstrap complete; activate restricted connections before opening the site",
        flush=True,
    )


if __name__ == "__main__":
    from invenio_app.wsgi import application

    with application.app_context():
        try:
            bootstrap()
        except Exception as error:
            db.session.rollback()
            original = getattr(error, "orig", error)
            diagnostic = getattr(
                getattr(original, "diag", None), "message_primary", None
            )
            print(
                "Bootstrap failed:",
                type(error).__name__,
                getattr(original, "pgcode", ""),
                diagnostic or "See the last completed stage above",
                flush=True,
            )
            raise SystemExit(1) from None
