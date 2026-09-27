"""What PUT and PATCH each do to a ticket, and where they used to disagree.

Two clients edit the same ticket through two different verbs: the web app
PATCHes, the phone PUTs. Both differences below were found that way, and both
were invisible from either client alone.

* PUT is a full replace, so it clears `contacts` and `teams` unconditionally.
  A client that PUTs without sending them destroys them, silently, on an edit
  that was about something else entirely.
* Only PUT told a newly assigned person they had been assigned, so the web app
  handed tickets to people who were never notified.
"""

from unittest.mock import patch

import pytest

from cases.models import Case
from common.models import Teams
from contacts.models import Contact

pytestmark = pytest.mark.django_db


def _case(org, user, **kw):
    case = Case.objects.create(
        name=kw.pop("name", "Printer is on fire"),
        status=kw.pop("status", "New"),
        priority=kw.pop("priority", "Normal"),
        org=org,
        **kw,
    )
    Case.objects.filter(pk=case.pk).update(created_by=user)
    case.refresh_from_db()
    return case


def _contact(org, user, first_name="Dana"):
    contact = Contact.objects.create(
        first_name=first_name, last_name="Reed", org=org, email=f"{first_name}@x.test"
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=user)
    return contact


def _url(case):
    return f"/api/cases/{case.pk}/"


class TestPutIsAFullReplace:
    """The verb the phone uses. Documented here so the shape is not a surprise."""

    @patch("cases.updates.send_email_to_assigned_user")
    def test_put_without_contacts_clears_them(
        self, _email, admin_client, admin_user, org_a
    ):
        case = _case(org_a, admin_user)
        case.contacts.add(_contact(org_a, admin_user))
        response = admin_client.put(
            _url(case),
            {"name": case.name, "status": "New", "priority": "Normal"},
            format="json",
        )
        assert response.status_code == 200
        assert case.contacts.count() == 0

    @patch("cases.updates.send_email_to_assigned_user")
    def test_put_keeps_the_contacts_it_is_sent(
        self, _email, admin_client, admin_user, org_a
    ):
        case = _case(org_a, admin_user)
        contact = _contact(org_a, admin_user)
        case.contacts.add(contact)
        response = admin_client.put(
            _url(case),
            {
                "name": case.name,
                "status": "New",
                "priority": "Normal",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert list(case.contacts.values_list("id", flat=True)) == [contact.id]


class TestPatchLeavesUnsentFieldsAlone:
    """The verb the web app uses, and the one the phone now uses too."""

    @patch("cases.updates.send_email_to_assigned_user")
    def test_patch_without_contacts_keeps_them(
        self, _email, admin_client, admin_user, org_a
    ):
        case = _case(org_a, admin_user)
        contact = _contact(org_a, admin_user)
        case.contacts.add(contact)
        response = admin_client.patch(_url(case), {"priority": "High"}, format="json")
        assert response.status_code == 200
        assert list(case.contacts.values_list("id", flat=True)) == [contact.id]

    @patch("cases.updates.send_email_to_assigned_user")
    def test_patch_with_an_empty_list_clears_them(
        self, _email, admin_client, admin_user, org_a
    ):
        """Sending the key is how you unlink; omitting it is how you abstain."""
        case = _case(org_a, admin_user)
        case.contacts.add(_contact(org_a, admin_user))
        response = admin_client.patch(_url(case), {"contacts": []}, format="json")
        assert response.status_code == 200
        assert case.contacts.count() == 0

    @patch("cases.updates.send_email_to_assigned_user")
    def test_patch_without_teams_keeps_them(
        self, _email, admin_client, admin_user, org_a
    ):
        case = _case(org_a, admin_user)
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        case.teams.add(team)
        response = admin_client.patch(_url(case), {"priority": "Low"}, format="json")
        assert response.status_code == 200
        assert list(case.teams.values_list("id", flat=True)) == [team.id]


class TestAssignmentNotifiesOnBothVerbs:
    @patch("cases.updates.send_email_to_assigned_user")
    def test_put_emails_the_newly_assigned(
        self,
        email,
        admin_client,
        admin_user,
        org_a,
        user_profile,
        django_capture_on_commit_callbacks,
    ):
        case = _case(org_a, admin_user)
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.put(
                _url(case),
                {
                    "name": case.name,
                    "status": "New",
                    "priority": "Normal",
                    "assigned_to": [str(user_profile.id)],
                },
                format="json",
            )
        assert email.delay.call_args[0][0] == [user_profile.id]

    @patch("cases.updates.send_email_to_assigned_user")
    def test_patch_emails_the_newly_assigned(
        self,
        email,
        admin_client,
        admin_user,
        org_a,
        user_profile,
        django_capture_on_commit_callbacks,
    ):
        """The half that did not exist. The web app assigns with PATCH."""
        case = _case(org_a, admin_user)
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.patch(
                _url(case), {"assigned_to": [str(user_profile.id)]}, format="json"
            )
        assert email.delay.call_args[0][0] == [user_profile.id]

    @patch("cases.updates.send_email_to_assigned_user")
    def test_an_edit_that_changes_nobody_emails_nobody(
        self, email, admin_client, admin_user, org_a, user_profile
    ):
        """Re-sending the same assignee is not a new assignment."""
        case = _case(org_a, admin_user)
        case.assigned_to.add(user_profile)
        admin_client.patch(
            _url(case),
            {"priority": "High", "assigned_to": [str(user_profile.id)]},
            format="json",
        )
        assert email.delay.call_count == 0
