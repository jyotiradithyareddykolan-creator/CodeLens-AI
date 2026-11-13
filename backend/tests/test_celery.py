from app.workers.celery_app import (
    ANALYZE_PULL_REQUEST_TASK_NAME,
    INDEX_REPOSITORY_TASK_NAME,
    INCREMENTAL_REINDEX_TASK_NAME,
    celery_app,
    missing_application_tasks,
    ping_task,
)


def test_celery_task_registration():
    """Verify Celery tasks are properly discovered and registered."""
    registered_tasks = celery_app.tasks.keys()
    assert "app.workers.tasks.ping" in registered_tasks


def test_missing_phase_tasks_are_explicitly_reported():
    assert missing_application_tasks() == {
        INDEX_REPOSITORY_TASK_NAME,
        INCREMENTAL_REINDEX_TASK_NAME,
        ANALYZE_PULL_REQUEST_TASK_NAME,
    }


def test_ping_task_execution():
    """Test ping task returns pong directly."""
    result = ping_task.apply().get()
    assert result == "pong"
