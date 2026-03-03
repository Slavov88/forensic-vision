"""
AuditLog model – append-only event log for all significant actions.
"""
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class AuditLog(models.Model):
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="Актьор",
    )
    action = models.CharField("Действие", max_length=100)
    target_type = models.CharField("Тип обект", max_length=100, blank=True)
    target_id = models.CharField("ID на обект", max_length=50, blank=True)
    timestamp = models.DateTimeField("Час", auto_now_add=True)
    details_json = models.JSONField("Детайли", default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Одит запис"
        verbose_name_plural = "Одит записи"

    def __str__(self) -> str:
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.actor} – {self.action}"

    @classmethod
    def log(
        cls,
        action: str,
        actor=None,
        target=None,
        details: dict | None = None,
    ) -> "AuditLog":
        """Convenience factory for creating audit log entries."""
        entry = cls(
            actor=actor,
            action=action,
            details_json=details or {},
        )
        if target is not None:
            entry.target_type = type(target).__name__
            entry.target_id = str(target.pk)
        entry.save()
        return entry
