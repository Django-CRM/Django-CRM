"""One default pipeline per org, even when two admins take it at once.

`make_only_default` demotes the org's current default before a lead, ticket or
task pipeline takes the slot. Without a lock, two saves in flight each demoted
before the other committed, and the second then hit the one-default-per-org
unique constraint: an IntegrityError and a 500. It now locks the org's row
first, so the two take turns and the second demotes the first. The org row,
not the pipeline rows, so the race is closed for an org with no pipeline yet.
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


def _put_default(client, app, pipeline):
    return client.put(
        f"/api/{app}/pipelines/{pipeline.id}/", {"is_default": True}, format="json"
    )


@pytest.mark.parametrize("app,model,_", BOARDS)
class TestTakingTheDefault:
    def test_it_demotes_the_old_default(self, admin_client, org_a, app, model, _):
        old = model.objects.create(name="Old", org=org_a, is_default=True)
        new = model.objects.create(name="New", org=org_a)

        assert _put_default(admin_client, app, new).status_code == 200

        old.refresh_from_db()
        new.refresh_from_db()
        assert (old.is_default, new.is_default) == (False, True)

    def test_the_org_row_is_locked_before_the_demote(
        self, admin_client, org_a, app, model, _
    ):
        """SQLite drops FOR UPDATE, so this asks which querysets were locked;
        the race itself is the postgres_only test below."""
        model.objects.create(name="Old", org=org_a, is_default=True)
        new = model.objects.create(name="New", org=org_a)
        original = QuerySet.select_for_update
        with mock.patch.object(
            QuerySet, "select_for_update", autospec=True, side_effect=original
        ) as spy:
            assert _put_default(admin_client, app, new).status_code == 200
        assert Org in {call.args[0].model for call in spy.call_args_list}

    def test_a_save_that_does_not_take_the_default_changes_no_default(
        self, org_a, app, model, _
    ):
        """The helper on its own: no `is_default` asked for, no lock of its
        own and no demote. (A pipeline update locks the org regardless, in
        `lock_pipeline`; see test_pipeline_write_locks.py.)"""
        old = model.objects.create(name="Old", org=org_a, is_default=True)
        original = QuerySet.select_for_update
        with mock.patch.object(
            QuerySet, "select_for_update", autospec=True, side_effect=original
        ) as spy:
            kanban.make_only_default(model, org_a, {"name": "Renamed"})
        assert spy.call_args_list == []
        old.refresh_from_db()
        assert old.is_default is True


def _race(admin_user, org, profile, module, send):
    """Run ``send(client)`` in two threads that start together, with a pause
    after the demote so both are in flight before either saves."""
    barrier = threading.Barrier(2)
    statuses, errors = [], []
    real = kanban.make_only_default

    def slow(*args, **kwargs):
        real(*args, **kwargs)
        time.sleep(0.5)

    def worker(n):
        try:
            client = _make_authenticated_client(admin_user, org, profile)
            barrier.wait()
            statuses.append(send(client, n).status_code)
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with mock.patch(f"{module}.make_only_default", slow):
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    return statuses, errors


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("app,model,module", BOARDS)
def test_two_admins_taking_the_default_at_once_both_succeed(
    admin_user, org_a, admin_profile, app, model, module
):
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")
    model.objects.create(name="Old", org=org_a, is_default=True)
    pipelines = [model.objects.create(name=f"P{n}", org=org_a) for n in range(2)]

    statuses, errors = _race(
        admin_user,
        org_a,
        admin_profile,
        module,
        lambda client, n: _put_default(client, app, pipelines[n]),
    )

    assert errors == []
    assert statuses == [200, 200]
    assert model.objects.filter(org=org_a, is_default=True).count() == 1


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("app,model,module", BOARDS)
def test_an_orgs_first_two_defaults_at_once_both_succeed(
    admin_user, org_a, admin_profile, app, model, module
):
    """No pipeline yet, so no pipeline row to lock: the org row is the lock."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")

    statuses, errors = _race(
        admin_user,
        org_a,
        admin_profile,
        module,
        lambda client, n: client.post(
            f"/api/{app}/pipelines/",
            {"name": f"First {n}", "is_default": True, "create_default_stages": False},
            format="json",
        ),
    )

    assert errors == []
    assert statuses == [201, 201]
    assert model.objects.filter(org=org_a, is_default=True).count() == 1
