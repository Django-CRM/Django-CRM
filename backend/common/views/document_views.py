from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common import swagger_params
from common.models import Document, Profile, Teams
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    DocumentCreateSerializer,
    DocumentCreateSwaggerSerializer,
    DocumentEditSwaggerSerializer,
    DocumentSerializer,
    ProfileSerializer,
)
from common.validators import payload_id_list


def _visible_to(profile):
    """Q() describing which documents a non-admin profile may see.

    One definition, used by the list query and by every object-level check
    below, because they disagreed before: the list matched on `shared_to`
    while the detail view also tried to match the creator, so which documents
    you could open did not line up with which ones you could find.

    Three ways in, and each was broken in its own way:

    * **Creator.** `Document.created_by` is a **User** FK. The create path
      sets `created_by=request.profile.user`. The old checks compared it to
      `request.profile`, a different model, so the expression was always
      False and the uploader of a document could not open or delete it.
      Compare against `profile.user`.

    * **Shared with you.** Worked, and is unchanged.

    * **Shared with a team you are in.** `Document.teams` was written by POST
      and PUT and read by no query at all, so picking a team granted nobody
      anything while the interface presented it as a share. Honouring it is
      what makes the stored value mean what it says.

    Scope: this only ever narrows within one org. The caller has already
    filtered on `org=request.profile.org`, and RLS is underneath that. It is
    not a substitute for either.
    """
    return (
        Q(created_by=profile.user)
        | Q(shared_to=profile)
        | Q(teams__in=profile.user_teams.all())
    )


def may_read_document(profile, user, document):
    """Admins, plus anyone `_visible_to` covers: creator, share, team.

    Evaluated as a queryset filter rather than in Python so that it is
    literally the same predicate the list uses. Doing it by hand was how the
    two drifted apart in the first place.

    Module level because the download view needs it too, and a file is only
    ever as private as the narrowest thing that can hand it over.
    """
    if is_org_admin(profile) or user.is_superuser:
        return True
    return Document.objects.filter(pk=document.pk).filter(_visible_to(profile)).exists()


class DocumentListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Document

    def get_context_data(self, **kwargs):
        params = self.request.query_params
        queryset = self.model.objects.filter(org=self.request.profile.org).order_by(
            "-id"
        )
        if not (self.request.user.is_superuser or is_org_admin(self.request.profile)):
            queryset = queryset.filter(_visible_to(self.request.profile)).distinct()

        request_post = params
        if request_post:
            if request_post.get("title"):
                queryset = queryset.filter(title__icontains=request_post.get("title"))
            if request_post.get("status"):
                queryset = queryset.filter(status=request_post.get("status"))

            # `json.loads` on the raw value meant `?shared_to=<id>`, the
            # obvious spelling, raised JSONDecodeError and answered 500. Only a
            # JSON array ever worked, and a malformed id inside one answered
            # 500 as well.
            shared_to = payload_id_list(request_post.get("shared_to"), "shared_to")
            if shared_to:
                queryset = queryset.filter(shared_to__id__in=shared_to)

        context = {}
        profile_list = Profile.objects.filter(
            is_active=True, org=self.request.profile.org
        )
        if is_org_admin(self.request.profile):
            profiles = profile_list.order_by("user__email")
        else:
            profiles = profile_list.filter(role="ADMIN").order_by("user__email")
        search = False
        if (
            params.get("document_file")
            or params.get("status")
            or params.get("shared_to")
        ):
            search = True
        context["search"] = search

        queryset_documents_active = queryset.filter(status="active")
        results_documents_active = self.paginate_queryset(
            queryset_documents_active.distinct(), self.request, view=self
        )
        documents_active = DocumentSerializer(results_documents_active, many=True).data
        if results_documents_active:
            offset = queryset_documents_active.filter(
                id__gte=results_documents_active[-1].id
            ).count()
            if offset == queryset_documents_active.count():
                offset = None
        else:
            offset = 0
        context["documents_active"] = {
            "documents_active_count": self.count,
            "documents_active": documents_active,
            "offset": offset,
        }

        queryset_documents_inactive = queryset.filter(status="inactive")
        results_documents_inactive = self.paginate_queryset(
            queryset_documents_inactive.distinct(), self.request, view=self
        )
        documents_inactive = DocumentSerializer(
            results_documents_inactive, many=True
        ).data
        if results_documents_inactive:
            # This block computes the *inactive* envelope's offset, so it must
            # key off the last inactive row, not `results_documents_active[-1]`,
            # a copy-paste from the active block above. When the org has archived
            # documents but no active ones (e.g. the only document was just
            # archived), `results_documents_active` is empty and `[-1]` raised
            # IndexError -> 500 on the whole list. No client rendered this
            # endpoint before, so the crash sat unhit.
            offset = queryset_documents_inactive.filter(
                id__gte=results_documents_inactive[-1].id
            ).count()
            if offset == queryset_documents_inactive.count():
                offset = None
        else:
            offset = 0
        context["documents_inactive"] = {
            "documents_inactive_count": self.count,
            "documents_inactive": documents_inactive,
            "offset": offset,
        }

        context["users"] = ProfileSerializer(profiles, many=True).data
        context["status_choices"] = Document.DOCUMENT_STATUS_CHOICE
        return context

    @extend_schema(
        tags=["documents"],
        operation_id="documents_list",
        parameters=swagger_params.document_get_params,
        responses={
            200: inline_serializer(
                name="DocumentListResponse",
                fields={
                    "search": serializers.BooleanField(),
                    "documents_active": serializers.DictField(),
                    "documents_inactive": serializers.DictField(),
                    "users": ProfileSerializer(many=True),
                    "status_choices": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["documents"],
        operation_id="documents_create",
        parameters=swagger_params.organization_params,
        request=DocumentCreateSwaggerSerializer,
        responses={
            201: inline_serializer(
                name="DocumentCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        serializer = DocumentCreateSerializer(data=params, request_obj=request)
        if serializer.is_valid():
            doc = serializer.save(
                created_by=request.profile.user,
                org=request.profile.org,
                document_file=request.FILES.get("document_file"),
            )
            if params.get("shared_to"):
                assinged_to_list = params.get("shared_to")
                assigned_ids = payload_id_list(assinged_to_list, "shared_to")
                profiles = Profile.objects.filter(
                    id__in=assigned_ids, org=request.profile.org, is_active=True
                )
                if profiles:
                    doc.shared_to.add(*profiles)
            if params.get("teams"):
                teams_list = params.get("teams")
                team_ids = payload_id_list(teams_list, "teams")
                teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)
                if teams:
                    doc.teams.add(*teams)

            return Response(
                {"error": False, "message": "Document Created Successfully"},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class DocumentDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        # Security fix: Filter by org to prevent cross-org enumeration
        return get_object_or_404(Document, id=pk, org=self.request.profile.org)

    def _may_read(self, document):
        """See `may_read_document`, which the download view shares."""
        return may_read_document(self.request.profile, self.request.user, document)

    def _may_delete(self, document):
        """Deleting is destructive and not covered by a share.

        Being able to read a document does not imply being able to destroy it
        for everyone else, so this stays narrower than `_may_read`: the person
        who uploaded it, or an admin. `created_by` is a User FK. Comparing it
        to a Profile is the bug that made this branch unreachable.
        """
        if is_org_admin(self.request.profile) or self.request.user.is_superuser:
            return True
        return document.created_by_id == self.request.profile.user_id

    def _may_write(self, document):
        """Editing is as consequential as deleting, so it is gated the same way.

        PUT and PATCH used to authorise on `_may_read`, which meant anyone a
        document was merely *shared with*, or a member of a team it was shared
        with, could overwrite its file, rename it, flip its status to
        `inactive` (hiding it from the org), and, on PUT, wipe its entire
        `shared_to`/`teams` list and lock everyone else out. A share hands
        someone a copy to work with; it does not hand them the original to
        rewrite. Replacing the bytes behind a title is at least as destructive
        as deleting the row, so mutation stays exactly as narrow as
        `_may_delete`: the uploader or an admin. Reading (`_may_read`) remains
        broad on purpose (creator, share, team, admin), because seeing a
        document and changing it for everyone are different privileges.
        """
        return self._may_delete(document)

    @staticmethod
    def _forbidden():
        return Response(
            {
                "error": True,
                "errors": "You do not have Permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["documents"],
        operation_id="documents_retrieve",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="DocumentDetailResponse",
                fields={
                    "doc_obj": DocumentSerializer(),
                    "file_type_code": serializers.CharField(),
                    "users": ProfileSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        # get_object_or_404 now handles org filtering - returns 404 if not found/wrong org
        self.object = self.get_object(pk)
        if not self._may_read(self.object):
            return self._forbidden()
        profile_list = Profile.objects.filter(org=self.request.profile.org)
        if is_org_admin(request.profile) or request.user.is_superuser:
            profiles = profile_list.order_by("user__email")
        else:
            profiles = profile_list.filter(role="ADMIN").order_by("user__email")
        context = {}
        context.update(
            {
                "doc_obj": DocumentSerializer(self.object).data,
                "file_type_code": self.object.file_type()[1],
                "users": ProfileSerializer(profiles, many=True).data,
            }
        )
        return Response(context, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["documents"],
        operation_id="documents_destroy",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="DocumentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        # `get_object` already filters on org and raises 404 otherwise, so the
        # falsy-document and org-mismatch branches that used to sit here were
        # both unreachable.
        document = self.get_object(pk)
        if not self._may_delete(document):
            return self._forbidden()
        document.delete()
        return Response(
            {"error": False, "message": "Document deleted Successfully"},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["documents"],
        operation_id="documents_update",
        parameters=swagger_params.organization_params,
        request=DocumentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="DocumentUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        self.object = self.get_object(pk)
        params = request.data
        if not self._may_write(self.object):
            return self._forbidden()
        serializer = DocumentCreateSerializer(
            data=params, instance=self.object, request_obj=request, partial=True
        )
        if serializer.is_valid():
            # Parsed before anything is written: these raise a 400 on a
            # malformed list, which used to arrive after the rename was saved
            # and the shares cleared, leaving half a write behind.
            assigned_ids = payload_id_list(params.get("shared_to"), "shared_to")
            team_ids = payload_id_list(params.get("teams"), "teams")

            # The body is the full share list. A person is kept if they are
            # active in this org, or if this document is ALREADY shared with
            # them: sharing is picked from active people only, so without the
            # second arm every change of sharing dropped each share to
            # somebody deactivated since, resubmitted or not. That arm only
            # matches ids the document already holds, so it cannot add a new
            # inactive share, and both arms stay inside the caller's org.
            profiles = Profile.objects.filter(
                Q(is_active=True) | Q(id__in=self.object.shared_to.all()),
                id__in=assigned_ids,
                org=request.profile.org,
            )
            teams = Teams.objects.filter(id__in=team_ids, org=request.profile.org)

            save_kwargs = {
                "org": request.profile.org,
            }
            if request.FILES.get("document_file"):
                save_kwargs["document_file"] = request.FILES.get("document_file")
            if params.get("status"):
                save_kwargs["status"] = params.get("status")
            doc = serializer.save(**save_kwargs)
            # `list()` runs the query before `set` removes anything, so the
            # "already shared" arm above still sees the old shares.
            doc.shared_to.set(list(profiles))
            doc.teams.set(list(teams))
            return Response(
                {"error": False, "message": "Document Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["documents"],
        parameters=swagger_params.organization_params,
        request=DocumentEditSwaggerSerializer,
        description="Partial Document Update",
        responses={
            200: inline_serializer(
                name="DocumentPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a document."""
        self.object = self.get_object(pk)
        params = request.data
        if not self._may_write(self.object):
            return self._forbidden()
        serializer = DocumentCreateSerializer(
            data=params, instance=self.object, request_obj=request, partial=True
        )
        if serializer.is_valid():
            serializer.save(
                org=request.profile.org,
            )
            return Response(
                {"error": False, "message": "Document Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class DocumentDownloadView(APIView):
    """Stream a document's file to somebody allowed to read the record.

    Until this existed there was no authenticated way to reach the bytes at
    all, so both clients listed a filename, a size and nothing to click. The
    only other route to the file is its `/media/` path, and that path is
    guarded by `RLSContextMiddleware` alone, which asks for *an* org context
    rather than *the* org: it turns any signed-in user of any tenant into a
    reader of every uploaded file in the system. Handing a client a media URL
    is therefore not a smaller version of this view, it is the hole.

    The gate is `may_read_document`, the same predicate the detail view and
    the list use, so a document you cannot open is a document you cannot
    download. Deliberately *not* `_may_write`: reading is broad here on
    purpose (creator, share, team, admin) and a share exists to be read.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["documents"],
        operation_id="documents_download",
        parameters=swagger_params.organization_params,
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, pk, format=None):
        document = get_object_or_404(Document, id=pk, org=request.profile.org)
        if not may_read_document(request.profile, request.user, document):
            return Response(
                {
                    "error": True,
                    "errors": "You do not have Permission to perform this action",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if not document.document_file:
            raise Http404("That document has no file.")
        document.document_file.open("rb")
        return FileResponse(
            document.document_file,
            as_attachment=True,
            # `title` is the human name and the only one either client shows.
            # The stored name is a timestamped upload path nobody chose.
            filename=document.title or "document",
        )
