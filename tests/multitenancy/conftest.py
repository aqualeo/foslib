# SPDX-License-Identifier: MIT
"""Isolated PostgreSQL tests; never use the staging database.

Run with --confcutdir=tests/multitenancy to avoid the upstream ILS fixtures.
"""

import hashlib
import os
import secrets
from pathlib import Path

import pgserver
import psycopg
import pytest
from psycopg import sql

ALPHA = "11111111-1111-4111-8111-111111111111"
BETA = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory):
    configured = os.environ.get("FOSLIB_TEST_POSTGRES_DSN")
    if configured:
        from psycopg.conninfo import conninfo_to_dict

        if not conninfo_to_dict(configured).get("dbname", "").endswith("_test"):
            pytest.fail(
                "FOSLIB_TEST_POSTGRES_DSN must point to a disposable *_test database"
            )
        yield configured
    else:
        server = pgserver.get_server(tmp_path_factory.mktemp("postgres"))
        yield server.get_uri()


@pytest.fixture(scope="session")
def database(postgres_dsn):
    uri = postgres_dsn
    migration = (
        Path(__file__).parents[2] / "invenio_app_ils/multitenancy/sql/001_security.sql"
    )
    with psycopg.connect(uri, autocommit=True) as owner:
        with owner.transaction():
            owner.execute(migration.read_text())
        from psycopg.conninfo import conninfo_to_dict

        runtime_password = conninfo_to_dict(uri).get("password") or secrets.token_hex(
            32
        )
        owner.execute(
            sql.SQL(
                "CREATE ROLE foslib_test_runtime LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {} IN ROLE foslib_runtime"
            ).format(sql.Literal(runtime_password))
        )
        owner.execute("""
          INSERT INTO foslib_private.principals(user_id, platform_admin)
            VALUES (1, false), (2, false), (3, true), (4, false), (5, false), (6, false);
          INSERT INTO foslib_private.tenants(id,slug,name) VALUES
            ('11111111-1111-4111-8111-111111111111','alpha','Alpha School'),
            ('22222222-2222-4222-8222-222222222222','beta','Beta School');
          INSERT INTO foslib_private.memberships(tenant_id,user_id,role) VALUES
            ('11111111-1111-4111-8111-111111111111',1,'librarian'),
            ('22222222-2222-4222-8222-222222222222',2,'librarian'),
            ('11111111-1111-4111-8111-111111111111',4,'reader'),
            ('11111111-1111-4111-8111-111111111111',5,'worker'),
            ('11111111-1111-4111-8111-111111111111',6,'librarian'),
            ('22222222-2222-4222-8222-222222222222',6,'librarian');
        """)
        # Contract fixtures, deliberately not a second production inventory.
        # These policies must also be applied to the real ILS tables in the
        # ownership migration before multi-tenancy can be enabled.
        owner.execute("""
          SET ROLE foslib_owner;
          CREATE TABLE foslib.test_catalogue(id integer PRIMARY KEY, title text NOT NULL);
          INSERT INTO foslib.test_catalogue VALUES (1, 'Shared book');
          CREATE TABLE foslib.test_copies(
            tenant_id uuid NOT NULL, id integer NOT NULL,
            book_id integer NOT NULL REFERENCES foslib.test_catalogue(id),
            barcode text NOT NULL, PRIMARY KEY(tenant_id,id));
          CREATE TABLE foslib.test_loans(
            tenant_id uuid NOT NULL, id integer NOT NULL, copy_id integer NOT NULL,
            PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,copy_id)
              REFERENCES foslib.test_copies(tenant_id,id));
          INSERT INTO foslib.test_copies VALUES
            ('11111111-1111-4111-8111-111111111111',1,1,'ALPHA-001'),
            ('22222222-2222-4222-8222-222222222222',2,1,'BETA-001');
          ALTER TABLE foslib.test_copies ENABLE ROW LEVEL SECURITY;
          ALTER TABLE foslib.test_copies FORCE ROW LEVEL SECURITY;
          ALTER TABLE foslib.test_loans ENABLE ROW LEVEL SECURITY;
          ALTER TABLE foslib.test_loans FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_read ON foslib.test_copies FOR SELECT TO foslib_runtime
            USING (tenant_id = foslib.current_tenant_id());
          CREATE POLICY tenant_insert ON foslib.test_copies FOR INSERT TO foslib_runtime
            WITH CHECK (tenant_id = foslib.current_tenant_id() AND foslib.can_write());
          CREATE POLICY tenant_update ON foslib.test_copies FOR UPDATE TO foslib_runtime
            USING (tenant_id = foslib.current_tenant_id() AND foslib.can_write())
            WITH CHECK (tenant_id = foslib.current_tenant_id() AND foslib.can_write());
          CREATE POLICY tenant_delete ON foslib.test_copies FOR DELETE TO foslib_runtime
            USING (tenant_id = foslib.current_tenant_id() AND foslib.can_write());
          CREATE POLICY tenant_read ON foslib.test_loans FOR SELECT TO foslib_runtime
            USING (tenant_id = foslib.current_tenant_id());
          CREATE POLICY tenant_insert ON foslib.test_loans FOR INSERT TO foslib_runtime
            WITH CHECK (tenant_id = foslib.current_tenant_id() AND foslib.can_write());
          GRANT SELECT ON foslib.test_catalogue TO foslib_runtime;
          GRANT SELECT, INSERT, UPDATE, DELETE ON foslib.test_copies, foslib.test_loans TO foslib_runtime;
          RESET ROLE;
        """)
        tokens = {}
        for user_id in range(1, 7):
            token = secrets.token_hex(32)
            tokens[user_id] = token
            owner.execute("SET ROLE foslib_auth_broker")
            owner.execute(
                "SELECT foslib.issue_session(%s,%s,clock_timestamp()+interval '1 hour')",
                (user_id, hashlib.sha256(token.encode()).digest()),
            )
            owner.execute("RESET ROLE")
    return uri, tokens, runtime_password


@pytest.fixture
def runtime(database):
    uri, _, password = database
    with psycopg.connect(
        uri, user="foslib_test_runtime", password=password, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def tokens(database):
    return database[1]
