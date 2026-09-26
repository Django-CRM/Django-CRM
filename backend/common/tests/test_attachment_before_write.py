"""An oversized attachment is refused before the record is written.

``create_attachment`` refuses a file over ``ATTACHMENT_MAX_BYTES`` with a 400,
but every view that saves a record and attaches a file in one request called it
last. ``ATOMIC_REQUESTS`` is off, so the 400 arrived with the work already
done: a create left a new record behind (with its assignees notified), a PUT
left the fields and relations rewritten, and the detail POST left the comment
posted. The views now run ``validate_attachment`` in their parse step, before
the first write, so the refusal leaves nothing behind.

The limit is patched down to a few bytes so no test has to allocate 25 MB. The
helper reads the module constant at call time, so the patch is the real rule.
"""

from dataclasses import dataclass
from typing import Callable
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from cases.models import Case
from common.models import Attachments, Comment
from contacts.models import Contact
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task

LIMIT = 10


@dataclass
class Spec:
    url: str
    field: str
    model: type
    label: str
    create: dict
    update: dict
    notify: str
    make: Callable


SPECS = {
    "account": Spec(
        url="/api/accounts/",
        field="account_attachment",
        model=Account,
        label="name",
        create={"name": "New account"},
        update={"name": "After"},
        notify="accounts.views.send_email_to_assigned_user.delay",
        make=lambda org, user: Account.objects.create(name="Before", org=org),
    ),
    "contact": Spec(
        url="/api/contacts/",
        field="contact_attachment",
        model=Contact,
        label="first_name",
        create={"first_name": "New", "last_name": "C", "email": "new@example.com"},
        update={"first_name": "After", "last_name": "C", "email": "was@example.com"},
        notify="contacts.views.send_email_to_assigned_user.delay",
        make=lambda org, user: Contact.objects.create(
            first_name="Before", last_name="C", email="was@example.com", org=org
        ),
    ),
    "case": Spec(
        url="/api/cases/",
        field="case_attachment",
        model=Case,
        label="name",
        create={"name": "New case", "status": "New", "priority": "Normal"},
        update={"name": "After", "status": "New", "priority": "Normal"},
        notify="cases.views.send_email_to_assigned_user.delay",
        make=lambda org, user: Case.objects.create(
            name="Before", status="New", priority="Normal", org=org, created_by=user
        ),
    ),
    "lead": Spec(
        url="/api/leads/",
        field="lead_attachment",
        model=Lead,
        label="first_name",
        create={"first_name": "New", "last_name": "L", "email": "new@example.com"},
        update={"first_name": "After", "last_name": "L", "email": "was@example.com"},
        notify="leads.views.lead_views.send_email_to_assigned_user.delay",
        make=lambda org, user: Lead.objects.create(
            first_name="Before",
            last_name="L",
            email="was@example.com",
            org=org,
            created_by=user,
        ),
    ),
    "deal": Spec(
        url="/api/opportunities/",
        field="opportunity_attachment",
        model=Opportunity,
        label="name",
        create={"name": "New deal", "stage": "QUALIFICATION"},
        update={"name": "After", "stage": "QUALIFICATION"},
        notify="opportunity.views.opportunity_views.send_email_to_assigned_user.delay",
        make=lambda org, user: Opportunity.objects.create(
            name="Before", stage="QUALIFICATION", org=org, created_by=user
        ),
    ),
    # Tasks attach only on the detail POST: create and PUT take no file.
    "task": Spec(
        url="/api/tasks/",
        field="task_attachment",
        model=Task,
        label="title",
        create={},
        update={},
        notify="",
        make=lambda org, user: Task.objects.create(
            title="Before", status="New", priority="High", org=org, created_by=user
        ),
    ),
}
WRITE_SPECS = [name for name in SPECS if name != "task"]


def _file(size):
    return SimpleUploadedFile("evidence.pdf", b"x" * size)


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr("common.utils.ATTACHMENT_MAX_BYTES", LIMIT)


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("name", WRITE_SPECS)
    def test_oversized_is_400_and_creates_nothing(
        self, admin_client, admin_profile, org_a, name
    ):
        spec = SPECS[name]
        records = spec.model.objects.filter(org=org_a).count()
        files = Attachments.objects.count()

        with patch(spec.notify) as notify:
            response = admin_client.post(
                spec.url,
                {
                    **spec.create,
                    "assigned_to": str(admin_profile.id),
                    spec.field: _file(LIMIT + 1),
                },
                format="multipart",
            )

        assert response.status_code == 400, response.data
        assert "attachment" in response.data
        assert spec.model.objects.filter(org=org_a).count() == records
        assert Attachments.objects.count() == files
        notify.assert_not_called()

    @pytest.mark.parametrize("name", WRITE_SPECS)
    def test_a_file_at_the_limit_is_stored(self, admin_client, org_a, name):
        spec = SPECS[name]

        with patch(spec.notify):
            response = admin_client.post(
                spec.url,
                {**spec.create, spec.field: _file(LIMIT)},
                format="multipart",
            )

        assert response.status_code == 200, response.data
        record = spec.model.objects.get(org=org_a)
        assert Attachments.objects.filter(object_id=record.id).count() == 1


@pytest.mark.django_db
class TestPut:
    @pytest.mark.parametrize("name", WRITE_SPECS)
    def test_oversized_is_400_and_changes_nothing(
        self, admin_client, admin_profile, admin_user, org_a, name
    ):
        spec = SPECS[name]
        record = spec.make(org_a, admin_user)

        with patch(spec.notify) as notify:
            response = admin_client.put(
                f"{spec.url}{record.id}/",
                {
                    **spec.update,
                    "assigned_to": str(admin_profile.id),
                    spec.field: _file(LIMIT + 1),
                },
                format="multipart",
            )

        assert response.status_code == 400, response.data
        assert "attachment" in response.data
        record.refresh_from_db()
        assert getattr(record, spec.label) == "Before"
        assert record.assigned_to.count() == 0
        assert Attachments.objects.filter(object_id=record.id).count() == 0
        notify.assert_not_called()

    @pytest.mark.parametrize("name", WRITE_SPECS)
    def test_a_file_at_the_limit_is_stored(self, admin_client, admin_user, org_a, name):
        spec = SPECS[name]
        record = spec.make(org_a, admin_user)

        with patch(spec.notify):
            response = admin_client.put(
                f"{spec.url}{record.id}/",
                {**spec.update, spec.field: _file(LIMIT)},
                format="multipart",
            )

        assert response.status_code == 200, response.data
        record.refresh_from_db()
        assert getattr(record, spec.label) == "After"
        assert Attachments.objects.filter(object_id=record.id).count() == 1


@pytest.mark.django_db
class TestCommentAndAttachment:
    """The detail POST saved the comment, then refused the file."""

    @pytest.mark.parametrize("name", list(SPECS))
    def test_oversized_is_400_and_posts_no_comment(
        self, admin_client, admin_user, org_a, name
    ):
        spec = SPECS[name]
        record = spec.make(org_a, admin_user)

        response = admin_client.post(
            f"{spec.url}{record.id}/",
            {"comment": "See attached", spec.field: _file(LIMIT + 1)},
            format="multipart",
        )

        assert response.status_code == 400, response.data
        assert "attachment" in response.data
        assert Comment.objects.filter(object_id=record.id).count() == 0
        assert Attachments.objects.filter(object_id=record.id).count() == 0

    @pytest.mark.parametrize("name", list(SPECS))
    def test_a_file_at_the_limit_is_stored_with_the_comment(
        self, admin_client, admin_user, org_a, name
    ):
        spec = SPECS[name]
        record = spec.make(org_a, admin_user)

        response = admin_client.post(
            f"{spec.url}{record.id}/",
            {"comment": "See attached", spec.field: _file(LIMIT)},
            format="multipart",
        )

        assert response.status_code == 200, response.data
        assert Comment.objects.filter(object_id=record.id).count() == 1
        assert Attachments.objects.filter(object_id=record.id).count() == 1
