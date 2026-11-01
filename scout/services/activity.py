import logging

from scout.models import ActivityLog

logger = logging.getLogger("scout")


def log(kind, message, job=None, **details):
    """Record something the agent did. Also echoes to the console."""
    message = str(message)[:500]
    level = logging.WARNING if kind == ActivityLog.Kind.ERROR else logging.INFO
    logger.log(level, "[%s] %s", kind, message)
    return ActivityLog.objects.create(kind=kind, message=message, job=job, details=details or {})
