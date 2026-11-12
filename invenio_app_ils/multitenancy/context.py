# SPDX-License-Identifier: MIT
"""Transaction-bound database context shared by requests and background jobs.

The caller supplies an opaque authentication capability, never an asserted user
ID. Only the separately credentialed authentication broker can mint capabilities
after Invenio authentication. PostgreSQL validates membership on activation and
on each subsequent policy evaluation.
"""

import hashlib
import secrets
from contextlib import contextmanager


def new_capability():
    """Generate a 256-bit bearer secret and its database-safe digest.

    Keep the secret in the server-side session store. Never put it in a task
    log, URL, browser storage, or client-controlled tenant header.
    """
    token = secrets.token_hex(32)
    return token, hashlib.sha256(token.encode("ascii")).digest()


@contextmanager
def tenant_transaction(connection, capability, tenant_id):
    """Activate verified membership for exactly one psycopg transaction.

    A fresh, idle runtime connection is required. Nesting in an existing
    transaction would extend the context's lifetime and is rejected. Workers
    must receive a scoped capability through a protected queue and call this
    function just as HTTP handlers do; a raw tenant ID is insufficient.
    """
    from psycopg.pq import TransactionStatus

    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("Tenant context requires a fresh transaction")
    with connection.transaction():
        connection.execute(
            "SELECT foslib.activate_context(%s, %s)",
            (capability, tenant_id),
        )
        yield connection
