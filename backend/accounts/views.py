import json
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import (
    Count,
    DateField,
    F,
    IntegerField,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import access, swagger_params
from accounts.models import Account
from accounts.serializer import (
    AccountCommentEditSwaggerSerializer,
    AccountCreateSerializer,
    AccountDetailEditSwaggerSerializer,
    AccountSerializer,
    AccountWriteSerializer,
    EmailSerializer,
    EmailWriteSerializer,
    TagsSerializer,
)
from accounts.tasks import send_email, send_email_to_assigned_user
from cases.access import visible_cases_qs
from cases.models import Case
from cases.serializer import CaseSerializer, parent_access_context
from cases.workflow import TERMINAL_STATUSES
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.lookups import get_scoped_or_404
from common.models import (
    Attachments,
    Comment,
    CustomFieldDefinition,
    Profile,
    Tags,
    Teams,
)
from common.money import currency_block, deal_currency
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    AttachmentsSerializer,
    CommentSerializer,
    CustomFieldDefinitionSerializer,
    ProfileSerializer,
    TeamsSerializer,
)
from common.utils import (
    CASE_TYPE,
    COUNTRIES,
    CURRENCY_CODES,
    INDCHOICES,
    PRIORITY_CHOICE,
    STATUS_CHOICE,
    create_attachment,
    handle_m2m_assignment,
    validate_attachment,
)
from common.validators import date_param, payload_id_list, uuid_list_param
from contacts.access import replace_visible_contacts, visible_contacts_qs
from contacts.models import Contact
from contacts.serializer import ContactSerializer
from invoices.models import UNPAID_STATUSES, Invoice
from invoices.permissions import visible_invoices_qs
from invoices.serializer import InvoiceListSerializer
from leads.access import visible_leads_qs
from leads.serializer import LeadPickerSerializer
from opportunity.access import visible_deals_qs
from opportunity.models import SOURCES, DealPipeline, Opportunity, stage_kind_q
from opportunity.serializer import OpportunitySerializer
from opportunity.stages import stage_choices
from opportunity.workflow import OPEN, WON
from tasks.access import visible_tasks_qs
from tasks.serializer import TaskSerializer

# Money and counts the account page shows about an account: what it has been
# worth, what is still in play, what it owes, and what is on fire.
#
# Computed on read, never stored columns. A stored total is a total that can
# be wrong. It goes stale the moment a deal moves or an invoice is paid, and
# then the header disagrees with the very rows printed underneath it.
#
# The counts and the date are SQL annotations (`annotate_rollups`). The money
# is grouped by currency (`attach_money_rollups`), because deals and invoices
# each carry their own currency and there are no exchange rates to add them.
ROLLUP_FIELDS = (
    "won_count",
    "open_deal_count",
    "open_tickets",
    "first_won_on",
)
ROLLUP_MONEY = ("won_amount", "open_pipeline", "overdue_amount")


def _per_account(model, visible, aggregate, output_field, *conditions, **filters):
    """One aggregate over one account's related rows, as a correlated subquery.

    Only rows in `visible`, the viewer's read rule for `model`, are counted.
    Totalling every row on the account let a member subtract the deals they
    can open from "Won" and read the value of the ones they cannot. The rule
    is applied as `id__in` rather than by aggregating over it: a non-admin's
    rule joins the assignee table, so a deal with two assignees would count
    twice.

    Not `.annotate(Sum(...), Count(...))` on the outer queryset: two aggregates
    over two different relations join both tables at once, so every deal is
    counted once per invoice and every total comes back multiplied. Each
    aggregate therefore gets its own subquery, and the joins never meet.

    `.values("account").annotate(...)` groups inside the subquery so it yields
    at most one row. When an account has no matching rows it yields *none*, and
    an empty subquery is NULL rather than zero, which is why every caller
    wraps this in a `Coalesce`. Zero and "no invoices at all" look identical to
    a reader of the page, and should.
    """
    return Subquery(
        model.objects.filter(
            account=OuterRef("pk"),
            org=OuterRef("org"),
            *conditions,
            id__in=visible.values("id"),
            **filters,
        )
        .values("account")
        .annotate(value=aggregate)
        .values("value")[:1],
        output_field=output_field,
    )


# Both derived from the definitions the deal and ticket modules already use, so
# "open pipeline" here means what /opportunities means by it and "open tickets"
# means what /tickets means by it. Spelling either list out again by hand is how
# two pages end up disagreeing about the same account. A deal's won or open
# state is its stage's kind (`stage_kind_q`), whatever the stage is called.
OPEN_CASE_STATUSES = [
    value for value, _label in STATUS_CHOICE if value not in TERMINAL_STATUSES
]


def annotate_rollups(queryset, profile):
    """Annotate the `ROLLUP_FIELDS` counts on an Account queryset.

    Used by both the list and the detail endpoint, so a number cannot change
    meaning depending on which page you read it from. The money is added to
    the loaded rows afterwards by `attach_money_rollups`.

    Counted over the deals and tickets `profile` may open, the same rows the
    account page lists under the figures. An admin may open all of them.
    """
    deals = visible_deals_qs(profile, profile.user)
    cases = visible_cases_qs(profile)
    return queryset.annotate(
        won_count=Coalesce(
            _per_account(
                Opportunity, deals, Count("id"), IntegerField(), stage_kind_q(WON)
            ),
            0,
        ),
        open_deal_count=Coalesce(
            _per_account(
                Opportunity, deals, Count("id"), IntegerField(), stage_kind_q(OPEN)
            ),
            0,
        ),
        open_tickets=Coalesce(
            _per_account(
                Case, cases, Count("id"), IntegerField(), status__in=OPEN_CASE_STATUSES
            ),
            0,
        ),
        # The day this company stopped being a prospect: the close date of the
        # first deal won against them. There is no contract or subscription
        # model to ask, so this is derived from what the CRM actually knows
        # rather than presented as a stored fact.
        first_won_on=_per_account(
            Opportunity, deals, Min("closed_on"), DateField(), stage_kind_q(WON)
        ),
    )


def attach_money_rollups(accounts, profile):
    """Set `money_rollups` on each loaded account: its money, per currency.

    Only deals and invoices `profile` may open are summed, for the reason
    `_per_account` gives, and by id for the same reason: summing over the
    read rule itself would count a record once per assignee.

    Won is booked revenue (deals won, not cash collected), open pipeline is
    every deal still open, and overdue is past due and still owed. For overdue
    the due date is the fact; the "Overdue" status is a nightly task's opinion
    about it, and can be a day behind. See UNPAID_STATUSES.

    Shaped by `currency_block`, the invoice reports' convention: the plain
    figure when at most one currency is present, `None` when several, and
    `by_currency` either way. A deal with no currency counts in the org's
    default, which is what the deal serializer fills in on create.

    Three grouped queries for the whole page, however many accounts are on it.
    Each is its own query, so deals and invoices never join and multiply.
    """
    org = profile.org
    groups = {account.pk: {} for account in accounts}
    deals = Opportunity.objects.filter(
        org=org,
        id__in=visible_deals_qs(profile, profile.user).values("id"),
        account__in=list(groups),
        amount__isnull=False,
    )
    sources = (
        ("won_amount", deals.filter(stage_kind_q(WON)), "amount", deal_currency(org)),
        (
            "open_pipeline",
            deals.filter(stage_kind_q(OPEN)),
            "amount",
            deal_currency(org),
        ),
        (
            "overdue_amount",
            Invoice.objects.filter(
                org=org,
                id__in=visible_invoices_qs(profile, profile.user).values("id"),
                account__in=list(groups),
                status__in=UNPAID_STATUSES,
                due_date__lt=timezone.localdate(),
                amount_due__gt=0,
            ),
            "amount_due",
            F("currency"),
        ),
    )
    for field, queryset, amount, currency in sources:
        rows = (
            queryset.values("account", code=currency)
            .annotate(value=Sum(amount))
            .order_by()
        )
        for row in rows:
            groups[row["account"]].setdefault(row["code"], {})[field] = row["value"]
    for account in accounts:
        account.money_rollups = currency_block(groups[account.pk], money=ROLLUP_MONEY)


def account_list_queryset(profile, user, params):
    """The accounts ``GET /api/accounts/`` lists for this caller and query.

    The list and the CSV export both start here, so a downloaded file holds
    exactly the accounts the page would show with the same filters. The list
    splits these into active and inactive halves; ``?is_active=true`` or
    ``false`` asks for one of them, which is how a client that shows only one
    says which one it is exporting.
    """
    # The read rule itself, so the list holds exactly what the detail view
    # opens for this caller.
    queryset = access.visible_accounts_qs(profile, user).order_by("-id")
    if params.get("name"):
        queryset = queryset.filter(name__icontains=params.get("name"))
    if params.get("city"):
        queryset = queryset.filter(city__icontains=params.get("city"))
    if params.get("industry"):
        queryset = queryset.filter(industry__icontains=params.get("industry"))
    tags = uuid_list_param(params, "tags")
    if tags:
        queryset = queryset.filter(tags__id__in=tags)
    assigned_to = uuid_list_param(params, "assigned_to")
    if assigned_to:
        queryset = queryset.filter(assigned_to__id__in=assigned_to)
    if params.get("search"):
        queryset = queryset.filter(name__icontains=params.get("search"))
    created_at_gte = date_param(params, "created_at__gte")
    if created_at_gte:
        queryset = queryset.filter(created_at__date__gte=created_at_gte)
    created_at_lte = date_param(params, "created_at__lte")
    if created_at_lte:
        queryset = queryset.filter(created_at__date__lte=created_at_lte)
    # Custom-field filters: ?cf_<key>=<value> -> custom_fields contains pair.
    for raw_key, raw_value in params.items():
        if raw_key.startswith("cf_") and raw_value:
            cf_key = raw_key[3:]
            if cf_key:
                queryset = queryset.filter(custom_fields__contains={cf_key: raw_value})
    if params.get("is_active") in ("true", "false"):
        queryset = queryset.filter(is_active=params.get("is_active") == "true")
    return queryset.distinct()


class AccountsListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Account
    serializer_class = AccountSerializer

    def get_context_data(self, **kwargs):
        queryset = annotate_rollups(
            account_list_queryset(
                self.request.profile, self.request.user, self.request.query_params
            ),
            self.request.profile,
        )

        context = {}

        # Account model no longer has status field, return all accounts
        # Filter by is_active instead
        queryset_active = queryset.filter(is_active=True)
        results_accounts_active = self.paginate_queryset(
            queryset_active.distinct(), self.request, view=self
        )
        if results_accounts_active:
            offset = queryset_active.filter(
                id__gte=results_accounts_active[-1].id
            ).count()
            if offset == queryset_active.count():
                offset = None
        else:
            offset = 0
        attach_money_rollups(results_accounts_active, self.request.profile)
        accounts_active = AccountSerializer(results_accounts_active, many=True).data
        context["per_page"] = 10
        page_number = int(self.offset / 10) + 1
        context["page_number"] = page_number
        context["active_accounts"] = {
            "offset": offset,
            "open_accounts": accounts_active,
            "open_accounts_count": queryset_active.count(),
        }

        # Inactive accounts
        queryset_inactive = queryset.filter(is_active=False)
        results_accounts_inactive = self.paginate_queryset(
            queryset_inactive.distinct(), self.request, view=self
        )
        if results_accounts_inactive:
            offset = queryset_inactive.filter(
                id__gte=results_accounts_inactive[-1].id
            ).count()
            if offset == queryset_inactive.count():
                offset = None
        else:
            offset = 0
        attach_money_rollups(results_accounts_inactive, self.request.profile)
        accounts_inactive = AccountSerializer(results_accounts_inactive, many=True).data

        # The contact and lead catalogues below exist for the account *form*
        # (linking a contact, converting a lead). They are org-scoped, and for
        # a non-admin they must ALSO be narrowed the same way the account list
        # above is, because a side payload is a door like any other: this
        # endpoint used to hand a member the org's whole lead catalogue, in
        # full, including leads whose own detail route answers 403 for them.
        # `/api/cases/` and `/api/opportunities/` already narrow their
        # equivalents; these two were missed.
        #
        # Contacts use their own read rule, which is what the save path
        # accepts: it adds superusers and account assignment.
        contacts = visible_contacts_qs(self.request.profile).values("id", "first_name")
        context["contacts"] = contacts
        context["closed_accounts"] = {
            "offset": offset,
            "close_accounts": accounts_inactive,
            "close_accounts_count": queryset_inactive.count(),
        }
        context["teams"] = TeamsSerializer(
            Teams.objects.filter(org=self.request.profile.org), many=True
        ).data
        context["countries"] = COUNTRIES
        context["industries"] = INDCHOICES

        tags = Tags.objects.filter(org=self.request.profile.org, is_active=True)
        tags = TagsSerializer(tags, many=True).data

        context["tags"] = tags
        users = Profile.objects.filter(
            is_active=True, org=self.request.profile.org
        ).values("id", "user__email")
        context["users"] = users
        # The lead read rule itself, so the picker offers exactly the leads
        # `/api/leads/<id>/` would open for this caller, and only a label each.
        leads = visible_leads_qs(self.request.profile, self.request.user).exclude(
            Q(status="converted") | Q(status="closed")
        )
        context["leads"] = LeadPickerSerializer(leads, many=True).data
        context["status"] = ["active", "inactive"]  # Maps to is_active field
        return context

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_list",
        parameters=swagger_params.account_get_params,
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_create",
        parameters=swagger_params.organization_params,
        request=AccountCreateSerializer,
    )
    def post(self, request, *args, **kwargs):
        data = request.data
        # Every id list, as PUT and PATCH take them. Parsed before anything is
        # saved so a malformed id is a 400 with no half-created account behind
        # it. `ATOMIC_REQUESTS` is off, so a later 400 would not roll back.
        contact_ids = payload_id_list(data.get("contacts"), "contacts")
        tag_ids = payload_id_list(data.get("tags"), "tags")
        team_ids = payload_id_list(data.get("teams"), "teams")
        assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")
        # The file too, for the same reason.
        validate_attachment(request.FILES.get("account_attachment"))
        serializer = AccountCreateSerializer(
            data=data, request_obj=request, account=True
        )
        # Save Account
        if serializer.is_valid():
            cf_payload = data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Account", cf_payload or {}, request.profile.org
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            account_object = serializer.save(
                org=request.profile.org, custom_fields=cleaned_cf
            )

            # Handle M2M relationships using utilities. Contacts go through the
            # contact read rule: linking one to an account the caller is
            # assigned to would make it theirs to open.
            replace_visible_contacts(
                account_object.contacts, contact_ids, request.profile
            )
            if tag_ids:
                account_object.tags.add(
                    *Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                )
            handle_m2m_assignment(
                account_object, "teams", team_ids, Teams, request.profile.org
            )
            handle_m2m_assignment(
                account_object,
                "assigned_to",
                assigned_ids,
                Profile,
                request.profile.org,
                extra_filters={"is_active": True},
            )

            # Handle attachment
            if self.request.FILES.get("account_attachment"):
                create_attachment(
                    request.FILES.get("account_attachment"),
                    account_object,
                    request.profile,
                )

            recipients = list(
                account_object.assigned_to.all().values_list("id", flat=True)
            )
            send_email_to_assigned_user.delay(
                recipients,
                account_object.id,
                str(request.profile.org.id),
            )
            return Response(
                {
                    "error": False,
                    "message": "Account Created Successfully",
                    # Without this a client cannot open what it just created.
                    # The alternative is searching for it by name, which is a
                    # race and a guess. Additive; nothing reads it positionally.
                    "id": str(account_object.id),
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AccountDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    serializer_class = AccountSerializer

    def get_object(self, pk):
        # A malformed id is 404, not 500. The URL pattern is `<str:pk>`, so an
        # old bookmark or a typo reaches the UUID field as text, and
        # `UUIDField.to_python` raises `ValidationError`, which
        # `get_object_or_404` does not catch, because it only knows about
        # `DoesNotExist`. "That is not an id" and "no such account" are the
        # same answer to whoever asked.
        try:
            account = get_object_or_404(
                annotate_rollups(Account.objects.all(), self.request.profile),
                id=pk,
                org=self.request.profile.org,
            )
        except (DjangoValidationError, ValueError):
            raise Http404("No such account.")
        attach_money_rollups([account], self.request.profile)
        return account

    def assert_account_access(self, account):
        """Delegates to `accounts.access`, which holds the one definition.

        The attachment download view asks the same question, and four inline
        copies is how the creator branch came to be dead in all four verbs.
        """
        access.assert_account_access(self.request.profile, self.request.user, account)

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_update",
        parameters=swagger_params.organization_params,
        request=AccountWriteSerializer,
    )
    def put(self, request, pk, format=None):
        data = request.data
        account_object = self.get_object(pk=pk)
        # Authorise before validating. The check used to sit inside
        # `is_valid()`, so somebody with no right to touch the account learned
        # whether their payload was well-formed before being turned away.
        self.assert_account_access(account_object)
        # Parsed before the first write: a malformed id used to be a 400 after
        # the fields were saved and the contacts replaced.
        contact_ids = payload_id_list(data.get("contacts"), "contacts")
        tag_ids = payload_id_list(data.get("tags"), "tags")
        team_ids = payload_id_list(data.get("teams"), "teams")
        assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")
        # So is the file: an oversized one was refused after the save.
        validate_attachment(request.FILES.get("account_attachment"))
        serializer = AccountCreateSerializer(
            account_object, data=data, request_obj=request, account=True
        )

        if serializer.is_valid():
            save_kwargs = {}
            if "custom_fields" in data:
                cf_payload = data.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Account",
                    cf_payload or {},
                    request.profile.org,
                    existing=account_object.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            account_object = serializer.save(**save_kwargs)
            previous_assigned_to_users = list(
                account_object.assigned_to.all().values_list("id", flat=True)
            )

            replace_visible_contacts(
                account_object.contacts, contact_ids, request.profile
            )

            account_object.tags.clear()
            if tag_ids:
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                account_object.tags.add(*tag_objs)

            account_object.teams.clear()
            if team_ids:
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                if teams:
                    account_object.teams.add(*teams)

            account_object.assigned_to.clear()
            if assigned_ids:
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                if profiles:
                    account_object.assigned_to.add(*profiles)

            if self.request.FILES.get("account_attachment"):
                create_attachment(
                    self.request.FILES.get("account_attachment"),
                    account_object,
                    self.request.profile,
                )

            assigned_to_list = list(
                account_object.assigned_to.all().values_list("id", flat=True)
            )
            recipients = list(set(assigned_to_list) - set(previous_assigned_to_users))
            send_email_to_assigned_user.delay(
                recipients,
                account_object.id,
                str(request.profile.org.id),
            )
            return Response(
                {"error": False, "message": "Account Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_destroy",
        parameters=swagger_params.organization_params,
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        # Admins, superusers and the creator: the deal delete rule. Assignees
        # may open and edit an account but not delete it. Superusers were
        # refused here alone, while every sibling rule let them through.
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if self.request.profile.user_id != self.object.created_by_id:
                return Response(
                    {
                        "error": True,
                        "errors": "You do not have Permission to perform this action",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        try:
            self.object.delete()
        except ProtectedError:
            # All three of Invoice, Estimate and RecurringInvoice point here with
            # on_delete=PROTECT, so all three must be named. An account blocked
            # only by a recurring invoice used to be told to go and look at two
            # lists that were already empty.
            return Response(
                {
                    "error": True,
                    "errors": "This account can't be deleted while it still has "
                    "invoices, estimates or recurring invoices linked to it.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {"error": False, "message": "Account Deleted Successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_retrieve",
        parameters=swagger_params.organization_params,
    )
    def get(self, request, pk, format=None):
        self.account = self.get_object(pk=pk)
        self.assert_account_access(self.account)
        context = {}
        # Every related list below applies its own module's read rule, the one
        # that module's detail endpoint enforces. Being able to open the
        # account is not a licence to read every deal or ticket hanging off it.
        profile = self.request.profile
        context["account_obj"] = AccountSerializer(
            self.account, context={"profile": profile}
        ).data

        comment_permission = (
            self.request.profile.user_id == self.account.created_by_id
            or is_org_admin(self.request.profile)
        )

        if is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(
                    is_active=True, org=self.request.profile.org
                ).values("user__email")
            )
        elif self.request.profile.user_id != self.account.created_by_id:
            # `created_by` IS the User. Reading `created_by.user.email` raised
            # AttributeError and returned a 500 to every non-admin assignee who
            # opened an account, and the branch was reached every time, because
            # the Profile-vs-User comparison above it was never equal.
            #
            # The key is `user__email`, matching the admin branch. It used to be
            # `username` here, so the same endpoint described the same list two
            # different ways depending on who asked.
            if self.account.created_by:
                users_mention = [{"user__email": self.account.created_by.email}]
            else:
                users_mention = []
        else:
            users_mention = []
        account_content_type = ContentType.objects.get_for_model(Account)
        comments = Comment.objects.filter(
            content_type=account_content_type,
            object_id=self.account.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=account_content_type,
            object_id=self.account.id,
            org=self.request.profile.org,
        ).order_by("-id")
        account_cases = visible_cases_qs(profile).filter(account=self.account)
        context.update(
            {
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
                "contacts": ContactSerializer(
                    self.account.contacts.filter(
                        id__in=visible_contacts_qs(profile).values("id")
                    ),
                    many=True,
                ).data,
                "opportunity_list": OpportunitySerializer(
                    visible_deals_qs(profile, request.user).filter(
                        account=self.account
                    ),
                    many=True,
                ).data,
                "users": ProfileSerializer(
                    Profile.objects.filter(
                        is_active=True, org=self.request.profile.org
                    ).order_by("user__email"),
                    many=True,
                ).data,
                "cases": CaseSerializer(
                    account_cases,
                    many=True,
                    context=parent_access_context(profile, account_cases),
                ).data,
                "teams": TeamsSerializer(
                    Teams.objects.filter(org=self.request.profile.org), many=True
                ).data,
                # The default pipeline's stages, as `[code, label]`.
                "stages": stage_choices(DealPipeline.default_for(profile.org)),
                "sources": SOURCES,
                "countries": COUNTRIES,
                "currencies": CURRENCY_CODES,
                "case_types": CASE_TYPE,
                "case_priority": PRIORITY_CHOICE,
                "case_status": STATUS_CHOICE,
                "comment_permission": comment_permission,
                "tasks": TaskSerializer(
                    visible_tasks_qs(profile).filter(account=self.account), many=True
                ).data,
                "invoices": InvoiceListSerializer(
                    visible_invoices_qs(profile, request.user).filter(
                        account=self.account
                    ),
                    many=True,
                ).data,
                "users_mention": users_mention,
                "status": ["open", "close"],
            }
        )

        custom_field_defs = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Account",
            is_active=True,
        ).order_by("display_order", "label")
        context["custom_field_definitions"] = CustomFieldDefinitionSerializer(
            custom_field_defs, many=True
        ).data
        return Response(context)

    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_add_comment",
        parameters=swagger_params.organization_params,
        request=AccountDetailEditSwaggerSerializer,
    )
    def post(self, request, pk, **kwargs):
        data = request.data
        context = {}
        # `Account.objects.get()` raised DoesNotExist, a 500, for an id that
        # simply is not there, while GET on the same id answered 404. Commenting
        # on a deleted account is a normal race, not a server fault.
        self.account_obj = self.get_object(pk=pk)
        self.assert_account_access(self.account_obj)
        # Before the comment is saved, so a refused file does not leave it posted.
        validate_attachment(self.request.FILES.get("account_attachment"))
        # This block never created a comment. `object_id` and `org` were
        # required fields that the client is not supposed to send, so
        # `is_valid()` was False and the save was skipped in silence, leaving a
        # 200 and no comment. The `save(account_id=...)` below it was dead for
        # the same reason, and would have raised `TypeError` if reached, since
        # `Comment` is generic and has no `account_id`. Both halves are fixed
        # here: the target and the author come from the server, and a comment
        # that cannot be saved says so instead of vanishing.
        if data.get("comment"):
            comment_serializer = CommentSerializer(data=data)
            if not comment_serializer.is_valid():
                return Response(
                    {"error": True, "errors": comment_serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            comment_serializer.save(
                content_type=ContentType.objects.get_for_model(Account),
                object_id=self.account_obj.id,
                org=request.profile.org,
                commented_by=request.profile,
            )

        if self.request.FILES.get("account_attachment"):
            create_attachment(
                self.request.FILES.get("account_attachment"),
                self.account_obj,
                self.request.profile,
            )

        account_content_type = ContentType.objects.get_for_model(Account)
        comments = Comment.objects.filter(
            content_type=account_content_type,
            object_id=self.account_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=account_content_type,
            object_id=self.account_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "account_obj": AccountSerializer(
                    self.account_obj, context={"profile": request.profile}
                ).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["Accounts"],
        parameters=swagger_params.organization_params,
        request=AccountWriteSerializer,
        description="Partial Account Update",
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to an account."""
        data = request.data
        account_object = self.get_object(pk=pk)
        self.assert_account_access(account_object)
        # Parsed before the first write, as in PUT. An absent key parses to [].
        contact_ids = payload_id_list(data.get("contacts"), "contacts")
        tag_ids = payload_id_list(data.get("tags"), "tags")
        team_ids = payload_id_list(data.get("teams"), "teams")
        assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")

        serializer = AccountCreateSerializer(
            account_object,
            data=data,
            request_obj=request,
            account=True,
            partial=True,
        )

        if serializer.is_valid():
            save_kwargs = {}
            if "custom_fields" in data:
                cf_payload = data.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Account",
                    cf_payload or {},
                    request.profile.org,
                    existing=account_object.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            account_object = serializer.save(**save_kwargs)

            # Handle M2M fields if present in request
            if "contacts" in data:
                replace_visible_contacts(
                    account_object.contacts, contact_ids, request.profile
                )

            if "tags" in data:
                account_object.tags.clear()
                if tag_ids:
                    tag_objs = Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                    account_object.tags.add(*tag_objs)

            if "teams" in data:
                account_object.teams.clear()
                if team_ids:
                    teams = Teams.objects.filter(
                        id__in=team_ids, org=request.profile.org
                    )
                    account_object.teams.add(*teams)

            if "assigned_to" in data:
                previous = set(account_object.assigned_to.values_list("id", flat=True))
                account_object.assigned_to.clear()
                if assigned_ids:
                    profiles = Profile.objects.filter(
                        id__in=assigned_ids, org=request.profile.org, is_active=True
                    )
                    account_object.assigned_to.add(*profiles)
                # PUT has always told a newly assigned person; PATCH never did,
                # so every assignment made from the web went unannounced.
                current = account_object.assigned_to.values_list("id", flat=True)
                recipients = list(set(current) - previous)
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients, account_object.id, str(request.profile.org.id)
                    )

            return Response(
                {"error": False, "message": "Account Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AccountCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)
    serializer_class = AccountCommentEditSwaggerSerializer

    def get_object(self, pk):
        return get_scoped_or_404(self.model, pk, self.request.profile.org)

    @extend_schema(
        tags=["Accounts"],
        parameters=swagger_params.organization_params,
        request=AccountCommentEditSwaggerSerializer,
    )
    def put(self, request, pk, format=None):
        data = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            # No `if data.get("comment")` guard here: a body with a blank or
            # absent `comment` used to fall out of this branch and hit the 403
            # below, which told an author they may not edit their own comment.
            # Dropping the guard alone would have traded that for a silent
            # no-op 200, because this was the one comment PUT built on a
            # partial serializer. It is non-partial now, like the other four,
            # so an empty body is a 400 naming the field. `patch` below stays
            # partial, which is what PATCH means.
            serializer = CommentSerializer(obj, data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Submitted"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to edit this Comment",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Accounts"],
        parameters=swagger_params.organization_params,
        request=AccountCommentEditSwaggerSerializer,
        description="Partial Comment Update",
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a comment."""
        data = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            serializer = CommentSerializer(obj, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Updated"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to edit this Comment",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(tags=["Accounts"], parameters=swagger_params.organization_params)
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == self.object.commented_by:
            self.object.delete()
            return Response(
                {"error": False, "message": "Comment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class AccountAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)
    serializer_class = AccountDetailEditSwaggerSerializer

    @extend_schema(tags=["Accounts"], parameters=swagger_params.organization_params)
    def delete(self, request, pk, format=None):
        # Scoped to the caller's org. `Attachments` is one generic table shared
        # by every module, and this looked the row up by primary key alone, so
        # the endpoint would delete any attachment in the database, belonging to
        # any organisation, hanging off any kind of record. RLS is the only
        # thing that stood between that and a cross-tenant delete, and RLS is
        # the safety net, not the check.
        #
        # `created_by` is a User FK, so the ownership branch compares against
        # `profile.user_id`; comparing the Profile itself was never true, which
        # made this admin-only by accident.
        try:
            self.object = get_object_or_404(
                self.model, pk=pk, org=self.request.profile.org
            )
        except (DjangoValidationError, ValueError):
            raise Http404("No such attachment.")
        if (
            is_org_admin(request.profile)
            or request.profile.user_id == self.object.created_by_id
        ):
            self.object.delete()
            return Response(
                {"error": False, "message": "Attachment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to delete this Attachment",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class AccountCreateMailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Account
    serializer_class = EmailWriteSerializer

    @extend_schema(
        tags=["Accounts"],
        parameters=swagger_params.organization_params,
        request=EmailWriteSerializer,
    )
    def post(self, request, pk, *args, **kwargs):
        """Compose and send (or schedule) mail from one of this org's accounts.

        This endpoint had never worked. Four separate defects, each of which
        alone would have been enough:

        1. It passed `request_obj=` to `EmailSerializer`, whose `__init__` does
           not pop it and forwards `**kwargs` to `super()`. Every single call
           raised `TypeError` before any of the logic below ran. Only
           `AccountCreateSerializer` accepts that kwarg; this is its neighbour
           in the same module and was given the same call shape.
        2. `data = {}` rebound the name holding `request.data` before the code
           read `data.get("recipients")` and `data.get("scheduled_later")` from
           it. Both were therefore always `None`, so no recipient could ever be
           attached: the mail would have been sent to nobody.
        3. The `scheduled_later` branch set two attributes on `email_obj` and
           never saved, so a scheduled mail was not recorded as scheduled.
        4. Dispatch was gated on `data.get("scheduled_later") != "true"`, which
           with the clobbered dict was always true, so a mail the caller asked
           to schedule would have gone out immediately.

        The org scoping on the account and on each recipient was already right
        and is kept: without it a caller could send mail recorded as coming from
        another tenant's account, or to another tenant's contacts. Each
        recipient must also be a contact of this account, so the account's
        name cannot be put on mail to anyone else in the org.

        Within the org, only someone who may open the account may send from
        it. Anyone else is refused with the 403 that `GET` on the same account
        gives them, so the two verbs agree on what the caller may know.
        """
        params = request.data
        scheduled_date_time = params.get("scheduled_date_time")
        account = Account.objects.filter(id=pk, org=request.profile.org).first()
        if account is None:
            return Response(
                {"error": True, "errors": "Account not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        access.assert_account_access(request.profile, request.user, account)

        serializer = EmailSerializer(data=params, request_obj=request)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scheduled_later = str(params.get("scheduled_later", "")).lower() in (
            "true",
            "1",
            "yes",
        )
        if scheduled_later and scheduled_date_time in ("", None):
            return Response(
                {
                    "error": True,
                    "errors": {
                        "scheduled_date_time": [
                            "A scheduled email needs a date and time."
                        ]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Recipients arrive as a real list, a JSON-encoded list (a multipart body
        # cannot carry one natively), or a bare id. The old code assumed exactly
        # one of those shapes and `json.loads` raised a 500 on the others.
        #
        # Resolved BEFORE the mail row is created, not after. The old shape
        # saved first and deleted on a bad recipient, which leaves an orphan row
        # behind for anything that fails between the two (a malformed id raises
        # out of this call, so the compensating delete never ran).
        #
        # A recipient must be a contact of THIS account, by either link
        # (`Contact.account` or `Account.contacts`). Checking the org alone let
        # anyone who may open one account mail any person in the org under its
        # name. Ids are compared as UUIDs so two spellings of one id are one.
        recipients = {
            uuid.UUID(str(r))
            for r in payload_id_list(params.get("recipients"), "recipients")
        }
        valid = set()
        if recipients:
            valid = set(
                Contact.objects.filter(id__in=recipients, org=request.profile.org)
                .filter(Q(account=account) | Q(account_contacts=account))
                .values_list("id", flat=True)
            )
            refused = sorted(str(r) for r in recipients - valid)
            if refused:
                # Refuse the whole send rather than quietly mailing the valid
                # subset. The ids named are the caller's own input, and one in
                # another org reads the same as one merely unlinked.
                return Response(
                    {
                        "error": True,
                        "errors": {
                            "recipients": [
                                "Not a contact of this account: " + ", ".join(refused)
                            ]
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # `org` is the server's to set, never the caller's, and `AccountEmail.save`
        # would otherwise fall back to reading it off the account.
        email_obj = serializer.save(
            from_account=account,
            org=request.profile.org,
            scheduled_later=scheduled_later,
            scheduled_date_time=scheduled_date_time or None,
        )
        if valid:
            email_obj.recipients.add(*valid)

        if not scheduled_later:
            send_email.delay(email_obj.id, str(request.profile.org.id))
            message = "Email sent successfully"
        else:
            message = "Email scheduled successfully"
        return Response(
            {"error": False, "message": message, "id": str(email_obj.id)},
            status=status.HTTP_200_OK,
        )
