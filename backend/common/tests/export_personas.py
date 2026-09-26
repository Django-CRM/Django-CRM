"""The five callers every list/export visibility test runs as.

Shared by the per-module export tests so "same rows as the list" is asked of
the same people everywhere: an admin, a member the record is assigned to, a
member who created it, a member with no link to it, and a Django superuser
holding an ordinary member profile.
"""

from dataclasses import dataclass

from common.models import Profile, User
from common.testing import _make_authenticated_client


@dataclass
class Persona:
    user: User
    profile: Profile

    def client(self):
        return _make_authenticated_client(self.user, self.profile.org, self.profile)


def make_personas(org):
    def persona(email, role="USER", superuser=False):
        user = User.objects.create_user(email=email, password="pw-not-used")
        if superuser:
            User.objects.filter(pk=user.pk).update(is_superuser=True)
            user.refresh_from_db()
        profile = Profile.objects.create(user=user, org=org, role=role, is_active=True)
        return Persona(user, profile)

    return {
        "admin": persona("export-admin@test.com", role="ADMIN"),
        "assignee": persona("export-assignee@test.com"),
        "creator": persona("export-creator@test.com"),
        "unrelated": persona("export-unrelated@test.com"),
        "superuser": persona("export-super@test.com", superuser=True),
    }


def stamp_creator(model, obj, user):
    """Set ``created_by`` without ``save()``, which overwrites it from crum."""
    model.objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj
