"""What the task endpoints let people do, and what they must not.

Everything here was driven against the running server before it was written
down, and every defect it pins was reproduced live first. The list, in the
order a person would meet them:

1.  **A member could create a task and then never open it again.** `POST`
    succeeded, the task appeared in their list, and `GET` answered 500, with
    `PATCH`, comment and `DELETE` all answering 403. `created_by` is a `User`
    and every check compared it to a `Profile`, so the creator clause was dead,
    except in the list query, which spelled it correctly and therefore
    listed tasks the detail view refused.
2.  **No non-admin could open *any* task, including their own.**
    `created_by.user.email`, `created_by` is already the user, threw
    `AttributeError` behind an always-true branch.
3.  **The 403 path was a 500.** `get_context_data` returned a `Response`,
    which `get()` wrapped in a second one: "Object of type Response is not
    JSON serializable".
4.  **Any admin could delete any attachment in the database.** `Attachments`
    is one generic table and the lookup had no `org=`. Proven by destroying a
    file that belonged to another org and hung off a lead, not a task.
5.  **The uploader could not delete their own attachment**: same
    `Profile`/`User` comparison.
6.  **`created_by` was writable.** A task could be handed a creator from
    another org. Harmless only while the field was never read; the moment
    fix (1) lands it is a route to `delete`.
7.  **The four parent FKs were unscoped.** A task could be created holding
    another org's account, and the list rendered that org's account name back.
8.  **"One parent entity" did not survive `PATCH`.** Sending a second parent
    one request at a time walked past the serializer, and the model's own
    refusal surfaced as a 500 because Django's `ValidationError` is not DRF's.
9.  **Every malformed id was a 500**: task, comment and attachment, on
    every verb, and a well-formed id for a missing comment or attachment too.
"""

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import Account
from cases.models import Case
from common.models import Attachments, Profile, User
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task


def _task(org, creator, assignees=(), **kwargs):
    """A task owned by ``creator``.

    ``BaseModel.save()`` stamps ``created_by`` from the crum thread-local,
    which is empty in a test, so passing it to ``create()`` is silently
    dropped. ``.update()`` goes round ``save()`` and actually sets it, the
    same trick the contacts and cases suites needed.
    """
    task = Task.objects.create(
        title=kwargs.pop("title", "A task"),
        status=kwargs.pop("status", "New"),
        priority=kwargs.pop("priority", "Medium"),
        org=org,
        **kwargs,
    )
    Task.objects.filter(pk=task.pk).update(created_by=creator)
    task.assigned_to.set(assignees)
    task.refresh_from_db()
    return task


@pytest.fixture
def other_user():
    return User.objects.create_user(email="other@test.com", password="testpass123")


@pytest.fixture
def other_profile(other_user, org_a):
    """A second plain member of org A: the person on neither side of a task."""
    return Profile.objects.create(
        user=other_user, org=org_a, role="USER", is_active=True
    )


@pytest.fixture
def other_client(other_user, org_a, other_profile):
    from conftest import _make_authenticated_client

    return _make_authenticated_client(other_user, org_a, other_profile)


@pytest.mark.django_db
class TestWhoMayOpenATask:
    """`access`: admin, creator, assignee. Each clause, both directions."""

    def test_the_creator_can_open_their_own_task(
        self, user_client, regular_user, admin_user, org_a
    ):
        """The headline. This was a 500, which is why the rest of the file exists."""
        task = _task(org_a, regular_user)
        response = user_client.get(f"/api/tasks/{task.id}/")
        assert response.status_code == 200
        assert response.json()["task_obj"]["title"] == "A task"

    def test_an_assignee_can_open_a_task_somebody_else_made(
        self, user_client, user_profile, admin_user, org_a
    ):
        task = _task(org_a, admin_user, assignees=[user_profile])
        assert user_client.get(f"/api/tasks/{task.id}/").status_code == 200

    def test_a_member_on_neither_side_is_refused(
        self, other_client, admin_user, user_profile, org_a
    ):
        task = _task(org_a, admin_user, assignees=[user_profile])
        assert other_client.get(f"/api/tasks/{task.id}/").status_code == 403

    def test_the_refusal_is_a_403_and_not_a_500(self, other_client, admin_user, org_a):
        """`get_context_data` used to *return* a Response, which `get()` then
        wrapped in another one. The denial branch never rendered."""
        task = _task(org_a, admin_user)
        response = other_client.get(f"/api/tasks/{task.id}/")
        assert response.status_code == 403
        assert "Permission" in str(response.json())

    def test_an_admin_can_open_a_task_they_have_nothing_to_do_with(
        self, admin_client, regular_user, org_a
    ):
        task = _task(org_a, regular_user)
        assert admin_client.get(f"/api/tasks/{task.id}/").status_code == 200

    def test_another_org_gets_404_not_403(self, org_b_client, admin_user, org_a):
        """Not "you may not", which would confirm the task exists."""
        task = _task(org_a, admin_user)
        assert org_b_client.get(f"/api/tasks/{task.id}/").status_code == 404


@pytest.mark.django_db
class TestTheListAndTheDetailAgree:
    """A queue that lists what it will not open is the bug this module fixes."""

    def test_everything_a_member_can_list_they_can_also_open(
        self, user_client, regular_user, admin_user, user_profile, org_a
    ):
        _task(org_a, regular_user, title="Mine, made by me")
        _task(org_a, admin_user, assignees=[user_profile], title="Mine, handed to me")
        _task(org_a, admin_user, title="Not mine at all")

        listed = user_client.get("/api/tasks/").json()["tasks"]
        assert {row["title"] for row in listed} == {
            "Mine, made by me",
            "Mine, handed to me",
        }
        for row in listed:
            assert user_client.get(f"/api/tasks/{row['id']}/").status_code == 200

    def test_an_admin_sees_the_whole_org(
        self, admin_client, regular_user, admin_user, org_a
    ):
        _task(org_a, regular_user, title="Theirs")
        _task(org_a, admin_user, title="Mine")
        assert admin_client.get("/api/tasks/").json()["tasks_count"] == 2

    def test_the_list_stops_at_the_org_boundary(
        self, admin_client, admin_user, org_a, org_b
    ):
        _task(org_a, admin_user, title="Ours")
        _task(org_b, admin_user, title="Theirs")
        titles = [t["title"] for t in admin_client.get("/api/tasks/").json()["tasks"]]
        assert titles == ["Ours"]


@pytest.mark.django_db
class TestWritingAndDeletingDifferOnPurpose:
    """`access` covers reading and writing; `delete` is narrower.

    Being handed a task is a reason to work it, not a reason to erase it, the
    same line `cases.access` draws for an assignee, and it has to be asserted
    separately or a later tidy-up will collapse the two.
    """

    def test_an_assignee_may_edit(self, user_client, user_profile, admin_user, org_a):
        task = _task(org_a, admin_user, assignees=[user_profile])
        response = user_client.patch(
            f"/api/tasks/{task.id}/", {"priority": "High"}, format="json"
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.priority == "High"

    def test_an_assignee_may_not_delete(
        self, user_client, user_profile, admin_user, org_a
    ):
        task = _task(org_a, admin_user, assignees=[user_profile])
        assert user_client.delete(f"/api/tasks/{task.id}/").status_code == 403
        assert Task.objects.filter(pk=task.pk).exists()

    def test_the_creator_may_delete(self, user_client, regular_user, org_a):
        task = _task(org_a, regular_user)
        assert user_client.delete(f"/api/tasks/{task.id}/").status_code == 200
        assert not Task.objects.filter(pk=task.pk).exists()

    def test_an_admin_may_delete_anything_in_the_org(
        self, admin_client, regular_user, org_a
    ):
        task = _task(org_a, regular_user)
        assert admin_client.delete(f"/api/tasks/{task.id}/").status_code == 200

    def test_a_member_on_neither_side_may_not_edit(
        self, other_client, admin_user, org_a
    ):
        task = _task(org_a, admin_user)
        response = other_client.patch(
            f"/api/tasks/{task.id}/", {"priority": "High"}, format="json"
        )
        assert response.status_code == 403

    def test_a_member_on_neither_side_may_not_comment(
        self, other_client, admin_user, org_a
    ):
        task = _task(org_a, admin_user)
        response = other_client.post(
            f"/api/tasks/{task.id}/", {"comment": "hello"}, format="json"
        )
        assert response.status_code == 403

    def test_the_creator_can_run_the_whole_loop(self, user_client, regular_user, org_a):
        """Create, open, edit, comment, delete, as one plain member.

        Before the fix this read 200 / 500 / 403 / 403 / 403: a create endpoint
        that handed every non-admin a task they could never touch again.
        """
        created = user_client.post(
            "/api/tasks/",
            {"title": "Ring the customer", "status": "New", "priority": "Low"},
            format="json",
        )
        assert created.status_code == 200
        task = Task.objects.get(title="Ring the customer")
        assert task.created_by == regular_user

        assert user_client.get(f"/api/tasks/{task.id}/").status_code == 200
        assert (
            user_client.patch(
                f"/api/tasks/{task.id}/", {"status": "In Progress"}, format="json"
            ).status_code
            == 200
        )
        assert (
            user_client.post(
                f"/api/tasks/{task.id}/", {"comment": "left a voicemail"}, format="json"
            ).status_code
            == 200
        )
        assert user_client.delete(f"/api/tasks/{task.id}/").status_code == 200


@pytest.mark.django_db
class TestCreatedByIsServerDerived:
    """It decides `delete`, so it cannot come from the request body."""

    def test_a_client_cannot_name_the_creator_on_create(
        self, user_client, regular_user, admin_user, org_a
    ):
        user_client.post(
            "/api/tasks/",
            {
                "title": "Claimed",
                "status": "New",
                "priority": "Low",
                "created_by": str(admin_user.id),
            },
            format="json",
        )
        assert Task.objects.get(title="Claimed").created_by == regular_user

    def test_a_client_cannot_rewrite_the_creator_on_update(
        self, user_client, user_profile, regular_user, admin_user, org_a
    ):
        """An assignee may edit the task. If they could also edit this field
        they would be granting themselves the right to delete it."""
        task = _task(org_a, admin_user, assignees=[user_profile])
        response = user_client.patch(
            f"/api/tasks/{task.id}/",
            {"created_by": str(regular_user.id)},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.created_by == admin_user
        assert user_client.delete(f"/api/tasks/{task.id}/").status_code == 403

    def test_a_client_cannot_borrow_a_creator_from_another_org(
        self, admin_client, admin_user, user_b, org_a
    ):
        task = _task(org_a, admin_user)
        admin_client.patch(
            f"/api/tasks/{task.id}/", {"created_by": str(user_b.id)}, format="json"
        )
        task.refresh_from_db()
        assert task.created_by == admin_user


@pytest.mark.django_db
class TestParentsAreScopedToTheOrg:
    """A task links to one account, opportunity, case or lead, one of *ours*.

    Every one of these was a 200 before, and the list endpoint then rendered
    the other org's record name back to the requester.
    """

    def test_an_account_from_another_org_is_refused(self, admin_client, org_a, org_b):
        foreign = Account.objects.create(name="Their Co", org=org_b)
        response = admin_client.post(
            "/api/tasks/",
            {
                "title": "Reach out",
                "status": "New",
                "priority": "Low",
                "account": str(foreign.id),
            },
            format="json",
        )
        assert response.status_code == 400
        assert "account" in response.json()["errors"]
        assert not Task.objects.filter(title="Reach out").exists()

    def test_a_lead_from_another_org_is_refused(self, admin_client, org_a, org_b):
        foreign = Lead.objects.create(first_name="Their", last_name="Lead", org=org_b)
        response = admin_client.post(
            "/api/tasks/",
            {
                "title": "Chase",
                "status": "New",
                "priority": "Low",
                "lead": str(foreign.id),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_case_from_another_org_is_refused_on_update(
        self, admin_client, admin_user, org_a, org_b
    ):
        task = _task(org_a, admin_user)
        foreign = Case.objects.create(name="Their ticket", status="New", org=org_b)
        response = admin_client.patch(
            f"/api/tasks/{task.id}/", {"case": str(foreign.id)}, format="json"
        )
        assert response.status_code == 400
        task.refresh_from_db()
        assert task.case_id is None

    def test_an_opportunity_from_our_own_org_is_accepted(
        self, admin_client, admin_user, org_a
    ):
        """The refusals above have to be about the org and not about the field."""
        ours = Opportunity.objects.create(name="Ours", stage="QUALIFICATION", org=org_a)
        task = _task(org_a, admin_user)
        response = admin_client.patch(
            f"/api/tasks/{task.id}/", {"opportunity": str(ours.id)}, format="json"
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.opportunity_id == ours.id


@pytest.mark.django_db
class TestOneParentEntity:
    """The model has always said so; the API only enforced it per request."""

    def test_two_parents_in_one_request_are_refused(self, admin_client, org_a):
        account = Account.objects.create(name="Acme", org=org_a)
        lead = Lead.objects.create(first_name="Jo", last_name="Blow", org=org_a)
        response = admin_client.post(
            "/api/tasks/",
            {
                "title": "Both",
                "status": "New",
                "priority": "Low",
                "account": str(account.id),
                "lead": str(lead.id),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_second_parent_added_later_is_refused(
        self, admin_client, admin_user, org_a
    ):
        """One at a time used to look like a single-parent request and pass.

        It then hit `Task.clean()` inside `save()` and came back as a 500,
        because Django's ValidationError is not DRF's.
        """
        account = Account.objects.create(name="Acme", org=org_a)
        lead = Lead.objects.create(first_name="Jo", last_name="Blow", org=org_a)
        task = _task(org_a, admin_user, account=account)

        response = admin_client.patch(
            f"/api/tasks/{task.id}/", {"lead": str(lead.id)}, format="json"
        )
        assert response.status_code == 400
        assert "one parent entity" in str(response.json()["errors"])
        task.refresh_from_db()
        assert task.lead_id is None
        assert task.account_id == account.id

    def test_swapping_one_parent_for_another_in_a_single_request_works(
        self, admin_client, admin_user, org_a
    ):
        """Otherwise a task's parent would be impossible to change."""
        account = Account.objects.create(name="Acme", org=org_a)
        lead = Lead.objects.create(first_name="Jo", last_name="Blow", org=org_a)
        task = _task(org_a, admin_user, account=account)

        response = admin_client.patch(
            f"/api/tasks/{task.id}/",
            {"account": None, "lead": str(lead.id)},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.account_id is None
        assert task.lead_id == lead.id

    def test_editing_something_else_on_a_task_with_one_parent_still_works(
        self, admin_client, admin_user, org_a
    ):
        """The rule must read the payload's *effect*, not just count columns."""
        account = Account.objects.create(name="Acme", org=org_a)
        task = _task(org_a, admin_user, account=account)
        response = admin_client.patch(
            f"/api/tasks/{task.id}/", {"priority": "High"}, format="json"
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestAttachmentDeleteIsScopedToTheOrg:
    """`Attachments` is one table shared by every module.

    Without `org=` this endpoint was "delete any attachment in the database by
    UUID" for anybody's admin. Proven live against a file in another org that
    was attached to a lead.
    """

    def _attachment(self, org, uploader, task=None):
        target = task or _task(org, uploader)
        attachment = Attachments(
            file_name="notes.txt",
            content_object=target,
            org=org,
            attachment=SimpleUploadedFile("notes.txt", b"hello"),
        )
        attachment.save()
        Attachments.objects.filter(pk=attachment.pk).update(created_by=uploader)
        return attachment

    def test_an_admin_cannot_reach_another_orgs_attachment(
        self, admin_client, user_b, org_b
    ):
        attachment = self._attachment(org_b, user_b)
        assert (
            admin_client.delete(f"/api/tasks/attachment/{attachment.id}/").status_code
            == 404
        )
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_the_uploader_can_delete_their_own(self, user_client, regular_user, org_a):
        attachment = self._attachment(org_a, regular_user)
        assert (
            user_client.delete(f"/api/tasks/attachment/{attachment.id}/").status_code
            == 200
        )

    def test_another_member_cannot_delete_it(
        self, other_client, other_profile, regular_user, org_a
    ):
        attachment = self._attachment(org_a, regular_user)
        # A reader of the task who did not upload it; a non-reader gets 404
        # (`test_attachment_and_comment_reach_only_visible_tasks.py`).
        attachment.content_object.assigned_to.add(other_profile)
        assert (
            other_client.delete(f"/api/tasks/attachment/{attachment.id}/").status_code
            == 403
        )
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_an_admin_of_the_same_org_can(self, admin_client, regular_user, org_a):
        attachment = self._attachment(org_a, regular_user)
        assert (
            admin_client.delete(f"/api/tasks/attachment/{attachment.id}/").status_code
            == 200
        )


@pytest.mark.django_db
class TestMissingAndMalformedIds:
    """A bad id is a request for something that does not exist. That is a 404."""

    BAD = "not-a-uuid"

    def test_every_task_verb_survives_a_malformed_id(self, admin_client):
        assert admin_client.get(f"/api/tasks/{self.BAD}/").status_code == 404
        assert (
            admin_client.patch(
                f"/api/tasks/{self.BAD}/", {"priority": "Low"}, format="json"
            ).status_code
            == 404
        )
        assert (
            admin_client.put(
                f"/api/tasks/{self.BAD}/", {"title": "x"}, format="json"
            ).status_code
            == 404
        )
        assert (
            admin_client.post(
                f"/api/tasks/{self.BAD}/", {"comment": "x"}, format="json"
            ).status_code
            == 404
        )
        assert admin_client.delete(f"/api/tasks/{self.BAD}/").status_code == 404

    def test_a_well_formed_id_for_no_task_is_also_404(self, admin_client):
        assert admin_client.get(f"/api/tasks/{uuid.uuid4()}/").status_code == 404

    def test_comment_and_attachment_ids_too(self, admin_client):
        for prefix in ("comment", "attachment"):
            assert (
                admin_client.delete(f"/api/tasks/{prefix}/{self.BAD}/").status_code
                == 404
            )
            assert (
                admin_client.delete(f"/api/tasks/{prefix}/{uuid.uuid4()}/").status_code
                == 404
            )


@pytest.mark.django_db
class TestTheListPayload:
    """Totals and the form catalogues, in the shape cases and deals publish."""

    def test_totals_count_the_queryset_not_the_page(
        self, admin_client, admin_user, org_a
    ):
        for i in range(12):
            _task(org_a, admin_user, title=f"Task {i}")
        totals = admin_client.get("/api/tasks/?limit=5").json()["totals"]
        assert totals["count"] == 12
        assert totals["open"] == 12

    def test_overdue_ignores_finished_work(self, admin_client, admin_user, org_a):
        """A task completed late is not something anybody can act on."""
        from datetime import timedelta

        yesterday = timezone.localdate() - timedelta(days=1)
        _task(org_a, admin_user, title="Late and open", due_date=yesterday)
        _task(
            org_a,
            admin_user,
            title="Late but done",
            due_date=yesterday,
            status="Completed",
        )
        totals = admin_client.get("/api/tasks/").json()["totals"]
        assert totals["overdue"] == 1
        assert totals["open"] == 1
        assert totals["count"] == 2

    def test_totals_are_the_viewers_totals(
        self, user_client, regular_user, admin_user, org_a
    ):
        _task(org_a, regular_user, title="Mine")
        _task(org_a, admin_user, title="Not mine")
        assert user_client.get("/api/tasks/").json()["totals"]["count"] == 1

    def test_the_form_options_are_there(self, admin_client, admin_user, org_a):
        Account.objects.create(name="Acme", org=org_a)
        body = admin_client.get("/api/tasks/").json()
        assert [row[0] for row in body["status"]] == ["New", "In Progress", "Completed"]
        assert [row[0] for row in body["priority"]] == ["Low", "Medium", "High"]
        assert len(body["accounts_list"]) == 1
        assert "contacts_list" in body
        assert body["users"][0]["user__email"] == admin_user.email

    def test_slim_drops_the_catalogues(self, admin_client, org_a):
        Account.objects.create(name="Acme", org=org_a)
        body = admin_client.get("/api/tasks/?slim=true").json()
        assert "accounts_list" not in body
        assert "contacts_list" not in body
        assert "totals" in body

    def test_a_member_is_only_offered_admins_to_assign_to(
        self, user_client, admin_user, admin_profile, other_profile, org_a
    ):
        """Same narrowing the detail view has always applied.

        `other_profile` is a second plain member, and its absence from the
        answer is the point. Otherwise this would pass on an empty list.
        """
        emails = [
            row["user__email"] for row in user_client.get("/api/tasks/").json()["users"]
        ]
        assert emails == [admin_user.email]

    def test_the_list_does_not_cost_a_query_per_row(
        self, admin_client, admin_user, org_a, django_assert_num_queries
    ):
        """Fixed count, not a ceiling: a ceiling passes while it rots.

        The first request of a process pays for a few one-off lookups, the
        JWT's user and profile, the content types, so the count depends on
        what ran before it. Measuring the *second* request makes the number
        the query cost of serving the page and nothing else, which is the
        number worth pinning.
        """
        for i in range(12):
            _task(org_a, admin_user, title=f"Task {i}")
        admin_client.get("/api/tasks/?limit=12&slim=true")
        with django_assert_num_queries(12):
            admin_client.get("/api/tasks/?limit=12&slim=true")

    def test_twelve_rows_cost_the_same_as_two(
        self, admin_client, admin_user, org_a, django_assert_num_queries
    ):
        """The claim the number above is really making.

        Before `select_related`/`prefetch_related` the parents and assignees
        were fetched per row, so this is the assertion that would have caught
        it, and it holds whatever the fixed number turns out to be.
        """
        for i in range(2):
            _task(org_a, admin_user, title=f"Small {i}")
        admin_client.get("/api/tasks/?slim=true")
        with django_assert_num_queries(12):
            admin_client.get("/api/tasks/?slim=true")

        for i in range(20):
            _task(org_a, admin_user, title=f"Big {i}")
        with django_assert_num_queries(12):
            admin_client.get("/api/tasks/?limit=25&slim=true")


@pytest.mark.django_db
class TestCrossOrgIsolationOnTheOtherVerbs:
    """RLS is the safety net. The explicit org filter is the contract."""

    def test_another_orgs_admin_cannot_edit(self, org_b_client, admin_user, org_a):
        task = _task(org_a, admin_user)
        response = org_b_client.patch(
            f"/api/tasks/{task.id}/", {"title": "Theirs now"}, format="json"
        )
        assert response.status_code == 404
        task.refresh_from_db()
        assert task.title == "A task"

    def test_another_orgs_admin_cannot_delete(self, org_b_client, admin_user, org_a):
        task = _task(org_a, admin_user)
        assert org_b_client.delete(f"/api/tasks/{task.id}/").status_code == 404
        assert Task.objects.filter(pk=task.pk).exists()

    def test_another_orgs_admin_cannot_comment(self, org_b_client, admin_user, org_a):
        task = _task(org_a, admin_user)
        response = org_b_client.post(
            f"/api/tasks/{task.id}/", {"comment": "hello"}, format="json"
        )
        assert response.status_code == 404
