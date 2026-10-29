"""Every paginated list answers at most 1000 rows, whatever `limit` asks for."""

import pytest
from django.conf import settings

from common.pagination import MAX_PAGE_SIZE, CappedLimitOffsetPagination
from contacts.models import Contact
from webforms.models import WebForm


def test_the_default_paginator_is_the_capped_one():
    assert (
        settings.REST_FRAMEWORK["DEFAULT_PAGINATION_CLASS"]
        == "common.pagination.CappedLimitOffsetPagination"
    )
    assert CappedLimitOffsetPagination.max_limit == MAX_PAGE_SIZE == 1000


@pytest.mark.django_db
def test_list_limit_is_capped_at_1000(admin_client, org_a):
    Contact.objects.bulk_create(
        [
            Contact(org=org_a, first_name="Cap", last_name=f"{i:04d}")
            for i in range(MAX_PAGE_SIZE + 1)
        ]
    )
    WebForm.objects.bulk_create(
        [WebForm(org=org_a, name=f"form-{i:04d}") for i in range(MAX_PAGE_SIZE + 1)]
    )
    # A mixin view, and a view that builds its own paginator.
    contacts = admin_client.get("/api/contacts/?limit=5000")
    assert contacts.status_code == 200
    assert len(contacts.json()["contact_obj_list"]) == MAX_PAGE_SIZE
    forms = admin_client.get("/api/webforms/?limit=5000")
    assert forms.status_code == 200
    assert len(forms.json()["results"]) == MAX_PAGE_SIZE
