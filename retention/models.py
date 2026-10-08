"""The log written by each run of the daily retention job."""

from django.db import models


class RetentionRun(models.Model):
    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "Daily job"
        MANUAL = "manual", "Run by hand"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.SCHEDULED)
    run_by = models.CharField(max_length=150, blank=True, help_text="Admin who pressed Run now, if any.")
    dry_run = models.BooleanField(default=False)
    cutoff_delivery_date = models.DateField(help_text="Orders delivered on or before this date were deleted.")
    orders_deleted = models.PositiveIntegerField(default=0)
    order_numbers = models.JSONField(default=list, help_text="Order numbers deleted in this run.")
    drafts_deleted = models.PositiveIntegerField(default=0)
    succeeded = models.BooleanField(default=False)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Retention run {self.started_at:%Y-%m-%d %H:%M}"
