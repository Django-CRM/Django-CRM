"""`common.push`: phone pushes through Firebase Cloud Messaging's HTTP v1 API.

Google is never called: `_session` is replaced by a fake that answers each
post from a script. A token is a secret of the phone it belongs to, so no
log line, retry argument or reply may carry one.
"""

import json
import logging
from datetime import timedelta

import pytest
import requests
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from google.auth.exceptions import RefreshError

from common import push
from common.models import DeviceToken, Profile, User
from common.testing import restore_rls_context, rls_org

PROJECT = "bottlecrm-test"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n"
TOKEN = "tok-secret-" + "q" * 140
OTHER = "tok-other-" + "r" * 140
SEND_URL = f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send"


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    """Answers each post with the next scripted reply for its token."""

    def __init__(self, script):
        self.script = {token: list(replies) for token, replies in script.items()}
        self.posts = []

    def post(self, url, json=None, timeout=None):
        token = json["message"]["token"]
        self.posts.append((url, json, timeout))
        reply = self.script[token].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _error(code, status, error_code=None, field=None):
    details = []
    if error_code:
        details.append(
            {
                "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                "errorCode": error_code,
            }
        )
    if field:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.BadRequest",
                "fieldViolations": [{"field": field, "description": "Invalid"}],
            }
        )
    return FakeResponse(
        code, {"error": {"code": code, "status": status, "details": details}}
    )


OK = FakeResponse(200, {"name": f"projects/{PROJECT}/messages/1"})


@pytest.fixture
def service_account(tmp_path, settings):
    path = tmp_path / "fcm.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": PROJECT,
                "private_key": PRIVATE_KEY,
                "client_email": "push@bottlecrm-test.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    settings.FCM_SERVICE_ACCOUNT_FILE = str(path)
    return path


@pytest.fixture
def asha(org_a):
    user = User.objects.create_user(email="asha@example.com", password="x")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


@pytest.fixture
def phone(asha):
    return DeviceToken.objects.create(
        org=asha.org, user=asha.user, token=TOKEN, platform="android"
    )


@pytest.fixture
def fake(monkeypatch, service_account):
    def install(script):
        session = FakeSession(script)
        monkeypatch.setattr(push, "_session", lambda info: session)
        return session

    return install


def _send(asha, **kwargs):
    """Run the task the way a worker would, retries included."""
    result = push.send_push.apply(
        args=(str(asha.id), "BottleCRM", "New lead: Maria", "/leads/1"),
        kwargs={"verb": "lead.created", **kwargs},
    )
    restore_rls_context()
    return result


class TestConfigured:
    def test_empty_is_off(self, settings):
        settings.FCM_SERVICE_ACCOUNT_FILE = ""
        assert push.push_configured() is False

    def test_a_missing_file_is_off(self, settings, tmp_path):
        settings.FCM_SERVICE_ACCOUNT_FILE = str(tmp_path / "missing.json")
        assert push.push_configured() is False

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "[]",
            json.dumps({"type": "service_account"}),
            json.dumps({"type": "authorized_user", "project_id": PROJECT}),
        ],
    )
    def test_an_unreadable_file_is_off(self, settings, tmp_path, content):
        path = tmp_path / "fcm.json"
        path.write_text(content)
        settings.FCM_SERVICE_ACCOUNT_FILE = str(path)
        assert push.push_configured() is False

    def test_a_service_account_file_is_on(self, service_account):
        assert push.push_configured() is True

    def test_the_test_settings_leave_it_off(self):
        from django.conf import settings

        assert settings.FCM_SERVICE_ACCOUNT_FILE == ""


class TestSend:
    def test_one_v1_message_per_token(self, asha, phone, fake, org_a):
        DeviceToken.objects.create(
            org=org_a, user=asha.user, token=OTHER, platform="android"
        )
        session = fake({TOKEN: [OK], OTHER: [OK]})
        _send(asha)

        assert sorted(
            json_body["message"]["token"] for _, json_body, _ in session.posts
        ) == sorted([TOKEN, OTHER])
        url, body, timeout = session.posts[0]
        assert url == SEND_URL
        assert timeout == 10
        message = body["message"]
        assert message["notification"] == {
            "title": "BottleCRM",
            "body": "New lead: Maria",
        }
        assert message["data"] == {"link": "/leads/1", "verb": "lead.created"}
        assert DeviceToken.objects.count() == 2

    def test_only_the_profiles_own_tokens_in_its_org(
        self, asha, phone, fake, org_a, org_b, admin_profile
    ):
        DeviceToken.objects.create(
            org=org_a, user=admin_profile.user, token=OTHER, platform="android"
        )
        with rls_org(org_b):
            DeviceToken.objects.create(
                org=org_b, user=asha.user, token="tok-b", platform="android"
            )
        session = fake({TOKEN: [OK]})
        _send(asha)
        assert [body["message"]["token"] for _, body, _ in session.posts] == [TOKEN]

    def test_only_the_five_newest_phones(self, asha, fake, org_a):
        now = timezone.now()
        tokens = [f"tok-{age}-" + "z" * 40 for age in range(7)]
        for age, token in enumerate(tokens):
            DeviceToken.objects.create(
                org=org_a,
                user=asha.user,
                token=token,
                platform="android",
                last_seen_at=now - timedelta(hours=age),
            )
        session = fake({token: [OK] for token in tokens})
        _send(asha)
        sent = [body["message"]["token"] for _, body, _ in session.posts]
        assert sent == tokens[:5]

    def test_a_phone_unseen_for_a_sign_in_lifetime_is_dropped(
        self, asha, fake, org_a, admin_profile, settings
    ):
        """It has signed out, or went offline signing out, or was registered
        by someone who has since lost the account."""
        lifetime = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
        now = timezone.now()
        for user, token, seen in (
            (asha.user, TOKEN, now - lifetime + timedelta(minutes=1)),
            (asha.user, OTHER, now - lifetime - timedelta(minutes=1)),
            (admin_profile.user, "tok-admin", now - lifetime - timedelta(days=1)),
        ):
            DeviceToken.objects.create(
                org=org_a, user=user, token=token, platform="android", last_seen_at=seen
            )
        session = fake({TOKEN: [OK], OTHER: [OK]})
        _send(asha)
        assert [body["message"]["token"] for _, body, _ in session.posts] == [TOKEN]
        assert set(DeviceToken.objects.values_list("token", flat=True)) == {
            TOKEN,
            "tok-admin",
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"is_active": False},
            {"lead_alerts_push": False},
        ],
    )
    def test_nothing_for_a_profile_that_should_not_get_it(
        self, asha, phone, fake, change
    ):
        Profile.objects.filter(pk=asha.pk).update(**change)
        session = fake({})
        _send(asha)
        assert session.posts == []

    def test_nothing_for_an_inactive_user(self, asha, phone, fake):
        User.objects.filter(pk=asha.user_id).update(is_active=False)
        session = fake({})
        _send(asha)
        assert session.posts == []

    def test_nothing_when_push_is_off(self, asha, phone, settings, monkeypatch):
        settings.FCM_SERVICE_ACCOUNT_FILE = ""
        called = []
        monkeypatch.setattr(push, "_session", lambda info: called.append(info))
        _send(asha)
        assert called == []

    def test_deletes_unregistered_tokens(self, asha, phone, fake):
        fake({TOKEN: [_error(404, "NOT_FOUND", "UNREGISTERED")]})
        _send(asha)
        assert not DeviceToken.objects.exists()

    def test_deletes_a_token_fcm_calls_invalid(self, asha, phone, fake):
        fake(
            {
                TOKEN: [
                    _error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "message.token")
                ]
            }
        )
        _send(asha)
        assert not DeviceToken.objects.exists()

    def test_keeps_the_token_for_any_other_invalid_argument(
        self, asha, phone, fake, caplog
    ):
        fake(
            {
                TOKEN: [
                    _error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "message.data")
                ]
            }
        )
        with caplog.at_level(logging.WARNING, logger="common.push"):
            _send(asha)
        assert DeviceToken.objects.count() == 1
        assert "400" in caplog.text

    def test_a_404_that_is_not_unregistered_keeps_the_token(self, asha, phone, fake):
        fake({TOKEN: [_error(404, "NOT_FOUND")]})
        _send(asha)
        assert DeviceToken.objects.count() == 1

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_retries_5xx_and_429(self, asha, phone, fake, code):
        session = fake({TOKEN: [_error(code, "UNAVAILABLE"), OK]})
        _send(asha)
        assert len(session.posts) == 2
        assert DeviceToken.objects.count() == 1

    def test_a_retry_goes_only_to_the_tokens_that_failed(
        self, asha, phone, fake, org_a
    ):
        DeviceToken.objects.create(
            org=org_a, user=asha.user, token=OTHER, platform="android"
        )
        session = fake({TOKEN: [_error(503, "UNAVAILABLE"), OK], OTHER: [OK]})
        _send(asha)
        tokens = [body["message"]["token"] for _, body, _ in session.posts]
        assert sorted(tokens) == sorted([TOKEN, TOKEN, OTHER])

    def test_gives_up_after_three_retries(self, asha, phone, fake, caplog):
        failing = [_error(503, "UNAVAILABLE") for _ in range(4)]
        session = fake({TOKEN: failing})
        with caplog.at_level(logging.WARNING, logger="common.push"):
            _send(asha)
        assert len(session.posts) == 4
        assert DeviceToken.objects.count() == 1
        assert "gave up" in caplog.text

    def test_a_connection_error_is_retried(self, asha, phone, fake):
        session = fake({TOKEN: [requests.ConnectionError("down"), OK]})
        _send(asha)
        assert len(session.posts) == 2

    def test_credentials_google_refuses_are_logged_once_and_not_retried(
        self, asha, phone, fake, caplog
    ):
        session = fake({TOKEN: [RefreshError("invalid_grant"), OK]})
        with caplog.at_level(logging.WARNING, logger="common.push"):
            _send(asha)
        assert len(session.posts) == 1
        assert "credentials" in caplog.text

    def test_never_logs_the_token(self, asha, phone, fake, caplog, org_a):
        DeviceToken.objects.create(
            org=org_a, user=asha.user, token=OTHER, platform="android"
        )
        fake(
            {
                TOKEN: [_error(503, "UNAVAILABLE") for _ in range(4)],
                OTHER: [_error(403, "PERMISSION_DENIED", "SENDER_ID_MISMATCH")],
            }
        )
        with caplog.at_level(logging.DEBUG):
            _send(asha)
        assert caplog.text
        for secret in (TOKEN, OTHER, "tok-", PRIVATE_KEY, "not-a-real-key"):
            assert secret not in caplog.text

    def test_retries_carry_device_ids_not_tokens(self, asha, phone, fake, monkeypatch):
        fake({TOKEN: [_error(503, "UNAVAILABLE")]})
        seen = {}

        def retry(*args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop")

        monkeypatch.setattr(push.send_push, "retry", retry)
        _send(asha)
        assert seen["kwargs"]["devices"] == [str(phone.pk)]
        assert TOKEN not in json.dumps(seen["kwargs"])
        assert seen["countdown"] > 0

    def test_retry_after_is_honoured(self):
        reply = FakeResponse(429, headers={"Retry-After": "120"})
        assert push._retry_delay(reply, 0) == 120
        assert push._retry_delay(FakeResponse(503), 0) == 30
        assert push._retry_delay(FakeResponse(503), 2) == 120
        assert (
            push._retry_delay(FakeResponse(429, headers={"Retry-After": "x"}), 1) == 60
        )
        assert (
            push._retry_delay(FakeResponse(429, headers={"Retry-After": "99999"}), 0)
            == 600
        )

    def test_rls_context_is_set_before_tokens_are_read(
        self, asha, phone, fake, monkeypatch
    ):
        fake({TOKEN: [OK]})
        calls = []
        with CaptureQueriesContext(connection) as ctx:

            def record(org_id):
                read = any("device_token" in q["sql"] for q in ctx.captured_queries)
                calls.append((str(org_id), read))

            monkeypatch.setattr(push, "set_rls_context", record)
            monkeypatch.setattr(push, "clear_rls_context", lambda: None)
            _send(asha)
            read = any("device_token" in q["sql"] for q in ctx.captured_queries)
        assert calls == [(str(asha.org_id), False)]
        assert read
