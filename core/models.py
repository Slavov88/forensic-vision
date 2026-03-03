"""
Core app models: Case, CaseMembership, CaseShareLink, CaseComment,
Evidence, EvidencePage, AnalysisJob, Artifact.
"""
import hashlib
import uuid
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()



class Case(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Чернова"
        IN_REVIEW = "in_review", "В преглед"
        FINAL = "final", "Финален"

    title = models.CharField("Заглавие", max_length=255)
    description = models.TextField("Описание", blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    tags = models.JSONField("Тагове", default=list, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_cases",
        verbose_name="Създаден от",
    )
    created_at = models.DateTimeField("Създаден на", auto_now_add=True)
    updated_at = models.DateTimeField("Обновен на", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Кейс"
        verbose_name_plural = "Кейсове"

    def __str__(self) -> str:
        return self.title

    @classmethod
    def get_accessible_cases(cls, user):
        from django.db.models import Q
        if user.is_staff:
            return cls.objects.all()
        return cls.objects.filter(
            Q(created_by=user) | Q(memberships__user=user)
        ).distinct()



class CaseMembership(models.Model):
    class Role(models.TextChoices):
        VIEWER = "viewer", "Наблюдател"
        EDITOR = "editor", "Редактор"

    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Кейс",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="case_memberships",
        verbose_name="Потребител",
    )
    role = models.CharField(
        "Роля", max_length=10, choices=Role.choices, default=Role.VIEWER
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
        verbose_name="Поканен от",
    )
    created_at = models.DateTimeField("Добавен на", auto_now_add=True)

    class Meta:
        unique_together = [("case", "user")]
        verbose_name = "Членство в кейс"
        verbose_name_plural = "Членства в кейсове"

    def __str__(self) -> str:
        return f"{self.user} → {self.case} ({self.role})"



class CaseShareLink(models.Model):
    class Role(models.TextChoices):
        VIEWER = "viewer", "Наблюдател"
        EDITOR = "editor", "Редактор"

    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name="Кейс",
    )
    token = models.UUIDField("Токен", default=uuid.uuid4, unique=True, editable=False)
    role = models.CharField(
        "Роля", max_length=10, choices=Role.choices, default=Role.VIEWER
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="created_share_links",
        verbose_name="Създаден от",
    )
    created_at = models.DateTimeField("Създаден на", auto_now_add=True)
    expires_at = models.DateTimeField("Изтича на", null=True, blank=True)
    max_uses = models.PositiveIntegerField("Макс. използвания", null=True, blank=True)
    uses_count = models.PositiveIntegerField("Брой използвания", default=0)
    revoked = models.BooleanField("Отменен", default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Споделен линк"
        verbose_name_plural = "Споделени линкове"

    def __str__(self) -> str:
        return f"{self.case} – {self.role} [{self.token}]"

    @property
    def is_valid(self) -> bool:
        """Return True if link can still be redeemed."""
        if self.revoked:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        return True



class CaseComment(models.Model):
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Кейс",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Автор",
    )
    text = models.TextField("Текст")
    created_at = models.DateTimeField("Писан на", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Коментар"
        verbose_name_plural = "Коментари"

    def __str__(self) -> str:
        return f"{self.author} @ {self.case}: {self.text[:40]}"



def evidence_upload_to(instance, filename):
    return f"evidence/case_{instance.case_id}/{filename}"


class Evidence(models.Model):
    class EvidenceType(models.TextChoices):
        PDF = "pdf", "PDF"
        IMAGE = "image", "Изображение"

    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="evidence_items",
        verbose_name="Кейс",
    )
    type = models.CharField(
        "Тип", max_length=10, choices=EvidenceType.choices
    )
    original_file = models.FileField(
        "Оригинален файл", upload_to=evidence_upload_to
    )
    sha256 = models.CharField("SHA-256", max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="uploaded_evidence",
        verbose_name="Качен от",
    )
    uploaded_at = models.DateTimeField("Качен на", auto_now_add=True)
    is_reference = models.BooleanField("Еталон", default=False)
    metadata_json = models.JSONField("Метаданни", default=dict, blank=True)
    tags = models.JSONField("Тагове", default=list, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Доказателство"
        verbose_name_plural = "Доказателства"

    def __str__(self) -> str:
        return f"{self.type.upper()} – {self.original_file.name}"

    def compute_sha256(self) -> str:
        """Compute SHA-256 of the stored file in chunks."""
        h = hashlib.sha256()
        self.original_file.seek(0)
        for chunk in iter(lambda: self.original_file.read(65536), b""):
            h.update(chunk)
        self.original_file.seek(0)
        return h.hexdigest()



def page_image_upload_to(instance, filename):
    return f"evidence/case_{instance.evidence.case_id}/pages/{filename}"


class EvidencePage(models.Model):
    evidence = models.ForeignKey(
        Evidence,
        on_delete=models.CASCADE,
        related_name="pages",
        verbose_name="Доказателство",
    )
    page_index = models.PositiveIntegerField("Индекс на страница")
    rendered_image = models.ImageField(
        "Рендерирано изображение", upload_to=page_image_upload_to
    )
    width = models.PositiveIntegerField("Ширина (px)", default=0)
    height = models.PositiveIntegerField("Височина (px)", default=0)

    class Meta:
        ordering = ["evidence", "page_index"]
        unique_together = [("evidence", "page_index")]
        verbose_name = "Страница"
        verbose_name_plural = "Страници"

    def __str__(self) -> str:
        return f"Страница {self.page_index + 1} на {self.evidence}"



class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "На опашка"
        RUNNING = "running", "Изпълнява се"
        DONE = "done", "Завършен"
        FAILED = "failed", "Неуспешен"

    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
        verbose_name="Кейс",
    )
    evidence = models.ForeignKey(
        Evidence,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
        verbose_name="Доказателство",
    )
    page = models.ForeignKey(
        EvidencePage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_jobs",
        verbose_name="Страница",
    )
    pipeline_name = models.CharField("Pipeline", max_length=100)
    params_json = models.JSONField("Параметри", default=dict, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    progress = models.PositiveSmallIntegerField("Прогрес (%)", default=0)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="analysis_jobs",
        verbose_name="Стартиран от",
    )
    started_at = models.DateTimeField("Стартиран на", null=True, blank=True)
    finished_at = models.DateTimeField("Завършен на", null=True, blank=True)
    error_message = models.TextField("Съобщение за грешка", blank=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Задача за анализ"
        verbose_name_plural = "Задачи за анализ"

    def __str__(self) -> str:
        return f"{self.pipeline_name} → {self.case} [{self.status}]"



def artifact_upload_to(instance, filename):
    return f"artifacts/job_{instance.job_id}/{filename}"


class Artifact(models.Model):
    class Kind(models.TextChoices):
        OVERLAY = "overlay", "Overlay"
        METRICS = "metrics", "Метрики"
        REPORT = "report", "Отчет"
        PREVIEW = "preview", "Преглед"

    job = models.ForeignKey(
        AnalysisJob,
        on_delete=models.CASCADE,
        related_name="artifacts",
        verbose_name="Задача",
    )
    kind = models.CharField(
        "Вид", max_length=20, choices=Kind.choices
    )
    file = models.FileField(
        "Файл", upload_to=artifact_upload_to, blank=True, null=True
    )
    data_json = models.JSONField("Данни (JSON)", default=dict, blank=True)
    created_at = models.DateTimeField("Създаден на", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Артефакт"
        verbose_name_plural = "Артефакти"

    def __str__(self) -> str:
        return f"{self.kind} @ job#{self.job_id}"
