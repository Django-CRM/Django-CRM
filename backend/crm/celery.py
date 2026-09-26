from __future__ import absolute_import, unicode_literals

import os

from celery import Celery
from celery.schedules import crontab

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crm.settings")
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.dev_settings')
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.server_settings')

app = Celery("crm")

# Using a string here means the worker don't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()
app.autodiscover_tasks(related_name="celery_tasks")  # tasks app uses celery_tasks.py

# Celery Beat Schedule for recurring tasks
app.conf.beat_schedule = {
    # Generate invoices from recurring invoice templates - daily at midnight
    "generate-recurring-invoices": {
        "task": "invoices.tasks.generate_recurring_invoices",
        "schedule": crontab(hour=0, minute=0),
    },
    # Mark overdue invoices - daily at 1 AM
    "check-overdue-invoices": {
        "task": "invoices.tasks.check_overdue_invoices",
        "schedule": crontab(hour=1, minute=0),
    },
    # Process payment reminders - daily at 9 AM
    "process-payment-reminders": {
        "task": "invoices.tasks.process_payment_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
    # Mark expired estimates - daily at midnight
    "check-expired-estimates": {
        "task": "invoices.tasks.check_expired_estimates",
        "schedule": crontab(hour=0, minute=30),
    },
    # Check for stale/rotten opportunities - daily at 8 AM
    "check-stale-opportunities": {
        "task": "opportunity.tasks.check_stale_opportunities",
        "schedule": crontab(hour=8, minute=0),
    },
    # Check goal milestones and send notifications - daily at 9:15 AM
    "check-goal-milestones": {
        "task": "opportunity.tasks.check_goal_milestones",
        "schedule": crontab(hour=9, minute=15),
    },
    # Scan cases for SLA breach and fire configured escalations - every 5 minutes
    "scan-for-breached-cases": {
        "task": "cases.tasks.scan_for_breached_cases",
        "schedule": crontab(minute="*/5"),
    },
    # Purge already-read in-app notifications older than 90 days - daily at 3 AM
    "purge-read-notifications": {
        "task": "common.tasks.purge_read_notifications",
        "schedule": crontab(hour=3, minute=0),
    },
    # Stop forgotten time-tracking timers older than 12 hours - every 30 minutes
    "auto-stop-stale-timers": {
        "task": "cases.tasks.auto_stop_stale_timers",
        "schedule": crontab(minute="*/30"),
    },
    # Drop rotated/expired refresh token records - daily at 3:30 AM
    "flush-expired-refresh-tokens": {
        "task": "common.tasks.flush_expired_refresh_tokens",
        "schedule": crontab(hour=3, minute=30),
    },
    # Send webhook deliveries whose retry is due, every minute
    "dispatch-due-webhook-deliveries": {
        "task": "webhooks.tasks.dispatch_due_deliveries",
        "schedule": crontab(minute="*"),
    },
    # Drop webhook delivery logs older than 30 days, daily at 3:45 AM
    "prune-webhook-deliveries": {
        "task": "webhooks.tasks.prune_webhook_deliveries",
        "schedule": crontab(hour=3, minute=45),
    },
}
