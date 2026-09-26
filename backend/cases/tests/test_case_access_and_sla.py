"""Rules the cases module documented but did not enforce.

Every case below was driven against the running API before the fix and
answered the wrong thing. What was broken, in the order the classes appear:

1.  A non-admin got a **500** opening any ticket they were entitled to see:
    `users_mention` read `created_by.user.email`, but `created_by` is already
    a `User` and `Profile.user` is the FK pointing at one, so `User.user` does
    not exist. The guard above it compared a `Profile` to a `User` and was
    always true, so nothing shielded the line. For a helpdesk that meant no
    agent below admin could open a ticket at all.
2.  The watcher allowance was half-built. `CaseListView` and
    `watcher_views` granted it with a citation; the detail view dropped it.
    A watcher's queue showed the ticket and opening it answered 403.
3.  The verbs disagreed by accident: read, write and delete each had a
    different idea of who was allowed, spread over five copies.
4.  A missing case was a **500** on PUT / PATCH / DELETE (only GET checked
    for `None`), and a malformed id was a 500 on all four plus the comment
    and attachment endpoints, because a UUID column raises rather than
    returning nothing.
5.  `CaseAttachmentView.delete` looked its row up by pk with **no org
    filter**, one endpoint that deleted any attachment in the database,
    and its permission branch compared a `Profile` to a `User` FK, so the
    person who uploaded a file could not delete it.
6.  `account` accepted **another org's account** on create, stored the link,
    and echoed that account's name, email, phone and website back through the
    nested serializer.
7.  The close gate did not exist in practice. `Case.clean()` requires a
    `closed_on` and, where an active rule matches, a recorded approval, but
    DRF never calls `Model.clean()`. With a matching rule armed,
    `PATCH {"status": "Closed"}` returned 200 and recorded zero approvals.
8.  `resolved_at` was written by exactly one code path in the repo
    (`close_with_children`), so an ordinarily-closed case stayed NULL, and
    the property reads NULL as *not resolved*, leaving it permanently
    "resolution breached" and invisible to MTTR.
9.  `first_response_at` was written by **no** code path at all, while four
    things read it. Every case in every org was permanently first-response
    breached and the escalation scan re-fired on tickets answered hours ago.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import status

from accounts.models import Account
from cases.approvals import Approval, ApprovalRule
from cases.models import Case, CaseWatcher
from common.models import Attachments, Comment
from conftest import rls_org

CASES_URL = "/api/cases/"


def _detail(pk):
    return f"{CASES_URL}{pk}/"


def _attachment(pk):
    return f"{CASES_URL}attachment/{pk}/"


def _created_by(instance, user):
    """Set `created_by` after the fact.

    `BaseModel.save()` takes `created_by` from the crum thread-local and sets
    it to **None** when there is no request in flight, so passing it to
    `objects.create()` inside a test silently stores nothing. `.update()`
    writes the column directly and skips that.
    """
    type(instance).objects.filter(pk=instance.pk).update(created_by=user)
    instance.refresh_from_db()
    return instance


@pytest.fixture
def account_a(org_a):
    return Account.objects.create(name="Bluepeak Logistics", org=org_a)


@pytest.fixture
def account_b(org_b):
    with rls_org(org_b):
        return Account.objects.create(name="Someone Else Ltd", org=org_b)


@pytest.fixture
def assigned_case(case_a, user_profile):
    """A case the non-admin is assigned to but did not raise."""
    case_a.assigned_to.add(user_profile)
    return case_a


@pytest.fixture
def watched_case(case_a, user_profile, org_a):
    """A case the non-admin only watches, not creator, not assignee."""
    CaseWatcher.objects.create(
        case=case_a, profile=user_profile, org=org_a, subscribed_via="manual"
    )
    return case_a


@pytest.mark.django_db
class TestWhoMayOpenACase:
    def test_admin_opens_any_case_in_the_org(self, admin_client, case_a):
        response = admin_client.get(_detail(case_a.id))
        assert response.status_code == status.HTTP_200_OK

    def test_creator_opens_their_own_case(self, user_client, case_a, regular_user):
        """Was a 500: the creator branch reached the broken mention line."""
        _created_by(case_a, regular_user)
        response = user_client.get(_detail(case_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["cases_obj"]["id"] == str(case_a.id)

    def test_assignee_opens_a_case_they_did_not_raise(self, user_client, assigned_case):
        """Also a 500. Every non-admin hit the same line."""
        response = user_client.get(_detail(assigned_case.id))
        assert response.status_code == status.HTTP_200_OK

    def test_watcher_opens_the_case_their_queue_showed_them(
        self, user_client, watched_case
    ):
        """Was 403 while the list endpoint listed it. The list is right."""
        listing = user_client.get(CASES_URL)
        assert str(watched_case.id) in [c["id"] for c in listing.json()["cases"]]

        response = user_client.get(_detail(watched_case.id))
        assert response.status_code == status.HTTP_200_OK

    def test_unrelated_member_of_the_org_is_refused(self, user_client, case_a):
        """404 with a missing id's body, not 403, which confirmed it exists."""
        response = user_client.get(_detail(case_a.id))
        missing = user_client.get(_detail("00000000-0000-0000-0000-000000000000"))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert (response.status_code, response.json()) == (
            missing.status_code,
            missing.json(),
        )

    def test_another_org_gets_404_not_403(self, org_b_client, case_a):
        """404, not 403. Confirming a record exists is itself a disclosure."""
        response = org_b_client.get(_detail(case_a.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestReadWriteAndDeleteDifferOnPurpose:
    """The three rules are meant to be different widths. Prove each edge."""

    def test_watcher_may_read_but_not_write(self, user_client, watched_case):
        assert (
            user_client.get(_detail(watched_case.id)).status_code == status.HTTP_200_OK
        )
        patch = user_client.patch(
            _detail(watched_case.id), {"priority": "Low"}, format="json"
        )
        assert patch.status_code == status.HTTP_403_FORBIDDEN
        watched_case.refresh_from_db()
        assert watched_case.priority != "Low"

    def test_watcher_may_not_comment(self, user_client, watched_case):
        response = user_client.post(
            _detail(watched_case.id), {"comment": "hello"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_assignee_may_write_but_not_delete(self, user_client, assigned_case):
        patch = user_client.patch(
            _detail(assigned_case.id), {"priority": "Low"}, format="json"
        )
        assert patch.status_code == status.HTTP_200_OK
        assigned_case.refresh_from_db()
        assert assigned_case.priority == "Low"

        delete = user_client.delete(_detail(assigned_case.id))
        assert delete.status_code == status.HTTP_403_FORBIDDEN
        assert Case.objects.filter(pk=assigned_case.id).exists()

    def test_creator_may_delete(self, user_client, case_a, regular_user):
        _created_by(case_a, regular_user)
        response = user_client.delete(_detail(case_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert not Case.objects.filter(pk=case_a.id).exists()

    def test_comment_permission_flag_matches_the_endpoint(
        self, user_client, assigned_case
    ):
        """The flag said no while the endpoint said yes."""
        detail = user_client.get(_detail(assigned_case.id))
        assert detail.json()["comment_permission"] is True

        posted = user_client.post(
            _detail(assigned_case.id), {"comment": "on it"}, format="json"
        )
        assert posted.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestMissingAndMalformedIds:
    MALFORMED = "nobody"
    MISSING = "00000000-0000-4000-8000-000000000000"

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_get_is_404(self, admin_client, bad):
        assert admin_client.get(_detail(bad)).status_code == 404

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_patch_is_404(self, admin_client, bad):
        response = admin_client.patch(_detail(bad), {"name": "x"}, format="json")
        assert response.status_code == 404

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_put_is_404(self, admin_client, bad):
        response = admin_client.put(_detail(bad), {"name": "x"}, format="json")
        assert response.status_code == 404

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_delete_is_404(self, admin_client, bad):
        assert admin_client.delete(_detail(bad)).status_code == 404

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_comment_post_is_404(self, admin_client, bad):
        response = admin_client.post(_detail(bad), {"comment": "x"}, format="json")
        assert response.status_code == 404

    @pytest.mark.parametrize("bad", [MALFORMED, MISSING])
    def test_attachment_delete_is_404(self, admin_client, bad):
        assert admin_client.delete(_attachment(bad)).status_code == 404


@pytest.mark.django_db
class TestAttachmentDeleteIsScoped:
    def _attach(self, case, org, name="note.txt"):
        # `attachment` is org-scoped, so seeding one into the other tenant has
        # to happen as that tenant for the insert check to accept it.
        with rls_org(org):
            row = Attachments(file_name=name, org=org, content_object=case)
            row.attachment.save(name, ContentFile(b"data"), save=False)
            row.save()
            return row

    def test_cannot_delete_another_orgs_attachment(self, admin_client, case_b, org_b):
        foreign = self._attach(case_b, org_b, "theirs.txt")
        response = admin_client.delete(_attachment(foreign.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        # Proving the row survived means looking as its own tenant.
        with rls_org(org_b):
            assert Attachments.objects.filter(pk=foreign.id).exists()

    def test_uploader_can_delete_their_own(
        self, user_client, assigned_case, org_a, regular_user
    ):
        """Was 403: the check compared a Profile to a User FK."""
        mine = self._attach(assigned_case, org_a, "mine.txt")
        _created_by(mine, regular_user)
        response = user_client.delete(_attachment(mine.id))
        assert response.status_code == status.HTTP_200_OK
        assert not Attachments.objects.filter(pk=mine.id).exists()

    def test_someone_elses_upload_is_refused(
        self, user_client, watched_case, org_a, admin_user
    ):
        """403 for a reader of the ticket; one who cannot open it gets 404
        (`test_case_comment_attachment_scope`)."""
        theirs = self._attach(watched_case, org_a, "theirs-in-org.txt")
        _created_by(theirs, admin_user)
        response = user_client.delete(_attachment(theirs.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Attachments.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
class TestAccountLinkStaysInsideTheOrg:
    def test_foreign_account_is_rejected(self, admin_client, account_b):
        response = admin_client.post(
            CASES_URL,
            {
                "name": "Cross-org attempt",
                "status": "New",
                "priority": "Normal",
                "account": str(account_b.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "account" in response.json()["errors"]
        assert not Case.objects.filter(name="Cross-org attempt").exists()

    def test_own_account_is_accepted(self, admin_client, account_a):
        response = admin_client.post(
            CASES_URL,
            {
                "name": "Same-org link",
                "status": "New",
                "priority": "Normal",
                "account": str(account_a.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert Case.objects.get(name="Same-org link").account_id == account_a.id


@pytest.mark.django_db
class TestClosingACase:
    def test_closed_on_is_required(self, admin_client, case_a):
        response = admin_client.patch(
            _detail(case_a.id), {"status": "Closed"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "closed_on" in response.json()["errors"]
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_close_with_a_date_succeeds(self, admin_client, case_a):
        response = admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.status == "Closed"

    def test_matching_rule_blocks_the_close(self, admin_client, case_a, org_a):
        ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        response = admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "status" in response.json()["errors"]
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_an_approved_request_lets_it_through(
        self, admin_client, case_a, org_a, admin_profile
    ):
        rule = ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        Approval.objects.create(
            org=org_a,
            case=case_a,
            rule=rule,
            requested_by=admin_profile,
            state="approved",
        )
        response = admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.status == "Closed"

    def test_an_already_closed_case_can_still_be_edited(
        self, admin_client, case_a, org_a
    ):
        """The gate is on the transition, not on the state.

        Otherwise arming a rule would freeze every ticket that was closed
        before it existed.
        """
        case_a.status = "Closed"
        case_a.closed_on = timezone.localdate()
        case_a.save()
        ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        response = admin_client.patch(
            _detail(case_a.id), {"priority": "Low"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_a_rule_cannot_be_dodged_by_retargeting_in_the_same_request(
        self, admin_client, case_a, org_a
    ):
        """The rule matches on priority, so it is evaluated against the
        incoming values, not the stored ones a caller is about to replace."""
        case_a.priority = "High"
        case_a.save()
        ApprovalRule.objects.create(
            org=org_a,
            name="Urgent closes need sign-off",
            trigger_event="pre_close",
            match_priority="Urgent",
        )
        response = admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29", "priority": "Urgent"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        case_a.refresh_from_db()
        assert case_a.status == "New"


@pytest.mark.django_db
class TestResolvedAtIsRecorded:
    def test_closing_stamps_resolved_at(self, admin_client, case_a):
        assert case_a.resolved_at is None
        admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        case_a.refresh_from_db()
        assert case_a.resolved_at is not None
        assert case_a.is_sla_resolution_breached is False

    def test_reopening_clears_it(self, admin_client, case_a):
        admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        admin_client.patch(_detail(case_a.id), {"status": "New"}, format="json")
        case_a.refresh_from_db()
        assert case_a.status == "New"
        assert case_a.resolved_at is None

    def test_reopening_clears_the_closing_date_too(self, admin_client, case_a):
        """Otherwise the next close satisfies the gate with a stale date.

        The gate accepts a `closed_on` already on the record, which is what
        lets an edit to a closed ticket through. A ticket reopened while still
        holding last month's closing date would then close again on that date
        without anybody supplying one.
        """
        admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        admin_client.patch(_detail(case_a.id), {"status": "New"}, format="json")
        case_a.refresh_from_db()
        assert case_a.closed_on is None

        # And so closing it again has to name a date of its own.
        refused = admin_client.patch(
            _detail(case_a.id), {"status": "Closed"}, format="json"
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_second_edit_while_closed_does_not_move_it(self, admin_client, case_a):
        admin_client.patch(
            _detail(case_a.id),
            {"status": "Closed", "closed_on": "2026-07-29"},
            format="json",
        )
        case_a.refresh_from_db()
        first = case_a.resolved_at

        admin_client.patch(_detail(case_a.id), {"priority": "Low"}, format="json")
        case_a.refresh_from_db()
        assert case_a.resolved_at == first


@pytest.mark.django_db
class TestFirstResponseIsRecorded:
    def test_an_agent_reply_stamps_it(self, admin_client, case_a):
        assert case_a.first_response_at is None
        response = admin_client.post(
            _detail(case_a.id), {"comment": "Looking into it"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.first_response_at is not None
        assert case_a.is_sla_first_response_breached is False

    def test_an_internal_note_does_not(self, admin_client, case_a):
        """A note to the team is not a reply to the customer."""
        admin_client.post(
            _detail(case_a.id),
            {"comment": "Assigning to platform", "is_internal": "true"},
            format="json",
        )
        case_a.refresh_from_db()
        assert case_a.first_response_at is None

    def test_a_customer_comment_does_not(self, case_a, org_a):
        """A comment with no author is the customer's, per `_evaluate_reopen`."""
        Comment.objects.create(
            comment="Any update?",
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case_a.id,
            org=org_a,
        )
        case_a.refresh_from_db()
        assert case_a.first_response_at is None

    def test_the_second_reply_does_not_move_it(self, admin_client, case_a):
        admin_client.post(_detail(case_a.id), {"comment": "First"}, format="json")
        case_a.refresh_from_db()
        first = case_a.first_response_at

        admin_client.post(_detail(case_a.id), {"comment": "Second"}, format="json")
        case_a.refresh_from_db()
        assert case_a.first_response_at == first


@pytest.mark.django_db
class TestListOrderAndMentions:
    def test_newest_first(self, admin_client, case_a, case_b_same_org):
        """Ordering was `-id`, a random UUID, under a header that said
        the newest were on top."""
        Case.objects.filter(pk=case_a.id).update(
            created_at=timezone.now() - timezone.timedelta(days=3)
        )
        ids = [c["id"] for c in admin_client.get(CASES_URL).json()["cases"]]
        assert ids.index(str(case_b_same_org.id)) < ids.index(str(case_a.id))

    def test_mention_list_keeps_one_shape_for_every_role(
        self, admin_client, user_client, assigned_case, admin_user
    ):
        """The non-admin branch emitted `username` where the admin branch
        emitted `user__email`, so the shape changed with the reader."""
        _created_by(assigned_case, admin_user)

        as_admin = admin_client.get(_detail(assigned_case.id)).json()
        as_user = user_client.get(_detail(assigned_case.id)).json()

        for row in as_admin["users_mention"] + as_user["users_mention"]:
            assert set(row) == {"user__email"}
        assert as_user["users_mention"] == [{"user__email": admin_user.email}]
