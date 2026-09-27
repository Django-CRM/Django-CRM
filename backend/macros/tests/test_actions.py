"""Macro actions (G18): what a macro stores, and what applying one does.

Applying goes through `cases.updates.update_case`, the ticket PATCH's own
write, so every gate a PATCH meets is met here too; these tests pin the ones a
macro can reach.
"""

from unittest.mock import patch

import pytest
from django.utils import timezone

from cases.approvals import Approval, ApprovalRule
from cases.models import Case, CaseWatcher
from common.models import Profile, Tags, User
from macros.models import Macro

LIST_URL = "/api/macros/"
MISSING = "00000000-0000-0000-0000-000000000000"


def _detail_url(pk):
    return f"/api/macros/{pk}/"


def _apply_url(pk):
    return f"/api/macros/{pk}/apply/"


def _member(org, email, *, active=True):
    user = User.objects.create_user(email=email, password="x")
    return Profile.objects.create(user=user, org=org, role="USER", is_active=active)


def _tag(org, name, *, active=True):
    return Tags.objects.create(org=org, name=name, is_active=active)


def _case(org, *, creator=None, status="New", priority="Normal", name="Printer"):
    case = Case.objects.create(org=org, name=name, status=status, priority=priority)
    if creator is not None:
        Case.objects.filter(pk=case.pk).update(created_by=creator)
        case.refresh_from_db()
    return case


def _macro(org, **fields):
    assignees = fields.pop("assignees", [])
    tags = fields.pop("tags", [])
    fields.setdefault("title", "Escalate")
    fields.setdefault("body", "")
    macro = Macro.objects.create(org=org, **fields)
    macro.set_assignees.set(assignees)
    macro.tags.set(tags)
    return macro


@pytest.fixture
def mailer():
    with patch("cases.updates.send_email_to_assigned_user") as task:
        yield task


# ---------------------------------------------------------------------------
# Storing actions
# ---------------------------------------------------------------------------


class TestMacroActionsCrud:
    def test_admin_creates_org_macro_with_every_action(self, admin_client, org_a):
        agent = _member(org_a, "agent@a.test")
        tag = _tag(org_a, "vip")
        resp = admin_client.post(
            LIST_URL,
            {
                "title": "Escalate",
                "body": "Passing this on.",
                "scope": "org",
                "set_status": "Pending",
                "set_priority": "Urgent",
                "set_assignees": [str(agent.id)],
                "add_tags": [str(tag.id)],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["set_status"] == "Pending"
        assert body["set_priority"] == "Urgent"
        assert body["set_assignees"] == [str(agent.id)]
        assert body["add_tags"] == [str(tag.id)]
        assert body["set_assignees_details"] == [
            {
                "id": str(agent.id),
                "email": "agent@a.test",
                "name": "agent",
                "is_active": True,
            }
        ]
        assert body["add_tags_details"][0]["name"] == "vip"

    def test_actions_only_macro_with_empty_body_is_accepted(self, user_client):
        resp = user_client.post(
            LIST_URL,
            {
                "title": "Close it",
                "body": "",
                "scope": "personal",
                "set_status": "Closed",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()

    def test_empty_body_and_no_action_is_refused(self, user_client):
        resp = user_client.post(
            LIST_URL,
            {"title": "Nothing", "body": "  ", "scope": "personal"},
            format="json",
        )
        assert resp.status_code == 400
        assert "body" in resp.json()

    def test_patch_cannot_strip_the_last_thing_a_macro_does(
        self, user_client, org_a, user_profile
    ):
        macro = _macro(org_a, scope="personal", owner=user_profile, set_priority="High")
        resp = user_client.patch(
            _detail_url(macro.id), {"set_priority": ""}, format="json"
        )
        assert resp.status_code == 400

    def test_duplicate_status_is_refused(self, user_client):
        resp = user_client.post(
            LIST_URL,
            {"title": "t", "body": "b", "scope": "personal", "set_status": "Duplicate"},
            format="json",
        )
        assert resp.status_code == 400
        assert "set_status" in resp.json()

    def test_off_enum_priority_is_refused(self, user_client):
        resp = user_client.post(
            LIST_URL,
            {"title": "t", "body": "b", "scope": "personal", "set_priority": "Now"},
            format="json",
        )
        assert resp.status_code == 400
        assert "set_priority" in resp.json()

    def test_another_orgs_profile_is_refused(self, user_client, profile_b):
        resp = user_client.post(
            LIST_URL,
            {
                "title": "t",
                "body": "b",
                "scope": "personal",
                "set_assignees": [str(profile_b.id)],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "set_assignees" in resp.json()

    def test_another_orgs_tag_is_refused(self, user_client, org_b):
        foreign = _tag(org_b, "theirs")
        resp = user_client.post(
            LIST_URL,
            {
                "title": "t",
                "body": "b",
                "scope": "personal",
                "add_tags": [str(foreign.id)],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "add_tags" in resp.json()

    def test_a_malformed_id_is_400_not_500(self, user_client):
        resp = user_client.post(
            LIST_URL,
            {"title": "t", "body": "b", "scope": "personal", "add_tags": ["nope"]},
            format="json",
        )
        assert resp.status_code == 400

    def test_a_deactivated_member_cannot_be_newly_named(self, user_client, org_a):
        gone = _member(org_a, "gone@a.test", active=False)
        resp = user_client.post(
            LIST_URL,
            {
                "title": "t",
                "body": "b",
                "scope": "personal",
                "set_assignees": [str(gone.id)],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "deactivated" in str(resp.json()["set_assignees"])

    def test_an_archived_tag_cannot_be_newly_added(self, user_client, org_a):
        old = _tag(org_a, "old", active=False)
        resp = user_client.post(
            LIST_URL,
            {
                "title": "t",
                "body": "b",
                "scope": "personal",
                "add_tags": [str(old.id)],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "add_tags" in resp.json()

    def test_a_stored_deactivated_assignee_and_archived_tag_survive_an_edit(
        self, user_client, org_a, user_profile
    ):
        gone = _member(org_a, "gone@a.test")
        old = _tag(org_a, "old")
        macro = _macro(
            org_a,
            scope="personal",
            owner=user_profile,
            body="b",
            assignees=[gone],
            tags=[old],
        )
        Profile.objects.filter(pk=gone.pk).update(is_active=False)
        Tags.objects.filter(pk=old.pk).update(is_active=False)
        resp = user_client.patch(
            _detail_url(macro.id),
            {
                "title": "Renamed",
                "set_assignees": [str(gone.id)],
                "add_tags": [str(old.id)],
            },
            format="json",
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["set_assignees_details"][0]["is_active"] is False
        assert resp.json()["add_tags_details"][0]["is_active"] is False

    def test_patch_without_the_relations_leaves_them(
        self, user_client, org_a, user_profile
    ):
        agent = _member(org_a, "agent@a.test")
        macro = _macro(
            org_a, scope="personal", owner=user_profile, body="b", assignees=[agent]
        )
        resp = user_client.patch(_detail_url(macro.id), {"title": "x"}, format="json")
        assert resp.status_code == 200
        assert list(macro.set_assignees.all()) == [agent]

    def test_member_cannot_edit_org_macro_actions(self, user_client, org_a):
        macro = _macro(org_a, scope="org", body="b")
        resp = user_client.patch(
            _detail_url(macro.id), {"set_status": "Closed"}, format="json"
        )
        assert resp.status_code == 403
        macro.refresh_from_db()
        assert macro.set_status == ""

    def test_admin_can_edit_org_macro_actions(self, admin_client, org_a):
        macro = _macro(org_a, scope="org", body="b")
        resp = admin_client.patch(
            _detail_url(macro.id), {"set_status": "Closed"}, format="json"
        )
        assert resp.status_code == 200
        macro.refresh_from_db()
        assert macro.set_status == "Closed"

    def test_list_carries_the_actions(self, user_client, org_a):
        agent = _member(org_a, "agent@a.test")
        _macro(org_a, scope="org", set_priority="High", assignees=[agent])
        row = user_client.get(LIST_URL).json()["results"][0]
        assert row["set_priority"] == "High"
        assert row["set_assignees_details"][0]["email"] == "agent@a.test"


# ---------------------------------------------------------------------------
# Applying actions
# ---------------------------------------------------------------------------


class TestApplyAccess:
    def test_writer_may_apply(self, user_client, org_a, regular_user, mailer):
        case = _case(org_a, creator=regular_user)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = user_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["applied"] == ["priority"]
        assert resp.json()["skipped"] == []
        case.refresh_from_db()
        assert case.priority == "Urgent"

    def test_admin_may_apply_to_any_org_ticket(self, admin_client, org_a, mailer):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Low")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200
        case.refresh_from_db()
        assert case.priority == "Low"

    def test_watcher_is_403_and_nothing_changes(self, user_client, org_a, user_profile):
        case = _case(org_a)
        CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = user_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 403
        case.refresh_from_db()
        assert case.priority == "Normal"

    def test_hidden_same_org_ticket_is_404_like_a_missing_one(self, user_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        hidden = user_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        missing = user_client.post(
            _apply_url(macro.id), {"case_id": MISSING}, format="json"
        )
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        case.refresh_from_db()
        assert case.priority == "Normal"

    def test_malformed_case_id_is_404(self, user_client, org_a):
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = user_client.post(
            _apply_url(macro.id), {"case_id": "nope"}, format="json"
        )
        assert resp.status_code == 404

    def test_other_orgs_ticket_is_404(self, admin_client, org_a, org_b):
        case = _case(org_b)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 404

    def test_someone_elses_personal_macro_is_404(
        self, user_client, org_a, regular_user, admin_profile
    ):
        case = _case(org_a, creator=regular_user)
        macro = _macro(
            org_a, scope="personal", owner=admin_profile, set_priority="Urgent"
        )
        resp = user_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 404

    def test_own_personal_macro_applies(
        self, user_client, org_a, regular_user, user_profile, mailer
    ):
        case = _case(org_a, creator=regular_user)
        macro = _macro(
            org_a, scope="personal", owner=user_profile, set_priority="Urgent"
        )
        resp = user_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200

    def test_inactive_macro_is_400(self, admin_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent", is_active=False)
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Macro is inactive."}

    def test_macro_with_no_actions_is_400(self, admin_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", body="Just words")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 400

    def test_case_id_is_required(self, admin_client, org_a):
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = admin_client.post(_apply_url(macro.id), {}, format="json")
        assert resp.status_code == 400

    def test_apply_does_not_count_a_use(self, admin_client, org_a, mailer):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        macro.refresh_from_db()
        assert macro.usage_count == 0


class TestApplyGates:
    def test_merged_ticket_status_is_400(self, admin_client, org_a):
        target = _case(org_a, name="Target")
        merged = _case(org_a, name="Merged", status="Duplicate")
        Case.objects.filter(pk=merged.pk).update(merged_into=target)
        macro = _macro(org_a, scope="org", set_status="Pending")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(merged.id)}, format="json"
        )
        assert resp.status_code == 400
        assert "Unmerge" in str(resp.json()["errors"])
        merged.refresh_from_db()
        assert merged.status == "Duplicate"

    def test_close_under_a_pre_close_rule_is_400(self, admin_client, org_a):
        ApprovalRule.objects.create(
            name="Close gate", org=org_a, trigger_event="pre_close", is_active=True
        )
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_status="Closed", set_priority="Low")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 400
        assert "approval" in str(resp.json()["errors"]["status"]).lower()
        case.refresh_from_db()
        # Refused whole: the priority in the same macro did not land either.
        assert (case.status, case.priority) == ("New", "Normal")

    def test_close_with_the_approval_recorded_goes_through(
        self, admin_client, org_a, admin_profile, mailer
    ):
        rule = ApprovalRule.objects.create(
            name="Close gate", org=org_a, trigger_event="pre_close", is_active=True
        )
        case = _case(org_a)
        Approval.objects.create(
            case=case,
            rule=rule,
            state="approved",
            requested_by=admin_profile,
            org=org_a,
        )
        macro = _macro(org_a, scope="org", set_status="Closed")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        case.refresh_from_db()
        assert case.status == "Closed"
        assert case.closed_on == timezone.localdate()

    def test_close_supplies_todays_date(self, admin_client, org_a, mailer):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_status="Closed")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        case.refresh_from_db()
        assert case.status == "Closed" and case.closed_on is not None

    def test_a_forced_duplicate_status_is_still_refused(self, admin_client, org_a):
        # The serializer refuses Duplicate; a row written around it still
        # meets the ticket's own rule, Duplicate only by merge.
        case = _case(org_a)
        macro = _macro(org_a, scope="org", body="b")
        Macro.objects.filter(pk=macro.pk).update(set_status="Duplicate")
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 400
        case.refresh_from_db()
        assert case.status == "New"


class TestApplyRelations:
    def test_assignees_replace_and_only_the_newly_added_are_emailed(
        self, admin_client, org_a, mailer, django_capture_on_commit_callbacks
    ):
        stays = _member(org_a, "stays@a.test")
        leaves = _member(org_a, "leaves@a.test")
        joins = _member(org_a, "joins@a.test")
        case = _case(org_a)
        case.assigned_to.add(stays, leaves)
        macro = _macro(org_a, scope="org", assignees=[stays, joins])
        with django_capture_on_commit_callbacks(execute=True):
            resp = admin_client.post(
                _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
            )
        assert resp.status_code == 200
        assert set(case.assigned_to.all()) == {stays, joins}
        mailer.delay.assert_called_once()
        assert mailer.delay.call_args[0][0] == [joins.id]

    def test_tags_are_appended(self, admin_client, org_a, mailer):
        keep = _tag(org_a, "keep")
        add = _tag(org_a, "add")
        case = _case(org_a)
        case.tags.add(keep)
        macro = _macro(org_a, scope="org", tags=[add])
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200
        assert set(case.tags.values_list("name", flat=True)) == {"keep", "add"}

    def test_a_deactivated_assignee_is_dropped_the_rest_apply(
        self, admin_client, org_a, mailer
    ):
        live = _member(org_a, "live@a.test")
        gone = _member(org_a, "gone@a.test")
        macro = _macro(org_a, scope="org", assignees=[live, gone])
        Profile.objects.filter(pk=gone.pk).update(is_active=False)
        case = _case(org_a)
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200
        assert list(case.assigned_to.all()) == [live]

    def test_only_deactivated_assignees_are_skipped_not_unassigned(
        self, admin_client, org_a, mailer
    ):
        current = _member(org_a, "current@a.test")
        gone = _member(org_a, "gone@a.test")
        macro = _macro(org_a, scope="org", set_priority="High", assignees=[gone])
        Profile.objects.filter(pk=gone.pk).update(is_active=False)
        case = _case(org_a)
        case.assigned_to.add(current)
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] == ["priority"]
        assert [s["action"] for s in body["skipped"]] == ["assignees"]
        assert list(case.assigned_to.all()) == [current]
        case.refresh_from_db()
        assert case.priority == "High"

    def test_only_archived_tags_are_skipped(self, admin_client, org_a, mailer):
        old = _tag(org_a, "old")
        macro = _macro(org_a, scope="org", tags=[old])
        Tags.objects.filter(pk=old.pk).update(is_active=False)
        case = _case(org_a)
        resp = admin_client.post(
            _apply_url(macro.id), {"case_id": str(case.id)}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == []
        assert resp.json()["skipped"][0]["action"] == "tags"
        assert case.tags.count() == 0


class TestApplyOnly:
    def test_only_the_kept_actions_apply(self, admin_client, org_a, mailer):
        agent = _member(org_a, "agent@a.test")
        case = _case(org_a)
        macro = _macro(
            org_a,
            scope="org",
            set_status="Pending",
            set_priority="Urgent",
            assignees=[agent],
        )
        resp = admin_client.post(
            _apply_url(macro.id),
            {"case_id": str(case.id), "only": ["priority"]},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == ["priority"]
        case.refresh_from_db()
        assert (case.status, case.priority) == ("New", "Urgent")
        assert case.assigned_to.count() == 0

    def test_an_unknown_action_name_is_400(self, admin_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = admin_client.post(
            _apply_url(macro.id),
            {"case_id": str(case.id), "only": ["delete"]},
            format="json",
        )
        assert resp.status_code == 400

    def test_only_not_a_list_is_400(self, admin_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = admin_client.post(
            _apply_url(macro.id),
            {"case_id": str(case.id), "only": "priority"},
            format="json",
        )
        assert resp.status_code == 400

    def test_nothing_left_after_only_is_400(self, admin_client, org_a):
        case = _case(org_a)
        macro = _macro(org_a, scope="org", set_priority="Urgent")
        resp = admin_client.post(
            _apply_url(macro.id),
            {"case_id": str(case.id), "only": ["status"]},
            format="json",
        )
        assert resp.status_code == 400
        case.refresh_from_db()
        assert case.priority == "Normal"


class TestMacroTagsAreTagUsage:
    """A macro that adds a tag is a use of it (`_TAGGABLE`), so the tag does
    not read as unused and a merge moves the macro onto the surviving tag."""

    def test_counted_in_usage(self, admin_client, org_a):
        tag = _tag(org_a, "vip")
        _macro(org_a, scope="org", tags=[tag])
        rows = admin_client.get("/api/tags/").json()["tags"]
        row = next(r for r in rows if r["id"] == str(tag.id))
        assert row["usage"]["macros"] == 1

    def test_merge_moves_the_macro(self, admin_client, org_a):
        source = _tag(org_a, "old")
        target = _tag(org_a, "new")
        macro = _macro(org_a, scope="org", tags=[source])
        resp = admin_client.post(
            f"/api/tags/{source.id}/merge/", {"into": str(target.id)}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        assert list(macro.tags.all()) == [target]
