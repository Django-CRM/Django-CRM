"""Renderers for endpoints that answer with something other than JSON."""

import json

from rest_framework.renderers import BaseRenderer, BrowsableAPIRenderer, JSONRenderer


class CsvRenderer(BaseRenderer):
    """Lets a view accept ``Accept: text/csv``.

    Nothing is rendered through this in the normal case: the CSV endpoints
    build a :class:`django.http.StreamingHttpResponse` themselves so a large
    export streams instead of being assembled in memory, and DRF hands a plain
    ``HttpResponse`` straight to the client.

    It exists because content negotiation runs *before* the handler. Without a
    renderer claiming ``text/csv``, a client that politely says what it wants
    is answered 406 and the handler never runs, which is what both CSV exports
    did to their own download proxies.

    ``render`` is still implemented, because a view can return a DRF
    ``Response`` on the error path (a bad date, a forbidden profile) while
    ``text/csv`` is the negotiated type. That body is JSON, and saying so in
    bytes beats handing Django a dict it cannot write.
    """

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        return json.dumps(data)


# What a CSV endpoint declares: JSON first, so an error keeps its usual shape
# for a client that did not ask for anything in particular, plus CSV, plus the
# browsable renderer DRF's own default list carries for the API explorer.
CSV_RENDERERS = (JSONRenderer, CsvRenderer, BrowsableAPIRenderer)
