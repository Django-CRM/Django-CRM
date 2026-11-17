"""Tests for the small-deployment email task fallback."""

from unittest.mock import MagicMock

from common.task_dispatch import dispatch_email_task


def test_queues_email_task_when_celery_is_available():
    task = MagicMock()
    task.delay.return_value = "queued"

    result = dispatch_email_task(task, "user-123", important=True)

    assert result == "queued"
    task.delay.assert_called_once_with("user-123", important=True)
    task.apply.assert_not_called()


def test_runs_email_task_inline_when_queueing_fails():
    task = MagicMock()
    task.delay.side_effect = RuntimeError("Redis connection refused")
    task.apply.return_value = "sent"

    result = dispatch_email_task(task, "user-123", important=True)

    assert result == "sent"
    task.apply.assert_called_once_with(
        args=("user-123",), kwargs={"important": True}, throw=True
    )


def test_email_failure_does_not_escape_authentication_flow():
    task = MagicMock()
    task.delay.side_effect = RuntimeError("Redis connection refused")
    task.apply.side_effect = RuntimeError("Email provider unavailable")

    assert dispatch_email_task(task, "user-123") is None
