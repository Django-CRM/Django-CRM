"""Keep uploaded spreadsheets and push secrets out of Sentry.

An import upload is a whole customer list. With `send_default_pii` on, a failed
import request would attach its body, and a frame's local variables hold the
rows being parsed. `scrub_import_data` drops both. On an import URL every
frame loses its locals (stdlib `csv` and `openpyxl` frames hold rows and cells)
and breadcrumbs and extra are cleared; elsewhere, for example a Celery task that
runs import code, only frames in `IMPORT_MODULES` lose them.

`scrub_push_secrets` drops every frame's locals from an event whose stack runs
through `PUSH_MODULES`. The push sender holds the FCM service account, private
key included, and the device views a phone's push token; Sentry's scrubber
matches variable names, not the keys of a dict, and the frames below (google.auth,
DRF, the ORM) hold the same values.

Neither raises: a scrubber that throws makes Sentry drop the event, so a
malformed event passes through.
"""

import re
from urllib.parse import urlsplit

from common.public_tokens import scrub_public_tokens

IMPORT_MODULES: tuple[str, ...] = (
    "common.spreadsheet",
    "common.import_mapping",
    "contacts.services.csv_import",
    "contacts.import_views",
    "leads.csv_import",
    "leads.views.import_views",
)

PUSH_MODULES: tuple[str, ...] = (
    "common.push",
    "common.views.device_views",
)

# A lead webhook post is treated the same way: its body is a stranger's
# personal data and, for some senders, carries the webhook's key.
_IMPORT_PATH = re.compile(r"/api/(contacts|leads)/import/|/api/public/leads/")


def _frames(event):
    """Every stack frame in the event, as dicts."""
    for key in ("exception", "threads"):
        section = event.get(key)
        values = section.get("values") if isinstance(section, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            stacktrace = value.get("stacktrace") if isinstance(value, dict) else None
            frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
            if isinstance(frames, list):
                yield from (frame for frame in frames if isinstance(frame, dict))


def _in(frame, modules):
    module = frame.get("module")
    return isinstance(module, str) and module.startswith(modules)


def scrub_import_data(event):
    try:
        request = event.get("request")
        on_import_path = False
        if isinstance(request, dict):
            url = request.get("url")
            if isinstance(url, str) and _IMPORT_PATH.search(urlsplit(url).path):
                on_import_path = True
                request.pop("data", None)
        if on_import_path:
            event.pop("breadcrumbs", None)
            event.pop("extra", None)
        for frame in _frames(event):
            if on_import_path or _in(frame, IMPORT_MODULES):
                frame.pop("vars", None)
    except Exception:
        return event
    return event


def scrub_push_secrets(event):
    try:
        frames = list(_frames(event))
        if any(_in(frame, PUSH_MODULES) for frame in frames):
            for frame in frames:
                frame.pop("vars", None)
    except Exception:
        return event
    return event


def before_send(event, hint):
    return scrub_push_secrets(scrub_import_data(scrub_public_tokens(event, hint)))
