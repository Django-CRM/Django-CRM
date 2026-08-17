from django.apps import AppConfig


class WebFormsConfig(AppConfig):
    name = "webforms"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Imported here rather than at module scope: `ready()` is the first
        # point at which the app registry is populated, and webforms.cors
        # imports a model.
        #
        # Connecting this is what makes the per-form CORS rule take effect at
        # all. A correct receiver that nobody calls is the same as no receiver,
        # which is why `test_the_receiver_is_registered_with_corsheaders`
        # exists.
        from corsheaders.signals import check_request_enabled

        from webforms.cors import allow_webform_origin

        check_request_enabled.connect(allow_webform_origin)
