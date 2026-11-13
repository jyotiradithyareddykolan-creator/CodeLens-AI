from celery import Celery
from app.core.config import settings


# These task names are referenced by webhook handling and are intentionally
# kept in one place so missing Phase 3/4 implementations are observable.
PING_TASK_NAME = "app.workers.tasks.ping"
INDEX_REPOSITORY_TASK_NAME = "app.workers.tasks.index_repository"
INCREMENTAL_REINDEX_TASK_NAME = "app.workers.tasks.incremental_reindex"
ANALYZE_PULL_REQUEST_TASK_NAME = "app.workers.tasks.analyze_pull_request"

APPLICATION_TASK_NAMES = frozenset(
    {
        PING_TASK_NAME,
        INDEX_REPOSITORY_TASK_NAME,
        INCREMENTAL_REINDEX_TASK_NAME,
        ANALYZE_PULL_REQUEST_TASK_NAME,
    }
)

celery_app = Celery(
    "codelens_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
)


@celery_app.task(name=PING_TASK_NAME)
def ping_task() -> str:
    """Diagnostic task to verify worker execution and Redis broker roundtrips."""
    return "pong"


def missing_application_tasks() -> frozenset[str]:
    """Return application task names referenced but not implemented/registered."""
    return frozenset(name for name in APPLICATION_TASK_NAMES if name not in celery_app.tasks)


def dispatch_registered_task(task_name: str, *args: object) -> bool:
    """Dispatch only registered tasks; return False for known missing work."""
    if task_name not in celery_app.tasks:
        return False
    celery_app.send_task(task_name, args=list(args))
    return True
