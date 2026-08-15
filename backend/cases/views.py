import json
import statistics
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Account
from accounts.serializer import AccountSerializer
from cases import swagger_params
from cases.access import (
    assert_case_delete_access,
    assert_case_read_access,
    assert_case_write_access,
    get_case_or_404,
    has_case_write_access,
    is_org_admin,
    visible_cases_qs,
)
from cases.models import Case, ReopenPolicy, Solution
from cases.models import EmailMessage as _EmailMessageModel  # noqa: F401  (used below)
from cases.serializer import (
    CaseCommentEditSwaggerSerializer,
    CaseCreateSerializer,
    CaseCreateSwaggerSerializer,
    CaseDetailEditSwaggerSerializer,
    CaseSerializer,
    EmailMessageSerializer,
    ReopenPolicySerializer,
)
from cases.solution_serializers import SolutionSerializer
from cases.tasks import send_email_to_assigned_user
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import (
    Activity,
    Attachments,
    Comment,
    CustomFieldDefinition,
    Profile,
    Tags,
    Teams,
)
from common.permissions import HasOrgContext
from common.serializer import (
    ActivitySerializer,
    AttachmentsSerializer,
    CommentSerializer,
    CustomFieldDefinitionSerializer,
)
from common.utils import (
    CASE_TYPE,
    PRIORITY_CHOICE,
    STATUS_CHOICE,
    create_attachment,
)
from common.validators import date_param, payload_id_list, uuid_list_param, uuid_param
from contacts.models import Contact
from contacts.serializer import ContactSerializer

# A ticket is "open" while somebody still owes the customer something. The
# other three values in STATUS_CHOICE (Closed, Rejected, Duplicate) are all
# ways of being finished with it.
OPEN_STATUSES = ("New", "Assigned", "Pending")

_ALLOWED_CASE_ORDERINGS = frozenset(
    {
        "-created_at",
        "created_at",
        "-priority",
        "priority",
        "-id",
        "id",
        "-name",
        "name",
    }
)


def apply_case_list_filters(queryset, params):
    """Apply the case-list query-param filters to ``queryset``.

    Shared between CaseListView and WatchingListView so both endpoints accept
    the same filter chips from the mobile client. Caller is responsible for
    the base scope (org membership, watcher allowance, etc.). This only
    layers in user-supplied filters.
    """
    if not params:
        return queryset

    if params.get("name"):
        queryset = queryset.filter(name__icontains=params.get("name"))
    # Status can be a single value or a list. Mobile uses the list form for
    # its Open/Closed quick chips (Open = New, Assigned, Pending).
    status_values = [s for s in params.getlist("status") if s]
    if len(status_values) > 1:
        queryset = queryset.filter(status__in=status_values)
    elif status_values:
        queryset = queryset.filter(status=status_values[0])
    if params.get("priority"):
        queryset = queryset.filter(priority=params.get("priority"))
    account = uuid_param(params, "account")
    if account:
        queryset = queryset.filter(account=account)
    if params.get("case_type"):
        queryset = queryset.filter(case_type=params.get("case_type"))
    assigned_to = uuid_list_param(params, "assigned_to")
    if assigned_to:
        queryset = queryset.filter(assigned_to__id__in=assigned_to).distinct()
    tags = uuid_list_param(params, "tags")
    if tags:
        queryset = queryset.filter(tags__id__in=tags).distinct()
    if params.get("search"):
        search = params.get("search")
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    created_at_gte = date_param(params, "created_at__gte")
    if created_at_gte:
        queryset = queryset.filter(created_at__date__gte=created_at_gte)
    created_at_lte = date_param(params, "created_at__lte")
    if created_at_lte:
        queryset = queryset.filter(created_at__date__lte=created_at_lte)
    if params.get("sla_breached") == "true":
        # Wall-clock approximation matching the mobile card's
        # `isFirstResponseSlaBreached` getter; `Case.is_sla_*_breached` uses
        # business hours, but the badges on both clients compute from
        # created_at + hours, so this filter mirrors what the user actually
        # sees on the row. Postgres-specific (INTERVAL).
        #
        # Every column is table-qualified. Unqualified, these resolve fine
        # against a bare Case queryset and raise
        # `ProgrammingError: column reference "first_response_at" is ambiguous`
        # the moment the queryset joins a table sharing any of these names,
        # which is what a filter combination such as `?sla_breached=true` plus
        # a status list produced: a 500 for any authenticated caller.
        queryset = queryset.extra(
            where=[
                '("case"."first_response_at" IS NULL '
                'AND "case"."sla_first_response_hours" IS NOT NULL '
                'AND "case"."created_at" + "case"."sla_first_response_hours" '
                "* INTERVAL '1 hour' < NOW()) "
                'OR ("case"."resolved_at" IS NULL '
                'AND "case"."sla_resolution_hours" IS NOT NULL '
                'AND "case"."created_at" + "case"."sla_resolution_hours" '
                "* INTERVAL '1 hour' < NOW())"
            ]
        )
    # Custom-field filters: ?cf_<key>=<value> -> custom_fields contains pair.
    for raw_key, raw_value in params.items():
        if raw_key.startswith("cf_") and raw_value:
            cf_key = raw_key[3:]
            if cf_key:
                queryset = queryset.filter(custom_fields__contains={cf_key: raw_value})
    # Ordering: whitelisted so callers can't sort on arbitrary cols.
    ordering = params.get("ordering")
    if ordering in _ALLOWED_CASE_ORDERINGS:
        queryset = queryset.order_by(ordering, "-id")

    return queryset


class CaseListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Case

    def get_context_data(self, **kwargs):
        params = self.request.query_params
        # `-id` is a random UUID, so the default "newest first" was in fact no
        # order at all, the queue came back shuffled and the page still said
        # it was sorted. `-created_at` is the order the header promises;
        # `-id` stays as a tiebreak so pagination is stable when a batch of
        # cases shares a timestamp (which the seeded data does exactly).
        queryset = (
            self.model.objects.filter(org=self.request.profile.org)
            .order_by("-created_at", "-id")
            .select_related("account", "org", "created_by", "parent")
            .prefetch_related("assigned_to__user", "contacts", "teams", "tags")
        )
        # COORDINATION_DECISIONS.md D4: hide soft-deleted cases by default; admins may opt in.
        include_deleted = params.get("include_deleted") == "true" and is_org_admin(
            self.request.profile
        )
        if not include_deleted:
            queryset = queryset.filter(is_active=True)
        # Hide merged duplicates by default. Agents can opt in with
        # `?show_merged=true` to audit prior merges.
        if params.get("show_merged") != "true":
            queryset = queryset.filter(merged_into__isnull=True).exclude(
                status="Duplicate"
            )
        accounts = Account.objects.filter(org=self.request.profile.org).order_by("-id")
        contacts = Contact.objects.filter(org=self.request.profile.org).order_by("-id")
        profiles = Profile.objects.filter(is_active=True, org=self.request.profile.org)
        if not is_org_admin(self.request.profile):
            # Watcher allowance: a non-admin who is a watcher must still be
            # able to see the case even when un-assigned. The rule now lives
            # in `cases.access` so the detail view enforces the same one. It
            # used to drop the watcher clause, which meant this list handed
            # somebody a ticket that answered 403 when they clicked it.
            queryset = queryset.filter(
                pk__in=visible_cases_qs(self.request.profile).values("pk")
            )
            accounts = accounts.filter(
                Q(created_by=self.request.profile.user)
                | Q(assigned_to=self.request.profile)
            ).distinct()
            contacts = contacts.filter(
                Q(created_by=self.request.profile.user)
                | Q(assigned_to=self.request.profile)
            ).distinct()
            profiles = profiles.filter(role="ADMIN")

        queryset = apply_case_list_filters(queryset, params)

        context = {}

        # Queue totals, counted over the whole filtered queryset rather than
        # the page, so a header that says "12 open" is not really saying
        # "12 on this screen". All three are plain DB counts.
        #
        # `awaiting_first_reply` is deliberately not called "breaching": a
        # breach depends on the org's business calendar and is a Python
        # property per row, so counting it here would mean instantiating every
        # case in the queue. Nobody having replied yet is the fact the queue
        # can actually establish, and it is the one worth acting on.
        open_cases = queryset.filter(status__in=OPEN_STATUSES)
        context["open_count"] = open_cases.count()
        context["urgent_count"] = open_cases.filter(priority="Urgent").count()
        context["awaiting_first_reply"] = open_cases.filter(
            first_response_at__isnull=True
        ).count()

        results_cases = self.paginate_queryset(queryset, self.request, view=self)
        cases = CaseSerializer(results_cases, many=True).data

        if results_cases:
            offset = queryset.filter(id__gte=results_cases[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        context.update(
            {
                "cases_count": self.count,
                "offset": offset,
            }
        )
        context["cases"] = cases
        context["status"] = STATUS_CHOICE
        context["priority"] = PRIORITY_CHOICE
        context["type_of_case"] = CASE_TYPE
        # The account and contact catalogues exist for the case *form*, but
        # they were serialized in full on every list call, 190 KB of response
        # for a queue of five tickets in the seeded org, and it grows with the
        # org rather than with the page. `?slim=true` omits them for callers
        # that only want the queue. The default is unchanged, so v1 and the
        # mobile client see exactly what they saw before.
        if params.get("slim") != "true":
            context["accounts_list"] = AccountSerializer(accounts, many=True).data
            context["contacts_list"] = ContactSerializer(contacts, many=True).data
        # `profiles` was computed a few lines up, narrowed to admins for
        # non-admins, even, and then dropped on the floor. So a ticket form
        # had no way to populate an assignee picker from the endpoint that
        # already knew the answer. Same `id` / `user__email` shape the
        # contacts and accounts lists publish.
        context["users"] = list(profiles.values("id", "user__email"))
        return context

    @extend_schema(
        operation_id="cases_list",
        tags=["Cases"],
        parameters=swagger_params.cases_list_get_params,
        responses={
            200: inline_serializer(
                name="CaseListResponse",
                fields={
                    "cases_count": serializers.IntegerField(),
                    "offset": serializers.IntegerField(allow_null=True),
                    "cases": CaseSerializer(many=True),
                    "status": serializers.ListField(),
                    "priority": serializers.ListField(),
                    "type_of_case": serializers.ListField(),
                    "accounts_list": AccountSerializer(many=True),
                    "contacts_list": ContactSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        operation_id="cases_create",
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="CaseCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "id": serializers.CharField(),
                    "cases_obj": CaseSerializer(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        serializer = CaseCreateSerializer(data=params, request_obj=request)
        if serializer.is_valid():
            cf_payload = params.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Case", cf_payload or {}, request.profile.org
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cases_obj = serializer.save(
                created_by=request.profile.user,
                org=request.profile.org,
                closed_on=params.get("closed_on"),
                case_type=params.get("case_type"),
                custom_fields=cleaned_cf,
            )

            if params.get("contacts"):
                contacts_list = params.get("contacts")
                contact_ids = payload_id_list(contacts_list, "contacts")
                contacts = Contact.objects.filter(
                    id__in=contact_ids, org=request.profile.org
                )
                if contacts:
                    cases_obj.contacts.add(*contacts)

            if params.get("teams"):
                teams_list = params.get("teams")
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                if teams.exists():
                    cases_obj.teams.add(*teams)

            if params.get("assigned_to"):
                assinged_to_list = params.get("assigned_to")
                assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                if profiles:
                    cases_obj.assigned_to.add(*profiles)

            if params.get("tags"):
                tags = params.get("tags")
                if isinstance(tags, str):
                    tags = json.loads(tags)
                # Extract IDs if tags contains objects with 'id' field
                tag_ids = [
                    item.get("id") if isinstance(item, dict) else item for item in tags
                ]
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                cases_obj.tags.add(*tag_objs)

            if self.request.FILES.get("case_attachment"):
                create_attachment(
                    self.request.FILES.get("case_attachment"),
                    cases_obj,
                    self.request.profile,
                )

            recipients = list(cases_obj.assigned_to.all().values_list("id", flat=True))
            send_email_to_assigned_user.delay(
                recipients,
                cases_obj.id,
                str(request.profile.org.id),
            )
            return Response(
                {
                    "error": False,
                    "message": "Case Created Successfully",
                    "id": str(cases_obj.id),
                    "cases_obj": CaseSerializer(cases_obj).data,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


def notify_newly_assigned(request, case, previous_assignee_ids):
    """Email whoever the edit just put on this ticket, and nobody else.

    Shared by PUT and PATCH. It used to live only in PUT, so the web app,
    which edits with PATCH, assigned people who were never told. The phone
    edits with PUT and did notify them, which is how the two clients came to
    disagree about whether assignment says anything to the person assigned.
    """
    current = set(case.assigned_to.all().values_list("id", flat=True))
    recipients = list(current - set(previous_assignee_ids))
    if not recipients:
        return
    send_email_to_assigned_user.delay(
        recipients,
        case.id,
        str(request.profile.org.id),
    )


class CaseDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Case

    def get_object(self, pk):
        """A case in the requester's org, or 404.

        This used to return ``None`` for a missing case and leave every caller
        to notice. Only `get` did; `put`, `patch` and `delete` went straight
        on to `cases_object.org` and answered **500**. A malformed id was a
        500 on all four, because the UUID column raises rather than missing.
        """
        return get_case_or_404(self.request.profile, pk)

    @extend_schema(
        operation_id="cases_update",
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="CaseUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        cases_object = self.get_object(pk=pk)
        assert_case_write_access(request.profile, cases_object)

        serializer = CaseCreateSerializer(
            cases_object,
            data=params,
            request_obj=request,
        )

        if serializer.is_valid():
            cf_payload = params.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Case",
                cf_payload or {},
                request.profile.org,
                existing=cases_object.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cases_object = serializer.save(
                closed_on=params.get("closed_on"),
                case_type=params.get("case_type"),
                custom_fields=cleaned_cf,
            )
            previous_assigned_to_users = list(
                cases_object.assigned_to.all().values_list("id", flat=True)
            )
            cases_object.contacts.clear()
            if params.get("contacts"):
                contacts_list = params.get("contacts")
                contact_ids = payload_id_list(contacts_list, "contacts")
                contacts = Contact.objects.filter(
                    id__in=contact_ids, org=request.profile.org
                )
                if contacts:
                    cases_object.contacts.add(*contacts)

            cases_object.teams.clear()
            if params.get("teams"):
                teams_list = params.get("teams")
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                if teams.exists():
                    cases_object.teams.add(*teams)

            cases_object.assigned_to.clear()
            if params.get("assigned_to"):
                assinged_to_list = params.get("assigned_to")
                assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                if profiles:
                    cases_object.assigned_to.add(*profiles)

            cases_object.tags.clear()
            if params.get("tags"):
                tags = params.get("tags")
                if isinstance(tags, str):
                    tags = json.loads(tags)
                # Extract IDs if tags contains objects with 'id' field
                tag_ids = [
                    item.get("id") if isinstance(item, dict) else item for item in tags
                ]
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                cases_object.tags.add(*tag_objs)

            if self.request.FILES.get("case_attachment"):
                create_attachment(
                    self.request.FILES.get("case_attachment"),
                    cases_object,
                    self.request.profile,
                )

            notify_newly_assigned(request, cases_object, previous_assigned_to_users)
            return Response(
                {"error": False, "message": "Case Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        operation_id="cases_destroy",
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="CaseDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        assert_case_delete_access(request.profile, self.object)
        self.object.delete()
        return Response(
            {"error": False, "message": "Case Deleted Successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="cases_retrieve",
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="CaseDetailResponse",
                fields={
                    "cases_obj": CaseSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": CommentSerializer(many=True),
                    "comment_permission": serializers.BooleanField(),
                    "users_mention": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        self.cases = self.get_object(pk=pk)
        # Merged duplicate → tell the client to redirect. JSON form (200) keeps
        # the SvelteKit route's error handling simple. The query param
        # `?show_merged=true` lets agents view the duplicate directly via
        # bookmark / list-view escape hatch.
        if (
            self.cases.merged_into_id
            and request.query_params.get("show_merged") != "true"
        ):
            return Response(
                {
                    "redirect_to": str(self.cases.merged_into_id),
                    "merged_into": str(self.cases.merged_into_id),
                    "source_case_id": str(self.cases.id),
                    "source_case_name": self.cases.name,
                },
                status=status.HTTP_200_OK,
            )
        # Authorise before serialising: the old order built the response body
        # for a case the requester was about to be refused.
        assert_case_read_access(request.profile, self.cases)

        context = {}
        context["cases_obj"] = CaseSerializer(self.cases).data

        # `comment_permission` used to be creator-or-admin while `post` below
        # accepted creator, admin *or assignee*. The flag told an assignee they
        # could not reply to their own ticket and the endpoint then let them.
        # Both now read the same rule, so the button matches the answer.
        comment_permission = has_case_write_access(request.profile, self.cases)

        if is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(
                    is_active=True, org=self.request.profile.org
                ).values("user__email")
            )
        elif self.request.profile.user_id != self.cases.created_by_id:
            # `created_by` is a `User`; `Profile.user` is the FK pointing at
            # one, so `User.user` does not exist and this line raised
            # AttributeError, a 500 for *every* non-admin on *every* ticket
            # they were entitled to open. The guard above it compared a
            # Profile to a User and was always true, so nothing shielded it.
            # The admin branch above emits `user__email` keys, so this one does
            # too. It used to say `username`, which meant a mention list that
            # changed shape with the reader's role.
            users_mention = (
                [{"user__email": self.cases.created_by.email}]
                if self.cases.created_by_id
                else []
            )
        else:
            users_mention = []

        case_content_type = ContentType.objects.get_for_model(Case)
        attachments = Attachments.objects.filter(
            content_type=case_content_type,
            object_id=self.cases.id,
            org=self.request.profile.org,
        ).order_by("-id")
        comments_qs = Comment.objects.filter(
            content_type=case_content_type,
            object_id=self.cases.id,
            org=self.request.profile.org,
        ).order_by("-id")
        public_comments = comments_qs.filter(is_internal=False)
        internal_notes = comments_qs.filter(is_internal=True)

        linked_solutions = self.cases.solutions.filter(org=self.request.profile.org)

        recent_activities = Activity.objects.filter(
            entity_type="Case",
            entity_id=self.cases.id,
            org=self.request.profile.org,
        ).select_related("user__user")[:20]

        custom_field_defs = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Case",
            is_active=True,
        ).order_by("display_order", "label")

        # Inbound emails associated with this case (most recent first), so the
        # discussion tab can render them with an "Email" badge.
        email_messages = _EmailMessageModel.objects.filter(
            case=self.cases, drop_reason=""
        ).order_by("-received_at")[:50]

        merged_from = list(
            self.cases.merged_from_cases.filter(org=self.request.profile.org)
            .order_by("-merged_at")
            .values("id", "name", "merged_at")
        )

        context.update(
            {
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(public_comments, many=True).data,
                "internal_notes": CommentSerializer(internal_notes, many=True).data,
                "contacts": ContactSerializer(
                    self.cases.contacts.all(), many=True
                ).data,
                "solutions": SolutionSerializer(linked_solutions, many=True).data,
                "activities": ActivitySerializer(recent_activities, many=True).data,
                "email_messages": EmailMessageSerializer(
                    email_messages, many=True
                ).data,
                "merged_from_cases": merged_from,
                "custom_field_definitions": CustomFieldDefinitionSerializer(
                    custom_field_defs, many=True
                ).data,
                "status": STATUS_CHOICE,
                "priority": PRIORITY_CHOICE,
                "type_of_case": CASE_TYPE,
                "comment_permission": comment_permission,
                "users_mention": users_mention,
            }
        )
        return Response(context)

    @extend_schema(
        operation_id="cases_comment_attachment",
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseDetailEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="CaseCommentAttachmentResponse",
                fields={
                    "cases_obj": CaseSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": CommentSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        params = request.data
        # `.get()` raised DoesNotExist, a 500, when the case was missing or
        # belonged to another org. Replying to a ticket that is not there is a
        # 404, not a crash.
        self.cases_obj = self.get_object(pk)
        assert_case_write_access(request.profile, self.cases_obj)
        context = {}
        comment_text = params.get("comment")
        if comment_text:
            is_internal_raw = params.get("is_internal", False)
            if isinstance(is_internal_raw, str):
                is_internal = is_internal_raw.lower() in ("true", "1", "yes")
            else:
                is_internal = bool(is_internal_raw)
            Comment.objects.create(
                comment=comment_text,
                content_type=ContentType.objects.get_for_model(Case),
                object_id=self.cases_obj.id,
                commented_by=self.request.profile,
                is_internal=is_internal,
                org=self.request.profile.org,
            )

        if self.request.FILES.get("case_attachment"):
            create_attachment(
                self.request.FILES.get("case_attachment"),
                self.cases_obj,
                self.request.profile,
            )

        case_content_type = ContentType.objects.get_for_model(Case)
        attachments = Attachments.objects.filter(
            content_type=case_content_type,
            object_id=self.cases_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        comments_qs = Comment.objects.filter(
            content_type=case_content_type,
            object_id=self.cases_obj.id,
            org=request.profile.org,
        ).order_by("-id")

        context.update(
            {
                "cases_obj": CaseSerializer(self.cases_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(
                    comments_qs.filter(is_internal=False), many=True
                ).data,
                "internal_notes": CommentSerializer(
                    comments_qs.filter(is_internal=True), many=True
                ).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseCreateSwaggerSerializer,
        description="Partial Case Update",
        responses={
            200: inline_serializer(
                name="CasePatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a case."""
        params = request.data
        cases_object = self.get_object(pk=pk)
        assert_case_write_access(request.profile, cases_object)

        serializer = CaseCreateSerializer(
            cases_object,
            data=params,
            request_obj=request,
            partial=True,
        )

        if serializer.is_valid():
            previous_assigned_to_users = list(
                cases_object.assigned_to.all().values_list("id", flat=True)
            )
            save_kwargs = {
                "closed_on": (
                    params.get("closed_on")
                    if "closed_on" in params
                    else cases_object.closed_on
                ),
                "case_type": (
                    params.get("case_type")
                    if "case_type" in params
                    else cases_object.case_type
                ),
            }
            if "custom_fields" in params:
                cf_payload = params.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Case",
                    cf_payload or {},
                    request.profile.org,
                    existing=cases_object.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            cases_object = serializer.save(**save_kwargs)

            # Handle M2M fields if present in request
            if "contacts" in params:
                cases_object.contacts.clear()
                contacts_list = params.get("contacts")
                if contacts_list:
                    contact_ids = payload_id_list(contacts_list, "contacts")
                    contacts = Contact.objects.filter(
                        id__in=contact_ids, org=request.profile.org
                    )
                    cases_object.contacts.add(*contacts)

            if "teams" in params:
                cases_object.teams.clear()
                teams_list = params.get("teams")
                if teams_list:
                    team_ids = payload_id_list(teams_list, "teams")
                    teams = Teams.objects.filter(
                        id__in=team_ids, org=request.profile.org
                    )
                    cases_object.teams.add(*teams)

            if "assigned_to" in params:
                cases_object.assigned_to.clear()
                assigned_to_list = params.get("assigned_to")
                if assigned_to_list:
                    assigned_ids = payload_id_list(assigned_to_list, "assigned_to")
                    profiles = Profile.objects.filter(
                        id__in=assigned_ids, org=request.profile.org, is_active=True
                    )
                    cases_object.assigned_to.add(*profiles)

            if "tags" in params:
                cases_object.tags.clear()
                tags_list = params.get("tags")
                if tags_list:
                    tag_ids = payload_id_list(tags_list, "tags")
                    tag_objs = Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                    cases_object.tags.add(*tag_objs)

            notify_newly_assigned(request, cases_object, previous_assigned_to_users)
            return Response(
                {"error": False, "message": "Case Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class CaseCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        """Org-scoped already; `.get()` was the problem. A comment id that
        does not exist raised DoesNotExist and answered 500 instead of 404."""
        try:
            comment = self.model.objects.filter(
                pk=pk, org=self.request.profile.org
            ).first()
        except (DjangoValidationError, ValueError):
            raise Http404("No such comment.")
        if comment is None:
            raise Http404("No such comment.")
        return comment

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseCommentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="CaseCommentUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            # No `if params.get("comment")` guard around the block below. It
            # used to sit here, and a body with a blank or absent `comment`
            # fell out of the authorization branch entirely and landed on the
            # 403 at the bottom, telling an author they may not edit their own
            # comment when the real answer is that the field is required.
            # `comment` is a non-blank CharField, so the serializer already
            # answers that with a 400 naming the field.
            serializer = CommentSerializer(obj, data=params)
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
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=CaseCommentEditSwaggerSerializer,
        description="Partial Comment Update",
        responses={
            200: inline_serializer(
                name="CaseCommentPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a comment."""
        params = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            serializer = CommentSerializer(obj, data=params, partial=True)
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
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="CaseCommentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
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
                "errors": "You do not have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class CaseAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="CaseAttachmentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        """Delete one attachment hanging off a case.

        Two defects, both proven live before the fix:

        1. `objects.get(pk=pk)` had **no org filter**. `Attachments` is one
           generic table shared by leads, accounts, contacts, deals, cases and
           tasks, so this endpoint deleted any attachment in the database
           belonging to any organisation, given only its UUID. RLS blocks that
           in a correctly-configured deployment, but per CLAUDE.md the ORM
           filter is the contract and RLS is the safety net, and the dev role
           here is a superuser, so the probe went through.
        2. `request.profile == self.object.created_by` compares a `Profile` to
           a `User` FK and is therefore never true, which quietly made the
           endpoint admin-only: the person who uploaded the file could not
           remove it.

        The same one-line lookup bug is still open in `leads`, `tasks` and
        `opportunity`.
        """
        try:
            self.object = self.model.objects.filter(
                pk=pk, org=request.profile.org
            ).first()
        except (DjangoValidationError, ValueError):
            raise Http404("No such attachment.")
        if self.object is None:
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
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class CaseSolutionLinkView(APIView):
    """Link or unlink Solutions (Knowledge Base articles) to a Case."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_solution(self, pk, org):
        try:
            return Solution.objects.filter(pk=pk, org=org).first()
        except (DjangoValidationError, ValueError):
            # A malformed UUID is a solution that does not exist, not a 500.
            return None

    @extend_schema(
        tags=["Cases"],
        request=inline_serializer(
            name="CaseSolutionLinkRequest",
            fields={"solution_id": serializers.CharField()},
        ),
    )
    def post(self, request, pk, solution_pk=None):
        # This class is routed twice: `<pk>/solutions/` for POST and
        # `<pk>/solutions/<solution_pk>/` for DELETE. Django hands every route's
        # captured kwargs to whichever method it dispatches, so POST to the
        # second route arrived with an argument it had no parameter for and
        # DELETE to the first arrived missing one. Both were an uncaught
        # TypeError, which is a 500 on a route the client is simply using the
        # wrong verb on. 405 is the answer to a wrong verb.
        if solution_pk is not None:
            self.http_method_not_allowed(request)
        case = get_case_or_404(request.profile, pk)
        # Attaching an article to a ticket changes the ticket, so it takes the
        # same permission as any other change to it. Org alone was the whole
        # check, which meant somebody refused the case with a 403 could still
        # link to it, and then read the case straight back out of the
        # article's `linked_cases`. Closing that read-around at the serializer
        # is not enough on its own: writing to a ticket you cannot open is a
        # defect whichever way the data comes back.
        assert_case_write_access(request.profile, case)

        solution_id = request.data.get("solution_id")
        if not solution_id:
            return Response(
                {"error": True, "errors": "solution_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sol = self._get_solution(solution_id, request.profile.org)
        if not sol:
            return Response(
                {"error": True, "errors": "Solution not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        already_linked = case.solutions.filter(pk=sol.pk).exists()
        if not already_linked:
            case.solutions.add(sol)
        return Response(
            {"error": False, "solution": SolutionSerializer(sol).data},
            status=(status.HTTP_200_OK if already_linked else status.HTTP_201_CREATED),
        )

    @extend_schema(tags=["Cases"])
    def delete(self, request, pk, solution_pk=None):
        # See `post` above: reached without a solution id, this is the
        # collection route being deleted, which is a wrong verb, not a crash.
        if solution_pk is None:
            self.http_method_not_allowed(request)
        case = get_case_or_404(request.profile, pk)
        assert_case_write_access(request.profile, case)
        sol = self._get_solution(solution_pk, request.profile.org)
        if not sol:
            return Response(
                {"error": True, "errors": "Solution not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        case.solutions.remove(sol)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CaseActivityListView(APIView, LimitOffsetPagination):
    """Paginated audit-log feed for a single Case (newest-first)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="CaseActivityListResponse",
                fields={
                    "activities": ActivitySerializer(many=True),
                    "count": serializers.IntegerField(),
                    "offset": serializers.IntegerField(allow_null=True),
                },
            )
        },
    )
    def get(self, request, pk):
        case = get_case_or_404(request.profile, pk)
        assert_case_read_access(request.profile, case)

        queryset = Activity.objects.filter(
            entity_type="Case",
            entity_id=case.id,
            org=request.profile.org,
        ).select_related("user__user")

        page = self.paginate_queryset(queryset, request, view=self)
        data = ActivitySerializer(page, many=True).data
        if page:
            offset = queryset.filter(id__gte=page[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        return Response({"activities": data, "count": self.count, "offset": offset})


def _reopen_analytics(org):
    """Honest 30-day reopen metrics for the settings page.

    All three come from what the reopen path in `cases/signals.py` actually
    persists:
    - `reopened_last_30d`: one REOPENED Activity is written per reopen.
    - `replies_after_window_30d`: when an external reply lands too late the
      signal flags the COMMENT Activity `out_of_reopen_window=True`, the
      authoritative "reopened nothing" record, judged against the window in
      force at the time (which a recompute here could not reproduce).
    - `median_days_to_reply`: days-from-close-to-reply across every post-close
      external reply we can date. A reply that reopened nulled `closed_on`, so
      its delta survives only in the REOPENED Activity's `days_since_close`; a
      reply that did not reopen left its case Closed, so its delta is
      `commented_on − closed_on`. The median unions both, with no double count.
      A reopened case is no longer Closed, so it drops out of the comment side.

    Every query is `org=`-scoped explicitly (RLS is inert for the app's DB role
    in dev/test), so another org's cases, comments and activities cannot leak in.
    """
    cutoff = timezone.now() - timedelta(days=30)

    # Reopen side: REOPENED activities (count + the delta carried in each).
    reopened_meta = list(
        Activity.objects.filter(
            org=org,
            action="REOPENED",
            entity_type="Case",
            created_at__gte=cutoff,
        ).values_list("metadata", flat=True)
    )

    # Missed-window side: the signal's authoritative flag on COMMENT activities.
    comment_meta = Activity.objects.filter(
        org=org,
        action="COMMENT",
        entity_type="Case",
        created_at__gte=cutoff,
    ).values_list("metadata", flat=True)
    replies_after_window_30d = sum(
        1 for m in comment_meta if (m or {}).get("out_of_reopen_window") is True
    )

    # Median: reopened replies contribute their metadata delta...
    deltas = []
    for metadata in reopened_meta:
        try:
            days = int((metadata or {}).get("days_since_close"))
        except (TypeError, ValueError):
            continue
        if days >= 0:
            deltas.append(days)

    # ...and non-reopened post-close replies contribute commented_on − closed_on.
    # `Comment` is a generic relation, so join to Case by hand.
    case_ct = ContentType.objects.get_for_model(Case)
    external_comments = list(
        Comment.objects.filter(
            org=org,
            content_type=case_ct,
            commented_by__isnull=True,
            is_internal=False,
            commented_on__gte=cutoff,
        ).values_list("object_id", "commented_on")
    )
    object_ids = {oid for oid, _ in external_comments}
    closed_on_by_id = {}
    if object_ids:
        closed_on_by_id = dict(
            Case.objects.filter(org=org, id__in=object_ids, status="Closed")
            .exclude(closed_on__isnull=True)
            .values_list("id", "closed_on")
        )
    for object_id, commented_on in external_comments:
        closed_on = closed_on_by_id.get(object_id)
        if closed_on is None:
            continue  # reopened / reassigned open / comment predates the close
        days = (timezone.localdate(commented_on) - closed_on).days
        if days >= 0:
            deltas.append(days)

    return {
        "reopened_last_30d": len(reopened_meta),
        "replies_after_window_30d": replies_after_window_30d,
        "median_days_to_reply": (round(statistics.median(deltas), 1) if deltas else 0),
    }


class ReopenPolicyView(APIView):
    """Per-org reopen policy. Admin-only. Auto-creates the singleton on first read."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_or_create_policy(self, org):
        policy = ReopenPolicy.objects.filter(org=org).first()
        if policy is None:
            policy = ReopenPolicy.objects.create(org=org)
        return policy

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        responses={200: ReopenPolicySerializer},
    )
    def get(self, request, format=None):
        if not is_org_admin(request.profile):
            return Response(
                {"error": True, "errors": "Admin access required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        policy = self._get_or_create_policy(request.profile.org)
        data = ReopenPolicySerializer(policy).data
        data.update(_reopen_analytics(request.profile.org))
        return Response(data)

    @extend_schema(
        tags=["Cases"],
        parameters=swagger_params.organization_params,
        request=ReopenPolicySerializer,
        responses={200: ReopenPolicySerializer},
    )
    def put(self, request, format=None):
        if not is_org_admin(request.profile):
            return Response(
                {"error": True, "errors": "Admin access required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        policy = self._get_or_create_policy(request.profile.org)
        serializer = ReopenPolicySerializer(policy, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(serializer.data)
