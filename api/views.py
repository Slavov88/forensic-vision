"""
DRF ViewSets for ForensicVision API.

URL structure:
  /api/cases/                              CaseViewSet
  /api/cases/{id}/members/                 CaseViewSet.members
  /api/cases/{id}/comments/                CaseViewSet.comments
  /api/cases/{id}/share-links/             CaseViewSet.share_links
  /api/share-links/{token}/revoke/         ShareLinkRevokeView
  /api/evidence/                           EvidenceViewSet
  /api/evidence/{id}/pages/               EvidenceViewSet.pages
  /api/analysis/jobs/                      AnalysisJobViewSet
  /api/analysis/jobs/{id}/               AnalysisJobViewSet detail
  /api/auth/token/                         ObtainAuthToken (DRF built-in)
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from core.models import (
    Artifact,
    AnalysisJob,
    Case,
    CaseComment,
    CaseMembership,
    CaseShareLink,
    Evidence,
    EvidencePage,
)
from .permissions import IsCaseMember, IsEditorOrOwnerOrAdmin, IsOwnerOrAdmin
from .serializers import (
    ArtifactSerializer,
    AnalysisJobSerializer,
    CaseSerializer,
    CaseCommentSerializer,
    CaseMembershipSerializer,
    CaseShareLinkSerializer,
    EvidenceSerializer,
    EvidencePageSerializer,
)



def _user_cases(user):
    """Return Case queryset visible to the given user."""
    if user.is_staff:
        return Case.objects.all()
    return Case.objects.filter(memberships__user=user).distinct()


def _assert_case_access(user, case: Case, min_role: str | None = None) -> None:
    """
    Raise PermissionError (caught by DRF as 403) unless user has access.
    min_role: None=any member, 'editor'=editor+owner, 'owner'=owner only.
    """
    if user.is_staff:
        return
    if case.created_by == user:
        return
    membership = CaseMembership.objects.filter(case=case, user=user).first()
    if membership is None:
        raise PermissionError("Нямате достъп до този кейс.")
    if min_role == "editor" and membership.role != CaseMembership.Role.EDITOR:
        raise PermissionError("Само редактори и собственици могат да извършат това действие.")
    if min_role == "owner":
        raise PermissionError("Само собственикът може да извърши това действие.")



class CaseViewSet(viewsets.ModelViewSet):
    serializer_class = CaseSerializer
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _user_cases(self.request.user).select_related("created_by")

    def get_case(self) -> Case:
        """Helper used by nested serializers."""
        return self.get_object()

    def check_object_permissions(self, request, obj):
        # Only owner/admin can edit/delete
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not (request.user.is_staff or obj.created_by == request.user):
                # Editors can't edit the case itself, only its contents
                try:
                    _assert_case_access(request.user, obj)
                except PermissionError as exc:
                    self.permission_denied(request, message=str(exc))
        super().check_object_permissions(request, obj)


    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        case = self.get_object()
        try:
            _assert_case_access(request.user, case)
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        memberships = CaseMembership.objects.filter(case=case).select_related("user", "invited_by")
        serializer = CaseMembershipSerializer(memberships, many=True, context={"request": request})
        return Response(serializer.data)


    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        case = self.get_object()
        try:
            _assert_case_access(request.user, case)
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            qs = CaseComment.objects.filter(case=case).select_related("author")
            serializer = CaseCommentSerializer(qs, many=True, context={"request": request})
            return Response(serializer.data)

        # POST – owner and editor can comment, viewer cannot
        is_owner = case.created_by == request.user
        is_admin = request.user.is_staff
        membership = CaseMembership.objects.filter(case=case, user=request.user).first()
        is_editor = membership and membership.role == CaseMembership.Role.EDITOR

        if not (is_owner or is_admin or is_editor):
            return Response(
                {"грешка": "Само собственици и редактори могат да добавят коментари."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CaseCommentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(case=case, author=request.user)
        try:
            AuditLog.log("comment_created", actor=request.user, target=case, details={"case_id": case.pk})
        except Exception:
            pass
        return Response(serializer.data, status=status.HTTP_201_CREATED)


    @action(detail=True, methods=["get", "post"], url_path="share-links")
    def share_links(self, request, pk=None):
        case = self.get_object()
        try:
            _assert_case_access(request.user, case)
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            qs = CaseShareLink.objects.filter(case=case).select_related("created_by")
            serializer = CaseShareLinkSerializer(qs, many=True, context={"request": request})
            return Response(serializer.data)

        # POST – check who can create what
        requested_role = request.data.get("role", CaseShareLink.Role.VIEWER)
        is_owner = case.created_by == request.user
        is_admin = request.user.is_staff
        membership = CaseMembership.objects.filter(case=case, user=request.user).first()
        is_editor = membership and membership.role == CaseMembership.Role.EDITOR

        if requested_role == CaseShareLink.Role.EDITOR and not (is_owner or is_admin):
            return Response(
                {"грешка": "Само собственикът може да дава редакция."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not (is_owner or is_admin or is_editor):
            return Response(
                {"грешка": "Само редактори и собственици могат да създават споделени линкове."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CaseShareLinkSerializer(
            data=request.data, context={"request": request, "view": self}
        )
        serializer.is_valid(raise_exception=True)
        link = serializer.save(case=case, created_by=request.user)
        AuditLog.log("share_link_created", actor=request.user, target=link,
                     details={"role": link.role, "case_id": case.pk})
        return Response(
            CaseShareLinkSerializer(link, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )



class ShareLinkRevokeView(APIView):
    """POST /api/share-links/{token}/revoke/  – owner or admin only."""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        link = get_object_or_404(CaseShareLink, token=token)
        case = link.case
        if not (request.user.is_staff or case.created_by == request.user):
            return Response(
                {"грешка": "Само собственикът може да отмени линка."},
                status=status.HTTP_403_FORBIDDEN,
            )
        link.revoked = True
        link.save(update_fields=["revoked"])
        try:
            AuditLog.log("share_link_revoked", actor=request.user, target=link, details={"case_id": case.pk})
        except Exception:
            pass
        return Response({"съобщение": "Линкът е отменен."})



class CommentDeleteView(APIView):
    """DELETE /api/comments/{id}/"""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        comment = get_object_or_404(CaseComment, pk=pk)
        if not request.user.is_staff and comment.author != request.user:
            return Response(
                {"грешка": "Имате право да изтриете само собствените си коментари."},
                status=status.HTTP_403_FORBIDDEN,
            )
        case = comment.case
        comment.delete()
        try:
            AuditLog.log("comment_deleted", actor=request.user, target=case, details={"case_id": case.pk})
        except Exception:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)



class EvidenceViewSet(viewsets.ModelViewSet):
    serializer_class = EvidenceSerializer
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]  # no PUT/PATCH/DELETE on evidence

    def get_queryset(self):
        cases = _user_cases(self.request.user)
        qs = Evidence.objects.filter(case__in=cases).select_related("uploaded_by", "case")
        case_id = self.request.query_params.get("case")
        if case_id:
            qs = qs.filter(case_id=case_id)
        return qs

    def create(self, request, *args, **kwargs):
        case_id = request.data.get("case")
        case = get_object_or_404(Case, pk=case_id)
        # Must be editor/owner to upload
        try:
            _assert_case_access(request.user, case, min_role="editor")
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        evidence = serializer.save()
        AuditLog.log("evidence_uploaded", actor=request.user, target=evidence,
                     details={"case_id": case.pk, "type": evidence.type})
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["get"], url_path="pages")
    def pages(self, request, pk=None):
        evidence = self.get_object()
        pages = EvidencePage.objects.filter(evidence=evidence).order_by("page_index")
        serializer = EvidencePageSerializer(pages, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="set-reference")
    def set_reference(self, request, pk=None):
        evidence = self.get_object()
        case = evidence.case
        try:
            _assert_case_access(request.user, case, min_role="editor")
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        is_ref = request.data.get("is_reference")
        if is_ref is None:
            return Response({"грешка": "Липсва параметър is_reference."}, status=status.HTTP_400_BAD_REQUEST)

        evidence.is_reference = bool(is_ref)
        evidence.save(update_fields=["is_reference"])
        
        # Backward compatibility for tags (can be removed later)
        if evidence.is_reference and "reference" not in evidence.tags:
            evidence.tags.append("reference")
        elif not evidence.is_reference and "reference" in evidence.tags:
            evidence.tags.remove("reference")
        evidence.save(update_fields=["tags"])

        AuditLog.log(
            "evidence_reference_set" if evidence.is_reference else "evidence_reference_removed",
            actor=request.user, 
            target=evidence, 
            details={"case_id": evidence.case.pk, "is_reference": evidence.is_reference}
        )
        msg = "Файлът е зададен като еталон." if evidence.is_reference else "Файлът вече не е еталон."
        
        serializer = self.get_serializer(evidence, context={"request": request})
        data = serializer.data
        data["message"] = msg
        return Response(data)



class AnalysisJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AnalysisJobSerializer
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        cases = _user_cases(self.request.user)
        qs = AnalysisJob.objects.filter(case__in=cases).select_related(
            "created_by", "case", "evidence", "page"
        ).prefetch_related("artifacts")
        case_id = self.request.query_params.get("case")
        if case_id:
            qs = qs.filter(case_id=case_id)
        return qs

    def create(self, request, *args, **kwargs):
        """Create and immediately queue a new analysis job."""
        case_id = request.data.get("case")
        case = get_object_or_404(Case, pk=case_id)
        try:
            _assert_case_access(request.user, case, min_role="editor")
        except PermissionError as e:
            return Response({"грешка": str(e)}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        AuditLog.log("analysis_job_created", actor=request.user, target=job,
                     details={"pipeline": job.pipeline_name, "case_id": case.pk})
        return Response(
            self.get_serializer(job).data, status=status.HTTP_201_CREATED
        )

    # Allow POST for job creation (ReadOnlyModelViewSet doesn't include it)
    http_method_names = ["get", "post", "head", "options"]
