"""Access rules for /api/documents/.

There was no coverage of this endpoint at all, which is why three separate
defects survived in it: the list crashed for non-admins on an AttributeError,
the object-level checks compared a `Profile` to a `User` FK and were therefore
always False, and `Document.teams` was written but never read.

Every check below asserts BOTH directions, a permission test that only ever
sees one answer cannot tell a working check from a constant.
"""

import pytest

from common.models import Document, Teams

DOCUMENTS_URL = "/api/documents/"


def _detail_url(pk):
    return f"/api/documents/{pk}/"


def _doc(org, user, **kw):
    """Create a Document owned by `user`. `created_by` is a **User** FK.

    The second step is not optional: `BaseModel.save()` overwrites
    `created_by` from crum's `get_current_user()`, which is None outside a
    request, so passing it to `create()` has no effect. `.update()` goes
    straight to SQL and is the only way to set it from a test.
    """
    doc = Document.objects.create(
        title=kw.pop("title", "Doc"),
        document_file=kw.pop("document_file", "documents/probe.pdf"),
        status=kw.pop("status", "active"),
        org=org,
        **kw,
    )
    Document.objects.filter(pk=doc.pk).update(created_by=user)
    doc.refresh_from_db()
    return doc


def _active_titles(response):
    return {d["title"] for d in response.json()["documents_active"]["documents_active"]}


@pytest.mark.django_db
class TestDocumentListAccess:
    def test_non_admin_list_does_not_500(self, user_client, regular_user, org_a):
        """Regression: `request.profile.documents()` does not exist on Profile.

        This raised AttributeError -> 500 for every non-ADMIN caller, which is
        most of them. Admins skipped the branch, so the endpoint looked fine.
        """
        _doc(org_a, regular_user, title="Mine")
        response = user_client.get(DOCUMENTS_URL)
        assert response.status_code == 200

    def test_non_admin_sees_own_upload(self, user_client, regular_user, org_a):
        _doc(org_a, regular_user, title="Mine")
        response = user_client.get(DOCUMENTS_URL)
        assert "Mine" in _active_titles(response)

    def test_non_admin_sees_document_shared_with_them(
        self, user_client, admin_user, org_a, user_profile
    ):
        doc = _doc(org_a, admin_user, title="Shared with me")
        doc.shared_to.add(user_profile)
        response = user_client.get(DOCUMENTS_URL)
        assert "Shared with me" in _active_titles(response)

    def test_non_admin_sees_document_shared_with_their_team(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Regression: `teams` was written by POST/PUT and read by no query."""
        doc = _doc(org_a, admin_user, title="Shared with my team")
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        response = user_client.get(DOCUMENTS_URL)
        assert "Shared with my team" in _active_titles(response)

    def test_non_admin_does_not_see_unshared_document(
        self, user_client, admin_user, org_a
    ):
        """The other direction: no share, no team, not the creator -> invisible."""
        _doc(org_a, admin_user, title="Not for you")
        response = user_client.get(DOCUMENTS_URL)
        assert "Not for you" not in _active_titles(response)

    def test_non_admin_does_not_see_other_teams_document(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Being in *a* team is not being in *the* team."""
        doc = _doc(org_a, admin_user, title="Other team only")
        mine = Teams.objects.create(name="Mine", org=org_a, created_by=admin_user)
        mine.users.add(user_profile)
        theirs = Teams.objects.create(name="Theirs", org=org_a, created_by=admin_user)
        doc.teams.add(theirs)
        response = user_client.get(DOCUMENTS_URL)
        assert "Other team only" not in _active_titles(response)

    def test_admin_sees_everything_in_the_org(self, admin_client, admin_user, org_a):
        _doc(org_a, admin_user, title="Admin visible")
        response = admin_client.get(DOCUMENTS_URL)
        assert "Admin visible" in _active_titles(response)

    def test_list_with_only_archived_documents_does_not_500(
        self, admin_client, admin_user, org_a
    ):
        """Regression: the inactive-envelope offset was keyed off the *active*
        results (`results_documents_active[-1]`), a copy-paste from the block
        above. An org with archived documents but no active ones made that an
        empty-list `[-1]` -> IndexError -> 500. Archiving the last document is
        exactly how a real user reaches this state.
        """
        _doc(org_a, admin_user, title="Archived only", status="inactive")
        response = admin_client.get(DOCUMENTS_URL)
        assert response.status_code == 200
        inactive = response.json()["documents_inactive"]["documents_inactive"]
        assert "Archived only" in {d["title"] for d in inactive}
        assert response.json()["documents_active"]["documents_active"] == []

    def test_list_is_not_duplicated_by_multiple_grants(
        self, user_client, regular_user, org_a, user_profile
    ):
        """Creator AND share AND team is one row, not three.

        The visibility filter joins two M2Ms, so without .distinct() a
        document reachable more than one way is returned once per join row.
        """
        doc = _doc(org_a, regular_user, title="Reachable three ways")
        doc.shared_to.add(user_profile)
        team = Teams.objects.create(name="Support", org=org_a, created_by=regular_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        titles = [
            d["title"]
            for d in user_client.get(DOCUMENTS_URL).json()["documents_active"][
                "documents_active"
            ]
        ]
        assert titles.count("Reachable three ways") == 1


@pytest.mark.django_db
class TestDocumentDetailAccess:
    def test_creator_can_open_own_document(
        self, user_client, regular_user, org_a, user_profile
    ):
        """Regression: `request.profile == document.created_by` compared a
        Profile to a User FK, so it was always False and the uploader got 403
        on their own file."""
        doc = _doc(org_a, regular_user, title="Mine")
        response = user_client.get(_detail_url(doc.id))
        assert response.status_code == 200

    def test_shared_user_can_open(self, user_client, admin_user, org_a, user_profile):
        doc = _doc(org_a, admin_user)
        doc.shared_to.add(user_profile)
        assert user_client.get(_detail_url(doc.id)).status_code == 200

    def test_team_member_can_open(self, user_client, admin_user, org_a, user_profile):
        doc = _doc(org_a, admin_user)
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        assert user_client.get(_detail_url(doc.id)).status_code == 200

    def test_stranger_is_forbidden(self, user_client, admin_user, org_a):
        """The other direction. The check must still be able to say no."""
        doc = _doc(org_a, admin_user)
        assert user_client.get(_detail_url(doc.id)).status_code == 403

    def test_other_org_is_404_not_403(self, user_client, user_b, org_b):
        """Cross-org must not confirm the row exists."""
        doc = _doc(org_b, user_b)
        assert user_client.get(_detail_url(doc.id)).status_code == 404


@pytest.mark.django_db
class TestDocumentDelete:
    def test_creator_can_delete_own_document(
        self, user_client, regular_user, org_a, user_profile
    ):
        doc = _doc(org_a, regular_user)
        assert user_client.delete(_detail_url(doc.id)).status_code == 200
        assert not Document.objects.filter(id=doc.id).exists()

    def test_admin_can_delete_anyones_document(self, admin_client, regular_user, org_a):
        doc = _doc(org_a, regular_user)
        assert admin_client.delete(_detail_url(doc.id)).status_code == 200

    def test_shared_user_cannot_delete(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Being able to read a document is not being able to destroy it.

        `_may_delete` is deliberately narrower than `_may_read`: a share hands
        someone a copy to work with, not the right to remove it from everyone
        else.
        """
        doc = _doc(org_a, admin_user)
        doc.shared_to.add(user_profile)
        assert user_client.delete(_detail_url(doc.id)).status_code == 403
        assert Document.objects.filter(id=doc.id).exists()

    def test_team_member_cannot_delete(
        self, user_client, admin_user, org_a, user_profile
    ):
        doc = _doc(org_a, admin_user)
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        assert user_client.delete(_detail_url(doc.id)).status_code == 403


@pytest.mark.django_db
class TestDocumentUpdate:
    def test_creator_can_update_own_document(
        self, user_client, regular_user, org_a, user_profile
    ):
        doc = _doc(org_a, regular_user, title="Before")
        response = user_client.patch(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 200
        doc.refresh_from_db()
        assert doc.title == "After"

    def test_stranger_cannot_update(self, user_client, admin_user, org_a):
        doc = _doc(org_a, admin_user, title="Before")
        response = user_client.patch(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 403
        doc.refresh_from_db()
        assert doc.title == "Before"

    def test_admin_can_update_anyones_document(self, admin_client, regular_user, org_a):
        """The permissive direction for an admin: not the creator, still allowed."""
        doc = _doc(org_a, regular_user, title="Before")
        response = admin_client.patch(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 200
        doc.refresh_from_db()
        assert doc.title == "After"

    def test_shared_user_cannot_update(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Reading via a share does not grant rewriting.

        PUT/PATCH once authorised on `_may_read`, so anyone a document was
        shared with could overwrite it. Editing is now gated by `_may_write`,
        which is as narrow as delete: the creator or an admin only. A merely
        shared-with user can open it and gets 403 on any change.
        """
        doc = _doc(org_a, admin_user, title="Before")
        doc.shared_to.add(user_profile)
        response = user_client.patch(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 403
        doc.refresh_from_db()
        assert doc.title == "Before"

    def test_shared_user_cannot_update_via_put(
        self, user_client, admin_user, org_a, user_profile
    ):
        """PUT is the sharper case: it also clears `shared_to`/`teams`, so a
        shared-with user reaching it could have locked everyone else out. It is
        gated by the same `_may_write`, and shares survive the refused call."""
        doc = _doc(org_a, admin_user, title="Before")
        doc.shared_to.add(user_profile)
        response = user_client.put(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 403
        doc.refresh_from_db()
        assert doc.title == "Before"
        assert list(doc.shared_to.all()) == [user_profile]

    def test_team_member_cannot_update(
        self, user_client, admin_user, org_a, user_profile
    ):
        """A team share is a read grant, not a write grant, exactly like delete."""
        doc = _doc(org_a, admin_user, title="Before")
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        response = user_client.patch(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 403
        doc.refresh_from_db()
        assert doc.title == "Before"

    def test_creator_can_update_via_put(self, user_client, regular_user, org_a):
        """The creator keeps full edit rights through PUT, not just PATCH."""
        doc = _doc(org_a, regular_user, title="Before")
        response = user_client.put(
            _detail_url(doc.id), {"title": "After"}, content_type="application/json"
        )
        assert response.status_code == 200
        doc.refresh_from_db()
        assert doc.title == "After"


def _inactive_profile(org, email="gone@test.com"):
    from common.models import Profile, User

    user = User.objects.create_user(email=email, password="testpass123")
    return Profile.objects.create(user=user, org=org, role="USER", is_active=False)


def _shared_ids(doc):
    return set(doc.shared_to.values_list("id", flat=True))


@pytest.mark.django_db
class TestDocumentPutSharing:
    """PUT carries the whole share list, and a share to somebody deactivated
    since survives it when the body names them again.

    The PUT used to clear `shared_to` and re-add only ACTIVE profiles, so any
    change to a document's sharing silently dropped every share to a
    deactivated person, resubmitted or not. The rule now: the body is the full
    list; a resubmitted id is kept if it is an active profile in the caller's
    org OR already one of this document's shares. That second arm only reaches
    ids the document already holds, so it cannot add a new inactive share, and
    everything is still filtered on the caller's org.
    """

    def test_resubmitted_inactive_share_is_kept(
        self, user_client, regular_user, org_a, admin_profile
    ):
        gone = _inactive_profile(org_a)
        doc = _doc(org_a, regular_user, title="Before")
        doc.shared_to.add(gone, admin_profile)
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(gone.id), str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert _shared_ids(doc) == {gone.id, admin_profile.id}

    def test_omitted_inactive_share_is_removed(
        self, user_client, regular_user, org_a, admin_profile
    ):
        """The body is the full list, for inactive shares as for active ones."""
        gone = _inactive_profile(org_a)
        doc = _doc(org_a, regular_user, title="Before")
        doc.shared_to.add(gone, admin_profile)
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert _shared_ids(doc) == {admin_profile.id}

    def test_cannot_add_a_new_inactive_share(
        self, user_client, regular_user, org_a, admin_profile
    ):
        gone = _inactive_profile(org_a)
        doc = _doc(org_a, regular_user, title="Before")
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(gone.id), str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert _shared_ids(doc) == {admin_profile.id}

    def test_cannot_add_another_orgs_profile(
        self, user_client, regular_user, org_a, profile_b
    ):
        doc = _doc(org_a, regular_user, title="Before")
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(profile_b.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert _shared_ids(doc) == set()

    def test_active_shares_are_replaced(
        self, user_client, regular_user, org_a, admin_profile, user_profile
    ):
        doc = _doc(org_a, regular_user, title="Before")
        doc.shared_to.add(admin_profile)
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(user_profile.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert _shared_ids(doc) == {user_profile.id}

    def test_shared_user_cannot_change_sharing(
        self, user_client, admin_user, org_a, user_profile, admin_profile
    ):
        """A share is a read grant: its holder cannot re-share or unshare."""
        doc = _doc(org_a, admin_user, title="Before")
        doc.shared_to.add(user_profile)
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "Before", "shared_to": [str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 403
        assert _shared_ids(doc) == {user_profile.id}

    def test_malformed_list_changes_nothing(
        self, user_client, regular_user, org_a, admin_profile
    ):
        """A 400 must not leave half a write behind.

        The ids used to be parsed after the title was saved and the shares
        cleared, so a malformed list answered 400 with the rename applied and
        every share gone.
        """
        doc = _doc(org_a, regular_user, title="Before")
        doc.shared_to.add(admin_profile)
        response = user_client.put(
            _detail_url(doc.id),
            {"title": "After", "shared_to": "[not json"},
            format="json",
        )
        assert response.status_code == 400
        doc.refresh_from_db()
        assert doc.title == "Before"
        assert _shared_ids(doc) == {admin_profile.id}
