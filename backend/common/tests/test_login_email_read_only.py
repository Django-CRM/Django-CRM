"""The login email cannot be changed through the user API (owner decision, 1.11.0).

``User.email`` is the account's global sign-in identity. ``PUT`` and ``PATCH
/api/user/<id>/`` let an org admin rename another member's email, answered 200:
the admin could then take a magic link to the new address and sign in as that
person in every org they belong to, and as platform superadmin if they were
one. Now a changed email is a 400 for everyone, self included. Echoing the
stored address (any case) is accepted so forms that resend the record work.
"""

import pytest
from rest_framework import status


def _url(user):
    return f"/api/user/{user.id}/"


VERBS = ["put", "patch"]


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_admin_cannot_change_another_members_email(
    verb, admin_client, regular_user, user_profile
):
    response = getattr(admin_client, verb)(
        _url(regular_user),
        {"email": "attacker@evil.test", "role": "USER", "phone": "+14155550100"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Email cannot be changed here." in str(response.data)
    regular_user.refresh_from_db()
    user_profile.refresh_from_db()
    assert regular_user.email == "user@test.com"
    # Refused as a whole: nothing else in the body was written either.
    assert user_profile.phone != "+14155550100"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_member_cannot_change_their_own_email(
    verb, user_client, regular_user, user_profile
):
    response = getattr(user_client, verb)(
        _url(regular_user), {"email": "me-now@test.com"}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    regular_user.refresh_from_db()
    assert regular_user.email == "user@test.com"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_admin_cannot_change_their_own_email(verb, admin_client, admin_user):
    response = getattr(admin_client, verb)(
        _url(admin_user), {"email": "boss-now@test.com"}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    admin_user.refresh_from_db()
    assert admin_user.email == "admin@test.com"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("echoed", ["user@test.com", "USER@Test.com"])
def test_unchanged_email_is_accepted_and_other_fields_update(
    verb, echoed, admin_client, regular_user, user_profile
):
    response = getattr(admin_client, verb)(
        _url(regular_user),
        {"email": echoed, "role": "ADMIN", "phone": "+14155550100"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    regular_user.refresh_from_db()
    user_profile.refresh_from_db()
    # The stored spelling is kept, not the echoed one.
    assert regular_user.email == "user@test.com"
    assert user_profile.phone == "+14155550100"
    assert user_profile.role == "ADMIN"


@pytest.mark.django_db
def test_patch_without_email_still_updates(admin_client, regular_user, user_profile):
    response = admin_client.patch(
        _url(regular_user), {"phone": "+14155550100"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    user_profile.refresh_from_db()
    regular_user.refresh_from_db()
    assert user_profile.phone == "+14155550100"
    assert regular_user.email == "user@test.com"


@pytest.mark.django_db
def test_self_profile_update_ignores_an_email(user_client, regular_user):
    """`PATCH /api/profile/` names only phone and name; an email is not written."""
    user_client.patch("/api/profile/", {"email": "me-now@test.com"}, format="json")

    regular_user.refresh_from_db()
    assert regular_user.email == "user@test.com"


@pytest.mark.django_db
def test_inviting_a_new_member_still_sets_their_email(admin_client, org_a):
    from common.models import Profile

    response = admin_client.post(
        "/api/users/", {"email": "New.Person@test.com", "role": "USER"}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert Profile.objects.filter(org=org_a, user__email="new.person@test.com").exists()


# ── the rest of the account row: profile_pic ─────────────────────────────
#
# `User` is shared by every org a person belongs to, so an admin editing
# another member may not change any field on it (coordinator decision,
# 1.11.0). `CreateUserSerializer` names two account fields, email (above) and
# profile_pic (below). Profile fields on the same request are unaffected.

PIC = "https://cdn.test/me.png"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_admin_cannot_change_another_members_picture(
    verb, admin_client, regular_user, user_profile
):
    regular_user.profile_pic = PIC
    regular_user.save(update_fields=["profile_pic"])

    response = getattr(admin_client, verb)(
        _url(regular_user),
        {
            "email": regular_user.email,
            "profile_pic": "https://evil.test/x.png",
            "phone": "+14155550100",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "profile_pic" in str(response.data)
    regular_user.refresh_from_db()
    user_profile.refresh_from_db()
    assert regular_user.profile_pic == PIC
    assert user_profile.phone != "+14155550100"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_admin_resending_the_stored_picture_is_accepted(
    verb, admin_client, regular_user, user_profile
):
    regular_user.profile_pic = PIC
    regular_user.save(update_fields=["profile_pic"])

    response = getattr(admin_client, verb)(
        _url(regular_user),
        {"email": regular_user.email, "profile_pic": PIC, "role": "ADMIN"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    regular_user.refresh_from_db()
    user_profile.refresh_from_db()
    assert regular_user.profile_pic == PIC
    assert user_profile.role == "ADMIN"


@pytest.mark.django_db
@pytest.mark.parametrize("verb", VERBS)
def test_a_member_may_change_their_own_picture(verb, user_client, regular_user):
    response = getattr(user_client, verb)(
        _url(regular_user),
        {"email": regular_user.email, "profile_pic": PIC},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    regular_user.refresh_from_db()
    assert regular_user.profile_pic == PIC


@pytest.mark.django_db
def test_the_serializer_fails_closed_when_the_caller_is_not_named(regular_user, org_a):
    from common.serializer import CreateUserSerializer

    serializer = CreateUserSerializer(
        data={"email": regular_user.email, "profile_pic": PIC},
        instance=regular_user,
        org=org_a,
    )

    assert not serializer.is_valid()
    assert "profile_pic" in serializer.errors
