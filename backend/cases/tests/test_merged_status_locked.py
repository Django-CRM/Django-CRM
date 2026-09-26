"""A merged ticket's status changes only by unmerging it (owner decision, 1.11.0).

Editing a merged ticket (``merged_into`` set) to another status used to keep
``merged_into``, so the ticket was open and merged at once. Every path that
writes a ticket's status now refuses that with the same sentence, and each
refusal here is paired with the same request on an unmerged ticket going
through.
"""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from cases.models import Case
from cases.workflow import MERGED_STATUS_LOCKED
from common.models import Comment
from conftest import rls_org


def _case(org, creator, name, **extra):
    fields = {"status": "New", "priority": "Normal"}
    fields.update(extra)
    return Case.objects.create(name=name, org=org, created_by=creator, **fields)


@pytest.fixture
def merged(admin_client, admin_user, org_a):
    """A ticket merged into another through the real endpoint."""
    target = _case(org_a, admin_user, "Survivor")
    source = _case(org_a, admin_user, "Duplicate of survivor")
    response = admin_client.post(f"/api/cases/{source.id}/merge/{target.id}/")
    assert response.status_code == 200, response.content
    source.refresh_from_db()
    assert source.merged_into_id == target.id and source.status == "Duplicate"
    return source


@pytest.fixture
def plain(admin_user, org_a):
    return _case(org_a, admin_user, "Ordinary ticket")


def _detail(case):
    return f"/api/cases/{case.id}/"


def _unchanged(case):
    before = case.status
    case.refresh_from_db()
    assert case.status == before
    assert case.merged_into_id is not None


class TestDetailEdit:
    def test_patch_status_on_a_merged_ticket_is_refused(self, admin_client, merged):
        response = admin_client.patch(
            _detail(merged), {"status": "Assigned"}, format="json"
        )
        assert response.status_code == 400
        assert response.json()["errors"] == {"status": [MERGED_STATUS_LOCKED]}
        _unchanged(merged)

    def test_put_status_on_a_merged_ticket_is_refused(self, admin_client, merged):
        response = admin_client.put(
            _detail(merged),
            {"name": merged.name, "status": "Closed", "priority": "Normal"},
            format="json",
        )
        assert response.status_code == 400
        assert MERGED_STATUS_LOCKED in str(response.json())
        _unchanged(merged)

    def test_other_fields_on_a_merged_ticket_still_save(self, admin_client, merged):
        """Re-sending the status it has is not a change of status."""
        response = admin_client.put(
            _detail(merged),
            {
                "name": merged.name,
                "status": "Duplicate",
                "priority": "High",
                "description": "Kept for the record.",
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        merged.refresh_from_db()
        assert merged.priority == "High"
        assert merged.status == "Duplicate"

    def test_an_unmerged_ticket_changes_status(self, admin_client, plain):
        response = admin_client.patch(
            _detail(plain), {"status": "Assigned"}, format="json"
        )
        assert response.status_code == 200, response.content
        plain.refresh_from_db()
        assert plain.status == "Assigned"

    def test_after_unmerge_the_status_changes_again(self, admin_client, merged):
        response = admin_client.post(f"/api/cases/{merged.id}/unmerge/")
        assert response.status_code == 200, response.content
        response = admin_client.patch(
            _detail(merged), {"status": "Assigned"}, format="json"
        )
        assert response.status_code == 200, response.content
        merged.refresh_from_db()
        assert merged.status == "Assigned" and merged.merged_into_id is None


class TestBoardMove:
    def test_move_of_a_merged_ticket_is_refused(self, admin_client, merged):
        response = admin_client.patch(
            f"/api/cases/{merged.id}/move/", {"status": "Assigned"}, format="json"
        )
        assert response.status_code == 400
        assert response.json()["errors"] == {"status": [MERGED_STATUS_LOCKED]}
        _unchanged(merged)

    def test_move_of_an_unmerged_ticket_goes_through(self, admin_client, plain):
        response = admin_client.patch(
            f"/api/cases/{plain.id}/move/", {"status": "Assigned"}, format="json"
        )
        assert response.status_code == 200, response.content
        plain.refresh_from_db()
        assert plain.status == "Assigned"


class TestBulkUpdate:
    def test_merged_ticket_reported_and_left_alone(self, admin_client, merged, plain):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(merged.id), str(plain.id)], "fields": {"status": "Pending"}},
            format="json",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["updated"] == 1
        by_id = {r["id"]: r for r in body["results"]}
        assert by_id[str(merged.id)] == {
            "id": str(merged.id),
            "status": "merged",
            "detail": MERGED_STATUS_LOCKED,
        }
        assert by_id[str(plain.id)]["status"] == "updated"
        _unchanged(merged)
        plain.refresh_from_db()
        assert plain.status == "Pending"

    def test_a_non_status_bulk_edit_reaches_a_merged_ticket(self, admin_client, merged):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(merged.id)], "fields": {"priority": "Urgent"}},
            format="json",
        )
        assert response.json()["results"] == [
            {"id": str(merged.id), "status": "updated"}
        ]
        merged.refresh_from_db()
        assert merged.priority == "Urgent" and merged.status == "Duplicate"


class TestCloseWithChildren:
    def test_closing_a_merged_ticket_is_refused(self, admin_client, merged):
        response = admin_client.post(
            f"/api/cases/{merged.id}/close-with-children/",
            {"cascade": False},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["errors"] == {"status": [MERGED_STATUS_LOCKED]}
        _unchanged(merged)

    def test_the_cascade_passes_over_a_merged_child(
        self, admin_client, admin_user, org_a
    ):
        parent = _case(org_a, admin_user, "Incident")
        open_child = _case(org_a, admin_user, "Open child", parent=parent)
        survivor = _case(org_a, admin_user, "Survivor")
        merged_child = _case(org_a, admin_user, "Merged child", parent=parent)
        assert (
            admin_client.post(
                f"/api/cases/{merged_child.id}/merge/{survivor.id}/"
            ).status_code
            == 200
        )

        response = admin_client.post(
            f"/api/cases/{parent.id}/close-with-children/",
            {"cascade": True},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.json()["cascaded_case_ids"] == [str(open_child.id)]
        merged_child.refresh_from_db()
        assert merged_child.status == "Duplicate"
        open_child.refresh_from_db()
        assert open_child.status == "Closed"


class TestReopen:
    """A merged ticket left Closed by an edit before this rule existed is not
    reopened by a customer reply; an ordinary closed ticket still is."""

    def _closed(self, case, **extra):
        with rls_org(case.org):
            Case.objects.filter(pk=case.pk).update(
                status="Closed",
                closed_on=timezone.localdate() - timedelta(days=1),
                **extra,
            )
        case.refresh_from_db()
        return case

    def _customer_reply(self, case):
        with rls_org(case.org):
            Comment.objects.create(
                comment="Still broken",
                content_type=ContentType.objects.get_for_model(Case),
                object_id=case.pk,
                commented_by=None,
                is_internal=False,
                org=case.org,
            )
        case.refresh_from_db()

    def test_merged_and_closed_stays_closed(self, admin_user, org_a):
        survivor = _case(org_a, admin_user, "Survivor")
        legacy = self._closed(
            _case(org_a, admin_user, "Legacy merged"), merged_into=survivor
        )
        self._customer_reply(legacy)
        assert legacy.status == "Closed"

    def test_unmerged_and_closed_reopens(self, admin_user, org_a):
        closed = self._closed(_case(org_a, admin_user, "Closed"))
        self._customer_reply(closed)
        assert closed.status != "Closed"
