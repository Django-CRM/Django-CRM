"""Two activity feeds, two shapes, and neither may take the other's name.

`common/serializer.py` defined `ActivitySerializer` twice, 580 lines apart.
Python keeps the second, so both consumers received the dashboard shape:

* the dashboard feed wants `timestamp` and `humanized_time`, and got them
* the ticket activity feed wants `metadata` and `created_at`, and got
  neither, so the ticket timeline of the day rendered no metadata and an
  invalid date. Today the case detail's `activities` are read by the web
  ticket page (`getTicket` in `lib/server/v2/tickets.js`: `created_at`,
  `action_display`, `user`) and the mobile ticket screen (`metadata`,
  `created_at`)

Only one of the pair broke, which is why it survived: the dashboard, the
louder surface, was the one being served correctly. The second class is now
`DashboardActivitySerializer`.

These tests assert the field sets rather than a rendered page, because the
field set is what the two frontends destructure and it is what a rename or a
merge of the two classes would quietly change.
"""

from __future__ import annotations

import pytest
from crum import impersonate

from cases.models import Case
from common.models import Activity
from common.serializer import ActivitySerializer, DashboardActivitySerializer

# What the ticket activity readers (web ticket page, mobile ticket screen)
# take off each row.
TIMELINE_REQUIRED = {"id", "action", "user", "metadata", "created_at"}

# What the dashboard feed renders.
DASHBOARD_REQUIRED = {"id", "action", "action_display", "timestamp", "humanized_time"}


class TestTheTwoSerializersAreDistinct:
    def test_they_are_not_the_same_class(self):
        assert ActivitySerializer is not DashboardActivitySerializer

    def test_the_timeline_serializer_carries_what_the_timeline_reads(self):
        fields = set(ActivitySerializer().fields)
        missing = TIMELINE_REQUIRED - fields
        assert not missing, f"ticket timeline would read undefined fields: {missing}"

    def test_the_dashboard_serializer_carries_what_the_dashboard_reads(self):
        fields = set(DashboardActivitySerializer().fields)
        missing = DASHBOARD_REQUIRED - fields
        assert not missing, f"dashboard feed would read undefined fields: {missing}"

    def test_the_shapes_genuinely_differ(self):
        """If these ever converge, one class is enough and this file is wrong.

        Written as an assertion rather than a comment so that merging them
        becomes a deliberate act with a failing test to delete, not something
        that happens by accident a second time.
        """
        assert set(ActivitySerializer().fields) != set(
            DashboardActivitySerializer().fields
        )


@pytest.mark.django_db
class TestTheCaseActivityEndpointServesTheTimelineShape:
    """The endpoint, not just the class: `cases/views.py` imports by name."""

    def test_activity_rows_carry_metadata_and_created_at(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        with impersonate(admin_user):
            case = Case.objects.create(
                name="Leaking valve", org=org_a, status="New", priority="Normal"
            )
            Activity.objects.create(
                org=org_a,
                user=admin_profile,
                action="COMMENT",
                entity_type="Case",
                entity_id=case.id,
                entity_name=case.name,
                description="left a note",
                metadata={"visibility_changed": True, "after": False},
            )

        response = admin_client.get(f"/api/cases/{case.id}/activities/")

        assert response.status_code == 200
        row = response.data["activities"][0]
        assert row["metadata"] == {"visibility_changed": True, "after": False}
        assert row["created_at"] is not None
