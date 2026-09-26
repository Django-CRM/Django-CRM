"""`merged_from_cases` follows the ticket read rule on each source (D52).

The ticket detail GET listed the id, name and merge time of every ticket merged
into this one, filtered by org alone. A member who can read the surviving
ticket learned the subjects of source tickets the detail view answers 403 for.
A source the viewer cannot open now comes back as `{id, name: None, merged_at,
restricted: True}`, the rule `parent_summary` follows (D51). The entry stays,
so the list still counts every merge.

`user_client` is a plain member (role USER) whose user is `regular_user`.
"""

from __future__ import annotations

import pytest
from crum import impersonate
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from cases.models import Case, CaseWatcher
from conftest import rls_org

SECRET = "Secret payroll duplicate"


def _case(org, creator, name, *, merged_into=None):
    with impersonate(creator):
        with rls_org(org):
            return Case.objects.create(
                name=name,
                status="Closed" if merged_into else "New",
                priority="Normal",
                org=org,
                merged_into=merged_into,
                merged_at=timezone.now() if merged_into else None,
            )


def _entries(resp):
    assert resp.status_code == 200, resp.content
    return {e["id"]: e for e in resp.json()["merged_from_cases"]}


@pytest.mark.django_db
class TestMergedFrom:
    def test_member_who_cannot_read_a_source_gets_it_redacted(
        self, user_client, admin_user, regular_user, org_a
    ):
        target = _case(org_a, regular_user, "My ticket")
        hidden = _case(org_a, admin_user, SECRET, merged_into=target)
        mine = _case(org_a, regular_user, "My duplicate", merged_into=target)
        # The source really is hidden from this member.
        assert (
            user_client.get(f"/api/cases/{hidden.id}/?show_merged=true").status_code
            == 403
        )

        resp = user_client.get(f"/api/cases/{target.id}/")
        entries = _entries(resp)
        # Kept, not dropped: the list still counts both merges.
        assert len(entries) == 2
        redacted = entries[str(hidden.id)]
        assert redacted["name"] is None
        assert redacted["restricted"] is True
        assert redacted["merged_at"] is not None
        # `can_unmerge` is the only field added since, and a source the
        # member cannot open is never one they may unmerge.
        assert set(redacted) == {
            "id",
            "name",
            "merged_at",
            "restricted",
            "can_unmerge",
        }
        assert redacted["can_unmerge"] is False
        assert entries[str(mine.id)]["name"] == "My duplicate"
        assert entries[str(mine.id)]["restricted"] is False
        # Nowhere in the body, not only absent from this field.
        assert SECRET.encode() not in resp.content

    def test_member_who_can_read_the_source_gets_its_name(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        target = _case(org_a, regular_user, "My ticket")
        source = _case(org_a, admin_user, "Watched duplicate", merged_into=target)
        # Watching is enough to read, so it is enough to see the name.
        CaseWatcher.objects.create(case=source, profile=user_profile, org=org_a)

        entry = _entries(user_client.get(f"/api/cases/{target.id}/"))[str(source.id)]
        assert entry["name"] == "Watched duplicate"
        assert entry["restricted"] is False

    def test_admin_gets_every_name(self, admin_client, regular_user, org_a):
        target = _case(org_a, regular_user, "Member ticket")
        source = _case(org_a, regular_user, SECRET, merged_into=target)

        resp = admin_client.get(f"/api/cases/{target.id}/")
        entry = _entries(resp)[str(source.id)]
        assert entry["name"] == SECRET
        assert entry["restricted"] is False

    def test_readable_set_is_one_query_however_many_sources(
        self, user_client, admin_user, regular_user, org_a
    ):
        target = _case(org_a, regular_user, "My ticket")
        _case(org_a, admin_user, "Source 0", merged_into=target)
        with CaptureQueriesContext(connection) as one:
            assert user_client.get(f"/api/cases/{target.id}/").status_code == 200

        for i in range(1, 5):
            _case(
                org_a,
                admin_user if i % 2 else regular_user,
                f"Source {i}",
                merged_into=target,
            )
        with CaptureQueriesContext(connection) as five:
            resp = user_client.get(f"/api/cases/{target.id}/")
        assert len(_entries(resp)) == 5
        assert len(five) == len(one)
