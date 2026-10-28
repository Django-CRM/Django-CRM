"""`/api/devices/`: a phone registers its push token after sign-in and drops
it on sign-out. A token belongs to one org and, inside it, to whoever signed
in on the phone last."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from common.models import DeviceToken, PersonalAccessToken, Profile, User
from common.testing import _make_authenticated_client, rls_org

URL = "/api/devices/"
TOKEN = "fcm-" + "x" * 150


def _register(client, token=TOKEN, **extra):
    body = {"token": token, "platform": "android", "app_version": "1.23.0", **extra}
    return client.post(URL, body, format="json")


def _forget(client, token=TOKEN):
    return client.delete(URL, {"token": token}, format="json")


@pytest.fixture
def ravi(org_a):
    user = User.objects.create_user(email="ravi@example.com", password="x")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


@pytest.fixture
def ravi_client(ravi):
    return _make_authenticated_client(ravi.user, ravi.org, ravi)


class TestRegister:
    def test_register_creates_one_row_for_the_caller(
        self, user_client, regular_user, org_a
    ):
        response = _register(user_client)
        assert response.status_code == 204, response.content
        device = DeviceToken.objects.get()
        assert (device.org_id, device.user_id) == (org_a.id, regular_user.id)
        assert (device.token, device.platform, device.app_version) == (
            TOKEN,
            "android",
            "1.23.0",
        )
        assert device.last_seen_at is not None

    def test_register_again_upserts(self, user_client):
        assert _register(user_client).status_code == 204
        first = DeviceToken.objects.get()
        assert _register(user_client, app_version="1.24.0").status_code == 204
        device = DeviceToken.objects.get()
        assert device.pk == first.pk
        assert device.app_version == "1.24.0"
        assert device.last_seen_at >= first.last_seen_at

    def test_a_device_that_changed_hands_moves_to_the_new_user(
        self, user_client, ravi_client, ravi
    ):
        _register(user_client)
        assert _register(ravi_client).status_code == 204
        device = DeviceToken.objects.get()
        assert device.user_id == ravi.user_id

    def test_the_same_phone_in_another_org_is_its_own_row(
        self, user_client, org_b_client, org_a, org_b
    ):
        _register(user_client)
        with rls_org(org_b):
            assert _register(org_b_client).status_code == 204
            assert DeviceToken.objects.filter(org=org_b).count() == 1
        assert DeviceToken.objects.filter(org=org_a).count() == 1

    def test_the_owner_and_org_come_from_the_token_not_the_body(
        self, user_client, regular_user, admin_user, org_a, org_b
    ):
        response = _register(user_client, user=str(admin_user.id), org=str(org_b.id))
        assert response.status_code == 204
        device = DeviceToken.objects.get()
        assert (device.user_id, device.org_id) == (regular_user.id, org_a.id)

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"token": "", "platform": "android"},
            {"token": TOKEN},
            {"token": TOKEN, "platform": "windows"},
            {"token": "x" * 2049, "platform": "android"},
            {"token": TOKEN, "platform": "android", "app_version": "1" * 33},
            {"token": ["a"], "platform": "android"},
        ],
    )
    def test_a_bad_body_is_refused(self, user_client, body):
        assert user_client.post(URL, body, format="json").status_code == 400
        assert not DeviceToken.objects.exists()

    def test_refused_unauthenticated(self, unauthenticated_client):
        # 403 rather than 401: the middleware refuses on the missing org
        # context before DRF gets to say the caller is unauthenticated.
        assert _register(unauthenticated_client).status_code == 403
        assert _forget(unauthenticated_client).status_code == 403
        assert not DeviceToken.objects.exists()

    def test_refused_without_org(self, regular_user, user_profile):
        client = APIClient()
        token = RefreshToken.for_user(regular_user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        assert _register(client).status_code == 403
        assert _forget(client).status_code == 403
        assert not DeviceToken.objects.exists()


class TestTokenAlphabet:
    """An FCM token is ASCII letters, digits, `_`, `:` and `-`. Anything else
    is not one, and a long multibyte value would overflow the unique index
    entry on Postgres (a 500)."""

    @pytest.mark.parametrize(
        "token",
        ["\u6c49" * 900, "tok en", "tok/en", "tok\nen", "tok%en", "tök"],
    )
    def test_a_token_outside_it_is_refused(self, user_client, token):
        assert _register(user_client, token=token).status_code == 400
        assert _forget(user_client, token=token).status_code == 400
        assert not DeviceToken.objects.exists()

    def test_a_real_shaped_token_is_accepted(self, user_client):
        token = "cX1_y-Z:APA91b" + "Hk9-_" * 30
        assert _register(user_client, token=token).status_code == 204
        assert DeviceToken.objects.get().token == token


class TestPerMemberCap:
    """Five phones per member in each org; registering another drops the one
    seen longest ago."""

    def test_a_member_keeps_their_five_newest(
        self, user_client, regular_user, ravi_client, ravi, org_a, org_b
    ):
        _register(ravi_client, token="fcm-ravi")
        with rls_org(org_b):
            DeviceToken.objects.create(
                org=org_b,
                user=regular_user,
                token="fcm-elsewhere",
                platform="android",
                last_seen_at=timezone.now() - timedelta(days=1),
            )
        tokens = [f"fcm-{i}" for i in range(7)]
        for token in tokens:
            assert _register(user_client, token=token).status_code == 204

        mine = DeviceToken.objects.filter(org=org_a, user=regular_user)
        assert set(mine.values_list("token", flat=True)) == set(tokens[2:])
        assert DeviceToken.objects.filter(user=ravi.user, token="fcm-ravi").exists()
        with rls_org(org_b):
            assert DeviceToken.objects.filter(token="fcm-elsewhere").exists()

    def test_registering_again_makes_a_phone_newest(
        self, user_client, regular_user, org_a
    ):
        for token in [f"fcm-{i}" for i in range(5)]:
            _register(user_client, token=token)
        _register(user_client, token="fcm-0")
        _register(user_client, token="fcm-5")

        mine = DeviceToken.objects.filter(org=org_a, user=regular_user)
        assert set(mine.values_list("token", flat=True)) == {
            "fcm-0",
            "fcm-2",
            "fcm-3",
            "fcm-4",
            "fcm-5",
        }


class TestOnlyTheSignedInApp:
    """A registered phone keeps receiving the member's lead alerts after the
    credential that registered it is revoked, so, like minting a token, it is
    a session's to do."""

    def test_a_personal_access_token_cannot(self, admin_profile):
        raw, _ = PersonalAccessToken.generate(profile=admin_profile, name="cli")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert _register(client).status_code == 403
        assert _forget(client).status_code == 403
        assert not DeviceToken.objects.exists()

    def test_the_org_api_key_cannot(self, org_a, admin_profile):
        client = APIClient()
        client.credentials(HTTP_TOKEN=org_a.api_key)
        assert _register(client).status_code == 403
        assert not DeviceToken.objects.exists()

    def test_a_signed_in_session_can(self, admin_client):
        assert _register(admin_client).status_code == 204


class TestForget:
    def test_delete_own(self, user_client):
        _register(user_client)
        assert _forget(user_client).status_code == 204
        assert not DeviceToken.objects.exists()

    def test_delete_answers_204_when_there_is_nothing(self, user_client):
        assert _forget(user_client).status_code == 204

    def test_delete_own_only(self, user_client, ravi_client):
        _register(ravi_client)
        assert _forget(user_client).status_code == 204
        assert DeviceToken.objects.count() == 1

    def test_other_orgs_token_untouched(self, user_client, org_b_client, org_b):
        with rls_org(org_b):
            _register(org_b_client)
        _register(user_client)
        assert _forget(user_client).status_code == 204
        with rls_org(org_b):
            assert DeviceToken.objects.filter(org=org_b, token=TOKEN).count() == 1

    def test_a_delete_without_a_token_is_refused(self, user_client):
        _register(user_client)
        assert user_client.delete(URL, {}, format="json").status_code == 400
        assert DeviceToken.objects.count() == 1


def test_the_api_never_returns_a_token(user_client):
    for response in (_register(user_client), _forget(user_client)):
        assert TOKEN not in response.content.decode()


def test_the_delete_body_is_in_the_schema():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    operations = schema["paths"]["/api/devices/"]
    for method, name, fields in (
        ("post", "DeviceTokenRequest", {"token", "platform", "app_version"}),
        ("delete", "DeviceTokenDeleteRequest", {"token"}),
    ):
        body = operations[method]["requestBody"]["content"]["application/json"]
        assert body["schema"] == {"$ref": f"#/components/schemas/{name}"}
        assert set(schema["components"]["schemas"][name]["properties"]) == fields


@pytest.mark.postgres_only
def test_rls_hides_another_orgs_device_tokens(org_a, org_b, user_b, regular_user):
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    mine = DeviceToken.objects.create(
        org=org_a, user=regular_user, token="mine", platform="android"
    )
    with rls_org(org_b):
        DeviceToken.objects.create(
            org=org_b, user=user_b, token="theirs", platform="android"
        )
    assert list(DeviceToken.objects.all()) == [mine]
    assert not DeviceToken.objects.filter(token="theirs").exists()
