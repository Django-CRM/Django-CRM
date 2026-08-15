import json

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    extend_schema,
    inline_serializer,
)
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
    CommentSerializer,
    CustomFieldDefinitionSerializer,
)
from common.utils import COUNTRIES, create_attachment
from common.validators import date_param, payload_id_list, uuid_list_param
from contacts import access, swagger_params
from contacts.models import Contact
from contacts.serializer import (
    ContactCommentEditSwaggerSerializer,
    ContactDetailEditSwaggerSerializer,
    ContactSerializer,
    CreateContactSerializer,
)
from contacts.services.account_link import link_primary_account
from contacts.tasks import send_email_to_assigned_user
from tasks.serializer import TaskSerializer


class ContactsListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Contact

    def get_context_data(self, **kwargs):
        params = self.request.query_params
        queryset = (
            self.model.objects.filter(org=self.request.profile.org)
            # `-id` is a random UUID, so "the list" was in no order at all --
            # a page that says "most recent first" was shuffling people. The
            # model's own Meta.ordering is `-created_at`; this now agrees.
            .order_by("-created_at")
            .select_related("account")
            .prefetch_related("account_contacts", "assigned_to__user", "teams", "tags")
        )
        if not is_org_admin(self.request.profile):
            queryset = queryset.filter(
                Q(assigned_to__in=[self.request.profile])
                | Q(created_by=self.request.profile.user)
            ).distinct()

        if params:
            if params.get("name"):
                name = params.get("name")
                queryset = queryset.filter(
                    Q(first_name__icontains=name) | Q(last_name__icontains=name)
                )
            if params.get("city"):
                # Contact keeps a flat `city`; there has been no related
                # address object to traverse since the model was flattened, so
                # `address__city` raised FieldError and the filter answered 500.
                queryset = queryset.filter(city__icontains=params.get("city"))
            if params.get("phone"):
                queryset = queryset.filter(phone__icontains=params.get("phone"))
            if params.get("email"):
                queryset = queryset.filter(email__icontains=params.get("email"))
            assigned_to = uuid_list_param(params, "assigned_to")
            if assigned_to:
                # `getlist` to ask and `get` to read gave `__in` a single
                # string, which Django iterates character by character -- each
                # character then failed to parse as a UUID, so filtering by an
                # owner answered 500.
                queryset = queryset.filter(assigned_to__id__in=assigned_to).distinct()
            tags = uuid_list_param(params, "tags")
            if tags:
                queryset = queryset.filter(tags__id__in=tags).distinct()
            if params.get("search"):
                search = params.get("search")
                queryset = queryset.filter(
                    Q(first_name__icontains=search)
                    | Q(last_name__icontains=search)
                    | Q(email__icontains=search)
                    | Q(phone__icontains=search)
                )
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
                        queryset = queryset.filter(
                            custom_fields__contains={cf_key: raw_value}
                        )

        context = {}
        # Both halves counted before the split, so a list showing one of them
        # can say how many it is holding back. There was no way to ask for
        # either: `is_active` was stored, shown and never filterable, so a page
        # that wanted "people who still work there" had to fetch everyone and
        # discard rows -- which quietly lies as soon as there is a second page.
        context["active_count"] = queryset.filter(is_active=True).distinct().count()
        context["inactive_count"] = queryset.filter(is_active=False).distinct().count()
        if params.get("is_active") in ("true", "false"):
            queryset = queryset.filter(is_active=params.get("is_active") == "true")

        results_contact = self.paginate_queryset(
            queryset.distinct(), self.request, view=self
        )
        contacts = ContactSerializer(results_contact, many=True).data
        if results_contact:
            offset = queryset.filter(id__gte=results_contact[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        context["per_page"] = 10
        page_number = int(self.offset / 10) + 1
        context["page_number"] = page_number
        # Standard DRF pagination format for frontend compatibility
        context["count"] = self.count
        context["results"] = contacts
        # Legacy format for backwards compatibility
        context["contacts_count"] = self.count
        context["offset"] = offset
        context["contact_obj_list"] = contacts
        context["countries"] = COUNTRIES
        users = Profile.objects.filter(
            is_active=True, org=self.request.profile.org
        ).values("id", "user__email")
        context["users"] = users

        return context

    @extend_schema(
        operation_id="contacts_list",
        tags=["contacts"],
        parameters=swagger_params.contact_list_get_params,
        responses={
            200: inline_serializer(
                name="ContactListResponse",
                fields={
                    "count": serializers.IntegerField(),
                    "results": ContactSerializer(many=True),
                    "per_page": serializers.IntegerField(),
                    "page_number": serializers.IntegerField(),
                    "contacts_count": serializers.IntegerField(),
                    "offset": serializers.IntegerField(allow_null=True),
                    "contact_obj_list": ContactSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        operation_id="contacts_create",
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        request=CreateContactSerializer,
        responses={
            200: inline_serializer(
                name="ContactCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        contact_serializer = CreateContactSerializer(data=params, request_obj=request)

        if not contact_serializer.is_valid():
            return Response(
                {"error": True, "errors": contact_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cf_payload = params.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "Contact", cf_payload or {}, request.profile.org
        )
        if cf_errors:
            return Response(
                {"error": True, "errors": {"custom_fields": cf_errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Contact model uses flat address fields, no separate Address object needed
        contact_obj = contact_serializer.save(
            org=request.profile.org, custom_fields=cleaned_cf
        )
        link_primary_account(contact_obj)

        if params.get("teams"):
            teams_list = params.get("teams")
            team_ids = payload_id_list(teams_list, "teams")
            teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
            contact_obj.teams.add(*teams)

        if params.get("assigned_to"):
            assinged_to_list = params.get("assigned_to")
            assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
            profiles = Profile.objects.filter(
                id__in=assigned_ids, org=request.profile.org
            )
            contact_obj.assigned_to.add(*profiles)

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
            contact_obj.tags.add(*tag_objs)

        recipients = list(contact_obj.assigned_to.all().values_list("id", flat=True))
        send_email_to_assigned_user.delay(
            recipients,
            contact_obj.id,
            str(request.profile.org.id),
        )

        if request.FILES.get("contact_attachment"):
            create_attachment(
                request.FILES.get("contact_attachment"),
                contact_obj,
                request.profile,
            )
        return Response(
            # The id, so that whatever created the contact can go to it. Without
            # it a caller has to guess -- list the org and hope the newest row
            # is the one it just made.
            {
                "error": False,
                "message": "Contact created Successfuly",
                "id": str(contact_obj.id),
            },
            status=status.HTTP_200_OK,
        )


class ContactDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Contact

    def get_object(self, pk):
        try:
            return get_object_or_404(
                Contact.objects.select_related("account").prefetch_related(
                    "account_contacts", "assigned_to__user", "teams", "tags"
                ),
                pk=pk,
                org=self.request.profile.org,
            )
        except (DjangoValidationError, ValueError):
            # The route matches <str:pk>, so anything at all can arrive here.
            # UUIDField.to_python raises Django's ValidationError, which
            # get_object_or_404 does not catch, so /api/contacts/banana/ was a
            # 500 -- an error report for a URL somebody simply mistyped.
            raise Http404("No such contact.")

    def assert_contact_access(self, contact):
        """Delegates to `contacts.access`, which holds the one definition.

        The attachment download view asks the same question. Four verbs once
        carried four copies of this check and all four had the same dead
        branch, which is the reason it lives in exactly one place now.
        """
        access.assert_contact_access(self.request.profile, contact)

    @staticmethod
    def account_ids(contact):
        """Every account this contact is joined to, by either route."""
        return access.contact_account_ids(contact)

    def related_deals(self, contact):
        return [
            {
                "id": str(deal.id),
                "name": deal.name,
                "stage": deal.stage,
                "amount": deal.amount,
                "closed_on": deal.closed_on,
            }
            for deal in contact.opportunity_contacts.filter(
                org=self.request.profile.org
            ).order_by("-created_at")[:10]
        ]

    def related_cases(self, contact):
        return [
            {
                "id": str(case.id),
                "name": case.name,
                "status": case.status,
                "priority": case.priority,
                "created_at": case.created_at,
            }
            for case in contact.case_contacts.filter(
                org=self.request.profile.org
            ).order_by("-created_at")[:10]
        ]

    def related_colleagues(self, contact):
        """Other people at the same company.

        Matched on the account link rather than on `organization`, which is free
        text and, across the seeded org, frequently names a different company
        from the account the person is actually attached to.
        """
        accounts = self.account_ids(contact)
        if not accounts:
            return []
        colleagues = (
            Contact.objects.filter(org=self.request.profile.org)
            .filter(Q(account_contacts__id__in=accounts) | Q(account_id__in=accounts))
            .exclude(id=contact.id)
            .distinct()
            .order_by("first_name", "last_name")[:8]
        )
        return [
            {
                "id": str(person.id),
                "first_name": person.first_name,
                "last_name": person.last_name,
                "title": person.title or "",
            }
            for person in colleagues
        ]

    @extend_schema(
        operation_id="contacts_update",
        tags=["contacts"],
        parameters=swagger_params.contact_create_post_params,
        request=CreateContactSerializer,
        responses={
            200: inline_serializer(
                name="ContactUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        data = request.data
        contact_obj = self.get_object(pk=pk)
        self.assert_contact_access(contact_obj)

        contact_serializer = CreateContactSerializer(
            data=data, instance=contact_obj, request_obj=request
        )
        if not contact_serializer.is_valid():
            return Response(
                {"error": True, "errors": contact_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        save_kwargs = {}
        if "custom_fields" in data:
            cf_payload = data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Contact",
                cf_payload or {},
                request.profile.org,
                existing=contact_obj.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs["custom_fields"] = cleaned_cf

        contact_obj = contact_serializer.save(**save_kwargs)
        link_primary_account(contact_obj)
        contact_obj.teams.clear()
        if data.get("teams"):
            teams_list = data.get("teams")
            team_ids = payload_id_list(teams_list, "teams")
            teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
            contact_obj.teams.add(*teams)

        contact_obj.assigned_to.clear()
        if data.get("assigned_to"):
            assinged_to_list = data.get("assigned_to")
            assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
            profiles = Profile.objects.filter(
                id__in=assigned_ids, org=request.profile.org
            )
            contact_obj.assigned_to.add(*profiles)

        contact_obj.tags.clear()
        if data.get("tags"):
            tags = data.get("tags")
            if isinstance(tags, str):
                tags = json.loads(tags)
            # Extract IDs if tags contains objects with 'id' field
            tag_ids = [
                item.get("id") if isinstance(item, dict) else item for item in tags
            ]
            tag_objs = Tags.objects.filter(
                id__in=tag_ids, org=request.profile.org, is_active=True
            )
            contact_obj.tags.add(*tag_objs)

        previous_assigned_to_users = list(
            contact_obj.assigned_to.all().values_list("id", flat=True)
        )

        assigned_to_list = list(
            contact_obj.assigned_to.all().values_list("id", flat=True)
        )
        recipients = list(set(assigned_to_list) - set(previous_assigned_to_users))
        send_email_to_assigned_user.delay(
            recipients,
            contact_obj.id,
            str(request.profile.org.id),
        )
        if request.FILES.get("contact_attachment"):
            create_attachment(
                request.FILES.get("contact_attachment"),
                contact_obj,
                request.profile,
            )
        return Response(
            {"error": False, "message": "Contact Updated Successfully"},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="contacts_retrieve",
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ContactDetailResponse",
                fields={
                    "contact_obj": ContactSerializer(),
                    "address_obj": serializers.DictField(),
                    "comments": CommentSerializer(many=True),
                    "attachments": AttachmentsSerializer(many=True),
                    "assigned_data": serializers.ListField(),
                    "tasks": TaskSerializer(many=True),
                    "users_mention": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        context = {}
        contact_obj = self.get_object(pk)
        self.assert_contact_access(contact_obj)
        context["contact_obj"] = ContactSerializer(contact_obj).data
        assigned_data = []
        for each in contact_obj.assigned_to.all():
            assigned_dict = {}
            assigned_dict["id"] = each.user.id
            assigned_dict["name"] = each.user.email
            assigned_data.append(assigned_dict)

        if is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(is_active=True, org=request.profile.org).values(
                    "user__email"
                )
            )
        elif self.request.profile.user_id != contact_obj.created_by_id:
            # `created_by` is a User, so `.user` was an attribute that never
            # existed and this line raised AttributeError. Every non-admin who
            # was allowed to open a contact got a 500 at the last step -- which
            # is to say the detail page has never worked for a non-admin at
            # all, since the other branch was unreachable for the same
            # Profile-versus-User reason. The key is `user__email` to match the
            # admin branch above; it was `username`, so the two shapes
            # disagreed even in the case that did run.
            users_mention = (
                [{"user__email": contact_obj.created_by.email}]
                if contact_obj.created_by_id
                else []
            )
        else:
            users_mention = list(contact_obj.assigned_to.all().values("user__email"))

        # Address is now flat fields on Contact model
        context["address_obj"] = {
            "address_line": contact_obj.address_line,
            "city": contact_obj.city,
            "state": contact_obj.state,
            "postcode": contact_obj.postcode,
            "country": contact_obj.country,
        }
        context["countries"] = COUNTRIES
        contact_content_type = ContentType.objects.get_for_model(Contact)
        comments = Comment.objects.filter(
            content_type=contact_content_type,
            object_id=contact_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=contact_content_type,
            object_id=contact_obj.id,
            org=request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "comments": CommentSerializer(comments, many=True).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "assigned_data": assigned_data,
                "tasks": TaskSerializer(
                    contact_obj.task_contacts.all(), many=True
                ).data,
                "users_mention": users_mention,
                # What this person is involved in. Contact is on the far side of
                # a many-to-many from Opportunity and from Case, so a page that
                # wanted "their deals" had no way to ask for them and had to
                # settle for the account's -- which is a different question, and
                # the wrong one on an account with four people and eight deals.
                #
                # Deliberately hand-built rather than run through the module
                # serializers: OpportunitySerializer nests its account, its
                # contacts and every assignee, which is several hundred lines of
                # JSON per row to render a name and an amount.
                "opportunities": self.related_deals(contact_obj),
                "cases": self.related_cases(contact_obj),
                "colleagues": self.related_colleagues(contact_obj),
            }
        )

        custom_field_defs = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Contact",
            is_active=True,
        ).order_by("display_order", "label")
        context["custom_field_definitions"] = CustomFieldDefinitionSerializer(
            custom_field_defs, many=True
        ).data

        return Response(context)

    @extend_schema(
        operation_id="contacts_destroy",
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ContactDetailDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        # Deliberately narrower than `assert_contact_access`: an assignee may
        # work on a contact, only an admin or the person who entered it may
        # destroy the record. This comparison was already the right one.
        if (
            not is_org_admin(self.request.profile)
            and self.request.profile.user_id != self.object.created_by_id
        ):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        self.object.delete()
        return Response(
            {"error": False, "message": "Contact Deleted Successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="contacts_comment_attachment",
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        request=ContactDetailEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="ContactCommentAttachmentResponse",
                fields={
                    "contact_obj": ContactSerializer(),
                    "comments": CommentSerializer(many=True),
                    "attachments": AttachmentsSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        params = request.data
        context = {}
        # Was `Contact.objects.get(pk=pk)`: no org filter, so an admin of any
        # org could post to any contact id in the database and get that
        # contact's name, email, phone and address back in the response. It
        # also raised DoesNotExist -- a 500 -- for an id that was merely gone.
        self.contact_obj = self.get_object(pk)
        self.assert_contact_access(self.contact_obj)
        if params.get("comment"):
            # Comments on a contact have never been recorded. `Comment` is a
            # generic relation with no `contact` field, so `save(contact_id=)`
            # raised TypeError; and because `object_id` and `org` are required
            # on the serializer and the client is not supposed to send them,
            # `is_valid()` was False anyway and the whole block was skipped in
            # silence -- 200, with the comment dropped. Both halves are fixed
            # here: the target and the author come from the server, and a
            # comment that cannot be saved says so.
            comment_serializer = CommentSerializer(
                data={
                    "comment": params.get("comment"),
                    "is_internal": str(params.get("is_internal", "")).lower()
                    in ("true", "1"),
                }
            )
            if not comment_serializer.is_valid():
                return Response(
                    {"error": True, "errors": comment_serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Which record and which org are the server's to say, not the
            # caller's. They now travel through `save()` rather than the
            # `data=` dict, because they are read-only on the serializer and a
            # read-only field supplied in `data=` is discarded without comment.
            comment_serializer.save(
                content_type=ContentType.objects.get_for_model(Contact),
                object_id=self.contact_obj.id,
                org=request.profile.org,
                commented_by=self.request.profile,
            )

        if self.request.FILES.get("contact_attachment"):
            create_attachment(
                self.request.FILES.get("contact_attachment"),
                self.contact_obj,
                self.request.profile,
            )

        contact_content_type = ContentType.objects.get_for_model(Contact)
        comments = Comment.objects.filter(
            content_type=contact_content_type,
            object_id=self.contact_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=contact_content_type,
            object_id=self.contact_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "contact_obj": ContactSerializer(self.contact_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        request=CreateContactSerializer,
        description="Partial Contact Update",
        responses={
            200: inline_serializer(
                name="ContactPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a contact."""
        data = request.data
        contact_obj = self.get_object(pk=pk)
        self.assert_contact_access(contact_obj)

        contact_serializer = CreateContactSerializer(
            data=data, instance=contact_obj, request_obj=request, partial=True
        )
        if not contact_serializer.is_valid():
            return Response(
                {"error": True, "errors": contact_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        save_kwargs = {}
        if "custom_fields" in data:
            cf_payload = data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Contact",
                cf_payload or {},
                request.profile.org,
                existing=contact_obj.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs["custom_fields"] = cleaned_cf

        contact_obj = contact_serializer.save(**save_kwargs)
        link_primary_account(contact_obj)

        # Handle M2M fields if present in request
        if "teams" in data:
            contact_obj.teams.clear()
            teams_list = data.get("teams")
            if teams_list:
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                contact_obj.teams.add(*teams)

        if "assigned_to" in data:
            contact_obj.assigned_to.clear()
            assigned_to_list = data.get("assigned_to")
            if assigned_to_list:
                assigned_ids = payload_id_list(assigned_to_list, "assigned_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org
                )
                contact_obj.assigned_to.add(*profiles)

        if "tags" in data:
            contact_obj.tags.clear()
            tags_list = data.get("tags")
            if tags_list:
                if isinstance(tags_list, str):
                    tags_list = json.loads(tags_list)
                # Extract IDs if tags_list contains objects with 'id' field
                tag_ids = [
                    tag.get("id") if isinstance(tag, dict) else tag for tag in tags_list
                ]
                tag_objs = Tags.objects.filter(
                    id__in=tag_ids, org=request.profile.org, is_active=True
                )
                contact_obj.tags.add(*tag_objs)

        return Response(
            {"error": False, "message": "Contact Updated Successfully"},
            status=status.HTTP_200_OK,
        )


class ContactCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        # `.get()` raised DoesNotExist for a comment somebody else had already
        # deleted, which reached the client as a 500.
        try:
            return get_object_or_404(self.model, pk=pk, org=self.request.profile.org)
        except (DjangoValidationError, ValueError):
            raise Http404("No such comment.")

    @extend_schema(
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        request=ContactCommentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="ContactCommentUpdateResponse",
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
                "errors": "You don't have permission to edit this Comment",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        request=ContactCommentEditSwaggerSerializer,
        description="Partial Comment Update",
        responses={
            200: inline_serializer(
                name="ContactCommentPatchResponse",
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
                "errors": "You don't have permission to edit this Comment",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ContactCommentDeleteResponse",
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
                "errors": "You don't have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class ContactAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["contacts"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ContactAttachmentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        # Two defects in one line, both proven against a running server:
        #
        # `Attachments` is a single generic table shared by every module, and
        # this lookup had no org filter -- so this endpoint deleted any
        # attachment in the database, belonging to any org, hanging off any
        # kind of record. As MicroPyramid's admin I deleted another org's file
        # and got a 200. Row-level security blocks that in a deployment whose
        # DB role is not a superuser, which the dev one is; the ORM filter is
        # the contract, RLS is the net.
        #
        # And `request.profile == self.object.created_by` compares a Profile to
        # a User, so it was always False: the person who uploaded a file could
        # not delete it unless they were an admin.
        try:
            self.object = get_object_or_404(self.model, pk=pk, org=request.profile.org)
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
