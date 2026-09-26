"""Editing a contact's assignees emails whoever was added, and nobody else.

PUT read the "previous" assignees after it had already replaced them, so the
before and after sets were always equal and the recipient list was always
empty: nobody added to a contact on the phone (which edits with PUT) was ever
told. PATCH, which the web app edits with, sent nothing at all. Both now
capture the assignees before the write and share one helper, as cases does.
"""

from unittest.mock import patch

import pytest

from contacts.models import Contact

URL = "/api/contacts/"
BODY = {"first_name": "Jo", "last_name": "Contact", "email": "jo@example.com"}
NOTIFY = "contacts.views.send_email_to_assigned_user.delay"


@pytest.fixture
def contact(org_a, user_profile):
    contact = Contact.objects.create(
        first_name="Jo", last_name="Contact", email="jo@example.com", org=org_a
    )
    contact.assigned_to.add(user_profile)
    return contact


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["put", "patch"])
class TestContactAssignmentNotification:
    def test_an_added_assignee_is_notified_once(
        self, admin_client, contact, user_profile, admin_profile, org_a, method
    ):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{contact.id}/",
                {
                    **BODY,
                    "assigned_to": [str(user_profile.id), str(admin_profile.id)],
                },
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_called_once_with([admin_profile.id], contact.id, str(org_a.id))

    def test_unchanged_assignees_notify_nobody(
        self, admin_client, contact, user_profile, method
    ):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{contact.id}/",
                {**BODY, "assigned_to": [str(user_profile.id)]},
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_not_called()

    def test_a_removed_assignee_is_not_notified(self, admin_client, contact, method):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{contact.id}/",
                {**BODY, "assigned_to": []},
                format="json",
            )

        assert response.status_code == 200, response.data
        assert contact.assigned_to.count() == 0
        notify.assert_not_called()


@pytest.mark.django_db
def test_patch_without_assigned_to_notifies_nobody(admin_client, contact, user_profile):
    with patch(NOTIFY) as notify:
        response = admin_client.patch(
            f"{URL}{contact.id}/", {"first_name": "Jolene"}, format="json"
        )

    assert response.status_code == 200, response.data
    assert list(contact.assigned_to.all()) == [user_profile]
    notify.assert_not_called()
