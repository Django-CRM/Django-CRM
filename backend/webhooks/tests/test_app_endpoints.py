"""Endpoints a connected app owns: its subscriptions, not the admin's webhooks.

An endpoint with `app` set answers to that app member alone. The admin's
webhook routes never list, change, test, rotate, redeliver or take it over,
the admin's cap does not count it, and automation cannot send to it. It is
paused only when its app member is inactive, never for its creator's
standing, and it delivers while the member is active.
"""

from unittest import mock

import pytest

from automation.actions import RunContext, StepFailed, plan_action
from automation.models import AutomationRule
from automation.objects import adapter_for
from automation.validation import validate_rule
from common.app_members import create_app_member, deactivate_app_member
from common.audit_log import SecurityAuditLog
from common.models import Profile, User
from opportunity.models import DealPipeline, Opportunity
from webhooks import ownership, views
from webhooks.emit import envelope, queue_delivery
from webhooks.models import FAILED, SUCCEEDED, WebhookDelivery, WebhookEndpoint
from webhooks.tasks import attempt_delivery

BASE = "/api/webhooks/"


@pytest.fixture(autouse=True)
def dns_and_broker(public_dns):
    with mock.patch("webhooks.emit.deliver_webhook.delay"):
        yield


@pytest.fixture
def app_member(org_a):
    return create_app_member(org_a, "Sync app")


def _app_endpoint(app_member, **extra):
    fields = {
        "org": app_member.org,
        "app": app_member,
        "url": "https://app.example.com/hook",
        "events": ["lead.created"],
        "created_by": app_member.user,
    }
    fields.update(extra)
    return WebhookEndpoint.objects.create(**fields)


@pytest.fixture
def app_hook(app_member):
    return _app_endpoint(app_member)


def _settled(endpoint):
    delivery = queue_delivery(
        endpoint, "lead.created", envelope(endpoint.org_id, "lead.created", {})
    )
    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        status=FAILED, next_attempt_at=None
    )
    return delivery


def _send(delivery, status=200):
    with mock.patch("webhooks.tasks.ssrf.post", return_value=status) as post:
        result = attempt_delivery(str(delivery.id), str(delivery.org_id))
    return result, post


ROUTES = [
    ("detail", "get", "{hook}/", None),
    ("patch", "patch", "{hook}/", {"url": "https://admin.example.com/take"}),
    ("delete", "delete", "{hook}/", None),
    ("test", "post", "{hook}/test/", None),
    ("rotate", "post", "{hook}/rotate-secret/", None),
    ("deliveries", "get", "{hook}/deliveries/", None),
    ("redeliver", "post", "deliveries/{delivery}/redeliver/", None),
]


@pytest.mark.parametrize(
    "method,path,body", [route[1:] for route in ROUTES], ids=[r[0] for r in ROUTES]
)
def test_admin_webhook_views_hide_app_endpoints(
    method, path, body, admin_client, endpoint, app_hook
):
    delivery = _settled(app_hook)
    before = WebhookEndpoint.objects.values().get(pk=app_hook.pk)

    listed = admin_client.get(BASE).json()["endpoints"]
    assert [row["id"] for row in listed] == [str(endpoint.id)]

    url = BASE + path.format(hook=app_hook.id, delivery=delivery.id)
    response = getattr(admin_client, method)(url, body, format="json")
    assert response.status_code == 404
    assert WebhookEndpoint.objects.values().get(pk=app_hook.pk) == before
    assert WebhookDelivery.objects.filter(endpoint=app_hook).count() == 1

    # The admin's own endpoint answers on the same route.
    own = BASE + path.format(hook=endpoint.id, delivery=_settled(endpoint).id)
    assert getattr(admin_client, method)(own, body, format="json").status_code < 300


def test_cap_ignores_app_endpoints(admin_client, admin_profile, app_member, org_a):
    for n in range(3):
        _app_endpoint(app_member, url=f"https://app.example.com/{n}")
    for n in range(views.MAX_ENDPOINTS_PER_ORG - 1):
        WebhookEndpoint.objects.create(
            org=org_a,
            url=f"https://hooks.example.com/{n}",
            events=["lead.created"],
            created_by=admin_profile.user,
        )
    new = {"url": "https://hooks.example.com/last", "events": ["lead.created"]}

    assert admin_client.post(BASE, new, format="json").status_code == 201
    refused = admin_client.post(BASE, new, format="json")
    assert refused.status_code == 400
    assert WebhookEndpoint.objects.filter(app__isnull=True).count() == (
        views.MAX_ENDPOINTS_PER_ORG
    )


def test_app_endpoint_pauses_only_when_its_member_is_inactive(app_member, app_hook):
    # Its creator is the app's own user, which is never active, so the
    # creator rules alone would pause it before its first send.
    assert ownership._problem(app_hook.created_by, app_member) == ownership.DEACTIVATED
    assert ownership.creator_problem(app_hook) is None

    deactivate_app_member(app_member)
    app_hook.refresh_from_db()
    assert app_hook.is_active is True
    assert ownership.creator_problem(app_hook) == ownership.APP_DISCONNECTED

    delivery = queue_delivery(
        app_hook, "lead.created", envelope(app_hook.org_id, "lead.created", {})
    )
    result, post = _send(delivery)
    post.assert_not_called()
    assert result.status == FAILED
    app_hook.refresh_from_db()
    assert app_hook.is_active is False
    assert app_hook.disabled_reason == ownership.APP_DISCONNECTED


def test_admin_demotion_or_deactivation_never_pauses_an_app_endpoint(
    admin_client, org_a, app_member
):
    user = User.objects.create_user(email="creator@test.com", password="x")
    creator = Profile.objects.create(user=user, org=org_a, role="ADMIN", is_active=True)
    theirs = WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created"],
        created_by=user,
    )
    # The worst case: an app endpoint stamped with a person as its creator.
    app_hook = _app_endpoint(app_member, created_by=user)

    demoted = admin_client.patch(
        f"/api/user/{user.id}/", {"role": "USER"}, format="json"
    )
    assert demoted.status_code == 200
    stopped = admin_client.post(f"/api/user/{user.id}/status/", {"status": "Inactive"})
    assert stopped.status_code == 200
    user.is_active = False
    user.save(update_fields=["is_active"])
    creator.delete()

    theirs.refresh_from_db()
    app_hook.refresh_from_db()
    assert theirs.is_active is False
    assert app_hook.is_active is True
    assert app_hook.disabled_reason == ""
    assert not SecurityAuditLog.objects.filter(
        event_type="WEBHOOK_PAUSED", metadata__endpoint_id=str(app_hook.id)
    ).exists()


def test_app_endpoint_delivers_while_its_member_is_active(app_hook):
    delivery = queue_delivery(
        app_hook, "lead.created", envelope(app_hook.org_id, "lead.created", {"a": 1})
    )
    result, post = _send(delivery, status=200)
    post.assert_called_once()
    assert post.call_args.args[0] == app_hook.url
    assert result.status == SUCCEEDED
    app_hook.refresh_from_db()
    assert app_hook.is_active is True


def test_automation_cannot_send_to_an_app_endpoint(
    admin_client, admin_profile, org_a, endpoint, app_hook
):
    catalogue = admin_client.get("/api/automation/catalogue/").json()
    assert [row["id"] for row in catalogue["endpoints"]] == [str(endpoint.id)]

    def errors(hook):
        rule = {
            "object_key": "lead",
            "trigger": {"event": "created", "field": None, "to": None},
            "match": "all",
            "conditions": [],
            "actions": [{"type": "webhook", "endpoint": str(hook.id)}],
        }
        return validate_rule(org_a, rule)

    assert errors(endpoint) == {}
    assert set(errors(app_hook)) == {"actions[0].endpoint"}

    # A rule saved before the check, run now.
    deal = Opportunity.objects.create(
        org=org_a,
        name="Renewal",
        pipeline=DealPipeline.default_for(org_a),
        stage="QUALIFICATION",
    )
    rule = AutomationRule.objects.create(
        org=org_a,
        name="Deal won",
        object_key="deal",
        trigger={"event": "updated", "field": None, "to": None},
        actions=[],
        owner=admin_profile,
        is_active=True,
    )
    ctx = RunContext(org_a, rule, deal, adapter_for(org_a, "deal"), admin_profile)
    assert plan_action(ctx, 0, {"type": "webhook", "endpoint": str(endpoint.id)})
    with pytest.raises(StepFailed):
        plan_action(ctx, 0, {"type": "webhook", "endpoint": str(app_hook.id)})
