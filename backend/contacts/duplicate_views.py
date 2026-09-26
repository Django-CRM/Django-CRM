"""Possible duplicate contacts, and merging one contact into another (G19).

The views live in `common.views.duplicate_views`; this names the contact rules
they apply.
"""

from drf_spectacular.utils import extend_schema, extend_schema_view

from common.views.duplicate_views import (
    DuplicateCheckView,
    DuplicateQuerySerializer,
    DuplicateSpec,
    DuplicatesResponseSerializer,
    MergeRequestSerializer,
    MergeResponseSerializer,
    MergeView,
    RecordDuplicatesResponseSerializer,
    RecordDuplicatesView,
)
from contacts import access
from contacts.models import Contact


class ContactDuplicates(DuplicateSpec):
    model = Contact
    entity = "contact"

    @staticmethod
    def visible(profile):
        return access.visible_contacts_qs(profile)

    @staticmethod
    def may_delete(profile, record):
        return access.may_delete_contact(profile, record)

    @staticmethod
    def display_name(record):
        name = f"{record.first_name or ''} {record.last_name or ''}".strip()
        return name or record.email or "Contact"

    @staticmethod
    def criteria_of(record):
        return {
            "email": record.email,
            "phone": record.phone,
            "first_name": record.first_name,
            "last_name": record.last_name,
        }


@extend_schema_view(
    post=extend_schema(
        tags=["contacts"],
        operation_id="contacts_duplicates_check",
        request=DuplicateQuerySerializer,
        responses={200: DuplicatesResponseSerializer},
    )
)
class ContactDuplicateCheckView(DuplicateCheckView):
    spec = ContactDuplicates


@extend_schema_view(
    get=extend_schema(
        tags=["contacts"],
        operation_id="contacts_duplicates",
        responses={200: RecordDuplicatesResponseSerializer},
    )
)
class ContactRecordDuplicatesView(RecordDuplicatesView):
    spec = ContactDuplicates


@extend_schema_view(
    post=extend_schema(
        tags=["contacts"],
        operation_id="contacts_merge",
        request=MergeRequestSerializer,
        responses={200: MergeResponseSerializer},
    )
)
class ContactMergeView(MergeView):
    spec = ContactDuplicates
