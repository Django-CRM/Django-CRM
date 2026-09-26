"""Possible duplicate accounts, and merging one account into another (G19).

The views live in `common.views.duplicate_views`; this names the account rules
they apply.
"""

from drf_spectacular.utils import extend_schema, extend_schema_view

from accounts import access
from accounts.models import Account
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


class AccountDuplicates(DuplicateSpec):
    model = Account
    entity = "account"

    @staticmethod
    def visible(profile):
        return access.visible_accounts_qs(profile)

    @staticmethod
    def may_delete(profile, record):
        return access.may_delete_account(profile, record)

    @staticmethod
    def display_name(record):
        return record.name

    @staticmethod
    def criteria_of(record):
        return {
            "name": record.name,
            "email": record.email,
            "phone": record.phone,
            "website": record.website,
        }


@extend_schema_view(
    post=extend_schema(
        tags=["Accounts"],
        operation_id="accounts_duplicates_check",
        request=DuplicateQuerySerializer,
        responses={200: DuplicatesResponseSerializer},
    )
)
class AccountDuplicateCheckView(DuplicateCheckView):
    spec = AccountDuplicates


@extend_schema_view(
    get=extend_schema(
        tags=["Accounts"],
        operation_id="accounts_duplicates",
        responses={200: RecordDuplicatesResponseSerializer},
    )
)
class AccountRecordDuplicatesView(RecordDuplicatesView):
    spec = AccountDuplicates


@extend_schema_view(
    post=extend_schema(
        tags=["Accounts"],
        operation_id="accounts_merge",
        request=MergeRequestSerializer,
        responses={200: MergeResponseSerializer},
    )
)
class AccountMergeView(MergeView):
    spec = AccountDuplicates
