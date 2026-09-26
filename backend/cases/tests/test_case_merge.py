"""Tests for the Tier-1 ticket merge feature.

Covers `CaseMergeView`, `CaseDetailView`'s redirect short-circuit, the cases
list/kanban default exclusion of Duplicate status, and the inbound threading
matcher's follow-merge behavior.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from cases.models import Case, EmailMessage
from common.models import Activity, Attachments, Comment
from conftest import rls_org


def _create_comment(case, profile, body="hello"):
    return Comment.objects.create(
        comment=body,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        commented_by=profile,
        org=case.org,
    )


def _create_attachment(case, profile, name="file.txt"):
    return Attachments.objects.create(
        file_name=name,
        attachment=f"attachments/test/{name}",
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        created_by=profile.user,
        org=case.org,
    )


def _make_case(
    org,
    creator,
    *,
    name="Sample case",
    status_value="New",
    priority="Normal",
    external_thread_id=None,
):
    """Create a Case with `created_by` set deterministically.

    `BaseModel.save()` reads `crum.get_current_user()` to populate
    `created_by`: outside a request that's None, which would break the
    permission tests. Set the current user explicitly for the duration of
    the create.
    """
    from crum import impersonate

    with impersonate(creator):
        # Seeded into another tenant: the insert-check policy compares
        # against `app.current_org`, so writing this row means being that
        # tenant for the length of the write.
        with rls_org(org):
            return Case.objects.create(
                name=name,
                status=status_value,
                priority=priority,
                external_thread_id=external_thread_id,
                org=org,
            )


@pytest.mark.django_db
class TestCaseMergeHappyPath:
    def test_merge_repoints_comments_attachments_and_emails(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(
            org_a,
            admin_user,
            name="Duplicate",
            external_thread_id="<dup-thread@example.com>",
        )
        _create_comment(duplicate, admin_profile, body="dup comment")
        _create_attachment(duplicate, admin_profile, name="dup.txt")
        EmailMessage.objects.create(
            org=org_a,
            case=duplicate,
            direction="inbound",
            message_id="<msg-1@example.com>",
            from_address="customer@example.com",
            received_at=timezone.now(),
        )

        response = admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["error"] is False
        # `/tickets/`, not `/cases/`: this is a path a client has to be able to
        # route, and no client has ever served `/cases/`. Built by
        # `cases.notifications.case_link`, which is also what the notification
        # producer uses, so the two cannot drift apart again.
        assert body["redirected_url"] == f"/tickets/{primary.id}"
        assert body["source_case_id"] == str(duplicate.id)

        case_ct = ContentType.objects.get_for_model(Case)
        assert (
            Comment.objects.filter(content_type=case_ct, object_id=primary.id).count()
            == 1
        )
        assert (
            Attachments.objects.filter(
                content_type=case_ct, object_id=primary.id
            ).count()
            == 1
        )
        assert EmailMessage.objects.filter(case=primary).count() == 1
        assert (
            Comment.objects.filter(content_type=case_ct, object_id=duplicate.id).count()
            == 0
        )

        primary.refresh_from_db()
        duplicate.refresh_from_db()
        assert duplicate.merged_into_id == primary.id
        assert duplicate.merged_by_id == admin_profile.id
        assert duplicate.merged_at is not None
        assert duplicate.status == "Duplicate"
        assert duplicate.closed_on is not None
        assert "<dup-thread@example.com>" in primary.alt_thread_ids

        verbs = list(
            Activity.objects.filter(entity_type="Case").values_list("action", flat=True)
        )
        assert verbs.count("MERGE_TARGET") == 1
        assert verbs.count("MERGED") == 1


@pytest.mark.django_db
class TestCaseMergeIdempotency:
    def test_second_call_returns_already_merged(self, admin_client, admin_user, org_a):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")

        first = admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")
        assert first.status_code == 200
        assert first.json().get("already_merged") is not True

        second = admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")
        assert second.status_code == 200
        assert second.json()["already_merged"] is True

    def test_merging_into_different_target_rejected(
        self, admin_client, admin_user, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        other = _make_case(org_a, admin_user, name="Other primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")

        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")
        retry = admin_client.post(f"/api/cases/{duplicate.id}/merge/{other.id}/")
        assert retry.status_code == 400


@pytest.mark.django_db
class TestCaseMergeValidation:
    def test_self_merge_rejected(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user)
        response = admin_client.post(f"/api/cases/{case.id}/merge/{case.id}/")
        assert response.status_code == 400

    def test_chain_rejected_target_already_merged(
        self, admin_client, admin_user, org_a
    ):
        a = _make_case(org_a, admin_user, name="A")
        b = _make_case(org_a, admin_user, name="B")
        c = _make_case(org_a, admin_user, name="C")
        # Merge B → C first.
        first = admin_client.post(f"/api/cases/{b.id}/merge/{c.id}/")
        assert first.status_code == 200
        # Now A → B should fail because B has been merged into C.
        chain = admin_client.post(f"/api/cases/{a.id}/merge/{b.id}/")
        assert chain.status_code == 400

    def test_cross_org_merge_404s(self, admin_client, admin_user, user_b, org_a, org_b):
        local = _make_case(org_a, admin_user, name="Local")
        remote = _make_case(org_b, user_b, name="Remote")
        response = admin_client.post(f"/api/cases/{local.id}/merge/{remote.id}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestCaseMergePermissions:
    def test_admin_can_merge(self, admin_client, admin_user, org_a):
        a = _make_case(org_a, admin_user, name="A")
        b = _make_case(org_a, admin_user, name="B")
        response = admin_client.post(f"/api/cases/{a.id}/merge/{b.id}/")
        assert response.status_code == 200

    def test_creator_of_both_can_merge(self, user_client, regular_user, org_a):
        a = _make_case(org_a, regular_user, name="A")
        b = _make_case(org_a, regular_user, name="B")
        response = user_client.post(f"/api/cases/{a.id}/merge/{b.id}/")
        assert response.status_code == 200

    def test_creator_of_only_one_forbidden(
        self, user_client, regular_user, admin_user, org_a
    ):
        a = _make_case(org_a, regular_user, name="A. User-created")
        b = _make_case(org_a, admin_user, name="B, admin-created")
        response = user_client.post(f"/api/cases/{a.id}/merge/{b.id}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseDetailRedirect:
    def test_merged_duplicate_returns_redirect_payload(
        self, admin_client, admin_user, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        response = admin_client.get(f"/api/cases/{duplicate.id}/")
        assert response.status_code == 200
        body = response.json()
        assert body.get("redirect_to") == str(primary.id)
        assert body.get("source_case_id") == str(duplicate.id)
        # No full case payload when redirecting.
        assert "cases_obj" not in body

    def test_show_merged_query_param_returns_full_payload(
        self, admin_client, admin_user, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        response = admin_client.get(f"/api/cases/{duplicate.id}/?show_merged=true")
        assert response.status_code == 200
        body = response.json()
        assert "cases_obj" in body
        assert body.get("redirect_to") is None or "redirect_to" not in body

    def test_primary_includes_merged_from_list(self, admin_client, admin_user, org_a):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        response = admin_client.get(f"/api/cases/{primary.id}/")
        assert response.status_code == 200
        body = response.json()
        merged_from = body.get("merged_from_cases") or []
        assert len(merged_from) == 1
        assert str(merged_from[0]["id"]) == str(duplicate.id)
        assert merged_from[0]["name"] == "Duplicate"


@pytest.mark.django_db
class TestCaseListHidesDuplicates:
    def test_list_excludes_merged_duplicates_by_default(
        self, admin_client, admin_user, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        response = admin_client.get("/api/cases/")
        assert response.status_code == 200
        body = response.json()
        ids = {row["id"] for row in body["cases"]}
        assert str(primary.id) in ids
        assert str(duplicate.id) not in ids

    def test_list_show_merged_includes_duplicates(
        self, admin_client, admin_user, org_a
    ):
        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        response = admin_client.get("/api/cases/?show_merged=true")
        assert response.status_code == 200
        body = response.json()
        ids = {row["id"] for row in body["cases"]}
        assert str(primary.id) in ids
        assert str(duplicate.id) in ids


@pytest.mark.django_db
class TestInboundMatcherFollowsMerge:
    def test_follow_merged_into_for_external_thread_match(
        self, admin_user, admin_client, org_a
    ):
        from cases.inbound.parser import ParsedEmail
        from cases.inbound.threading import find_existing_case

        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(
            org_a,
            admin_user,
            name="Duplicate",
            external_thread_id="<dup-original@example.com>",
        )
        # Merge: alt_thread_ids on primary inherits the dup's thread id.
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")

        parsed = ParsedEmail(
            raw_headers={},
            message_id="<reply-1@example.com>",
            in_reply_to="<dup-original@example.com>",
            references=["<dup-original@example.com>"],
            from_address="customer@example.com",
            from_display_name="Customer",
            to_addresses=["support@example.com"],
            cc_addresses=[],
            subject="Re: still broken",
            body_text="ping",
            body_html="",
            received_at=datetime.now(dt_timezone.utc),
        )
        matched = find_existing_case(parsed, org_a)
        assert matched is not None
        assert matched.case.id == primary.id
        assert matched.by_header is True

    def test_follow_merged_into_for_email_message_match(
        self, admin_user, admin_client, admin_profile, org_a
    ):
        from cases.inbound.parser import ParsedEmail
        from cases.inbound.threading import find_existing_case

        primary = _make_case(org_a, admin_user, name="Primary")
        duplicate = _make_case(org_a, admin_user, name="Duplicate")
        EmailMessage.objects.create(
            org=org_a,
            case=duplicate,
            direction="inbound",
            message_id="<dup-msg@example.com>",
            from_address="customer@example.com",
            received_at=timezone.now(),
        )
        admin_client.post(f"/api/cases/{duplicate.id}/merge/{primary.id}/")
        # Post-merge, the EmailMessage row is now attached to the primary;
        # the matcher should still resolve the In-Reply-To to the primary.

        parsed = ParsedEmail(
            raw_headers={},
            message_id="<reply-2@example.com>",
            in_reply_to="<dup-msg@example.com>",
            references=[],
            from_address="customer@example.com",
            from_display_name="Customer",
            to_addresses=["support@example.com"],
            cc_addresses=[],
            subject="Re: still broken",
            body_text="ping",
            body_html="",
            received_at=datetime.now(dt_timezone.utc),
        )
        matched = find_existing_case(parsed, org_a)
        assert matched is not None
        assert matched.case.id == primary.id
        assert matched.by_header is True
