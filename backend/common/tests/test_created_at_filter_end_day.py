"""A `created_at` range includes the day at each end of it.

`created_at` is a DateTimeField and `?created_at__lte=` carries a date, so
comparing the two directly put the upper bound at midnight and dropped
everything recorded during the end day itself. A user filtering "up to today"
saw none of today. Filtering on the date part instead makes the range inclusive
by calendar day, and resolves it in the org's timezone rather than the server's,
since `__date` answers in whichever timezone `GetProfileAndOrg` activated.

Every endpoint accepting the pair is covered here rather than in each app's own
suite, because the defect was one shape repeated eight times. Seven of the eight
were still live after the first was fixed in isolation, which is the case for
pinning them together: a new list view that copies the old two lines fails here
immediately.

Each assertion is paired with its mirror, so a filter that quietly stopped being
applied at all fails the pair rather than passing the half that only asks
whether the record is present.
"""

import datetime

import pytest

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task

# Midday, so the record sits inside its day rather than on either edge. The org
# fixtures keep the default UTC timezone, so this is the 10th for the filter too.
CREATED_AT = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=datetime.timezone.utc)
THE_DAY = "2026-03-10"
DAY_BEFORE = "2026-03-09"
DAY_AFTER = "2026-03-11"


def _account(org, user):
    return Account.objects.create(name="Filter Me", org=org, created_by=user)


def _lead(org, user):
    # `status` is nullable on the model, and the kanban builds one column per
    # LEAD_STATUS value, so a lead without one is counted by the board and shown
    # in none of its columns. Set it, or the kanban case would assert nothing.
    return Lead.objects.create(
        first_name="Filter", last_name="Me", status="assigned", org=org, created_by=user
    )


def _contact(org, user):
    return Contact.objects.create(
        first_name="Filter", last_name="Me", org=org, created_by=user
    )


def _case(org, user):
    return Case.objects.create(
        name="Filter Me", status="New", priority="Normal", org=org, created_by=user
    )


def _opportunity(org, user):
    return Opportunity.objects.create(
        name="Filter Me", stage="QUALIFICATION", org=org, created_by=user
    )


def _task(org, user):
    return Task.objects.create(
        title="Filter Me", status="New", priority="Medium", org=org, created_by=user
    )


ENDPOINTS = [
    ("/api/accounts/", _account),
    ("/api/leads/", _lead),
    ("/api/leads/kanban/", _lead),
    ("/api/contacts/", _contact),
    ("/api/cases/", _case),
    ("/api/cases/kanban/", _case),
    ("/api/opportunities/", _opportunity),
    ("/api/tasks/", _task),
]
IDS = [url for url, _factory in ENDPOINTS]


def _record(factory, org, user):
    """One record whose `created_at` is a known instant.

    `created_at` is `auto_now_add`, so it can only be moved by an UPDATE that
    goes around the field's default.
    """
    obj = factory(org, user)
    type(obj).objects.filter(pk=obj.pk).update(created_at=CREATED_AT)
    return obj


def _shows(response, obj):
    """Whether the record came back.

    Matched on the raw body rather than a results key: the list and kanban
    endpoints answer with different shapes, and the question here is only
    whether the filter kept the row.
    """
    assert response.status_code == 200
    return str(obj.id) in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(("url", "factory"), ENDPOINTS, ids=IDS)
def test_lte_includes_the_end_day(admin_client, org_a, admin_user, url, factory):
    obj = _record(factory, org_a, admin_user)
    assert _shows(admin_client.get(f"{url}?created_at__lte={THE_DAY}"), obj)
    assert not _shows(admin_client.get(f"{url}?created_at__lte={DAY_BEFORE}"), obj)


@pytest.mark.django_db
@pytest.mark.parametrize(("url", "factory"), ENDPOINTS, ids=IDS)
def test_gte_includes_the_start_day(admin_client, org_a, admin_user, url, factory):
    obj = _record(factory, org_a, admin_user)
    assert _shows(admin_client.get(f"{url}?created_at__gte={THE_DAY}"), obj)
    assert not _shows(admin_client.get(f"{url}?created_at__gte={DAY_AFTER}"), obj)
