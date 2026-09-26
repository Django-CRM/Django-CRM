"""The lead read rule admits a superuser through the profile alone.

Since 1.12.0 `has_lead_access` and `visible_leads_qs` take only the profile:
the separate ``user`` argument and its ``or user.is_superuser`` clause are
gone, because `is_org_admin` already admits a superuser's profile. These pin
that dropping the clause did not drop superuser access, that a plain member is
still refused, and that a superuser still sees only their own org.
"""

import pytest

from common.models import Profile, User
from common.testing import rls_org
from leads.access import has_lead_access, visible_leads_qs
from leads.models import Lead


@pytest.fixture
def super_profile(org_a):
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    # Loaded the way the middleware loads it, so the flag is read from the row.
    return Profile.objects.select_related("org", "user").get(pk=profile.pk)


@pytest.fixture
def ownerless_lead(org_a):
    return Lead.objects.create(org=org_a, first_name="Nobody's")


def test_superuser_with_a_user_role_profile_may_open_any_lead(
    super_profile, ownerless_lead
):
    assert super_profile.role == "USER"
    assert has_lead_access(super_profile, ownerless_lead) is True
    assert ownerless_lead in visible_leads_qs(super_profile)


def test_plain_member_is_refused_a_lead_they_neither_made_nor_hold(
    user_profile, ownerless_lead
):
    assert has_lead_access(user_profile, ownerless_lead) is False
    assert ownerless_lead not in visible_leads_qs(user_profile)


def test_superuser_still_sees_only_their_own_org(super_profile, org_b):
    with rls_org(org_b):
        other = Lead.objects.create(org=org_b, first_name="Elsewhere")
    assert not visible_leads_qs(super_profile).filter(pk=other.pk).exists()
