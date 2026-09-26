"""Possible duplicate leads, and merging one lead into another (G19).

The views live in `common.views.duplicate_views`; this names the lead rules
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
from leads import access
from leads.models import Lead


class LeadDuplicates(DuplicateSpec):
    model = Lead
    entity = "lead"

    @staticmethod
    def visible(profile):
        return access.visible_leads_qs(profile)

    @staticmethod
    def may_delete(profile, record):
        return access.may_delete_lead(profile, record)

    @staticmethod
    def display_name(record):
        name = f"{record.first_name or ''} {record.last_name or ''}".strip()
        return name or record.company_name or record.email or record.title or "Lead"

    @staticmethod
    def criteria_of(record):
        return {
            "email": record.email,
            "phone": record.phone,
            "first_name": record.first_name,
            "last_name": record.last_name,
            "company_name": record.company_name,
        }

    @staticmethod
    def merge_refusal(record):
        # A converted lead already became an account, a contact and a deal.
        # Merging into it would park open work on a record nobody lists;
        # merging it away would delete the lead those three came from.
        if record.status == "converted":
            return "A converted lead cannot be merged."
        return None


@extend_schema_view(
    post=extend_schema(
        tags=["Leads"],
        operation_id="leads_duplicates_check",
        request=DuplicateQuerySerializer,
        responses={200: DuplicatesResponseSerializer},
    )
)
class LeadDuplicateCheckView(DuplicateCheckView):
    spec = LeadDuplicates


@extend_schema_view(
    get=extend_schema(
        tags=["Leads"],
        operation_id="leads_duplicates",
        responses={200: RecordDuplicatesResponseSerializer},
    )
)
class LeadRecordDuplicatesView(RecordDuplicatesView):
    spec = LeadDuplicates


@extend_schema_view(
    post=extend_schema(
        tags=["Leads"],
        operation_id="leads_merge",
        request=MergeRequestSerializer,
        responses={200: MergeResponseSerializer},
    )
)
class LeadMergeView(MergeView):
    spec = LeadDuplicates
