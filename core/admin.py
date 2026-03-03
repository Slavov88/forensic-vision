"""
Django admin registrations for core models.
"""
from django.contrib import admin

from .models import (
    Artifact,
    AnalysisJob,
    Case,
    CaseComment,
    CaseMembership,
    CaseShareLink,
    Evidence,
    EvidencePage,
)


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "description")
    raw_id_fields = ("created_by",)


@admin.register(CaseMembership)
class CaseMembershipAdmin(admin.ModelAdmin):
    list_display = ("case", "user", "role", "invited_by", "created_at")
    list_filter = ("role",)
    raw_id_fields = ("case", "user", "invited_by")


@admin.register(CaseShareLink)
class CaseShareLinkAdmin(admin.ModelAdmin):
    list_display = ("case", "role", "token", "revoked", "uses_count", "expires_at")
    list_filter = ("role", "revoked")
    readonly_fields = ("token", "uses_count", "created_at")
    raw_id_fields = ("case", "created_by")


@admin.register(CaseComment)
class CaseCommentAdmin(admin.ModelAdmin):
    list_display = ("case", "author", "created_at", "text_preview")
    raw_id_fields = ("case", "author")

    @admin.display(description="Текст")
    def text_preview(self, obj):
        return obj.text[:60]


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("original_file", "type", "case", "uploaded_by", "uploaded_at", "sha256_short")
    list_filter = ("type",)
    raw_id_fields = ("case", "uploaded_by")

    @admin.display(description="SHA-256")
    def sha256_short(self, obj):
        return obj.sha256[:16] + "…" if obj.sha256 else "—"


@admin.register(EvidencePage)
class EvidencePageAdmin(admin.ModelAdmin):
    list_display = ("evidence", "page_index", "width", "height")
    raw_id_fields = ("evidence",)


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ("pipeline_name", "case", "status", "progress", "created_by", "started_at", "finished_at")
    list_filter = ("status", "pipeline_name")
    raw_id_fields = ("case", "evidence", "page", "created_by")


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("kind", "job", "created_at")
    list_filter = ("kind",)
    raw_id_fields = ("job",)
