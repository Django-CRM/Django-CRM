"""The comment body, and who is allowed to have written it.

`Comment.save()` calls `full_clean()`, so both the field width and the author
constraint are enforced on every ordinary write, not only at the database.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from cases.models import Case
from common.models import COMMENT_MAX_LENGTH, Comment
from contacts.models import Contact


def _case(org):
    return Case.objects.create(
        org=org, name="Long reply", status="New", priority="Normal"
    )


def _contact(org):
    return Contact.objects.create(
        org=org, first_name="Pat", last_name="Smith", email="pat@example.com"
    )


@pytest.mark.django_db
def test_comment_body_is_text_with_a_deliberate_bound():
    """255 characters cannot hold a support reply, let alone a customer's.

    Widening to TextField removed the only limit on the input, so the bound was
    put back explicitly. One number governs every write path, because `save`
    calls `full_clean` and DRF derives a serializer validator from the same
    attribute.
    """
    field = Comment._meta.get_field("comment")
    assert field.get_internal_type() == "TextField"
    assert field.max_length == COMMENT_MAX_LENGTH
    assert COMMENT_MAX_LENGTH > 255


@pytest.mark.django_db
def test_the_bound_is_not_enforced_at_the_model_layer(org_a, admin_profile):
    """Pinning a Django behaviour that is easy to assume the other way round.

    `max_length` on a TextField is not enforced by `full_clean` or by the
    column; it only feeds a form widget and, usefully here, DRF's serializer
    mapping. So an over-long comment is rejected at the API and accepted by a
    direct ORM write. That asymmetry is intended (server-side callers are
    trusted), and this test exists so nobody "fixes" the model expecting the
    API test to be the redundant one.

    The API-layer enforcement is pinned in
    contacts/tests/test_contact_access_and_links.py.
    """
    case = _case(org_a)
    comment = Comment.objects.create(
        org=org_a,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment="x" * (COMMENT_MAX_LENGTH + 1),
        commented_by=admin_profile,
    )
    assert len(comment.comment) == COMMENT_MAX_LENGTH + 1


@pytest.mark.django_db
def test_comment_body_accepts_long_text(org_a, admin_profile):
    case = _case(org_a)
    body = "x" * 5000
    comment = Comment.objects.create(
        org=org_a,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment=body,
        commented_by=admin_profile,
    )
    comment.refresh_from_db()
    assert len(comment.comment) == 5000


@pytest.mark.django_db
def test_comment_can_be_authored_by_a_contact(org_a):
    """A portal reply has no Profile behind it."""
    case = _case(org_a)
    contact = _contact(org_a)
    comment = Comment.objects.create(
        org=org_a,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment="Still broken.",
        commented_by_contact=contact,
    )
    assert comment.commented_by is None
    assert comment.commented_by_contact == contact


@pytest.mark.django_db
def test_comment_cannot_claim_two_authors(org_a, admin_profile):
    """One row, one author. Never both a colleague and a customer."""
    case = _case(org_a)
    contact = _contact(org_a)
    with pytest.raises(ValidationError):
        Comment.objects.create(
            org=org_a,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment="Who wrote this?",
            commented_by=admin_profile,
            commented_by_contact=contact,
        )
