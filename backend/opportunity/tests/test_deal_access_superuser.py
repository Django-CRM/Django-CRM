"""The deal read rule admits a superuser through the profile alone.

Since 1.12.0 `has_deal_access`, `visible_deals_qs` and `get_visible_deal` take
only the profile: the separate ``user`` argument and its ``or
user.is_superuser`` clause are gone, because `is_org_admin` already admits a
superuser's profile. These pin that dropping the clause did not drop superuser
access and that a plain member is still refused.
"""

import pytest

from common.models import Profile, User
from opportunity.access import get_visible_deal, has_deal_access, visible_deals_qs
from opportunity.models import Opportunity


@pytest.fixture
def super_profile(org_a):
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    # Loaded the way the middleware loads it, so the flag is read from the row.
    return Profile.objects.select_related("org", "user").get(pk=profile.pk)


@pytest.fixture
def ownerless_deal(org_a):
    return Opportunity.objects.create(name="Nobody's", org=org_a)


def test_superuser_with_a_user_role_profile_may_open_any_deal(
    super_profile, ownerless_deal
):
    assert super_profile.role == "USER"
    assert has_deal_access(super_profile, ownerless_deal) is True
    assert ownerless_deal in visible_deals_qs(super_profile)
    assert get_visible_deal(super_profile, ownerless_deal.pk) == ownerless_deal


def test_plain_member_is_refused_a_deal_they_neither_made_nor_hold(
    user_profile, ownerless_deal
):
    assert has_deal_access(user_profile, ownerless_deal) is False
    assert ownerless_deal not in visible_deals_qs(user_profile)
    assert get_visible_deal(user_profile, ownerless_deal.pk) is None
