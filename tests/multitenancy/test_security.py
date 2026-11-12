# SPDX-License-Identifier: MIT
"""Database-level adversarial tests for the tenant context contract."""

import hashlib
import secrets

import psycopg
import pytest

from invenio_app_ils.multitenancy.context import new_capability, tenant_transaction

from conftest import ALPHA, BETA


@pytest.mark.parametrize("user,tenant,barcode", [(1, ALPHA, "ALPHA-001"), (2, BETA, "BETA-001")])
def test_reciprocal_read_isolation(runtime, tokens, user, tenant, barcode):
    with tenant_transaction(runtime, tokens[user], tenant):
        assert runtime.execute("SELECT barcode FROM foslib.test_copies").fetchall() == [(barcode,)]
        assert runtime.execute("SELECT title FROM foslib.test_catalogue").fetchall() == [("Shared book",)]


@pytest.mark.parametrize("sql", [
    "UPDATE foslib.test_copies SET barcode='stolen' WHERE id=2",
    "DELETE FROM foslib.test_copies WHERE id=2",
])
def test_other_tenant_write_matches_no_rows(runtime, tokens, sql):
    with tenant_transaction(runtime, tokens[1], ALPHA):
        assert runtime.execute(sql).rowcount == 0


def test_forged_tenant_insert(runtime, tokens):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(runtime, tokens[1], ALPHA):
            runtime.execute("INSERT INTO foslib.test_copies VALUES (%s,99,1,'forged')", (BETA,))


def test_cross_tenant_foreign_key(runtime, tokens):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_transaction(runtime, tokens[1], ALPHA):
            runtime.execute("INSERT INTO foslib.test_loans VALUES (%s,1,2)", (ALPHA,))


@pytest.mark.parametrize("token", [None, "", "x" * 64, "f" * 64, "'; SET ROLE foslib_owner; --"])
def test_forged_token(runtime, token):
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        with tenant_transaction(runtime, token, BETA):
            pytest.fail("forged token activated")


def test_unauthorised_school_switch(runtime, tokens):
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        with tenant_transaction(runtime, tokens[1], BETA):
            pytest.fail("unauthorised tenant activated")


def test_missing_context_and_arbitrary_guc(runtime):
    with runtime.transaction():
        runtime.execute("SELECT set_config('foslib.tenant_id', %s, true)", (BETA,))
        runtime.execute("SELECT set_config('app.tenant_id', %s, false)", (BETA,))
        assert runtime.execute("SELECT foslib.current_tenant_id()").fetchone() == (None,)
        assert runtime.execute("SELECT * FROM foslib.test_copies").fetchall() == []


@pytest.mark.parametrize("commit", [True, False])
def test_pool_connection_does_not_leak(runtime, tokens, commit):
    try:
        with tenant_transaction(runtime, tokens[1], ALPHA):
            assert runtime.execute("SELECT count(*) FROM foslib.test_copies").fetchone() == (1,)
            if not commit:
                raise ValueError("simulate request failure")
    except ValueError:
        pass
    with runtime.transaction():
        assert runtime.execute("SELECT foslib.current_user_id()").fetchone() == (None,)
        assert runtime.execute("SELECT * FROM foslib.test_copies").fetchall() == []
    with tenant_transaction(runtime, tokens[2], BETA):
        assert runtime.execute("SELECT barcode FROM foslib.test_copies").fetchall() == [("BETA-001",)]


def test_context_cannot_change_within_transaction(runtime, tokens):
    with pytest.raises(psycopg.errors.ActiveSqlTransaction):
        with tenant_transaction(runtime, tokens[6], ALPHA):
            runtime.execute("SELECT foslib.activate_context(%s,%s)", (tokens[6], BETA))


def test_nested_transaction_rejected(runtime, tokens):
    with runtime.transaction():
        with pytest.raises(RuntimeError, match="fresh transaction"):
            with tenant_transaction(runtime, tokens[1], ALPHA):
                pytest.fail("nested context")


def test_authorised_memberships_only(runtime, tokens):
    with tenant_transaction(runtime, tokens[1], None):
        assert runtime.execute("SELECT slug FROM foslib.my_schools()").fetchall() == [("alpha",)]
    with tenant_transaction(runtime, tokens[6], None):
        assert runtime.execute("SELECT slug FROM foslib.my_schools()").fetchall() == [("alpha",), ("beta",)]


def test_background_worker(runtime, tokens):
    with tenant_transaction(runtime, tokens[5], ALPHA):
        assert runtime.execute("SELECT barcode FROM foslib.test_copies").fetchall() == [("ALPHA-001",)]
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification):
        with tenant_transaction(runtime, tokens[5], BETA):
            pytest.fail("worker crossed tenant boundary")


def test_reader_cannot_write(runtime, tokens):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(runtime, tokens[4], ALPHA):
            runtime.execute("INSERT INTO foslib.test_copies VALUES (%s,98,1,'reader')", (ALPHA,))


@pytest.mark.parametrize("sql", [
    "SELECT * FROM foslib_private.auth_sessions",
    "SELECT * FROM foslib_private.memberships",
    "SELECT * FROM foslib_private.transaction_context",
    "SELECT * FROM foslib_private.admin_audit",
    "SET ROLE foslib_owner",
    "SET ROLE foslib_auth_broker",
    "SELECT foslib.issue_session(1,decode(repeat('ff',32),'hex'),now()+interval '1 hour')",
    "UPDATE foslib.test_catalogue SET title='unauthorised'",
    "TRUNCATE foslib.test_copies CASCADE",
])
def test_runtime_cannot_escalate(runtime, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        runtime.execute(sql)


def test_platform_admin_is_explicit_and_audited(runtime, tokens, database):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_transaction(runtime, tokens[1], ALPHA):
            runtime.execute("SELECT foslib.admin_set_school(%s,'alpha','Hijacked',true)", (ALPHA,))
    with tenant_transaction(runtime, tokens[3], None):
        assert runtime.execute("SELECT * FROM foslib.test_copies").fetchall() == []
        runtime.execute("SELECT foslib.admin_set_school(%s,'alpha','Alpha School',true)", (ALPHA,))
    with psycopg.connect(database[0]) as owner:
        assert owner.execute("SELECT actor_user_id,action FROM foslib_private.admin_audit").fetchall() == [(3, "school.upsert")]


def test_revocation_rechecked_in_active_transaction(runtime, tokens, database):
    token = secrets.token_hex(32)
    digest = hashlib.sha256(token.encode()).digest()
    with psycopg.connect(database[0], autocommit=True) as owner:
        owner.execute("SET ROLE foslib_auth_broker")
        owner.execute("SELECT foslib.issue_session(1,%s,now()+interval '1 hour')", (digest,))
        with tenant_transaction(runtime, token, ALPHA):
            assert runtime.execute("SELECT count(*) FROM foslib.test_copies").fetchone() == (1,)
            owner.execute("SELECT foslib.revoke_session(%s)", (digest,))
            assert runtime.execute("SELECT * FROM foslib.test_copies").fetchall() == []


def test_capabilities_are_random():
    token, digest = new_capability()
    assert len(token) == 64
    assert hashlib.sha256(token.encode()).digest() == digest
    assert new_capability()[0] != token
