from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    name = "webhooks"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Importing the modules is what connects their receivers. They live
        # here rather than beside the models they watch, so no other app has to
        # know webhooks exist.
        from webhooks import ownership, signals  # noqa: F401
