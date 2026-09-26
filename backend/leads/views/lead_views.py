import copy
import json
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import (
    Attachments,
    Comment,
    CustomFieldDefinition,
    Profile,
    Tags,
    Teams,
)
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    AttachmentsSerializer,
    CustomFieldDefinitionSerializer,
    LeadCommentSerializer,
    ProfileSerializer,
    TeamsSerializer,
)
from common.utils import (
    COUNTRIES,
    INDCHOICES,
    LEAD_SOURCE,
    LEAD_STATUS,
    create_attachment,
    validate_attachment,
)
from common.validators import (
    choice_list_param,
    date_param,
    payload_id_list,
    uuid_list_param,
)
from contacts.access import replace_visible_contacts, visible_contacts_qs
from leads import access, swagger_params
from leads.models import Lead
from leads.serializer import (
    LeadCreateSerializer,
    LeadCreateSwaggerSerializer,
    LeadDetailEditSwaggerSerializer,
    LeadSerializer,
    TagsSerializer,
)
from leads.services import conversion_refusal
from leads.tasks import send_email_to_assigned_user
from leads.workflow import IRREVERSIBLE_STATUSES


def _conversion_refused(request, serializer):
    """A 400 when converting would file the lead under an account the caller
    cannot open, else None.

    Asked with the lead as ``serializer.save()`` would leave it, before
    anything is written, so a refused conversion keeps the lead as it was:
    the fields sent with it are not saved either. A copy of the instance
    carries the values; the lead itself is not touched.
    """
    lead = copy.copy(serializer.instance) if serializer.instance else Lead()
    for field, value in serializer.validated_data.items():
        setattr(lead, field, value)
    refusal = conversion_refusal(request, lead)
    if refusal is None:
        return None
    return Response(
        {"error": True, "errors": {"status": [refusal]}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def lead_list_queryset(profile, user, params):
    """The rows ``GET /api/leads/`` lists for this caller, before it splits them.

    The read rule, every query parameter the list takes, and its order, in one
    place: the list and the CSV export both start here, so the file a person
    downloads holds exactly the leads the page would show them with the same
    filters. Converted leads are never listed. ``?open=true`` leaves out the
    closed ones too, which is what the web list shows; without it both halves
    come back, as the mobile list reads them.
    """
    queryset = (
        access.visible_leads_qs(profile, user)
        .exclude(status="converted")
        .order_by("-id")
    )
    if params.get("name"):
        name = params.get("name")
        queryset = queryset.filter(
            Q(first_name__icontains=name) | Q(last_name__icontains=name)
        )
    if params.get("salutation"):
        queryset = queryset.filter(salutation__icontains=params.get("salutation"))
    if params.get("source"):
        queryset = queryset.filter(source=params.get("source"))
    assigned_to = uuid_list_param(params, "assigned_to")
    if assigned_to:
        queryset = queryset.filter(assigned_to__id__in=assigned_to)
    # Repeatable, like the tasks list. The dashboard's Hot Leads count
    # means "assigned or in process", which is narrower than the
    # "not converted, not closed" set this list already shows, so no
    # single-value filter could reproduce it.
    statuses = choice_list_param(
        params, "status", [value for value, _label in LEAD_STATUS]
    )
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    tags = uuid_list_param(params, "tags")
    if tags:
        queryset = queryset.filter(tags__id__in=tags)
    if params.get("city"):
        queryset = queryset.filter(city__icontains=params.get("city"))
    if params.get("email"):
        queryset = queryset.filter(email__icontains=params.get("email"))
    if params.get("rating"):
        queryset = queryset.filter(rating=params.get("rating"))
    if params.get("search"):
        search = params.get("search")
        queryset = queryset.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(company_name__icontains=search)
            | Q(email__icontains=search)
        )
    created_at_gte = date_param(params, "created_at__gte")
    if created_at_gte:
        queryset = queryset.filter(created_at__date__gte=created_at_gte)
    created_at_lte = date_param(params, "created_at__lte")
    if created_at_lte:
        queryset = queryset.filter(created_at__date__lte=created_at_lte)
    close_date_gte = date_param(params, "close_date__gte")
    if close_date_gte:
        queryset = queryset.filter(close_date__gte=close_date_gte)
    close_date_lte = date_param(params, "close_date__lte")
    if close_date_lte:
        queryset = queryset.filter(close_date__lte=close_date_lte)
    # Exact day, because the one caller is "follow-ups due today" and a
    # range would be two parameters for a question nobody asks.
    next_follow_up = date_param(params, "next_follow_up")
    if next_follow_up:
        queryset = queryset.filter(next_follow_up=next_follow_up)
    # Custom-field filters: ?cf_<key>=<value> -> custom_fields contains pair.
    for raw_key, raw_value in params.items():
        if raw_key.startswith("cf_") and raw_value:
            cf_key = raw_key[3:]
            if cf_key:
                queryset = queryset.filter(custom_fields__contains={cf_key: raw_value})
    if params.get("open") == "true":
        queryset = queryset.exclude(status="closed")
    return queryset.distinct()


class LeadListView(APIView, LimitOffsetPagination):
    model = Lead
    permission_classes = (IsAuthenticated, HasOrgContext)

    #: A lead nobody has touched in this many days counts as unworked. Used
    #: only for the headline count. It is not a status and nothing is filtered
    #: on it.
    UNWORKED_AFTER_DAYS = 7

    def get_totals(self, queryset_open):
        """Headline counts for the open-leads list.

        Computed over the *whole* filtered queryset, deliberately. The caller
        only ever holds one page, so a client-side reduction would report the
        page and label it the list. Every filter applied above is already
        baked into `queryset_open`, including the org scope and the
        non-admin "only leads assigned to or created by me" narrowing, so
        these counts describe exactly the set the requester is allowed to see.

        `unworked_over_a_week` reads `last_contacted`, falling back to when the
        lead was created. A lead nobody has ever contacted is not unworked on
        the day it arrives. It becomes unworked once it has sat that long.
        Lead has no aging chain (rotting days live on a deal's `DealStage`, and
        `get_aging_status()` is Opportunity-only), so this is the strongest
        signal the model actually carries.
        """
        cutoff = timezone.localdate() - timedelta(days=self.UNWORKED_AFTER_DAYS)
        totals_queryset = queryset_open.distinct()
        return {
            "count": totals_queryset.count(),
            "unworked_over_a_week": totals_queryset.annotate(
                last_touch=Coalesce("last_contacted", TruncDate("created_at"))
            )
            .filter(last_touch__lt=cutoff)
            .count(),
        }

    def get_context_data(self, **kwargs):
        queryset = (
            lead_list_queryset(
                self.request.profile, self.request.user, self.request.query_params
            )
            .select_related("created_by")
            .prefetch_related("tags", "assigned_to")
        )
        context = {}
        queryset_open = queryset.exclude(status="closed")
        results_leads_open = self.paginate_queryset(
            queryset_open.distinct(), self.request, view=self
        )
        open_leads = LeadSerializer(results_leads_open, many=True).data
        if results_leads_open:
            offset = queryset_open.filter(id__gte=results_leads_open[-1].id).count()
            if offset == queryset_open.count():
                offset = None
        else:
            offset = 0
        context["per_page"] = 10
        page_number = int(self.offset / 10) + 1
        context["page_number"] = page_number
        context["open_leads"] = {
            "leads_count": self.count,
            "open_leads": open_leads,
            "offset": offset,
        }
        context["totals"] = self.get_totals(queryset_open)

        queryset_close = queryset.filter(status="closed")
        results_leads_close = self.paginate_queryset(
            queryset_close.distinct(), self.request, view=self
        )
        close_leads = LeadSerializer(results_leads_close, many=True).data
        if results_leads_close:
            offset = queryset_close.filter(id__gte=results_leads_close[-1].id).count()
            if offset == queryset_close.count():
                offset = None
        else:
            offset = 0

        context["close_leads"] = {
            "leads_count": self.count,
            "close_leads": close_leads,
            "offset": offset,
        }
        # The contact read rule itself. This catalogue feeds the lead form's
        # contact picker, and org scope alone would let a member read back the
        # first name of every contact in the org, including ones
        # `/api/contacts/` refuses them. It is also exactly what
        # `replace_visible_contacts` accepts on save.
        contacts = visible_contacts_qs(self.request.profile).values("id", "first_name")

        context["contacts"] = contacts
        context["status"] = LEAD_STATUS
        context["source"] = LEAD_SOURCE
        context["tags"] = TagsSerializer(
            Tags.objects.filter(org=self.request.profile.org, is_active=True), many=True
        ).data

        users = Profile.objects.filter(
            is_active=True, org=self.request.profile.org
        ).values("id", "user__email")
        context["users"] = users
        context["countries"] = COUNTRIES
        context["industries"] = INDCHOICES
        return context

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_list",
        parameters=swagger_params.lead_list_get_params,
        responses={
            200: inline_serializer(
                name="LeadListResponse",
                fields={
                    "per_page": serializers.IntegerField(),
                    "page_number": serializers.IntegerField(),
                    "open_leads": serializers.DictField(),
                    "close_leads": serializers.DictField(),
                    "contacts": serializers.ListField(),
                    "status": serializers.ListField(),
                    "source": serializers.ListField(),
                    "tags": TagsSerializer(many=True),
                    "users": serializers.ListField(),
                    "countries": serializers.ListField(),
                    "industries": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_create",
        description="Leads Create",
        parameters=swagger_params.organization_params,
        request=LeadCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="LeadCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "account_id": serializers.CharField(required=False),
                    "contact_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                    "opportunity_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        """Create a new lead, optionally converting it to an account immediately."""
        data = request.data
        serializer = LeadCreateSerializer(data=data, request_obj=request)
        if serializer.is_valid():
            cf_payload = data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Lead", cf_payload or {}, request.profile.org
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Every id list, and the file, is checked before the first write,
            # so a bad one is a 400 with no half-created lead behind it.
            tags = payload_id_list(data.get("tags"), "tags")
            contacts = payload_id_list(data.get("contacts"), "contacts")
            team_ids = payload_id_list(data.get("teams"), "teams")
            assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")
            validate_attachment(request.FILES.get("lead_attachment"))
            if data.get("status") == "converted":
                refused = _conversion_refused(request, serializer)
                if refused:
                    return refused
            try:
                lead_obj = serializer.save(
                    created_by=request.profile.user,
                    org=request.profile.org,
                    custom_fields=cleaned_cf,
                )
            except IntegrityError as e:
                if "email" in str(e).lower():
                    return Response(
                        {
                            "error": True,
                            "errors": {
                                "email": [
                                    "A lead with this email already exists in your organization."
                                ]
                            },
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {
                        "error": True,
                        "errors": "A lead with these details already exists.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if tags:
                tag_objs = Tags.objects.filter(
                    id__in=tags, org=request.profile.org, is_active=True
                )
                lead_obj.tags.add(*tag_objs)

            replace_visible_contacts(lead_obj.contacts, contacts, request.profile)

            if request.FILES.get("lead_attachment"):
                create_attachment(
                    request.FILES.get("lead_attachment"),
                    lead_obj,
                    request.profile,
                )

            if team_ids:
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                lead_obj.teams.add(*teams)

            if assigned_ids:
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                lead_obj.assigned_to.add(*profiles)

            # Send email to assigned users (after assignees are set)
            # Skip if status is "converted" - that branch handles its own email
            if data.get("status") != "converted":
                recipients = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )

            if data.get("status") == "converted":
                from leads.services import convert_lead_to_account

                account, contact, opportunity = convert_lead_to_account(
                    lead_obj, request
                )

                # Send email to assigned users for converted leads
                recipients = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )
                return Response(
                    {
                        "error": False,
                        "message": "Lead Converted Successfully",
                        "account_id": str(account.id),
                        "contact_id": str(contact.id) if contact else None,
                        "opportunity_id": str(opportunity.id) if opportunity else None,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": False, "message": "Lead Created Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class LeadDetailView(APIView):
    model = Lead
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return get_object_or_404(Lead, id=pk, org=self.request.profile.org)

    def assert_lead_access(self):
        """Delegates to `leads.access`, which holds the one definition.

        The attachment download view has to ask the same question, and a
        second copy of the answer is how the two copies this replaced drifted
        apart in the first place.
        """
        access.assert_lead_access(
            self.request.profile, self.request.user, self.lead_obj
        )

    def get_context_data(self, **kwargs):
        context = {}
        self.assert_lead_access()

        lead_content_type = ContentType.objects.get_for_model(Lead)
        comments = Comment.objects.filter(
            content_type=lead_content_type, object_id=self.lead_obj.id
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=lead_content_type, object_id=self.lead_obj.id
        ).order_by("-id")
        assigned_data = []
        for each in self.lead_obj.assigned_to.all():
            assigned_dict = {}
            assigned_dict["id"] = each.id
            assigned_dict["name"] = each.user.email
            assigned_data.append(assigned_dict)

        if self.request.user.is_superuser or is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(
                    is_active=True, org=self.request.profile.org
                ).values("user__email")
            )
        elif self.request.profile.user != self.lead_obj.created_by:
            # Two ways this used to be a 500, both masked by the permission
            # check above returning a Response instead of raising, nothing
            # reached this line.
            #
            # `username` is not a field on this User model at all: it sets
            # `USERNAME_FIELD = "email"` and defines no `username`, so the
            # attribute access raised for *every* lead. And `created_by` is
            # nullable, genuinely null on leads that arrived through
            # `CreateLeadFromSite`, which has no authenticated user to
            # attribute them to.
            #
            # The key is `user__email` to match the two sibling branches; this
            # list feeds @-mention autocomplete and the callers read one shape.
            users_mention = (
                [{"user__email": self.lead_obj.created_by.email}]
                if self.lead_obj.created_by
                else []
            )
        else:
            users_mention = list(self.lead_obj.assigned_to.all().values("user__email"))
        lead_content_type = ContentType.objects.get_for_model(Lead)
        comments = Comment.objects.filter(
            content_type=lead_content_type,
            object_id=self.lead_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=lead_content_type,
            object_id=self.lead_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        if is_org_admin(self.request.profile) or self.request.user.is_superuser:
            users = Profile.objects.filter(
                is_active=True, org=self.request.profile.org
            ).order_by("user__email")
        else:
            users = Profile.objects.filter(
                role="ADMIN", org=self.request.profile.org
            ).order_by("user__email")
        team_ids = [user.id for user in self.lead_obj.get_team_users]
        all_user_ids = [user.id for user in users]
        users_excluding_team_id = set(all_user_ids) - set(team_ids)
        users_excluding_team = Profile.objects.filter(id__in=users_excluding_team_id)
        context.update(
            {
                "lead_obj": LeadSerializer(self.lead_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": LeadCommentSerializer(comments, many=True).data,
                "users_mention": users_mention,
                "assigned_data": assigned_data,
            }
        )
        context["users"] = ProfileSerializer(users, many=True).data
        context["users_excluding_team"] = ProfileSerializer(
            users_excluding_team, many=True
        ).data
        context["source"] = LEAD_SOURCE
        context["status"] = LEAD_STATUS
        context["teams"] = TeamsSerializer(
            Teams.objects.filter(org=self.request.profile.org), many=True
        ).data
        context["countries"] = COUNTRIES
        # `lead_obj.stage` is only the id. Both detail pages show which
        # pipeline stage the lead is in, so the names travel with it. Null when
        # the lead is in no pipeline.
        stage = self.lead_obj.stage
        context["pipeline_stage"] = (
            {
                "id": str(stage.id),
                "name": stage.name,
                "color": stage.color,
                "stage_type": stage.stage_type,
                "pipeline": {"id": str(stage.pipeline_id), "name": stage.pipeline.name},
            }
            if stage
            else None
        )

        custom_field_defs = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Lead",
            is_active=True,
        ).order_by("display_order", "label")
        context["custom_field_definitions"] = CustomFieldDefinitionSerializer(
            custom_field_defs, many=True
        ).data

        return context

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_retrieve",
        parameters=swagger_params.organization_params,
        description="Lead Detail",
        responses={
            200: inline_serializer(
                name="LeadDetailResponse",
                fields={
                    "lead_obj": LeadSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": LeadCommentSerializer(many=True),
                    "users_mention": serializers.ListField(),
                    "assigned_data": serializers.ListField(),
                    "users": ProfileSerializer(many=True),
                    "users_excluding_team": ProfileSerializer(many=True),
                    "source": serializers.ListField(),
                    "status": serializers.ListField(),
                    "teams": TeamsSerializer(many=True),
                    "countries": serializers.ListField(),
                    "pipeline_stage": serializers.DictField(allow_null=True),
                },
            )
        },
    )
    def get(self, request, pk, **kwargs):
        self.lead_obj = self.get_object(pk)
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_comment_attachment",
        parameters=swagger_params.organization_params,
        request=LeadDetailEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="LeadCommentAttachmentResponse",
                fields={
                    "lead_obj": LeadSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": LeadCommentSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        params = request.data

        context = {}
        # `get_object` is what the other five methods on this view use, and it
        # answers both failures this handler used to get wrong: an id matching
        # no lead raised `DoesNotExist` out of the handler as a 500, and a lead
        # in another tenant got a 403 that confirmed the id was real.
        self.lead_obj = self.get_object(pk)
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if not (
                (self.request.profile.user == self.lead_obj.created_by)
                or (self.request.profile in self.lead_obj.assigned_to.all())
            ):
                return Response(
                    {
                        "error": True,
                        "errors": "You do not have Permission to perform this action",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        # Before the comment is saved, so a refused file does not leave it posted.
        validate_attachment(self.request.FILES.get("lead_attachment"))
        if params.get("comment"):
            lead_content_type = ContentType.objects.get_for_model(Lead)
            Comment.objects.create(
                content_type=lead_content_type,
                object_id=self.lead_obj.id,
                comment=params.get("comment"),
                commented_by=self.request.profile,
                org=self.request.profile.org,
            )

        # Outside the comment branch, where it used to sit. Nested, a file sent
        # on its own was dropped: the endpoint answered 200 with the unchanged
        # attachment list and no error, so the upload looked like it had worked.
        # Proven live against the dev server before this moved. The same three
        # lines in tasks, cases and opportunity were never nested, which is why
        # only leads behaved this way.
        if self.request.FILES.get("lead_attachment"):
            # This one also re-read the User from the database by the id of the
            # User it already had in hand. Same row, one more query.
            create_attachment(
                self.request.FILES.get("lead_attachment"),
                self.lead_obj,
                self.request.profile,
            )

        lead_content_type = ContentType.objects.get_for_model(Lead)
        comments = Comment.objects.filter(
            content_type=lead_content_type,
            object_id=self.lead_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=lead_content_type,
            object_id=self.lead_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "lead_obj": LeadSerializer(self.lead_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": LeadCommentSerializer(comments, many=True).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        request=LeadCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="LeadUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "account_id": serializers.CharField(required=False),
                    "contact_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                    "opportunity_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                },
            )
        },
    )
    def put(self, request, pk, **kwargs):
        """Fully update a lead, optionally converting it to an account."""
        params = request.data
        self.lead_obj = self.get_object(pk)
        # `get_object` scopes to the org, so a lead belonging to another
        # tenant is already a 404 by the time we get here. What was missing is
        # the check *within* the org: this method had no role or ownership test
        # at all, so any authenticated member could rewrite any lead by id,
        # reassign it, change its value, or push it through conversion,
        # including the ones the list view deliberately hides from them.
        self.assert_lead_access()
        previous_assigned_to_users = list(
            self.lead_obj.assigned_to.all().values_list("id", flat=True)
        )
        serializer = LeadCreateSerializer(
            data=params,
            instance=self.lead_obj,
            request_obj=request,
        )
        if serializer.is_valid():
            save_kwargs = {}
            if "custom_fields" in params:
                cf_payload = params.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Lead",
                    cf_payload or {},
                    request.profile.org,
                    existing=self.lead_obj.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            # Parsed before the first write, so a malformed id leaves the lead
            # exactly as it was. `contacts` and `assigned_to` used to go through
            # a raw `json.loads`, which answered 500 on text that was not JSON.
            tags = payload_id_list(params.get("tags"), "tags")
            contact_ids = payload_id_list(params.get("contacts"), "contacts")
            team_ids = payload_id_list(params.get("teams"), "teams")
            assigned_ids = payload_id_list(params.get("assigned_to"), "assigned_to")
            validate_attachment(request.FILES.get("lead_attachment"))
            if params.get("status") == "converted":
                refused = _conversion_refused(request, serializer)
                if refused:
                    return refused
            lead_obj = serializer.save(**save_kwargs)
            lead_obj.tags.clear()
            if tags:
                tag_objs = Tags.objects.filter(
                    id__in=tags, org=request.profile.org, is_active=True
                )
                lead_obj.tags.add(*tag_objs)

            if request.FILES.get("lead_attachment"):
                create_attachment(
                    request.FILES.get("lead_attachment"),
                    lead_obj,
                    request.profile,
                )

            replace_visible_contacts(lead_obj.contacts, contact_ids, request.profile)

            lead_obj.teams.clear()
            if team_ids:
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                lead_obj.teams.add(*teams)

            lead_obj.assigned_to.clear()
            if assigned_ids:
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                lead_obj.assigned_to.add(*profiles)

            # Send email to newly assigned users (after assignees are updated)
            # Skip if status is "converted" - that branch handles its own email
            if params.get("status") != "converted":
                current_assigned_users = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                # Only email users who were newly assigned
                recipients = list(
                    set(current_assigned_users) - set(previous_assigned_to_users)
                )
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )

            if params.get("status") == "converted":
                from leads.services import convert_lead_to_account

                account, contact, opportunity = convert_lead_to_account(
                    lead_obj, request
                )

                # Send email to all assigned users for converted leads
                recipients = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )

                return Response(
                    {
                        "error": False,
                        "message": "Lead Converted Successfully",
                        "account_id": str(account.id),
                        "contact_id": str(contact.id) if contact else None,
                        "opportunity_id": str(opportunity.id) if opportunity else None,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": False, "message": "Lead updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        request=LeadDetailEditSwaggerSerializer,
        description="Partial Lead Update",
        responses={
            200: inline_serializer(
                name="LeadPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "account_id": serializers.CharField(required=False),
                    "contact_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                    "opportunity_id": serializers.CharField(
                        required=False, allow_null=True
                    ),
                },
            )
        },
    )
    def patch(self, request, pk, **kwargs):
        """Handle partial updates to a lead, including conversion."""
        params = request.data
        # No org comparison after this. `get_object` filters on
        # `org=self.request.profile.org`, so a mismatch is already a 404 and
        # the 403 branch that used to sit here could never run.
        self.lead_obj = self.get_object(pk)

        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if not (
                (self.request.profile.user == self.lead_obj.created_by)
                or (self.request.profile in self.lead_obj.assigned_to.all())
            ):
                return Response(
                    {
                        "error": True,
                        "errors": "You do not have Permission to perform this action",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # A conversion rides on an ordinary partial save. The fields sent with
        # it are saved first and the lead is converted afterwards, which is the
        # order PUT has always used. This branch used to convert straight away
        # and drop everything else in the body, so a form that corrected the
        # email and chose "converted" was refused for having no email, and one
        # that fixed a job title converted the lead with the old one.
        converting = params.get("status") == "converted" or params.get("is_converted")
        # `LeadCreateSerializer.validate_status` refuses a repeat conversion
        # too, but only when `status` is in the body; `is_converted` is not a
        # serializer field, so this is the check that covers it.
        if converting and self.lead_obj.status in IRREVERSIBLE_STATUSES:
            return Response(
                {
                    "error": True,
                    "errors": {
                        "status": [
                            f"This lead is already {self.lead_obj.status}. "
                            "Converting it again would create a second "
                            "opportunity against the same account."
                        ]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Handle regular partial updates
        # Capture previous assignees for email notification
        previous_assigned_to_users = list(
            self.lead_obj.assigned_to.all().values_list("id", flat=True)
        )

        serializer = LeadCreateSerializer(
            data=params,
            instance=self.lead_obj,
            request_obj=request,
            partial=True,
        )
        if serializer.is_valid():
            # convert_lead_to_account only creates a Contact when the lead has
            # an email, so converting without one yields an account and an
            # opportunity with nobody attached. Checked against the email this
            # save leaves the lead with, before anything is written.
            email = serializer.validated_data.get("email", self.lead_obj.email)
            if converting and not (email or "").strip():
                return Response(
                    {
                        "error": True,
                        "errors": {
                            "email": [
                                "This lead needs an email address before it can be "
                                "converted. The contact record is created from it."
                            ]
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs = {}
            if "custom_fields" in params:
                cf_payload = params.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Lead",
                    cf_payload or {},
                    request.profile.org,
                    existing=self.lead_obj.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            # Parsed before the first write, as in PUT. An absent key parses to [].
            tag_ids = payload_id_list(params.get("tags"), "tags")
            contact_ids = payload_id_list(params.get("contacts"), "contacts")
            team_ids = payload_id_list(params.get("teams"), "teams")
            assigned_ids = payload_id_list(params.get("assigned_to"), "assigned_to")
            if converting:
                refused = _conversion_refused(request, serializer)
                if refused:
                    return refused
            lead_obj = serializer.save(**save_kwargs)

            # Handle M2M fields if present in request
            if "tags" in params:
                lead_obj.tags.clear()
                if tag_ids:
                    tag_objs = Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                    lead_obj.tags.add(*tag_objs)

            if "contacts" in params:
                replace_visible_contacts(
                    lead_obj.contacts, contact_ids, request.profile
                )

            if "teams" in params:
                lead_obj.teams.clear()
                if team_ids:
                    teams = Teams.objects.filter(
                        id__in=team_ids, org=request.profile.org
                    )
                    lead_obj.teams.add(*teams)

            if "assigned_to" in params:
                lead_obj.assigned_to.clear()
                if assigned_ids:
                    profiles = Profile.objects.filter(
                        id__in=assigned_ids, org=request.profile.org, is_active=True
                    )
                    lead_obj.assigned_to.add(*profiles)

                # Send email to newly assigned users (after assignees are updated)
                current_assigned_users = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                recipients = list(
                    set(current_assigned_users) - set(previous_assigned_to_users)
                )
                # A conversion emails every assignee below, so not twice.
                if recipients and not converting:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )

            if converting:
                from leads.services import convert_lead_to_account

                account, contact, opportunity = convert_lead_to_account(
                    lead_obj, request
                )
                recipients = list(
                    lead_obj.assigned_to.all().values_list("id", flat=True)
                )
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients,
                        lead_obj.id,
                        str(request.profile.org.id),
                    )
                return Response(
                    {
                        "error": False,
                        "message": "Lead Converted Successfully",
                        "account_id": str(account.id),
                        "contact_id": str(contact.id) if contact else None,
                        "opportunity_id": str(opportunity.id) if opportunity else None,
                    },
                    status=status.HTTP_200_OK,
                )

            return Response(
                {"error": False, "message": "Lead updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        description="Lead Delete",
        responses={
            200: inline_serializer(
                name="LeadDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, **kwargs):
        self.object = self.get_object(pk)
        # The `and self.object.org == request.profile.org` conjunct that used
        # to close this condition was always True: `get_object` already scoped
        # the lookup to the caller's org. What remains is the role and
        # ownership test, which is the only part that can answer False.
        if (
            is_org_admin(request.profile)
            or request.user.is_superuser
            or request.profile.user == self.object.created_by
        ):
            self.object.delete()
            return Response(
                {"error": False, "message": "Lead deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": "you don't have permission to delete this lead"},
            status=status.HTTP_403_FORBIDDEN,
        )
