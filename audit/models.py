"""
A single audit history shared by every part of the app.

Each entry records who did what, when, and (where relevant) the before and
after values of changed fields.
"""

from django.conf import settings
from django.db import models


class AuditEntry(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=50, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=500)
    # {"field": {"from": old, "to": new}, ...}
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name_plural = "audit entries"

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.summary}"


def record(actor, action, target, summary, changes=None):
    """Write an audit entry. `target` is any model instance."""
    return AuditEntry.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        summary=summary[:500],
        changes=changes or {},
    )
