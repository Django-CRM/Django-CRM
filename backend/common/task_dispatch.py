"""Best-effort dispatch for email tasks when the Celery broker is unavailable.

Email delivery must not make authentication fail just because the deployment
has no running broker/worker. When queuing fails, run the task inline as a
small-deployment fallback. A real Celery worker remains the preferred path.
"""

import logging

logger = logging.getLogger(__name__)


def dispatch_email_task(task, *args, **kwargs):
    """Queue an email task, falling back to synchronous execution.

    This helper is deliberately limited to email notifications. It should not
    be used for arbitrary or non-idempotent background work.
    """
    try:
        return task.delay(*args, **kwargs)
    except Exception:
        logger.warning(
            "Could not queue email task %s; attempting inline delivery",
            getattr(task, "name", repr(task)),
            exc_info=True,
        )

    try:
        return task.apply(args=args, kwargs=kwargs, throw=True)
    except Exception:
        # Authentication and privacy-preserving request responses should not
        # be taken down by an optional notification email. The task's own
        # logger records provider/backend delivery failures as well.
        logger.exception(
            "Inline email task %s also failed",
            getattr(task, "name", repr(task)),
        )
        return None
