# SPDX-License-Identifier: MIT
"""Apply tenant isolation to the real Invenio tables, including history/PIDs."""

import hashlib
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import ALPHA, BETA
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from sqlalchemy import URL

from invenio_app_ils.multitenancy.context import tenant_transaction


@pytest.fixture(scope="module")
def scoped_ils(postgres_dsn):
    from flask import Flask
    from invenio_accounts.models import User
    from invenio_db import InvenioDB, db
    from invenio_jsonschemas import InvenioJSONSchemas
    from invenio_pidstore import InvenioPIDStore
    from invenio_records import InvenioRecords

    with psycopg.connect(postgres_dsn, autocommit=True) as c:
        c.execute("CREATE DATABASE foslib_records_test")
    dsn = make_conninfo(postgres_dsn, dbname="foslib_records_test")
    options = conninfo_to_dict(dsn)
    uri = URL.create(
        "postgresql+psycopg2",
        username=options.get("user"),
        password=options.get("password"),
        database=options["dbname"],
        query={
            "host": options.get("host", "localhost"),
            "port": options.get("port", "5432"),
        },
    )
    app = Flask("foslib-scoped-records-test")
    app.config.from_object("invenio_opendefinition.config")
    app.config.from_object("invenio_app_ils.config")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=uri.render_as_string(hide_password=False),
        SERVER_NAME="localhost",
        JSONSCHEMAS_HOST="localhost",
    )
    InvenioDB(app)
    InvenioRecords(app)
    InvenioJSONSchemas(app)
    InvenioPIDStore(app)
    with app.app_context():
        db.create_all()
        db.session.add_all(
            [
                User(id=1, email="alpha-record-test@example.invalid", active=True),
                User(id=2, email="beta-record-test@example.invalid", active=True),
                User(id=3, email="platform-record-test@example.invalid", active=True),
            ]
        )
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
    base = Path(__file__).parents[2] / "invenio_app_ils/multitenancy/sql"
    password = secrets.token_hex(32)
    tokens = {
        1: secrets.token_hex(32),
        2: secrets.token_hex(32),
        3: secrets.token_hex(32),
    }
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.transaction():
            c.execute((base / "001_security.sql").read_text())
        c.execute("INSERT INTO foslib_private.principals(user_id) VALUES(1),(2),(3)")
        c.execute(
            "UPDATE foslib_private.principals SET platform_admin=true WHERE user_id=3"
        )
        c.execute(
            "INSERT INTO foslib_private.tenants(id,slug,name) VALUES(%s,'alpha','Alpha School'),(%s,'beta','Beta School')",
            (ALPHA, BETA),
        )
        c.execute(
            "INSERT INTO foslib_private.memberships(tenant_id,user_id,role) VALUES(%s,1,'librarian'),(%s,2,'librarian')",
            (ALPHA, BETA),
        )
        with c.transaction():
            c.execute((base / "002_records.sql").read_text())
        with c.transaction():
            c.execute((base / "003_catalogue_admin.sql").read_text())
        from psycopg import sql

        c.execute(
            sql.SQL(
                "CREATE ROLE foslib_record_test_runtime LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {} IN ROLE foslib_runtime"
            ).format(sql.Literal(password))
        )
        for uid, token in tokens.items():
            c.execute(
                "SELECT foslib.issue_session(%s,%s,now()+interval '1 hour')",
                (uid, hashlib.sha256(token.encode()).digest()),
            )
        put(
            c,
            "document",
            {
                "pid": "shared-book",
                "title": "Shared metadata",
                "authors": [{"full_name": "Fictional Author"}],
                "document_type": "BOOK",
                "publication_year": "2020",
                "created_by": {"type": "import", "value": "test"},
            },
            shared=True,
        )
    for uid, tenant in [(1, ALPHA), (2, BETA)]:
        with psycopg.connect(
            dsn, user="foslib_record_test_runtime", password=password, autocommit=True
        ) as c:
            with tenant_transaction(c, tokens[uid], tenant):
                prefix = "alpha" if uid == 1 else "beta"
                put(c, "location", {"pid": prefix + "-loc"})
                put(
                    c,
                    "internal_location",
                    {"pid": prefix + "-shelf", "location_pid": prefix + "-loc"},
                )
                put(
                    c,
                    "member",
                    {
                        "pid": prefix + "-member",
                        "code": "STUDENT-001",
                        "name": "Fictional Student",
                        "active": True,
                    },
                )
                put(
                    c,
                    "item",
                    {
                        "pid": prefix + "-copy",
                        "barcode": "COPY-001",
                        "document_pid": "shared-book",
                        "internal_location_pid": prefix + "-shelf",
                        "status": "CAN_CIRCULATE",
                        "medium": "PAPER",
                        "circulation_restriction": "NO_RESTRICTION",
                        "created_by": {"type": "script", "value": "test"},
                    },
                )
    return dsn, password, tokens, app


def put(c, kind, data, shared=False):
    schemas = {
        "document": "documents/document-v2.0.0.json",
        "item": "items/item-v2.0.0.json",
        "location": "locations/location-v1.0.0.json",
        "internal_location": "internal_locations/internal_location-v1.0.0.json",
        "loan": "loans/loan-v1.0.0.json",
    }
    data = dict(
        data,
        **{
            "$schema": "https://localhost/schemas/"
            + schemas.get(kind, "foslib/" + kind + "-v1.json")
        },
    )
    record_id = uuid.uuid4()
    c.execute(
        "INSERT INTO records_metadata(id,json,version_id,created,updated) VALUES(%s,%s,1,now(),now())",
        (record_id, Jsonb(data)),
    )
    pid_types = {
        "document": "docid",
        "item": "pitmid",
        "location": "locid",
        "internal_location": "ilocid",
        "member": "fmem",
        "loan": "loanid",
        "event": "fevent",
    }
    c.execute(
        "INSERT INTO pidstore_pid(pid_type,pid_value,status,object_type,object_uuid,created,updated) VALUES(%s,%s,'R','rec',%s,now(),now())",
        (pid_types.get(kind, "f" + kind[:5]), data["pid"], record_id),
    )
    return record_id


@pytest.fixture
def school_db(scoped_ils):
    dsn, password, tokens, _ = scoped_ils
    with psycopg.connect(
        dsn, user="foslib_record_test_runtime", password=password, autocommit=True
    ) as c:
        yield c, tokens


@pytest.mark.parametrize("uid,tenant,prefix", [(1, ALPHA, "alpha"), (2, BETA, "beta")])
def test_existing_metadata_is_reciprocally_isolated(school_db, uid, tenant, prefix):
    c, tokens = school_db
    with tenant_transaction(c, tokens[uid], tenant):
        assert c.execute(
            "SELECT record_pid FROM records_metadata WHERE record_kind='item'"
        ).fetchall() == [(prefix + "-copy",)]
        assert c.execute(
            "SELECT record_pid FROM records_metadata WHERE record_kind='document'"
        ).fetchall() == [("shared-book",)]
        assert (
            c.execute(
                'UPDATE records_metadata SET json=json||\'{"name":"stolen"}\'::jsonb WHERE record_pid=%s',
                ("beta-member" if uid == 1 else "alpha-member",),
            ).rowcount
            == 0
        )


def test_real_tables_default_deny(school_db):
    c, _ = school_db
    assert c.execute(
        "SELECT count(*) FROM records_metadata WHERE tenant_id IS NOT NULL"
    ).fetchone() == (0,)


def test_real_composite_foreign_key_rejects_cross_school(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_transaction(c, tokens[1], ALPHA):
            put(
                c,
                "loan",
                {
                    "pid": "cross-loan",
                    "state": "ITEM_ON_LOAN",
                    "document_pid": "shared-book",
                    "patron_pid": "beta-member",
                    "item_pid": {"type": "pitmid", "value": "alpha-copy"},
                    "transaction_location_pid": "alpha-loc",
                },
            )


def test_school_cannot_write_shared_catalogue(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(c, tokens[1], ALPHA):
            put(c, "document", {"pid": "forged-book", "title": "forged"})


def test_ownership_columns_cannot_be_forged(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(c, tokens[1], ALPHA):
            c.execute(
                "UPDATE records_metadata SET tenant_id=%s WHERE record_pid='alpha-copy'",
                (BETA,),
            )


def test_history_inherits_scope_and_is_protected(school_db, scoped_ils):
    c, tokens = school_db
    with tenant_transaction(c, tokens[1], ALPHA):
        c.execute(
            "INSERT INTO records_metadata_version(id,json,version_id,created,updated,transaction_id,operation_type) SELECT id,json,version_id,created,updated,9001,0 FROM records_metadata WHERE record_pid='alpha-copy'"
        )
        assert c.execute(
            "SELECT count(*) FROM records_metadata_version"
        ).fetchone() == (1,)
    with tenant_transaction(c, tokens[2], BETA):
        assert c.execute(
            "SELECT count(*) FROM records_metadata_version"
        ).fetchone() == (0,)


def test_active_copy_loan_is_unique(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.UniqueViolation):
        with tenant_transaction(c, tokens[1], ALPHA):
            loan = {
                "state": "ITEM_ON_LOAN",
                "document_pid": "shared-book",
                "patron_pid": "alpha-member",
                "item_pid": {"type": "pitmid", "value": "alpha-copy"},
                "transaction_location_pid": "alpha-loc",
            }
            put(c, "loan", dict(loan, pid="loan-a"))
            put(c, "loan", dict(loan, pid="loan-b"))


def test_committed_history_cannot_be_rewritten(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(c, tokens[1], ALPHA):
            c.execute(
                'UPDATE records_metadata_version SET json=json||\'{"barcode":"rewritten"}\'::jsonb WHERE transaction_id=9001'
            )


def test_append_only_events(school_db):
    c, tokens = school_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(c, tokens[1], ALPHA):
            rid = put(c, "event", {"pid": "append-only", "action": "test"})
            c.execute("UPDATE records_metadata SET json=null WHERE id=%s", (rid,))


@pytest.fixture(scope="module")
def library_app(scoped_ils):
    from flask import Flask
    from flask_login import LoginManager, UserMixin, login_user, logout_user
    from invenio_db import InvenioDB
    from invenio_jsonschemas import InvenioJSONSchemas
    from invenio_pidstore import InvenioPIDStore
    from invenio_records import InvenioRecords
    from psycopg import sql

    from invenio_app_ils.ext import InvenioAppIls
    from invenio_app_ils.multitenancy.views import create_blueprint

    dsn, password, tokens, _ = scoped_ils
    options = conninfo_to_dict(dsn)

    def uri(user, password):
        return URL.create(
            "postgresql+psycopg2",
            username=user,
            password=password,
            database=options["dbname"],
            query={
                "host": options.get("host", "localhost"),
                "port": options.get("port", "5432"),
            },
        ).render_as_string(hide_password=False)

    broker_password = secrets.token_hex(32)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            sql.SQL(
                "CREATE ROLE foslib_record_test_broker LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {} IN ROLE foslib_auth_broker"
            ).format(sql.Literal(broker_password))
        )
    app = Flask("foslib-api-test")
    app.config.from_object("invenio_opendefinition.config")
    app.config.from_object("invenio_circulation.config")
    app.config.from_object("invenio_app_ils.config")
    app.config.from_object("invenio_app_ils.circulation.config")
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=uri("foslib_record_test_runtime", password),
        FOSLIB_AUTH_BROKER_URI=uri("foslib_record_test_broker", broker_password),
        FOSLIB_MULTITENANT_ENABLED=True,
        SERVER_NAME="localhost",
        JSONSCHEMAS_HOST="localhost",
    )
    InvenioDB(app)
    InvenioRecords(app)
    InvenioJSONSchemas(app)
    InvenioPIDStore(app)
    InvenioAppIls(app)
    manager = LoginManager(app)

    class TestUser(UserMixin):
        def __init__(self, uid):
            self.id = int(uid)
            self.email = f"school-{uid}@example.invalid"
            self.displayname = f"School {uid}"

    manager.user_loader(lambda uid: TestUser(uid))

    @app.route("/test-login/<int:uid>")
    def login(uid):
        login_user(TestUser(uid))
        return "ok"

    @app.route("/test-logout")
    def logout():
        logout_user()
        return "ok"

    app.register_blueprint(create_blueprint(app), url_prefix="/api/foslib")
    return app


def sign_in(client, uid):
    assert client.get(f"/test-login/{uid}").status_code == 200
    me = client.get("/api/foslib/me")
    assert me.status_code == 200, me.json
    return {"X-Foslib-CSRF": me.json["csrf_token"]}


def test_api_login_memberships_and_unauthorised_switch(library_app):
    with library_app.test_client() as c:
        assert c.get("/api/foslib/me").status_code == 401
        headers = sign_in(c, 1)
        assert [s["slug"] for s in c.get("/api/foslib/me").json["schools"]] == ["alpha"]
        assert (
            c.post(
                "/api/foslib/school", json={"tenant_id": BETA}, headers=headers
            ).status_code
            == 403
        )
        assert (
            c.get("/api/foslib/inventory", headers={"X-Tenant-ID": BETA}).json["items"][
                0
            ]["pid"]
            == "alpha-copy"
        )
        assert (
            c.get("/api/foslib/catalogue").json["items"][0]["title"]
            == "Shared metadata"
        )
        assert c.get("/test-logout").status_code == 200
        assert c.get("/api/foslib/me").status_code == 401


def test_api_full_circulation_and_cross_school_access(library_app):
    alpha = library_app.test_client()
    beta = library_app.test_client()
    ah = sign_in(alpha, 1)
    bh = sign_in(beta, 2)
    book = alpha.get("/api/foslib/catalogue").json["items"][0]
    created = alpha.post(
        "/api/foslib/members",
        json={
            "name": "Fictional Alpha Borrower",
            "code": "ALPHA-API-002",
            "year_group": "Year 6",
        },
        headers=ah,
    )
    assert created.status_code == 200, created.json
    member = created.json
    copy = alpha.post(
        "/api/foslib/inventory",
        json={"book_id": book["id"], "barcode": "ALPHA-API-COPY"},
        headers=ah,
    )
    assert copy.status_code == 200, copy.json
    copy = copy.json
    reserved = alpha.post(
        "/api/foslib/reservations",
        json={"book_id": book["id"], "member_id": member["id"]},
        headers=ah,
    )
    assert reserved.status_code == 200, reserved.json
    issued = alpha.post(
        "/api/foslib/loans",
        json={"copy_id": copy["id"], "member_id": member["id"]},
        headers=ah,
    )
    assert issued.status_code == 200, issued.json
    loan = issued.json
    assert (
        alpha.get("/api/foslib/reservations").json["items"][0]["status"] == "fulfilled"
    )
    assert (
        alpha.post(
            "/api/foslib/loans",
            json={"copy_id": copy["id"], "member_id": member["id"]},
            headers=ah,
        ).status_code
        == 409
    )
    assert (
        beta.post(
            f"/api/foslib/loans/{loan['id']}/return", json={}, headers=bh
        ).status_code
        == 404
    )
    assert (
        beta.put(
            f"/api/foslib/members/{member['id']}", json={"name": "stolen"}, headers=bh
        ).status_code
        == 404
    )
    assert (
        beta.delete(
            f"/api/foslib/inventory/{copy['id']}", json={}, headers=bh
        ).status_code
        == 404
    )
    fine = alpha.post(
        "/api/foslib/fines",
        json={
            "loan_id": loan["id"],
            "amount_cents": 250,
            "reason": "Demonstration fine",
        },
        headers=ah,
    )
    assert fine.status_code == 200, fine.json
    paid = alpha.post(f"/api/foslib/fines/{fine.json['id']}/pay", json={}, headers=ah)
    assert paid.status_code == 200, paid.json
    assert (
        alpha.post(
            f"/api/foslib/fines/{fine.json['id']}/pay", json={}, headers=ah
        ).status_code
        == 409
    )
    assert (
        alpha.post(
            f"/api/foslib/loans/{loan['id']}/return", json={}, headers=ah
        ).status_code
        == 200
    )
    assert alpha.get("/api/foslib/transactions").json["items"]
    assert all(
        r["pid"] != loan["pid"] for r in beta.get("/api/foslib/loans").json["items"]
    )
    assert not beta.get("/api/foslib/catalogue?q=Fictional%20Alpha%20Borrower").json[
        "items"
    ]


def test_api_csrf_and_ownership_forgery(library_app):
    c = library_app.test_client()
    headers = sign_in(c, 1)
    assert (
        c.post("/api/foslib/members", json={"name": "x", "code": "x"}).status_code
        == 400
    )
    assert (
        c.post(
            "/api/foslib/members",
            json={"name": "x", "code": "x", "tenant_id": BETA},
            headers=headers,
        ).status_code
        == 400
    )
    assert c.get("/api/foslib/admin/schools").status_code == 403


def test_school_member_and_copy_crud(library_app):
    c = library_app.test_client()
    h = sign_in(c, 1)
    member = c.post(
        "/api/foslib/members",
        json={"name": "Fictional Temporary Member", "code": "TEMP-CRUD"},
        headers=h,
    )
    assert member.status_code == 200, member.json
    rid = member.json["id"]
    assert (
        c.put(
            "/api/foslib/members/" + rid,
            json={"name": "Updated Fictional Member", "active": False},
            headers=h,
        ).status_code
        == 200
    )
    assert c.delete("/api/foslib/members/" + rid, json={}, headers=h).status_code == 200
    book = c.get("/api/foslib/catalogue").json["items"][0]
    copy = c.post(
        "/api/foslib/inventory",
        json={"book_id": book["id"], "barcode": "TEMP-COPY"},
        headers=h,
    )
    assert copy.status_code == 200, copy.json
    rid = copy.json["id"]
    updated = c.put(
        "/api/foslib/inventory/" + rid,
        json={"barcode": "TEMP-COPY-UPDATED", "status": "MISSING"},
        headers=h,
    )
    assert updated.status_code == 200, updated.json
    removed = c.delete("/api/foslib/inventory/" + rid, json={}, headers=h)
    assert removed.status_code == 200, removed.json


def test_actual_worker_transactions_and_search_guard(library_app, scoped_ils):
    from invenio_db import db

    from invenio_app_ils.multitenancy.runtime import _guard_search, worker_context

    tokens = scoped_ils[2]
    with library_app.app_context():
        with worker_context(tokens[1], ALPHA):
            assert {
                r[0]
                for r in db.session.execute(
                    __import__("sqlalchemy").text(
                        "SELECT record_pid FROM records_metadata WHERE record_kind='item' AND json IS NOT NULL"
                    )
                )
            } >= {"alpha-copy"}
        with pytest.raises(PermissionError):
            with worker_context(tokens[1], BETA):
                db.session.execute(
                    __import__("sqlalchemy").text(
                        "SELECT count(*) FROM records_metadata"
                    )
                ).scalar()
        with pytest.raises(PermissionError):
            _guard_search(
                library_app,
                json={"$schema": "https://localhost/schemas/items/item-v2.0.0.json"},
            )
        document = {
            "$schema": "https://localhost/schemas/documents/document-v2.0.0.json",
            "title": "Shared",
            "circulation": {"transactions": ["sensitive"]},
            "stock": {"count": 99},
        }
        _guard_search(library_app, json=document)
        assert "circulation" not in document and "stock" not in document


def test_platform_functions_are_authorised_and_audited(library_app, scoped_ils):
    c = library_app.test_client()
    h = sign_in(c, 3)
    assert c.get("/api/foslib/me").json["platform_admin"] is True
    assert c.get("/api/foslib/inventory").status_code == 403
    schools = c.get("/api/foslib/admin/schools")
    assert schools.status_code == 200, schools.json
    assert {s["slug"] for s in schools.json["items"]} == {"alpha", "beta"}
    book = {
        "title": "Matilda",
        "authors": ["Roald Dahl"],
        "isbn13": "9780140328721",
        "publisher": "Puffin",
        "publication_year": "1988",
    }
    saved = c.post("/api/foslib/admin/catalogue", json=book, headers=h)
    assert saved.status_code == 200, saved.json
    assert (
        c.post("/api/foslib/admin/catalogue", json=book, headers=h).status_code == 409
    )
    audit = c.get("/api/foslib/admin/audit")
    assert audit.status_code == 200, audit.json
    assert any(e["action"] == "catalogue.save" for e in audit.json["items"])
    with psycopg.connect(scoped_ils[0]) as owner:
        assert owner.execute(
            "SELECT count(*) FROM foslib_private.admin_audit WHERE actor_user_id=3 AND action='catalogue.save'"
        ).fetchone() == (1,)


def test_legacy_jobs_fail_before_unscoped_search(library_app, monkeypatch):
    from invenio_db import db

    from invenio_app_ils.closures import tasks

    def forbidden_search():
        pytest.fail("Unscoped search was executed")

    monkeypatch.setattr(tasks, "get_active_loans", forbidden_search)
    with library_app.app_context():
        db.session.remove()
        with pytest.raises(PermissionError):
            tasks.extend_active_loans_location_closure.run("alpha-loc")
        db.session.remove()


def test_disabling_authentication_account_revokes_database_access(
    school_db, scoped_ils
):
    c, tokens = school_db
    with psycopg.connect(scoped_ils[0], autocommit=True) as owner:
        try:
            with tenant_transaction(c, tokens[1], ALPHA):
                assert (
                    c.execute(
                        "SELECT count(*) FROM records_metadata WHERE record_kind='item'"
                    ).fetchone()[0]
                    > 0
                )
                owner.execute("UPDATE accounts_user SET active=false WHERE id=1")
                assert c.execute(
                    "SELECT count(*) FROM records_metadata WHERE record_kind='item'"
                ).fetchone() == (0,)
        finally:
            owner.execute("UPDATE accounts_user SET active=true WHERE id=1")
