from webhooks.emit import flush


class WebhookEventsMiddleware:
    """Queue the webhook events a request produced, once its view has returned.

    Must sit after `crum.CurrentRequestUserMiddleware` (which is what lets
    `webhooks.emit.record` find the request) and after `RequireOrgContext`, so
    the flush runs while the request's RLS context is still set. See the
    module docstring of `webhooks.emit` for why events wait for this point.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        flush(request)
        return response
