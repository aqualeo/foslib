# SPDX-License-Identifier: MIT
"""Tenant-aware jobs using the same verified transaction path as HTTP requests."""

from celery import shared_task
from sqlalchemy.exc import DBAPIError

from .runtime import worker_context
from .service import scalar


@shared_task(ignore_result=False)
def recount_school(capability, tenant_id):
    """Count one school's inventory; missing/forged membership fails closed.

    Enqueuers must use ``queue_recount`` so bearer capabilities are never
    rendered in Celery argument logs. Capabilities must be freshly issued for
    scheduled jobs and expire within twelve hours; a tenant UUID alone is not
    authority. Workers have only the restricted operational database role.
    """
    try:
        with worker_context(capability, tenant_id):
            verified = scalar("SELECT foslib.current_tenant_id()")
            if not verified:
                raise PermissionError("An authorised school context is required")
            return {
                "tenant_id": str(verified),
                "copies": scalar(
                    "SELECT count(*) FROM records_metadata WHERE record_kind='item' AND json IS NOT NULL"
                ),
                "members": scalar(
                    "SELECT count(*) FROM records_metadata WHERE record_kind='member' AND json IS NOT NULL"
                ),
            }
    except DBAPIError:
        # SQLAlchemy exceptions can render bound parameters; do not log a
        # capability or personal data in a Celery traceback.
        raise RuntimeError("Scoped database operation failed") from None


def queue_recount(capability, tenant_id):
    return recount_school.apply_async(
        args=[capability, str(tenant_id)],
        argsrepr="(<redacted capability>, <authorised school>)",
        kwargsrepr="{}",
    )
