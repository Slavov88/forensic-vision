"""
DRF serializers for all core models.
"""
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import serializers

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

User = get_user_model()

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg"}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}



class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email")



class CaseMembershipSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source="user", write_only=True
    )

    class Meta:
        model = CaseMembership
        fields = ("id", "user", "user_id", "role", "invited_by", "created_at")
        read_only_fields = ("id", "invited_by", "created_at")



class CaseShareLinkSerializer(serializers.ModelSerializer):
    token = serializers.UUIDField(read_only=True)
    created_by = UserBriefSerializer(read_only=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = CaseShareLink
        fields = (
            "id", "token", "role", "created_by", "created_at",
            "expires_at", "max_uses", "uses_count", "revoked", "share_url",
        )
        read_only_fields = ("id", "token", "created_by", "created_at", "uses_count", "share_url")

    def get_share_url(self, obj) -> str:
        request = self.context.get("request")
        path = f"/share/{obj.token}/"
        if request:
            return request.build_absolute_uri(path)
        return path

    def validate_role(self, value):
        request = self.context.get("request")
        if not request:
            return value
        view = self.context.get("view")
        case = view.get_case() if view and hasattr(view, "get_case") else None
        if case and value == CaseShareLink.Role.EDITOR:
            if not (request.user.is_staff or case.created_by == request.user):
                raise serializers.ValidationError(
                    "Само собственикът може да дава редакция."
                )
        return value



class CaseCommentSerializer(serializers.ModelSerializer):
    author = UserBriefSerializer(read_only=True)
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = CaseComment
        fields = ("id", "author", "text", "created_at", "can_delete")
        read_only_fields = ("id", "author", "created_at", "can_delete")

    def get_can_delete(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return request.user == obj.author or request.user.is_staff



class CaseSerializer(serializers.ModelSerializer):
    created_by = UserBriefSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Case
        fields = (
            "id", "title", "description", "status", "tags",
            "created_by", "created_at", "updated_at", "member_count", "my_role",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")

    def get_member_count(self, obj) -> int:
        return obj.memberships.count()

    def get_my_role(self, obj) -> str | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if request.user == obj.created_by:
            return "owner"
        membership = obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["created_by"] = request.user
        case = super().create(validated_data)
        # Owner is always an editor member
        CaseMembership.objects.create(
            case=case,
            user=request.user,
            role=CaseMembership.Role.EDITOR,
            invited_by=request.user,
        )
        return case



class EvidenceSerializer(serializers.ModelSerializer):
    uploaded_by = UserBriefSerializer(read_only=True)
    file = serializers.FileField(write_only=True, source="original_file")
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Evidence
        fields = (
            "id", "case", "type", "file", "file_url",
            "sha256", "uploaded_by", "uploaded_at", "is_reference",
            "metadata_json", "tags",
        )
        read_only_fields = ("id", "type", "sha256", "uploaded_by", "uploaded_at", "file_url", "is_reference")

    def get_file_url(self, obj) -> str | None:
        request = self.context.get("request")
        if obj.original_file and request:
            return request.build_absolute_uri(obj.original_file.url)
        return None

    def validate(self, attrs):
        file_obj = attrs.get("original_file")
        if file_obj:
            max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
            if file_obj.size > max_bytes:
                raise serializers.ValidationError(
                    f"Файлът е прекалено голям. Максималният размер е {settings.MAX_UPLOAD_MB} MB."
                )
            ext = os.path.splitext(file_obj.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise serializers.ValidationError(
                    "Невалиден тип файл. Разрешени: PDF, PNG, JPG."
                )
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        file_obj = validated_data["original_file"]

        ext = os.path.splitext(file_obj.name)[1].lower()
        ev_type = Evidence.EvidenceType.PDF if ext == ".pdf" else Evidence.EvidenceType.IMAGE

        validated_data["uploaded_by"] = request.user
        validated_data["type"] = ev_type
        evidence = super().create(validated_data)

        # Compute SHA-256
        evidence.sha256 = evidence.compute_sha256()
        evidence.save(update_fields=["sha256"])

        # Queue PDF rendering
        if ev_type == Evidence.EvidenceType.PDF:
            from analysis.tasks import render_pdf_to_pages
            render_pdf_to_pages.delay(evidence.pk)

        return evidence



class EvidencePageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = EvidencePage
        fields = ("id", "evidence", "page_index", "image_url", "width", "height")
        read_only_fields = fields

    def get_image_url(self, obj) -> str | None:
        request = self.context.get("request")
        if obj.rendered_image and request:
            return request.build_absolute_uri(obj.rendered_image.url)
        return None



class ArtifactSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = ("id", "kind", "file_url", "data_json", "created_at")
        read_only_fields = fields

    def get_file_url(self, obj) -> str | None:
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None


class AnalysisJobSerializer(serializers.ModelSerializer):
    created_by = UserBriefSerializer(read_only=True)
    artifacts = ArtifactSerializer(many=True, read_only=True)

    class Meta:
        model = AnalysisJob
        fields = (
            "id", "case", "evidence", "page", "pipeline_name", "params_json",
            "status", "progress", "created_by",
            "started_at", "finished_at", "error_message", "artifacts",
        )
        read_only_fields = (
            "id", "status", "progress", "created_by",
            "started_at", "finished_at", "error_message", "artifacts",
        )

    def validate(self, attrs):
        case = attrs.get('case')
        pipeline_name = attrs.get('pipeline_name')
        params = attrs.get('params_json', {})

        from analysis.registry import PIPELINE_REGISTRY
        if pipeline_name not in PIPELINE_REGISTRY:
            available = ", ".join(PIPELINE_REGISTRY.keys())
            raise serializers.ValidationError(
                {"pipeline_name": f"Непознат pipeline '{pipeline_name}'. Налични: {available}"}
            )

        if pipeline_name == "compare_reference":
            ref_id = params.get('reference_evidence_id')
            if not ref_id:
                raise serializers.ValidationError(
                    {"params_json": {"reference_evidence_id": "Липсва ID на еталон."}}
                )
            
            try:
                ref_ev = Evidence.objects.get(id=ref_id, case=case)
            except Evidence.DoesNotExist:
                raise serializers.ValidationError(
                    {"params_json": {"reference_evidence_id": "Еталонът трябва да е в същия кейс."}}
                )
            
            if not ref_ev.is_reference:
                raise serializers.ValidationError(
                    {"params_json": {"reference_evidence_id": "Избраното доказателство не е маркирано като Еталон."}}
                )
            
            # Set default indices if missing
            # reference_page_index defaults to 1 (which refers to first page in 1-based logic or 0 in 0-based)
            # but we use 0-based indexing in the model. 
            # In the UI we might show 1-based. Let's stick to 0-based for internal params if not specified.
            if 'reference_page_index' not in params:
                params['reference_page_index'] = 0
            
            if 'target_page_index' not in params:
                params['target_page_index'] = params['reference_page_index']
            
            attrs['params_json'] = params

        return attrs

    def create(self, validated_data):
        from analysis.tasks import run_analysis_job
        request = self.context["request"]
        validated_data["created_by"] = request.user
        
        # If target_page_index is provided in params_json, try to link the 'page' FK
        params = validated_data.get('params_json', {})
        target_page_idx = params.get('target_page_index')
        evidence = validated_data.get('evidence')
        
        if target_page_idx is not None and evidence:
            try:
                page_obj = EvidencePage.objects.get(evidence=evidence, page_index=target_page_idx)
                validated_data['page'] = page_obj
            except EvidencePage.DoesNotExist:
                # Fallback: if page not rendered yet or invalid, we let the task handle it or error later
                pass
                
        job = super().create(validated_data)
        run_analysis_job.delay(job.pk)
        return job
