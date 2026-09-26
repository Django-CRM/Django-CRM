"""`may_read_document` and `can_mass_import` admit a superuser through the
profile alone.

Since 1.12.0 neither carries its own ``or ...is_superuser`` clause:
`may_read_document` no longer takes a separate ``user`` argument, and both
lean on `is_org_admin`, which already admits a superuser's profile. These pin
that dropping the clause did not drop superuser access and that a plain
member is still refused.
"""

import pytest

from common.models import Document, Profile, User
from common.permissions import can_mass_import
from common.views.document_views import may_read_document


@pytest.fixture
def super_profile(org_a):
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    # Loaded the way the middleware loads it, so the flag is read from the row.
    return Profile.objects.select_related("org", "user").get(pk=profile.pk)


@pytest.fixture
def ownerless_document(org_a):
    # Created outside a request, so `created_by` is empty: nobody's document.
    return Document.objects.create(
        title="Nobody's", document_file="documents/probe.pdf", org=org_a
    )


def test_superuser_with_a_user_role_profile_may_read_any_document(
    super_profile, ownerless_document
):
    assert super_profile.role == "USER"
    assert may_read_document(super_profile, ownerless_document) is True


def test_plain_member_is_refused_a_document_not_shared_with_them(
    user_profile, ownerless_document
):
    assert may_read_document(user_profile, ownerless_document) is False


def test_superuser_with_a_user_role_profile_may_mass_import(super_profile):
    assert super_profile.has_sales_access is False
    assert can_mass_import(super_profile) is True


def test_plain_member_without_sales_access_may_not_mass_import(user_profile):
    assert user_profile.has_sales_access is False
    assert can_mass_import(user_profile) is False
