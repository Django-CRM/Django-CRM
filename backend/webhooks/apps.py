from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    name = "webhooks"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Importing the module is what connects its receivers. They live here
        # rather than beside the models they watch, so no other app has to know
        # webhooks exist.
        from webhooks import signals  # noqa: F401
