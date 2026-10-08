"""A record of every order email: sent, or failed and waiting for a retry."""

from django.db import models


class OrderEmail(models.Model):
    class Kind(models.TextChoices):
        SUBMITTED = "submitted", "New order"
        AMENDED = "amended", "Amended"
        CANCELLED = "cancelled", "Cancelled"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        RESOLVED = "resolved", "Failed, then sent by a later retry or email"

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="emails")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=300)
    attempts = models.PositiveIntegerField(default=1)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    # The order as it was in this email, so the next AMENDED email can show what changed.
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        get_latest_by = "created_at"

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"
