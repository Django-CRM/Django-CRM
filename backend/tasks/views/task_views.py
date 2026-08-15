import json
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Account
from accounts.serializer import AccountSerializer
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import (
    Attachments,
    Comment,
    CustomFieldDefinition,
    Profile,
    Tags,
    Teams,
)
from common.permissions import HasOrgContext
from common.serializer import (
    AttachmentsSerializer,
    CommentSerializer,
    CustomFieldDefinitionSerializer,
    ProfileSerializer,
    TeamsSerializer,
)
from common.utils import create_attachment
from common.validators import (
    choice_list_param,
    date_param,
    payload_id_list,
    uuid_list_param,
    uuid_param,
)
from contacts.models import Contact
from contacts.serializer import ContactSerializer
from tasks import swagger_params
from tasks.access import (
    assert_task_access,
    assert_task_delete_access,
    get_task_or_404,
    is_org_admin,
    visible_tasks_qs,
)
from tasks.models import Task
from tasks.serializer import (
    TaskCommentEditSwaggerSerializer,
    TaskCreateSerializer,
    TaskCreateSwaggerSerializer,
    TaskDetailEditSwaggerSerializer,
    TaskListSerializer,
    TaskSerializer,
)


def _model_errors(exc):
    """Django's ``ValidationError`` in the shape this API answers 400 with.

    ``Task.save()`` calls ``full_clean()``, so a model-level rule, today, "one
    parent entity", fires on the way to the database. Django's exception is
    not DRF's, so nothing caught it and the client got a 500 for breaking a
    documented rule. Same rule, same wording, now with the right status.
    """
    return (
        exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
    )


class TaskListView(APIView, LimitOffsetPagination):
    model = Task
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_context_data(self, **kwargs):
        params = self.request.query_params
        # `visible_tasks_qs` is the same rule the detail view enforces. It used
        # to be spelled out here and spelled differently there, which is how a
        # member came to have tasks in their list that answered 403, or, once
        # the creator clause is live, tasks they created and could not open.
        queryset = (
            visible_tasks_qs(self.request.profile)
            .select_related("account", "opportunity", "case", "lead")
            .prefetch_related("assigned_to__user", "tags")
            .order_by("-id")
        )

        if params:
            if params.get("title"):
                queryset = queryset.filter(title__icontains=params.get("title"))
            # Repeatable: `?status=New&status=In Progress` is the only way to
            # ask for "still open", which is what the dashboard's Overdue and
            # Due Today counts mean. A single `?status=New` behaves exactly as
            # it did, so no existing caller changes.
            statuses = choice_list_param(
                params, "status", [value for value, _label in Task.STATUS_CHOICES]
            )
            if statuses:
                queryset = queryset.filter(status__in=statuses)
            if params.get("priority"):
                queryset = queryset.filter(priority=params.get("priority"))
            assigned_to = uuid_list_param(params, "assigned_to")
            if assigned_to:
                queryset = queryset.filter(assigned_to__id__in=assigned_to).distinct()
            tags = uuid_list_param(params, "tags")
            if tags:
                queryset = queryset.filter(tags__id__in=tags).distinct()
            if params.get("search"):
                queryset = queryset.filter(title__icontains=params.get("search"))
            due_date_gte = date_param(params, "due_date__gte")
            if due_date_gte:
                queryset = queryset.filter(due_date__gte=due_date_gte)
            due_date_lte = date_param(params, "due_date__lte")
            if due_date_lte:
                queryset = queryset.filter(due_date__lte=due_date_lte)
            created_at_gte = date_param(params, "created_at__gte")
            if created_at_gte:
                queryset = queryset.filter(created_at__date__gte=created_at_gte)
            created_at_lte = date_param(params, "created_at__lte")
            if created_at_lte:
                queryset = queryset.filter(created_at__date__lte=created_at_lte)
            for related in ("account", "opportunity", "case", "lead"):
                related_id = uuid_param(params, related)
                if related_id:
                    queryset = queryset.filter(**{f"{related}_id": related_id})
            for raw_key, raw_value in params.items():
                if raw_key.startswith("cf_") and raw_value:
                    cf_key = raw_key[3:]
                    if cf_key:
                        queryset = queryset.filter(
                            custom_fields__contains={cf_key: raw_value}
                        )
        context = {}
        queryset = queryset.distinct()

        # Totals over the whole filtered queryset, not the page, so a header
        # that says "6 overdue" is not really saying "6 on this screen".
        # `overdue` counts only tasks still open: a completed task that
        # happened to finish late is not something anyone can act on, which is
        # the same line the mock's `taskTotals` drew and the right one.
        today = timezone.localdate()
        week_out = today + timedelta(days=7)
        # One aggregate, not seven `.count()` calls: written the obvious way
        # this was eight COUNT(*) round trips per page load, all scanning the
        # same rows. `distinct=True` because a non-admin's queryset joins the
        # assignee many-to-many and would otherwise count a task once per
        # assignee.
        still_open = ~Q(status="Completed")
        totals = queryset.aggregate(
            count=Count("id", distinct=True),
            open=Count("id", distinct=True, filter=still_open),
            overdue=Count(
                "id", distinct=True, filter=still_open & Q(due_date__lt=today)
            ),
            due_today=Count("id", distinct=True, filter=still_open & Q(due_date=today)),
            due_this_week=Count(
                "id",
                distinct=True,
                filter=still_open & Q(due_date__gte=today, due_date__lte=week_out),
            ),
            # A task with no due date is never overdue and never due this week,
            # so it falls off both of the numbers above and out of anybody's
            # attention. That is the one worth counting on its own.
            no_due_date=Count(
                "id", distinct=True, filter=still_open & Q(due_date__isnull=True)
            ),
            unassigned=Count(
                "id", distinct=True, filter=still_open & Q(assigned_to__isnull=True)
            ),
        )
        context["totals"] = totals

        results_tasks = self.paginate_queryset(queryset, self.request, view=self)
        # Slim serializer: drops comments/attachments/contacts/teams from list
        # rows and renders FKs as {id, name}. Detail view still uses
        # TaskSerializer for the full nested payload.
        tasks = TaskListSerializer(results_tasks, many=True).data
        if results_tasks:
            offset = queryset.filter(id__gte=results_tasks[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        context.update(
            {
                "tasks_count": self.count,
                "offset": offset,
                "tasks": tasks,
            }
        )

        # What a task *form* needs, from the endpoint that already knows the
        # answer, the same keys `cases` and `opportunity` publish, because a
        # second name for the same list is how clients end up with two.
        context["status"] = Task.STATUS_CHOICES
        context["priority"] = Task.PRIORITY_CHOICES
        profiles = Profile.objects.filter(
            is_active=True, org=self.request.profile.org
        ).order_by("user__email")
        if not is_org_admin(self.request.profile):
            profiles = profiles.filter(role="ADMIN")
        context["users"] = list(profiles.values("id", "user__email"))
        # The catalogues are for the parent picker and grow with the org, not
        # with the page, `?slim=true` omits them for callers that only want
        # the list. Default unchanged, so v1 and mobile see what they saw.
        if params.get("slim") != "true":
            context["accounts_list"] = AccountSerializer(
                Account.objects.filter(org=self.request.profile.org), many=True
            ).data
            context["contacts_list"] = ContactSerializer(
                Contact.objects.filter(org=self.request.profile.org), many=True
            ).data
        return context

    @extend_schema(
        tags=["Tasks"],
        operation_id="tasks_list",
        parameters=swagger_params.task_list_get_params,
        responses={
            200: inline_serializer(
                name="TaskListResponse",
                fields={
                    "tasks_count": serializers.IntegerField(),
                    "offset": serializers.IntegerField(allow_null=True),
                    "tasks": TaskListSerializer(many=True),
                    "totals": serializers.DictField(),
                    "status": serializers.ListField(),
                    "priority": serializers.ListField(),
                    "users": serializers.ListField(),
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
        tags=["Tasks"],
        operation_id="tasks_create",
        parameters=swagger_params.organization_params,
        request=TaskCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="TaskCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        serializer = TaskCreateSerializer(data=params, request_obj=request)
        if serializer.is_valid():
            cf_payload = params.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Task", cf_payload or {}, request.profile.org
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                task_obj = serializer.save(
                    created_by=request.profile.user,
                    due_date=params.get("due_date"),
                    org=request.profile.org,
                    custom_fields=cleaned_cf,
                )
            except DjangoValidationError as exc:
                return Response(
                    {"error": True, "errors": _model_errors(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if params.get("contacts"):
                contacts_list = params.get("contacts")
                contact_ids = payload_id_list(contacts_list, "contacts")
                contacts = Contact.objects.filter(
                    id__in=contact_ids, org=request.profile.org
                )
                task_obj.contacts.add(*contacts)

            if params.get("teams"):
                teams_list = params.get("teams")
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                task_obj.teams.add(*teams)

            if params.get("assigned_to"):
                assinged_to_list = params.get("assigned_to")
                assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                task_obj.assigned_to.add(*profiles)

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
                task_obj.tags.add(*tag_objs)

            # The parent FKs used to be re-read from `params` and re-saved here
            # with an org filter, *after* the serializer had already written
            # whatever the client sent. On create that second pass only ever
            # assigned, it never cleared, so another org's lead survived it
            # and the list rendered that org's name back. The serializer now
            # scopes the four querysets itself, which is one place instead of
            # three and refuses out-of-org ids instead of quietly dropping them.
            return Response(
                {"error": False, "message": "Task Created Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class TaskDetailView(APIView):
    model = Task
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return get_task_or_404(self.request.profile, pk)

    def get_context_data(self, **kwargs):
        context = {}
        # Raise, don't return. This used to hand a `Response` back to `get()`,
        # which wrapped it in a second `Response`, so the one path that was
        # supposed to say 403 answered `TypeError: Object of type Response is
        # not JSON serializable` and a 500 instead.
        assert_task_access(self.request.profile, self.task_obj)

        task_content_type = ContentType.objects.get_for_model(Task)
        comments = Comment.objects.filter(
            content_type=task_content_type,
            object_id=self.task_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=task_content_type,
            object_id=self.task_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")

        assigned_data = self.task_obj.assigned_to.values("id", "user__email")

        # `created_by` is a `User`, so `created_by.user` does not exist. The
        # branch guarding it compared a `Profile` to that `User` and was
        # therefore always true, which made this line unreachable-by-intent and
        # unavoidable-in-practice: every non-admin who opened any task got an
        # AttributeError and a 500. Not "a member cannot open someone else's
        # task". A member could not open *their own*.
        if is_org_admin(self.request.profile):
            users_mention = list(
                Profile.objects.filter(
                    is_active=True, org=self.request.profile.org
                ).values("user__email")
            )
        elif self.request.profile.user_id != self.task_obj.created_by_id:
            users_mention = (
                [{"username": self.task_obj.created_by.email}]
                if self.task_obj.created_by_id
                else []
            )
        else:
            users_mention = list(self.task_obj.assigned_to.all().values("user__email"))
        if is_org_admin(self.request.profile):
            users = Profile.objects.filter(
                is_active=True, org=self.request.profile.org
            ).order_by("user__email")
        else:
            users = Profile.objects.filter(
                role="ADMIN", org=self.request.profile.org
            ).order_by("user__email")

        team_ids = [user.id for user in self.task_obj.get_team_users]
        all_user_ids = users.values_list("id", flat=True)
        users_excluding_team_id = set(all_user_ids) - set(team_ids)
        users_excluding_team = Profile.objects.filter(id__in=users_excluding_team_id)
        cf_definitions = CustomFieldDefinition.objects.filter(
            org=self.request.profile.org,
            target_model="Task",
            is_active=True,
        ).order_by("display_order", "label")
        context.update(
            {
                "task_obj": TaskSerializer(self.task_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
                "users_mention": users_mention,
                "assigned_data": assigned_data,
                "custom_field_definitions": CustomFieldDefinitionSerializer(
                    cf_definitions, many=True
                ).data,
            }
        )
        context["users"] = ProfileSerializer(users, many=True).data
        context["users_excluding_team"] = ProfileSerializer(
            users_excluding_team, many=True
        ).data
        context["teams"] = TeamsSerializer(
            Teams.objects.filter(org=self.request.profile.org), many=True
        ).data
        return context

    @extend_schema(
        tags=["Tasks"],
        operation_id="tasks_retrieve",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TaskDetailResponse",
                fields={
                    "task_obj": TaskSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": CommentSerializer(many=True),
                    "users_mention": serializers.ListField(),
                    "assigned_data": serializers.DictField(),
                    "users": ProfileSerializer(many=True),
                    "users_excluding_team": ProfileSerializer(many=True),
                    "teams": TeamsSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, pk, **kwargs):
        self.task_obj = self.get_object(pk)
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["Tasks"],
        operation_id="tasks_comment_attachment",
        parameters=swagger_params.organization_params,
        request=TaskDetailEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="TaskCommentAttachmentResponse",
                fields={
                    "task_obj": TaskSerializer(),
                    "attachments": AttachmentsSerializer(many=True),
                    "comments": CommentSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        params = request.data
        context = {}
        self.task_obj = self.get_object(pk)
        assert_task_access(request.profile, self.task_obj)
        task_content_type = ContentType.objects.get_for_model(Task)
        comment_text = params.get("comment")
        if comment_text:
            # Use the generic ContentType-based create directly. Validating
            # through CommentSerializer fails silently here because that
            # serializer requires object_id and org, neither of which the
            # client sends; is_valid() returns False and the save is skipped,
            # leaving the user with a 200 and no comment.
            Comment.objects.create(
                content_type=task_content_type,
                object_id=self.task_obj.id,
                comment=comment_text,
                commented_by=self.request.profile,
                org=self.request.profile.org,
            )

        if self.request.FILES.get("task_attachment"):
            create_attachment(
                self.request.FILES.get("task_attachment"),
                self.task_obj,
                self.request.profile,
            )

        comments = Comment.objects.filter(
            content_type=task_content_type,
            object_id=self.task_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        attachments = Attachments.objects.filter(
            content_type=task_content_type,
            object_id=self.task_obj.id,
            org=self.request.profile.org,
        ).order_by("-id")
        context.update(
            {
                "task_obj": TaskSerializer(self.task_obj).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
            }
        )
        return Response(context)

    @extend_schema(
        tags=["Tasks"],
        operation_id="tasks_update",
        parameters=swagger_params.organization_params,
        request=TaskCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="TaskUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, **kwargs):
        params = request.data
        self.task_obj = self.get_object(pk)
        assert_task_access(request.profile, self.task_obj)
        serializer = TaskCreateSerializer(
            data=params,
            instance=self.task_obj,
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
                    "Task",
                    cf_payload or {},
                    request.profile.org,
                    existing=self.task_obj.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            try:
                task_obj = serializer.save(**save_kwargs)
            except DjangoValidationError as exc:
                return Response(
                    {"error": True, "errors": _model_errors(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            task_obj.contacts.clear()
            if params.get("contacts"):
                contacts_list = params.get("contacts")
                contact_ids = payload_id_list(contacts_list, "contacts")
                contacts = Contact.objects.filter(
                    id__in=contact_ids, org=request.profile.org
                )
                task_obj.contacts.add(*contacts)

            task_obj.teams.clear()
            if params.get("teams"):
                teams_list = params.get("teams")
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                task_obj.teams.add(*teams)

            task_obj.assigned_to.clear()
            if params.get("assigned_to"):
                assinged_to_list = params.get("assigned_to")
                assigned_ids = payload_id_list(assinged_to_list, "assigned_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                task_obj.assigned_to.add(*profiles)

            task_obj.tags.clear()
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
                task_obj.tags.add(*tag_objs)

            # The parent FKs are the serializer's job now. It scopes all four
            # querysets to the org, so an id from somewhere else is a 400 here
            # instead of a silent `None`. A form that says "link this to Acme"
            # and gets a 200 back should not have unlinked it.
            return Response(
                {"error": False, "message": "Task updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Tasks"],
        parameters=swagger_params.organization_params,
        request=TaskCreateSwaggerSerializer,
        description="Partial Task Update",
        responses={
            200: inline_serializer(
                name="TaskPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, **kwargs):
        """Handle partial updates to a task."""
        params = request.data
        self.task_obj = self.get_object(pk)
        assert_task_access(request.profile, self.task_obj)

        serializer = TaskCreateSerializer(
            data=params,
            instance=self.task_obj,
            request_obj=request,
            partial=True,
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
                    "Task",
                    cf_payload or {},
                    request.profile.org,
                    existing=self.task_obj.custom_fields or {},
                )
                if cf_errors:
                    return Response(
                        {"error": True, "errors": {"custom_fields": cf_errors}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                save_kwargs["custom_fields"] = cleaned_cf
            try:
                task_obj = serializer.save(**save_kwargs)
            except DjangoValidationError as exc:
                return Response(
                    {"error": True, "errors": _model_errors(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Handle M2M fields if present in request
            if "contacts" in params:
                task_obj.contacts.clear()
                contacts_list = params.get("contacts")
                if contacts_list:
                    contact_ids = payload_id_list(contacts_list, "contacts")
                    contacts = Contact.objects.filter(
                        id__in=contact_ids, org=request.profile.org
                    )
                    task_obj.contacts.add(*contacts)

            if "teams" in params:
                task_obj.teams.clear()
                teams_list = params.get("teams")
                if teams_list:
                    team_ids = payload_id_list(teams_list, "teams")
                    teams = Teams.objects.filter(
                        id__in=team_ids, org=request.profile.org
                    )
                    task_obj.teams.add(*teams)

            if "assigned_to" in params:
                task_obj.assigned_to.clear()
                assigned_to_list = params.get("assigned_to")
                if assigned_to_list:
                    assigned_ids = payload_id_list(assigned_to_list, "assigned_to")
                    profiles = Profile.objects.filter(
                        id__in=assigned_ids, org=request.profile.org, is_active=True
                    )
                    task_obj.assigned_to.add(*profiles)

            if "tags" in params:
                task_obj.tags.clear()
                tags_list = params.get("tags")
                if tags_list:
                    tag_ids = payload_id_list(tags_list, "tags")
                    tag_objs = Tags.objects.filter(
                        id__in=tag_ids, org=request.profile.org, is_active=True
                    )
                    task_obj.tags.add(*tag_objs)

            # Parent FKs: the serializer's job, org-scoped there. See PUT.
            return Response(
                {"error": False, "message": "Task updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Tasks"],
        operation_id="tasks_destroy",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TaskDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, **kwargs):
        self.object = self.get_object(pk)
        # Narrower than `access`: an assignee may work the task, not erase it.
        assert_task_delete_access(request.profile, self.object)
        self.object.delete()
        return Response(
            {"error": False, "message": "Task deleted Successfully"},
            status=status.HTTP_200_OK,
        )


class TaskCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        """A comment in the requester's org, or 404.

        Was `objects.get(...)`, so a comment id that had been deleted, belonged
        to another org, or was not a UUID at all answered 500 rather than
        "there is no such comment".
        """
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
        tags=["Tasks"],
        parameters=swagger_params.organization_params,
        request=TaskCommentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="TaskCommentUpdateResponse",
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
            # No `if params.get("comment")` guard here: a body with a blank or
            # absent `comment` used to fall out of this branch and hit the 403
            # below, which told an author they may not edit their own comment.
            # `comment` is a non-blank CharField, so the serializer answers
            # with a 400 naming the field.
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
                "errors": "You don't have Permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Tasks"],
        parameters=swagger_params.organization_params,
        request=TaskCommentEditSwaggerSerializer,
        description="Partial Comment Update",
        responses={
            200: inline_serializer(
                name="TaskCommentPatchResponse",
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
                "errors": "You don't have Permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Tasks"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TaskCommentDeleteResponse",
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
                "errors": "You don't have Permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class TaskAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Tasks"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TaskAttachmentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        # `Attachments` is one generic table shared by every module, so a
        # lookup by pk alone reaches every attachment in the database. Without
        # `org=`, this endpoint was a "delete any attachment anywhere by UUID"
        # primitive for any org admin: proven live, an admin of one org
        # destroyed a file belonging to another org, and it was attached to a
        # lead, not even to a task. The org filter is the fix; the uploader
        # clause below is a separate bug in the same three lines.
        try:
            self.object = self.model.objects.filter(
                pk=pk, org=request.profile.org
            ).first()
        except (DjangoValidationError, ValueError):
            raise Http404("No such attachment.")
        if self.object is None:
            raise Http404("No such attachment.")
        # `created_by` is a `User`; `request.profile` is a `Profile`. Comparing
        # them is always False, so the person who uploaded the file could not
        # remove it unless they were an admin.
        if not (
            is_org_admin(request.profile)
            or request.profile.user_id == self.object.created_by_id
        ):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have Permission to perform this action",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        self.object.delete()
        return Response(
            {"error": False, "message": "Attachment Deleted Successfully"},
            status=status.HTTP_200_OK,
        )
