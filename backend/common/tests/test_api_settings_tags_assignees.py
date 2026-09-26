"""API settings resolve ``tags`` and ``lead_assigned_to`` inside the caller's org.

Both write paths looked a tag up by name with no org filter, so wherever RLS is
not enforced (a superuser DB role, as in dev and test) they attached another
org's tag. A name nobody had used yet went to ``Tags.objects.create(name=...)``
with no org, which cannot be saved, so that branch always answered 500.
``lead_assigned_to`` was ``.add(*ids)`` straight from the body: another org's
profile id attached, and ``webforms/legacy.py`` then hands leads to it.

The request contract: ``tags`` is a list of tag names and ``lead_assigned_to``
a list of profile ids, on POST, PUT and PATCH alike (Swagger used to say ids
for both, and PATCH used to ignore both). An assignee that is not an active
member of the caller's org is a 400, not silently dropped.
"""

import pytest
from rest_framework import status

from common.models import APISettings, Tags

LIST_URL = "/api/api-settings/"


def _detail_url(pk):
    return f"/api/api-settings/{pk}/"


def _setting(org, user):
    return APISettings.objects.create(
        title="Existing", website="https://existing.com", org=org, created_by=user
    )


def _body(**extra):
    return {"title": "Site", "website": "https://site.com", **extra}


@pytest.fixture(params=["post", "put"])
def write(request, admin_client, org_a, admin_user):
    """One call per write path: returns (response, setting or None)."""

    def _write(body):
        if request.param == "post":
            response = admin_client.post(LIST_URL, body, format="json")
            return response, APISettings.objects.filter(
                org=org_a, title=body.get("title")
            ).first()
        setting = _setting(org_a, admin_user)
        response = admin_client.put(_detail_url(setting.pk), body, format="json")
        setting.refresh_from_db()
        return response, setting

    _write.method = request.param
    return _write


@pytest.mark.django_db
class TestTagsByName:
    def test_existing_name_attaches_the_callers_tag_not_another_orgs(
        self, write, org_a, org_b
    ):
        Tags.objects.create(name="VIP", org=org_b)
        mine = Tags.objects.create(name="VIP", org=org_a)

        response, setting = write(_body(tags=["VIP"]))

        assert response.status_code in (200, 201), response.data
        assert list(setting.tags.all()) == [mine]

    def test_name_only_another_org_has_creates_one_in_the_callers_org(
        self, write, org_a, org_b
    ):
        foreign = Tags.objects.create(name="Partner", org=org_b)

        response, setting = write(_body(tags=["Partner"]))

        assert response.status_code in (200, 201), response.data
        attached = list(setting.tags.all())
        assert len(attached) == 1
        assert attached[0].org_id == org_a.id
        assert attached[0].pk != foreign.pk

    def test_new_name_is_created_in_the_callers_org(self, write, org_a):
        response, setting = write(_body(tags=["Brand New"]))

        assert response.status_code in (200, 201), response.data
        tag = Tags.objects.get(org=org_a, slug=Tags.slug_for("Brand New"))
        assert list(setting.tags.all()) == [tag]

    def test_name_matches_on_the_tag_slug(self, write, org_a):
        """``Tags.slug_for`` is the one owner of the name rule, so "vip" and
        "VIP" are the same tag here as everywhere else."""
        mine = Tags.objects.create(name="VIP", org=org_a)

        response, setting = write(_body(tags=["vip"]))

        assert response.status_code in (200, 201), response.data
        assert list(setting.tags.all()) == [mine]
        assert Tags.objects.filter(org=org_a).count() == 1

    @pytest.mark.parametrize(
        "tags", ["[not json", {"name": "VIP"}, 5, [5], [None], [["VIP"]], ["🎉"]]
    )
    def test_malformed_tags_are_400(self, write, org_a, tags):
        before = Tags.objects.count()

        response, setting = write(_body(title="Malformed", tags=tags))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True
        assert "tags" in response.data["errors"]
        assert Tags.objects.count() == before
        if write.method == "post":
            # Validated before the setting is saved: no half-created key.
            assert setting is None
        else:
            assert setting.title == "Existing"


@pytest.mark.django_db
class TestLeadAssignedTo:
    def test_own_active_profile_is_attached(self, write, admin_profile):
        response, setting = write(_body(lead_assigned_to=[str(admin_profile.id)]))

        assert response.status_code in (200, 201), response.data
        assert list(setting.lead_assigned_to.all()) == [admin_profile]

    def test_another_orgs_profile_is_refused(self, write, admin_profile, profile_b):
        response, setting = write(
            _body(
                title="Foreign",
                lead_assigned_to=[str(admin_profile.id), str(profile_b.id)],
            )
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lead_assigned_to" in response.data["errors"]
        if write.method == "post":
            assert setting is None
        else:
            assert setting.title == "Existing"
            assert setting.lead_assigned_to.count() == 0

    def test_inactive_profile_is_refused(self, write, user_profile):
        user_profile.is_active = False
        user_profile.save()

        response, setting = write(
            _body(title="Inactive", lead_assigned_to=[str(user_profile.id)])
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lead_assigned_to" in response.data["errors"]

    def test_unknown_id_is_refused(self, write):
        response, _setting_obj = write(
            _body(
                title="Unknown",
                lead_assigned_to=["00000000-0000-0000-0000-000000000000"],
            )
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lead_assigned_to" in response.data["errors"]

    @pytest.mark.parametrize(
        "assignees", [["not-a-uuid"], "[not json", {"id": 1}, 5, [5]]
    )
    def test_malformed_assignees_are_400(self, write, assignees):
        response, setting = write(_body(title="Malformed", lead_assigned_to=assignees))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True
        assert "lead_assigned_to" in response.data["errors"]
        if write.method == "post":
            assert setting is None
        else:
            assert setting.title == "Existing"


@pytest.mark.django_db
def test_put_on_another_orgs_setting_is_404(admin_client, org_b, user_b):
    """The object is fetched inside the caller's org, so another org's setting
    cannot be rewritten even by an admin."""
    setting = _setting(org_b, user_b)

    response = admin_client.put(
        _detail_url(setting.pk), _body(title="Hijacked"), format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    setting.refresh_from_db()
    assert setting.title == "Existing"


@pytest.mark.django_db
class TestPatch:
    """PATCH used to save `title`/`website` and drop both relations unread."""

    def _patch(self, client, setting, body):
        response = client.patch(_detail_url(setting.pk), body, format="json")
        setting.refresh_from_db()
        return response

    def test_tags_by_name_replace_inside_the_callers_org(
        self, admin_client, org_a, org_b, admin_user
    ):
        setting = _setting(org_a, admin_user)
        old = Tags.objects.create(name="Old", org=org_a)
        setting.tags.add(old)
        foreign = Tags.objects.create(name="VIP", org=org_b)

        response = self._patch(admin_client, setting, {"tags": ["VIP"]})

        assert response.status_code == status.HTTP_200_OK, response.data
        attached = list(setting.tags.all())
        assert len(attached) == 1
        assert attached[0].org_id == org_a.id
        assert attached[0].pk != foreign.pk

    def test_malformed_tags_are_400_and_change_nothing(
        self, admin_client, org_a, admin_user
    ):
        setting = _setting(org_a, admin_user)

        response = self._patch(admin_client, setting, {"title": "New", "tags": [5]})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "tags" in response.data["errors"]
        assert setting.title == "Existing"

    def test_assignees_replace_with_an_active_org_member(
        self, admin_client, org_a, admin_user, admin_profile, user_profile
    ):
        setting = _setting(org_a, admin_user)
        setting.lead_assigned_to.add(admin_profile)

        response = self._patch(
            admin_client, setting, {"lead_assigned_to": [str(user_profile.id)]}
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert list(setting.lead_assigned_to.all()) == [user_profile]

    def test_another_orgs_profile_is_refused(
        self, admin_client, org_a, admin_user, admin_profile, profile_b
    ):
        setting = _setting(org_a, admin_user)
        setting.lead_assigned_to.add(admin_profile)

        response = self._patch(
            admin_client, setting, {"lead_assigned_to": [str(profile_b.id)]}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lead_assigned_to" in response.data["errors"]
        assert list(setting.lead_assigned_to.all()) == [admin_profile]

    def test_absent_relations_are_left_alone(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        setting = _setting(org_a, admin_user)
        tag = Tags.objects.create(name="Keep", org=org_a)
        setting.tags.add(tag)
        setting.lead_assigned_to.add(admin_profile)

        response = self._patch(admin_client, setting, {"title": "Renamed"})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert setting.title == "Renamed"
        assert list(setting.tags.all()) == [tag]
        assert list(setting.lead_assigned_to.all()) == [admin_profile]

    def test_member_is_refused(self, user_client, org_a, admin_user):
        setting = _setting(org_a, admin_user)

        response = self._patch(user_client, setting, {"tags": ["VIP"]})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert setting.tags.count() == 0


def test_swagger_documents_tags_as_names_and_assignees_as_ids():
    from common.serializer import APISettingsSwaggerSerializer

    fields = APISettingsSwaggerSerializer().fields
    assert fields["tags"].child.__class__.__name__ == "CharField"
    assert fields["lead_assigned_to"].child.__class__.__name__ == "UUIDField"
