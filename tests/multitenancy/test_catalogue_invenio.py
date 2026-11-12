# SPDX-License-Identifier: MIT
"""Exercise the real Document/PID APIs, not a replacement catalogue table."""

from pathlib import Path

import psycopg
import pytest


def test_full_snapshot_import_is_idempotent(postgres_dsn):
    flask = pytest.importorskip("flask")
    pytest.importorskip("invenio_db")
    from invenio_db import InvenioDB, db
    from invenio_jsonschemas import InvenioJSONSchemas
    from invenio_pidstore import InvenioPIDStore
    from invenio_records import InvenioRecords
    from invenio_records.models import RecordMetadata
    from psycopg.conninfo import make_conninfo

    from invenio_app_ils.multitenancy.catalogue_cli import catalogue

    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute("CREATE DATABASE foslib_catalogue_test")
    catalogue_dsn = make_conninfo(postgres_dsn, dbname="foslib_catalogue_test")
    from psycopg.conninfo import conninfo_to_dict
    from sqlalchemy import URL

    connection_options = conninfo_to_dict(catalogue_dsn)
    uri = URL.create(
        "postgresql+psycopg2",
        username=connection_options.get("user"),
        password=connection_options.get("password"),
        database=connection_options["dbname"],
        query={
            "host": connection_options.get("host", "localhost"),
            "port": connection_options.get("port", "5432"),
        },
    )
    app = flask.Flask("foslib-catalogue-test")
    app.config.from_object("invenio_opendefinition.config")
    app.config.from_object("invenio_circulation.config")
    app.config.from_object("invenio_app_ils.config")
    app.config.from_object("invenio_app_ils.circulation.config")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=uri.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=True,
        SERVER_NAME="localhost",
        JSONSCHEMAS_HOST="localhost",
    )
    InvenioDB(app)
    InvenioRecords(app)
    InvenioJSONSchemas(app)
    InvenioPIDStore(app)
    from invenio_app_ils.ext import InvenioAppIls

    InvenioAppIls(app)
    snapshot = Path(__file__).parents[2] / "data/catalogue/openlibrary-1000.jsonl"
    with app.app_context():
        db.create_all()
        runner = app.test_cli_runner()
        plan = runner.invoke(catalogue, ["import", str(snapshot)])
        assert plan.exit_code == 0, (plan.output, plan.exception)
        assert RecordMetadata.query.count() == 0
        imported = runner.invoke(catalogue, ["import", str(snapshot), "--apply"])
        if imported.exception:
            raise imported.exception
        assert imported.exit_code == 0, (imported.output, imported.exception)
        assert RecordMetadata.query.count() == 1000
        first = RecordMetadata.query.first()
        modified = dict(first.json)
        modified["title"] = "Corrected by the platform librarian"
        first.json = modified
        db.session.commit()
        repeated = runner.invoke(catalogue, ["import", str(snapshot), "--apply"])
        assert repeated.exit_code == 0, (repeated.output, repeated.exception)
        assert "0 new editions; 1000 already present" in repeated.output
        assert RecordMetadata.query.count() == 1000
        db.session.refresh(first)
        assert first.json["title"] == "Corrected by the platform librarian"
        # Exercise the real additive ownership migrations and both complete
        # demonstration seeds after importing the genuine shared catalogue.
        from invenio_accounts.models import User

        db.session.add_all(
            [
                User(id=1, email="seed-alpha@example.invalid", active=True),
                User(id=2, email="seed-beta@example.invalid", active=True),
            ]
        )
        db.session.commit()
        migration_dir = Path(__file__).parents[2] / "invenio_app_ils/multitenancy/sql"
        with psycopg.connect(catalogue_dsn, autocommit=True) as owner:
            with owner.transaction():
                owner.execute((migration_dir / "001_security.sql").read_text())
            owner.execute(
                "INSERT INTO foslib_private.principals(user_id) VALUES(1),(2)"
            )
            owner.execute(
                "INSERT INTO foslib_private.tenants(id,slug,name) VALUES('11111111-1111-4111-8111-111111111111','alpha','Alpha School'),('22222222-2222-4222-8222-222222222222','beta','Beta School')"
            )
            owner.execute(
                "INSERT INTO foslib_private.memberships(tenant_id,user_id,role) VALUES('11111111-1111-4111-8111-111111111111',1,'librarian'),('22222222-2222-4222-8222-222222222222',2,'librarian')"
            )
            for filename in ("002_records.sql", "003_catalogue_admin.sql"):
                with owner.transaction():
                    owner.execute((migration_dir / filename).read_text())
            owner.execute(
                "CREATE TABLE foslib_private.demo_seeds(tenant_id uuid PRIMARY KEY,seeded_at timestamptz DEFAULT now())"
            )
        from invenio_app_ils.multitenancy.bootstrap import _seed_school
        from invenio_app_ils.multitenancy.service import scalar

        _seed_school("alpha", 1)
        _seed_school("beta", 2)
        assert (
            scalar("SELECT count(*) FROM records_metadata WHERE record_kind='item'")
            == 175
        )
        assert (
            scalar("SELECT count(*) FROM records_metadata WHERE record_kind='member'")
            == 45
        )
        assert (
            scalar(
                "SELECT count(*) FROM records_metadata WHERE record_kind='loan' AND json->>'state'='ITEM_ON_LOAN'"
            )
            == 14
        )
        db.session.commit()
        _seed_school("alpha", 1)
        _seed_school("beta", 2)
        assert (
            scalar("SELECT count(*) FROM records_metadata WHERE record_kind='item'")
            == 175
        )
        db.session.remove()
        db.engine.dispose()
