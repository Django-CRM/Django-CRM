"""App members: an org member that is a connected app, not a person.

It acts as an admin through a token it owns, and is never among people: no
list, picker, team, rotation, per-person row, alert or email includes it, the
org key never acts as it, and it cannot sign in. Each surface below is driven
with the app member planted in the relation it reads, past every serializer,
so the test fails if a query reaches it some other way.
"""

import secrets
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import authenticate
from django.core import mail
from django.core.cache import cache
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.test import Client, RequestFactory
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from cases.models import Case, CaseWatcher
from cases.notifications import _ensure_watcher, dispatch_for_comment, resolve_mentions
from common.app_members import (
    APP_MEMBER_EMAIL_DOMAIN,
    create_app_member,
    deactivate_app_member,
)
from common.models import (
    MagicLinkToken,
    Notification,
    PersonalAccessToken,
    Profile,
    Teams,
    User,
)
from common.pat_auth import PATAuthentication, resolve_valid_pat
from common.tasks import send_magic_link_email
from leads import alerts
from leads.models import Lead
from leads.tasks import send_email_to_assigned_user, send_enquired_again_email
from tasks.models import Board, BoardMember
from webforms.models import WebForm
from webforms.service import rotation_assignee


@pytest.fixture
def app_member(org_a, admin_profile):
    # Made after the human admin, so it is the newest admin: a query still
    # picking "an admin" by recency would pick it.
    return create_app_member(org_a, "Sync app")


@pytest.fixture
def colleague(org_a):
    user = User.objects.create_user(
        email="colleague@example.com", password="x", name="Colleague"
    )
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


def _token_client(profile, scopes=None):
    raw, pat = PersonalAccessToken.generate(profile=profile, name="key", scopes=scopes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client, raw, pat


def test_an_app_member_is_an_admin_whose_user_cannot_sign_in(app_member, org_a):
    user = app_member.user
    assert user.email.endswith(f"@{APP_MEMBER_EMAIL_DOMAIN}")
    assert user.name == "Sync app"
    assert user.is_active is False
    assert not user.has_usable_password()
    assert (app_member.org, app_member.role, app_member.is_active) == (
        org_a,
        "ADMIN",
        True,
    )
    assert not Profile.objects.filter(pk=app_member.pk).exists()
    assert Profile.all_objects.filter(pk=app_member.pk, is_app=True).exists()


def test_deactivate_refuses_a_person(admin_profile):
    with pytest.raises(ValueError):
        deactivate_app_member(admin_profile)
    admin_profile.refresh_from_db()
    assert admin_profile.is_active is True


# ---------------------------------------------------------------------------
# Never among people
# ---------------------------------------------------------------------------


def _member_list(ctx):
    body = ctx.admin_client.get("/api/users/").json()
    rows = (
        body["active_users"]["active_users"] + body["inactive_users"]["inactive_users"]
    )
    return {row["id"] for row in rows}


def _assignable_profiles(ctx):
    body = ctx.admin_client.get("/api/users/get-teams-and-users/").json()
    return {row["id"] for row in body["profiles"]}


def _team_members(ctx):
    team = Teams.objects.create(org=ctx.org, name="Support")
    team.users.add(ctx.colleague, ctx.app)
    body = ctx.admin_client.get(f"/api/teams/{team.id}/").json()
    return {row["id"] for row in body["team"]["users"]}


def _org_key_admin(ctx):
    body = APIClient().get("/api/profile/", HTTP_TOKEN=ctx.org.api_key).json()
    return {body["user_obj"]["id"]}


def _lead_routing_rotation(ctx):
    form = WebForm.objects.create(
        name="Contact us",
        org=ctx.org,
        is_published=True,
        assignment_mode=WebForm.ASSIGN_ROTATION,
        created_by=ctx.admin.user,
    )
    form.rotation_members.add(ctx.colleague, ctx.app, ctx.admin)
    chosen = set()
    for _ in range(6):
        with transaction.atomic():
            chosen.add(str(rotation_assignee(form).id))
    return chosen


def _outcome_rows(ctx):
    lead = Lead.objects.create(org=ctx.org, first_name="Won", status="converted")
    lead.assigned_to.add(ctx.app, ctx.colleague)
    body = ctx.admin_client.get("/api/leads/outcomes/").json()
    return {row["profile"] for row in body["rows"]}


def _response_time_rows(ctx):
    Lead.objects.create(
        org=ctx.org,
        first_name="Asked",
        first_response_by=ctx.app,
        first_response_at=timezone.now(),
    ).assigned_to.add(ctx.app)
    body = ctx.admin_client.get("/api/leads/response-times/").json()
    return {row["profile"] for row in body["rows"]}


def _ticket_agent_rows(ctx):
    case = Case.objects.create(
        org=ctx.org, name="Printer", status="Assigned", priority="Normal"
    )
    case.assigned_to.add(ctx.app, ctx.colleague)
    agents = ctx.admin_client.get("/api/cases/analytics/agents/").json()["results"]
    service = ctx.admin_client.get("/api/cases/analytics/service/").json()
    return {row["profile_id"] for row in agents} | {
        row["id"] for row in service["by_agent"] if row["id"]
    }


def _ticket_watchers(ctx):
    case = Case.objects.create(org=ctx.org, name="Printer", status="New")
    for profile in (ctx.colleague, ctx.app):
        CaseWatcher.objects.create(case=case, profile=profile, org=ctx.org)
    body = ctx.admin_client.get(f"/api/cases/{case.id}/watchers/").json()
    assert body["count"] == len(body["watchers"])
    return {row["profile_id"] for row in body["watchers"]}


def _board_members(ctx):
    board = Board.objects.create(org=ctx.org, name="Launch", owner=ctx.admin)
    BoardMember.objects.create(board=board, profile=ctx.admin, role="owner")
    BoardMember.objects.create(board=board, profile=ctx.app, role="member")
    body = ctx.admin_client.get(f"/api/boards/{board.id}/").json()
    assert body["member_count"] == len(body["members"])
    return {row["profile"]["id"] for row in body["members"]}


def _mentions(ctx):
    handles = [p.user.email.split("@")[0] for p in (ctx.colleague, ctx.app)]
    return {str(p.id) for p in resolve_mentions(handles, ctx.org.id)}


@pytest.mark.parametrize(
    "surface",
    [
        _member_list,
        _assignable_profiles,
        _team_members,
        _org_key_admin,
        _lead_routing_rotation,
        _outcome_rows,
        _response_time_rows,
        _ticket_agent_rows,
        _ticket_watchers,
        _board_members,
        _mentions,
    ],
    ids=lambda surface: surface.__name__.lstrip("_"),
)
def test_app_member_is_left_out_of_every_people_query(
    surface, admin_client, admin_profile, colleague, app_member, org_a
):
    ctx = SimpleNamespace(
        admin_client=admin_client,
        admin=admin_profile,
        colleague=colleague,
        app=app_member,
        org=org_a,
    )
    seen = surface(ctx)
    assert str(app_member.id) not in seen
    assert seen & {str(admin_profile.id), str(colleague.id)}


def test_app_member_never_becomes_a_ticket_watcher(
    admin_profile, colleague, app_member, org_a
):
    case = Case.objects.create(org=org_a, name="Printer", status="New")
    client, _, _ = _token_client(app_member, ["cases:read", "cases:write"])

    refused = client.post(f"/api/cases/{case.id}/watch/")
    assert refused.status_code == 403
    assert client.delete(f"/api/cases/{case.id}/watch/").status_code == 204
    # The mention path, which auto-watches whoever a comment names.
    assert _ensure_watcher(case, app_member) is False
    assert _ensure_watcher(case, colleague) is True
    assert list(CaseWatcher.objects.values_list("profile_id", flat=True)) == [
        colleague.id
    ]

    # A row planted past both paths still reaches nobody but people.
    CaseWatcher.objects.create(case=case, profile=app_member, org=org_a)
    told = dispatch_for_comment(SimpleNamespace(comment="Fixed it"), case)
    assert told["watchers_notified"] == [colleague]
    assert not Notification.objects.filter(recipient=app_member).exists()


def test_a_board_an_app_member_makes_shows_it_only_as_owner(app_member):
    client, _, _ = _token_client(app_member)
    created = client.post("/api/boards/", {"name": "Sync"}, format="json")
    assert created.status_code == 201, created.content
    body = client.get(f"/api/boards/{created.json()['id']}/").json()
    assert body["owner"]["id"] == str(app_member.id)
    assert body["members"] == []
    assert body["member_count"] == 0
    # Its own membership row is what lets it manage the board it made.
    column = client.post(
        f"/api/boards/{body['id']}/columns/", {"name": "Later"}, format="json"
    )
    assert column.status_code == 201


def test_member_count_and_team_choice_leave_out_app_members(
    admin_client, colleague, app_member
):
    body = admin_client.get("/api/org/settings/").json()
    assert body["member_count"] == 2

    response = admin_client.post(
        "/api/teams/",
        {"name": "Ops", "assign_users": [str(colleague.id), str(app_member.id)]},
        format="json",
    )
    assert response.status_code == 400
    assert not Teams.objects.filter(name="Ops").exists()


def test_an_app_member_address_cannot_be_invited(admin_client, app_member):
    response = admin_client.post(
        "/api/users/", {"email": app_member.user.email, "role": "USER"}, format="json"
    )
    assert response.status_code == 400
    assert Profile.all_objects.filter(user=app_member.user).count() == 1


# ---------------------------------------------------------------------------
# Never alerted, never mailed
# ---------------------------------------------------------------------------


def _alerted(profile):
    return Notification.objects.filter(recipient=profile, verb__startswith="lead.")


def test_alerts_and_alert_emails_never_reach_an_app_member(
    admin_profile, colleague, app_member, org_a
):
    lead = Lead.objects.create(org=org_a, first_name="Pat", status="assigned")
    alerts.notify_created(str(lead.id), str(org_a.id))
    assert _alerted(admin_profile).filter(verb=alerts.CREATED).exists()

    lead.assigned_to.add(app_member, colleague)
    alerts.notify_waiting(lead)
    alerts.notify_enquired(lead)
    assert _alerted(colleague).filter(verb=alerts.ASSIGNED).exists()
    assert _alerted(colleague).filter(verb=alerts.WAITING).exists()
    assert not _alerted(app_member).exists()

    mail.outbox.clear()
    send_email_to_assigned_user(
        [str(app_member.id), str(colleague.id)], lead.id, org_a.id
    )
    send_enquired_again_email(str(org_a.id), lead_id=str(lead.id))
    sent_to = [address for message in mail.outbox for address in message.recipients()]
    assert sent_to == [colleague.user.email] * 2


def test_no_email_is_sent_to_the_app_member_domain(app_member):
    address = app_member.user.email
    shouting = f"Sync app <{address.upper()}>"
    lookalikes = [
        f"someone@{APP_MEMBER_EMAIL_DOMAIN}.example.com",
        f"{APP_MEMBER_EMAIL_DOMAIN}@example.com",
    ]

    sent = EmailMessage(
        "Hi", "Body", to=[address, "person@example.com", *lookalikes], cc=[shouting]
    )
    sent.bcc = [address]
    assert sent.send() == 1
    assert mail.outbox[0].to == ["person@example.com", *lookalikes]
    assert mail.outbox[0].cc == []
    assert mail.outbox[0].bcc == []

    only_the_app = EmailMultiAlternatives("Hi", "Body", to=[address], bcc=[shouting])
    only_the_app.attach_alternative("<p>Body</p>", "text/html")
    assert only_the_app.send() == 0
    assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# No sign-in
# ---------------------------------------------------------------------------


def test_app_member_cannot_sign_in(app_member, unauthenticated_client):
    user = app_member.user
    assert authenticate(email=user.email, password="") is None
    assert Client().login(email=user.email, password="") is False

    cache.clear()

    def send_now(token_id, raw_code=None):
        send_magic_link_email(token_id, raw_code=raw_code)

    with patch("common.tasks.send_magic_link_email.delay", side_effect=send_now):
        for email in (user.email, "person@example.com"):
            response = unauthenticated_client.post(
                "/api/auth/magic-link/request/", {"email": email}, format="json"
            )
            assert response.status_code == 200
    cache.clear()
    assert [message.to for message in mail.outbox] == [["person@example.com"]]

    link = MagicLinkToken.objects.create(
        email=user.email,
        token=secrets.token_hex(32),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    response = unauthenticated_client.post(
        "/api/auth/magic-link/verify/", {"token": link.token}, format="json"
    )
    assert response.status_code == 403
    assert "access_token" not in response.json()


# ---------------------------------------------------------------------------
# Its token
# ---------------------------------------------------------------------------


def test_app_member_token_reaches_an_allowed_endpoint(app_member, org_a):
    client, _, pat = _token_client(app_member, ["leads:read", "leads:write"])

    response = client.post(
        "/api/leads/",
        {"first_name": "Pat", "last_name": "Okafor", "email": "pat@example.com"},
        format="json",
    )
    assert response.status_code == 200, response.content
    lead = Lead.objects.get(pk=response.json()["id"], org=org_a)
    assert lead.created_by == app_member.user

    assert client.get("/api/leads/").status_code == 200
    detail = client.get(f"/api/leads/{lead.id}/")
    assert detail.status_code == 200
    assert detail.json()["lead_obj"]["created_by"]["name"] == "Sync app"
    assert client.get("/api/contacts/").status_code == 403
    pat.refresh_from_db()
    assert pat.last_used_at is not None


def test_app_member_token_is_refused_once_deactivated(app_member):
    client, raw, _ = _token_client(app_member)
    assert client.get("/api/leads/").status_code == 200

    deactivate_app_member(app_member)

    # Through the middleware, which every /api/ request passes.
    assert client.get("/api/leads/").status_code == 403
    # Through the DRF class, for a path the middleware does not resolve.
    request = RequestFactory().get("/api/leads/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    with pytest.raises(AuthenticationFailed):
        PATAuthentication().authenticate(request)
    with pytest.raises(AuthenticationFailed):
        resolve_valid_pat(raw)


# ---------------------------------------------------------------------------
# Admin token oversight
# ---------------------------------------------------------------------------


def test_org_token_list_excludes_app_tokens(admin_client, admin_profile, app_member):
    _, person_token = PersonalAccessToken.generate(profile=admin_profile, name="cli")
    app_client, _, app_token = _token_client(app_member)

    body = admin_client.get("/api/org/tokens/").json()
    assert [row["id"] for row in body["tokens"]] == [str(person_token.id)]
    assert body["totals"]["count"] == 1
    assert body["totals"]["live"] == 1

    assert admin_client.delete(f"/api/org/tokens/{app_token.id}/").status_code == 404
    app_token.refresh_from_db()
    assert app_token.revoked_at is None
    assert admin_client.delete(f"/api/org/tokens/{person_token.id}/").status_code == 200

    # The self-service views are on the credential deny-list for every token.
    assert app_client.get("/api/profile/tokens/").status_code == 403
    created = app_client.post(
        "/api/profile/tokens/", {"name": "another"}, format="json"
    )
    assert created.status_code == 403
    assert PersonalAccessToken.objects.filter(profile=app_member).count() == 1


# ---------------------------------------------------------------------------
# What the app member does is credited to it
# ---------------------------------------------------------------------------


def test_a_retainer_drafted_by_an_app_member_bills_a_contact(app_member, org_a):
    from accounts.models import Account
    from contacts.models import Contact
    from projects.retainers import _contact

    account = Account.objects.create(org=org_a, name="Acme")
    contact = Contact.objects.create(org=org_a, first_name="Ada", last_name="Byron")
    account.contacts.add(contact)
    assert _contact(account, org_a, app_member.user) == contact


def test_an_app_members_activity_shows_its_name(
    admin_client, admin_profile, app_member
):
    from macros.render import _profile_full_name

    client, _, _ = _token_client(app_member, ["leads:write"])
    mine = client.post("/api/leads/", {"first_name": "Pat"}, format="json")
    assert mine.status_code == 200
    theirs = admin_client.post("/api/leads/", {"first_name": "Lee"}, format="json")
    assert theirs.status_code == 200

    rows = admin_client.get("/api/activities/?entity_type=Lead").json()["activities"]
    names = {row["user"]["id"]: row["user"]["name"] for row in rows}
    assert names[str(app_member.user_id)] == "Sync app"
    # A person's name is still the local part of their email.
    assert names[str(admin_profile.user_id)] == "admin"

    assert _profile_full_name(app_member) == "Sync app"
    assert _profile_full_name(admin_profile) == "admin"


def test_an_app_members_comment_carries_its_name(
    admin_client, admin_profile, app_member, org_a
):
    lead = Lead.objects.create(org=org_a, first_name="Pat")
    client, _, _ = _token_client(app_member, ["leads:read", "leads:write"])
    for writer in (client, admin_client):
        posted = writer.post(
            f"/api/leads/{lead.id}/", {"comment": "Called"}, format="json"
        )
        assert posted.status_code == 200, posted.content

    comments = admin_client.get(f"/api/leads/{lead.id}/").json()["comments"]
    details = {
        row["commented_by"]["id"]: row["commented_by"]["user_details"]
        for row in comments
    }
    assert details[str(app_member.id)]["name"] == "Sync app"
    assert details[str(admin_profile.id)]["name"] is None
    # The email is unchanged for both.
    assert details[str(admin_profile.id)]["email"] == admin_profile.user.email
    assert details[str(app_member.id)]["email"] == app_member.user.email
