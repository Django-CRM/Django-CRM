"""Pipeline update and delete read the pipeline under a lock.

Both used to read the row unlocked and then save all of it. So a rename saved
from a read taken before another admin made that pipeline the default wrote
the old `is_default` back and left the org with no default, and a rename could
bring back a pipeline deleted in between. Both now go through `lock_pipeline`,
which locks the org's row and then the pipeline's, the same order
`make_only_default` takes, so the writes take turns and cannot deadlock.
"""

import threading
import time
from unittest import mock

import pytest
from django.db import connection
from django.db.models.query import QuerySet

from cases.models import CasePipeline
from common import kanban
from common.models import Org
from common.testing import _make_authenticated_client
from leads.models import LeadPipeline
from tasks.models import TaskPipeline

BOARDS = [
    ("leads", LeadPipeline, "leads.views.kanban_views"),
    ("cases", CasePipeline, "cases.kanban_views"),
    ("tasks", TaskPipeline, "tasks.views.kanban_views"),
]


@pytest.mark.parametrize("app,model,_", BOARDS)
@pytest.mark.parametrize("verb", ["put", "delete"])
def test_update_and_delete_lock_the_org_then_the_pipeline(
    admin_client, org_a, app, model, _, verb
):
    """SQLite drops FOR UPDATE, so this asks which querysets were locked, in
    what order; the race itself is the postgres_only tests below."""
    pipeline = model.objects.create(name="Plain", org=org_a)
    url = f"/api/{app}/pipelines/{pipeline.id}/"
    original = QuerySet.select_for_update
    with mock.patch.object(
        QuerySet, "select_for_update", autospec=True, side_effect=original
    ) as spy:
        if verb == "put":
            response = admin_client.put(url, {"name": "Renamed"}, format="json")
        else:
            response = admin_client.delete(url)
    assert response.status_code in (200, 204), response.content
    locked = [call.args[0].model for call in spy.call_args_list]
    assert locked[:2] == [Org, model]


def _race(admin_user, org, profile, module, sends, pauses):
    """Run each of ``sends`` in its own thread, all starting together. Thread
    ``n`` pauses ``pauses[n]`` seconds after its read, so both are in flight
    before either saves, and without the lock the first to save is known."""
    barrier = threading.Barrier(len(sends))
    statuses, errors = {}, []
    real = kanban.lock_pipeline
    pause = threading.local()

    def slow(*args, **kwargs):
        pipeline = real(*args, **kwargs)
        time.sleep(pause.seconds)
        return pipeline

    def worker(n, send):
        pause.seconds = pauses[n]
        try:
            client = _make_authenticated_client(admin_user, org, profile)
            barrier.wait()
            statuses[n] = send(client).status_code
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with mock.patch(f"{module}.lock_pipeline", slow):
        threads = [
            threading.Thread(target=worker, args=(n, send))
            for n, send in enumerate(sends)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    return statuses, errors


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("app,model,module", BOARDS)
def test_a_rename_during_make_default_keeps_both(
    admin_user, org_a, admin_profile, app, model, module
):
    """Without the lock the rename, reading first and saving last, wrote the
    old `is_default` back."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")
    model.objects.create(name="Old default", org=org_a, is_default=True)
    pipeline = model.objects.create(name="Before", org=org_a)
    url = f"/api/{app}/pipelines/{pipeline.id}/"

    statuses, errors = _race(
        admin_user,
        org_a,
        admin_profile,
        module,
        [
            lambda c: c.put(url, {"name": "After"}, format="json"),
            lambda c: c.put(url, {"is_default": True}, format="json"),
        ],
        pauses=(0.8, 0.2),
    )

    assert errors == []
    assert statuses == {0: 200, 1: 200}
    pipeline.refresh_from_db()
    assert (pipeline.name, pipeline.is_default) == ("After", True)
    assert model.objects.filter(org=org_a, is_default=True).count() == 1


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("app,model,module", BOARDS)
def test_a_rename_during_a_delete_does_not_bring_it_back(
    admin_user, org_a, admin_profile, app, model, module
):
    """Without the lock the rename, reading first and saving last, wrote
    `is_active` back."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")
    pipeline = model.objects.create(name="Before", org=org_a)
    url = f"/api/{app}/pipelines/{pipeline.id}/"

    statuses, errors = _race(
        admin_user,
        org_a,
        admin_profile,
        module,
        [
            lambda c: c.put(url, {"name": "After"}, format="json"),
            lambda c: c.delete(url),
        ],
        pauses=(0.8, 0.2),
    )

    assert errors == []
    # Whichever went first, the rename either landed before the delete or
    # found the pipeline gone; it never undid the delete.
    assert statuses[1] == 204
    assert statuses[0] in (200, 404)
    pipeline.refresh_from_db()
    assert pipeline.is_active is False
