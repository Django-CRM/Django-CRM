import json
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import F, Q, Sum
from django.db.models.functions import Round
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Account
from accounts.serializer import AccountPickerSerializer, TagsSerializer
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import (
    Attachments,
    Comment,
    CustomFieldDefinition,
    Profile,
    Tags,
    Teams,
)
from common.money import currency_block, deal_currency, group_by_currency
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    AttachmentsSerializer,
    CommentSerializer,
    CustomFieldDefinitionSerializer,
    ProfileSerializer,
)
from common.utils import (
    CURRENCY_CODES,
    SOURCES,
    create_attachment,
    validate_attachment,
)
from common.validators import (
    date_param,
    decimal_param,
    payload_id_list,
    uuid_list_param,
    uuid_param,
)
from contacts.access import replace_visible_contacts, visible_contacts_qs
from contacts.serializer import ContactPickerSerializer
from opportunity import access, swagger_params
from opportunity.models import DealPipeline, DealStage, Opportunity, stage_kind_q
from opportunity.serializer import (
    DealContactSerializer,
    OpportunityCreateSerializer,
    OpportunityCreateSwaggerSerializer,
    OpportunityDetailEditSwaggerSerializer,
    OpportunitySerializer,
)
from opportunity.stages import aging_q, stage_choices, stage_index
from opportunity.tasks import send_email_to_assigned_user
from opportunity.workflow import CLOSED_KINDS


def stalled_filter(org):
    """`Q` matching open deals that have sat in one stage past the red line.

    Built by `opportunity.stages.aging_q` from the org's `DealStage` rows, the
    same whole-day thresholds `Opportunity.get_aging_status()` uses to return
    "red", so the `?rotten=true` filter, the `stalled` figure in the list
    totals and the pills on the rows all count the same deals.
    """
    return aging_q(DealStage.objects.filter(org=org), "red")


def deal_list_queryset(profile, user, params):
    """The deals ``GET /api/opportunities/`` lists for this caller and query.

    The read rule is `visible_deals_qs`, not a copy of it: the list used to
    carry its own inline version, which agreed today and had nothing keeping
    it that way. The list, its totals and the CSV export all start here, so a
    downloaded file holds exactly the deals the page would show.
    """
    queryset = access.visible_deals_qs(profile, user).order_by("-id")
    if params.get("name"):
        queryset = queryset.filter(name__icontains=params.get("name"))
    account = uuid_param(params, "account")
    if account:
        queryset = queryset.filter(account=account)
    pipeline = uuid_param(params, "pipeline")
    if pipeline:
        queryset = queryset.filter(pipeline_id=pipeline)
    if params.get("stage"):
        queryset = queryset.filter(stage__contains=params.get("stage"))
    if params.get("lead_source"):
        queryset = queryset.filter(lead_source__contains=params.get("lead_source"))
    tags = uuid_list_param(params, "tags")
    if tags:
        queryset = queryset.filter(tags__id__in=tags).distinct()
    assigned_to = uuid_list_param(params, "assigned_to")
    if assigned_to:
        queryset = queryset.filter(assigned_to__id__in=assigned_to).distinct()
    if params.get("search"):
        queryset = queryset.filter(name__icontains=params.get("search"))
    created_at_gte = date_param(params, "created_at__gte")
    if created_at_gte:
        queryset = queryset.filter(created_at__date__gte=created_at_gte)
    created_at_lte = date_param(params, "created_at__lte")
    if created_at_lte:
        queryset = queryset.filter(created_at__date__lte=created_at_lte)
    closed_on_gte = date_param(params, "closed_on__gte")
    if closed_on_gte:
        queryset = queryset.filter(closed_on__gte=closed_on_gte)
    closed_on_lte = date_param(params, "closed_on__lte")
    if closed_on_lte:
        queryset = queryset.filter(closed_on__lte=closed_on_lte)
    amount_gte = decimal_param(params, "amount__gte")
    if amount_gte:
        queryset = queryset.filter(amount__gte=amount_gte)
    amount_lte = decimal_param(params, "amount__lte")
    if amount_lte:
        queryset = queryset.filter(amount__lte=amount_lte)
    # Custom-field filters: ?cf_<key>=<value> -> custom_fields contains pair.
    for raw_key, raw_value in params.items():
        if raw_key.startswith("cf_") and raw_value:
            cf_key = raw_key[3:]
            if cf_key:
                queryset = queryset.filter(custom_fields__contains={cf_key: raw_value})

    # `?open=true`: everything not in a won or lost stage. The
    # existing `stage` filter is a `contains` match, so it cannot
    # express "not closed"; a caller wanting the working pipeline had to
    # fetch every deal and drop the closed ones client-side, which is
    # only correct until the first page boundary.
    if params.get("open") == "true":
        queryset = queryset.exclude(stage_kind_q(*CLOSED_KINDS))

    if params.get("rotten") == "true":
        # Rotten deals at DB level, using stage-specific thresholds.
        queryset = queryset.filter(stalled_filter(profile.org))
    return queryset.distinct()


class OpportunityListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Opportunity

    def get_totals(self, queryset):
        """Aggregates over the whole filtered queryset, not the current page.

        `opportunities_count` already existed but nothing else did, so a client
        wanting the pipeline value had to add up the rows it happened to be
        holding, which is one page of ten. The resulting header reads as a
        statement about the pipeline and is actually a statement about the
        page, and it changes when you paginate.

        `weighted_sum` is SUM(amount * probability / 100): the forecast, as
        opposed to `amount_sum`, which is what the deals are worth if every one
        of them lands. `probability` is never null on a saved row,
        `Opportunity.save()` fills it from `STAGE_PROBABILITIES`.

        Money is shaped by `currency_block`, as in the invoice reports and the
        account rollups: deals in different currencies are never added
        together, and a deal with no currency counts in the org's default.
        Grouped over a subquery of the visible pks, because the non-admin
        queryset joins `assigned_to` and a grouped `Sum` over that join adds a
        deal once per assignee.
        """
        org = self.request.profile.org
        totals_queryset = queryset.distinct()
        priced = Opportunity.objects.filter(
            org=org, pk__in=queryset.values("pk"), amount__isnull=False
        )
        money = currency_block(
            group_by_currency(
                priced,
                currency=deal_currency(org),
                amount_sum=Sum("amount"),
                weighted_sum=Round(
                    Sum(F("amount") * F("probability") / Decimal("100")), 2
                ),
            ),
            money=("amount_sum", "weighted_sum"),
        )
        return {
            "count": totals_queryset.count(),
            **money,
            # Closed deals are never stalled: `aging_q` only has clauses for
            # open stages, whether or not the caller asked for open deals only.
            "stalled_count": totals_queryset.filter(stalled_filter(org)).count(),
        }

    def get_context_data(self, **kwargs):
        queryset = deal_list_queryset(
            self.request.profile, self.request.user, self.request.query_params
        )
        accounts = Account.objects.filter(org=self.request.profile.org)
        # The contact read rule itself, which is what the save path accepts.
        contacts = visible_contacts_qs(self.request.profile)
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            accounts = accounts.filter(
                Q(created_by=self.request.profile.user)
                | Q(assigned_to=self.request.profile)
            ).distinct()

        context = {}
        context["totals"] = self.get_totals(queryset)
        org = self.request.profile.org
        results_opportunities = self.paginate_queryset(
            queryset.distinct(), self.request, view=self
        )
        # The org's stages once for the page, not per row.
        opportunities = OpportunitySerializer(
            results_opportunities, many=True, context={"stages": stage_index(org.id)}
        ).data
        if results_opportunities:
            offset = queryset.filter(id__gte=results_opportunities[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        context["per_page"] = 10
        page_number = int(self.offset / 10) + 1
        context["page_number"] = page_number
        context.update(
            {
                "opportunities_count": self.count,
                "offset": offset,
            }
        )
        context["opportunities"] = opportunities
        context["accounts_list"] = AccountPickerSerializer(accounts, many=True).data
        context["contacts_list"] = ContactPickerSerializer(contacts, many=True).data
        context["tags"] = TagsSerializer(
            Tags.objects.filter(org=self.request.profile.org, is_active=True), many=True
        ).data
        # The default pipeline's stages, in the `[code, label]` shape this key
        # has always had; every pipeline is on `/opportunities/pipelines/`.
        context["stage"] = stage_choices(DealPipeline.default_for(org))
        context["lead_source"] = SOURCES
        context["currency"] = CURRENCY_CODES

        return context

    @extend_schema(
        operation_id="opportunities_list",
        tags=["Opportunities"],
        parameters=swagger_params.opportunity_list_get_params,
        responses={
            200: inline_serializer(
                name="OpportunityListResponse",
                fields={
                    "opportunities_count": serializers.IntegerField(),
                    "totals": inline_serializer(
                        name="OpportunityListTotals",
                        fields={
                            "count": serializers.IntegerField(),
                            "amount_sum": serializers.DecimalField(
                                max_digits=14, decimal_places=2, allow_null=True
                            ),
                            "weighted_sum": serializers.DecimalField(
                                max_digits=14, decimal_places=2, allow_null=True
                            ),
                            "by_currency": serializers.ListField(
                                child=serializers.DictField()
                            ),
                            "stalled_count": serializers.IntegerField(),
                        },
                    ),
                    "offset": serializers.IntegerField(allow_null=True),
                    "per_page": serializers.IntegerField(),
                    "page_number": serializers.IntegerField(),
                    "opportunities": OpportunitySerializer(many=True),
                    "accounts_list": AccountPickerSerializer(many=True),
                    "contacts_list": ContactPickerSerializer(many=True),
                    "tags": TagsSerializer(many=True),
                    "stage": serializers.ListField(),
                    "lead_source": serializers.ListField(),
                    "currency": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        operation_id="opportunities_create",
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="OpportunityCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "id": serializers.UUIDField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        serializer = OpportunityCreateSerializer(data=params, request_obj=request)
        if serializer.is_valid():
            cf_payload = params.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Opportunity", cf_payload or {}, request.profile.org
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Every id list, and the file, is checked before the first write,
            # so a bad one is a 400 with no half-created deal behind it.
            contact_ids = payload_id_list(params.get("contacts"), "contacts")
            tag_ids = payload_id_list(params.get("tags"), "tags")
            team_ids = payload_id_list(params.get("teams"), "teams")
            assigned_ids = payload_id_list(params.get("assigned_to"), "assigned_to")
            validate_attachment(self.request.FILES.get("opportunity_attachment"))
            opportunity_obj = serializer.save(
                created_by=request.profile.user,
                closed_on=params.get("closed_on"),
                org=request.profile.org,
                custom_fields=cleaned_cf,
            )

            replace_visible_contacts(
                opportunity_obj.contacts, contact_ids, request.profile
            )

            if tag_ids:
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                opportunity_obj.tags.add(*tag_objs)

            # `.save()` is the point. This block used to assign `closed_by` and
            # then never persist it, so a deal could be created already won and
            # the record of who won it was discarded on the way out.
            if serializer.closing:
                opportunity_obj.closed_by = self.request.profile
                opportunity_obj.save()

            if team_ids:
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                opportunity_obj.teams.add(*teams)

            if assigned_ids:
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                opportunity_obj.assigned_to.add(*profiles)

            if self.request.FILES.get("opportunity_attachment"):
                create_attachment(
                    self.request.FILES.get("opportunity_attachment"),
                    opportunity_obj,
                    self.request.profile,
                )

            recipients = list(
                opportunity_obj.assigned_to.all().values_list("id", flat=True)
            )

            send_email_to_assigned_user.delay(
                recipients,
                opportunity_obj.id,
                str(request.profile.org.id),
            )
            # `id` is additive, no existing key changes, and without it a
            # client cannot open the deal it just created. The alternative is
            # guessing by name, which is a race and breaks on duplicates.
            return Response(
                {
                    "error": False,
                    "message": "Opportunity Created Successfully",
                    "id": str(opportunity_obj.id),
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class OpportunityDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Opportunity

    def get_object(self, pk):
        return self.model.objects.filter(id=pk, org=self.request.profile.org).first()

    def assert_deal_access(self, opportunity):
        """Delegates to `opportunity.access`, which holds the one definition.

        The attachment download view asks the same question, and four inline
        copies of this check is exactly how the creator branch came to be dead
        in all four of them.
        """
        access.assert_deal_access(self.request.profile, self.request.user, opportunity)

    @extend_schema(
        operation_id="opportunities_update",
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="OpportunityUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        opportunity_object = self.get_object(pk=pk)
        if not opportunity_object:
            return Response(
                {"error": True, "errors": "Opportunity not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.assert_deal_access(opportunity_object)

        serializer = OpportunityCreateSerializer(
            opportunity_object,
            data=params,
            request_obj=request,
        )

        if serializer.is_valid():
            save_kwargs = {"closed_on": params.get("closed_on")}
            if "custom_fields" in params:
                cf_payload = params.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Opportunity",
                    cf_payload or {},
                    request.profile.org,
                    existing=opportunity_object.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            # Parsed before the first write, so a malformed id or an oversized
            # file leaves the deal exactly as it was.
            contact_ids = payload_id_list(params.get("contacts"), "contacts")
            tag_ids = payload_id_list(params.get("tags"), "tags")
            team_ids = payload_id_list(params.get("teams"), "teams")
            assigned_ids = payload_id_list(params.get("assigned_to"), "assigned_to")
            validate_attachment(self.request.FILES.get("opportunity_attachment"))
            opportunity_object = serializer.save(**save_kwargs)
            previous_assigned_to_users = list(
                opportunity_object.assigned_to.all().values_list("id", flat=True)
            )
            replace_visible_contacts(
                opportunity_object.contacts, contact_ids, request.profile
            )

            opportunity_object.tags.clear()
            if tag_ids:
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                opportunity_object.tags.add(*tag_objs)

            # Same missing `.save()` as create: PUT could close a deal and drop
            # the name of whoever closed it.
            if serializer.closing:
                opportunity_object.closed_by = self.request.profile
                opportunity_object.save()

            opportunity_object.teams.clear()
            if team_ids:
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                opportunity_object.teams.add(*teams)

            opportunity_object.assigned_to.clear()
            if assigned_ids:
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                opportunity_object.assigned_to.add(*profiles)

            if self.request.FILES.get("opportunity_attachment"):
                create_attachment(
                    self.request.FILES.get("opportunity_attachment"),
                    opportunity_object,
                    self.request.profile,
                )

            assigned_to_list = list(
                opportunity_object.assigned_to.all().values_list("id", flat=True)
            )
            recipients = list(set(assigned_to_list) - set(previous_assigned_to_users))
            send_email_to_assigned_user.delay(
                recipients,
                opportunity_object.id,
                str(request.profile.org.id),
            )
            return Response(
                {"error": False, "message": "Opportunity Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        operation_id="opportunities_destroy",
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="OpportunityDetailDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        if not self.object:
            return Response(
                {"error": True, "errors": "Opportunity not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if self.object.org != request.profile.org:
            return Response(
                {"error": True, "errors": "User company doesnot match with header...."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if self.request.profile.user != self.object.created_by:
                return Response(
                    {
                        "error": True,
                        "errors": "You do not have Permission to perform this action",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        self.object.delete()
        return Response(
            {"error": False, "message": "Opportunity Deleted Successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="opportunities_retrieve",
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="OpportunityDetailResponse",
                fields={
                    "opportunity_obj": OpportunitySerializer(),
                    "comments": CommentSerializer(many=True),
                    "attachments": AttachmentsSerializer(many=True),
                    "contacts": DealContactSerializer(many=True),
                    "users": ProfileSerializer(many=True),
                    "stage": serializers.ListField(),
                    "lead_source": serializers.ListField(),
                    "currency": serializers.ListField(),
                    "comment_permission": serializers.BooleanField(),
                    "users_mention": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        self.opportunity = self.get_object(pk=pk)
        if not self.opportunity:
            return Response(
                {"error": True, "errors": "Opportunity not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.assert_deal_access(self.opportunity)

        context = {}
        context["opportunity_obj"] = OpportunitySerializer(self.opportunity).data

        comment_permission = (
            self.request.profile.user_id == self.opportunity.created_by_id
            or self.request.user.is_superuser
            or is_org_admin(self.request.profile)
        )

        if self.request.user.is_superuser or is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(
                    is_active=True, org=self.request.profile.org
                ).values("user__email")
            )
        elif self.opportunity.created_by:
            # `created_by` IS the User. The old code read `created_by.user.email`,
            # which raised AttributeError and returned a 500 for every non-admin
            # assignee opening a deal somebody else had created, the common
            # case, and invisible until the 403 above stopped firing wrongly.
            # Key is `user__email` to match the admin branch above; the two
            # returned different key names for the same list.
            users_mention = [{"user__email": self.opportunity.created_by.email}]
        else:
            users_mention = []

        opportunity_content_type = ContentType.objects.get_for_model(Opportunity)
        comments = Comment.objects.filter(
            content_type=opportunity_content_type,
            object_id=self.opportunity.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=opportunity_content_type,
            object_id=self.opportunity.id,
            org=self.request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "comments": CommentSerializer(comments, many=True).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                # Only the people the viewer may open: each row links to the
                # contact's own page, and opening the deal is not access to
                # everyone on it. The nested `opportunity_obj.contacts` still
                # names them all, as `ContactLinkSerializer` does everywhere.
                "contacts": DealContactSerializer(
                    self.opportunity.contacts.filter(
                        id__in=visible_contacts_qs(self.request.profile).values("id")
                    ),
                    many=True,
                ).data,
                "users": ProfileSerializer(
                    Profile.objects.filter(
                        is_active=True, org=self.request.profile.org
                    ).order_by("user__email"),
                    many=True,
                ).data,
                # The stages of this deal's own pipeline, as `[code, label]`.
                "stage": stage_choices(self.opportunity.pipeline),
                "lead_source": SOURCES,
                "currency": CURRENCY_CODES,
                "comment_permission": comment_permission,
                "users_mention": users_mention,
            }
        )

        custom_field_defs = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Opportunity",
            is_active=True,
        ).order_by("display_order", "label")
        context["custom_field_definitions"] = CustomFieldDefinitionSerializer(
            custom_field_defs, many=True
        ).data
        return Response(context)

    @extend_schema(
        operation_id="opportunities_comment",
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityDetailEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="OpportunityCommentAttachmentResponse",
                fields={
                    "opportunity_obj": OpportunitySerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": CommentSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        params = request.data
        context = {}
        # `.get()` here raised DoesNotExist (a 500) for a deal that had been
        # deleted or belongs to another org. `get_object` is the same lookup
        # with the org filter and answers 404, which is what the other verbs do.
        self.opportunity_obj = self.get_object(pk=pk)
        if not self.opportunity_obj:
            return Response(
                {"error": True, "errors": "Opportunity not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.assert_deal_access(self.opportunity_obj)
        # Before the comment is saved, so a refused file does not leave it posted.
        validate_attachment(self.request.FILES.get("opportunity_attachment"))

        # Create the comment directly via the generic Comment ORM path, the
        # previous code routed through CommentSerializer.save(opportunity_id=...,
        # commented_by_id=...) but CommentSerializer requires `object_id` and
        # `org` on input which the client never supplies, so is_valid() always
        # returned False and the comment was silently dropped (HTTP 200 with
        # no error). Matches the lead/case create patterns.
        comment_text = (params.get("comment") or "").strip()
        if comment_text:
            opportunity_content_type = ContentType.objects.get_for_model(Opportunity)
            Comment.objects.create(
                content_type=opportunity_content_type,
                object_id=self.opportunity_obj.id,
                comment=comment_text,
                commented_by=self.request.profile,
                org=self.request.profile.org,
            )

        if self.request.FILES.get("opportunity_attachment"):
            create_attachment(
                self.request.FILES.get("opportunity_attachment"),
                self.opportunity_obj,
                self.request.profile,
            )

        opportunity_content_type = ContentType.objects.get_for_model(Opportunity)
        comments = Comment.objects.filter(
            content_type=opportunity_content_type,
            object_id=self.opportunity_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=opportunity_content_type,
            object_id=self.opportunity_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "opportunity_obj": OpportunitySerializer(self.opportunity_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityCreateSwaggerSerializer,
        description="Partial Opportunity Update",
        responses={
            200: inline_serializer(
                name="OpportunityPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to an opportunity."""
        params = request.data
        opportunity_object = self.get_object(pk=pk)
        if not opportunity_object:
            return Response(
                {"error": True, "errors": "Opportunity not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.assert_deal_access(opportunity_object)

        serializer = OpportunityCreateSerializer(
            opportunity_object,
            data=params,
            request_obj=request,
            partial=True,
        )

        if serializer.is_valid():
            save_kwargs = {
                "closed_on": params.get("closed_on")
                if "closed_on" in params
                else opportunity_object.closed_on
            }
            if "custom_fields" in params:
                cf_payload = params.get("custom_fields")
                if isinstance(cf_payload, str):
                    try:
                        cf_payload = json.loads(cf_payload)
                    except (TypeError, ValueError):
                        cf_payload = None
                cleaned_cf, cf_errors = validate_custom_fields_payload(
                    "Opportunity",
                    cf_payload or {},
                    request.profile.org,
                    existing=opportunity_object.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            # Parsed before the first write, as in PUT. An absent key parses to [].
            contact_ids = payload_id_list(params.get("contacts"), "contacts")
            tag_ids = payload_id_list(params.get("tags"), "tags")
            team_ids = payload_id_list(params.get("teams"), "teams")
            assigned_ids = payload_id_list(params.get("assigned_to"), "assigned_to")
            opportunity_object = serializer.save(**save_kwargs)

            # Handle M2M fields if present in request
            if "contacts" in params:
                replace_visible_contacts(
                    opportunity_object.contacts, contact_ids, request.profile
                )

            if "tags" in params:
                opportunity_object.tags.clear()
                if tag_ids:
                    tag_objs = Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                    opportunity_object.tags.add(*tag_objs)

            if "teams" in params:
                opportunity_object.teams.clear()
                if team_ids:
                    teams = Teams.objects.filter(
                        id__in=team_ids, org=request.profile.org
                    )
                    opportunity_object.teams.add(*teams)

            if "assigned_to" in params:
                previous = set(
                    opportunity_object.assigned_to.values_list("id", flat=True)
                )
                opportunity_object.assigned_to.clear()
                if assigned_ids:
                    profiles = Profile.objects.filter(
                        id__in=assigned_ids, org=request.profile.org, is_active=True
                    )
                    opportunity_object.assigned_to.add(*profiles)
                # PUT has always told a newly assigned person; PATCH never did.
                current = opportunity_object.assigned_to.values_list("id", flat=True)
                recipients = list(set(current) - previous)
                if recipients:
                    send_email_to_assigned_user.delay(
                        recipients, opportunity_object.id, str(request.profile.org.id)
                    )

            # Handle closed_by if stage changed to closed
            if serializer.closing:
                opportunity_object.closed_by = self.request.profile
                opportunity_object.save()

            return Response(
                {"error": False, "message": "Opportunity Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
