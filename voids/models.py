"""
Voids management: properties being turned round between tenancies.

One Void per property job, with one TradeTask per trade. Job type (Void, Temp,
Concierge Station…) is a label used for filtering, not a separate workflow.
"""

import re

from django.db import models
from django.utils import timezone


class VoidsSettings(models.Model):
    """Warning thresholds for the voids list. There is only ever one row (edit it in Voids settings)."""

    stale_amber_days = models.PositiveIntegerField(
        "needs an update: amber after (days)", default=5,
        help_text="An open void with no update for this many days gets an amber 'No update' tag.")
    stale_red_days = models.PositiveIntegerField(
        "needs an update: red after (days)", default=10,
        help_text="…and a red tag and outline after this many days.")
    stale_filter_days = models.PositiveIntegerField(
        "'No update' filter (days)", default=7,
        help_text="Used by the 'No update in … days' filter (and the supervisor email, when it is added).")
    handback_soon_days = models.PositiveIntegerField(
        "hand-back due soon (days)", default=7,
        help_text="A hand-back date within this many days is highlighted and shown by the 'Due soon' filter.")

    class Meta:
        verbose_name = verbose_name_plural = "warning thresholds"

    def __str__(self):
        return "Warning thresholds"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.stale_red_days <= self.stale_amber_days:
            raise ValidationError("The red threshold must be more days than the amber one.")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class JobType(models.Model):
    name = models.CharField(max_length=60, unique=True)
    # Workbook tab this job type is imported from (blank = not imported).
    sheet_name = models.CharField(max_length=60, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class Area(models.Model):
    """Geographical area: NE, NW, SE, SW."""

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Trade(models.Model):
    name = models.CharField(max_length=60, unique=True)
    short_name = models.CharField(max_length=3, blank=True, help_text="Up to 3 letters shown in the trade circles, e.g. PL.")
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    # Other column headings this trade has had in the tracker, one per line.
    import_aliases = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

    @property
    def badge(self):
        return self.short_name or self.name[:2].upper()

    def heading_names(self):
        names = [self.name] + [a for a in self.import_aliases.splitlines() if a.strip()]
        return {normalise_heading(n) for n in names}


class Supervisor(models.Model):
    name = models.CharField(max_length=100, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    user = models.OneToOneField("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="supervisor")
    # A trade supervisor looks after one trade in every area (e.g. electrical).
    trade = models.ForeignKey(Trade, null=True, blank=True, on_delete=models.SET_NULL,
                              help_text="Only for a trade supervisor who covers this trade in every area.")
    # Other spellings used in the tracker ("Stee", "Martin"), one per line.
    import_aliases = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def names(self):
        return {n.strip().lower() for n in [self.name, *self.import_aliases.splitlines()] if n.strip()}


class PostcodeDistrict(models.Model):
    """The first part of a postcode (EH16). Gives a void its area and patch supervisor."""

    district = models.CharField(max_length=6, unique=True)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL)
    supervisor = models.ForeignKey(Supervisor, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="districts")

    class Meta:
        ordering = ["district"]

    def __str__(self):
        return self.district

    def save(self, *args, **kwargs):
        self.district = self.district.strip().upper()
        super().save(*args, **kwargs)


POSTCODE_DISTRICT = re.compile(r"^\s*([A-Z]{1,2}\d{1,2}[A-Z]?)\b", re.I)


def district_of(postcode):
    m = POSTCODE_DISTRICT.match(postcode or "")
    return m.group(1).upper() if m else ""


def normalise_heading(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


class Void(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New: needs visit"
        STC = "stc", "STC"
        IN_PROGRESS = "in_progress", "In progress"
        SNAGGING = "snagging", "Snagging"
        COMPLETE = "complete", "Ready for final check"
        HANDED_BACK = "handed_back", "Completed"

    OPEN_STATUSES = [Status.NEW, Status.STC, Status.IN_PROGRESS, Status.SNAGGING, Status.COMPLETE]

    job_type = models.ForeignKey(JobType, on_delete=models.PROTECT)
    simpro = models.CharField("SIMPRO number", max_length=60, blank=True, db_index=True)
    address = models.CharField(max_length=200)
    postcode = models.CharField(max_length=10, blank=True)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL)
    supervisor = models.ForeignKey(Supervisor, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="voids")
    access = models.CharField(max_length=100, blank=True)
    start_date = models.DateField("works start", null=True, blank=True,
                                  help_text="Not allowed to start before, or works booked to start on, this date.")
    # The client's nominated hand-back date: the job's target / expected completion.
    handback_date = models.DateField("hand-back date", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    electrical_flag = models.BooleanField(default=False)
    issue_flag = models.BooleanField("issue", default=False)
    issue_note = models.CharField("issue details", max_length=300, blank=True)
    status_note = models.CharField(max_length=300, blank=True,
                                   help_text="Short note shown in the list, e.g. \"Rewire\", \"Awaiting lines\".")
    key_info = models.TextField("key info", blank=True,
                                help_text="Important information shown prominently: key safe numbers, warnings, hazards.")
    on_hold = models.BooleanField(default=False, help_text="Paused (e.g. waiting on an asbestos survey). "
                                                           "Not flagged as needing an update.")
    # Last real progress: a diary update, a trade change or a status change.
    last_activity = models.DateTimeField(default=timezone.now, db_index=True)
    last_inspection = models.DateField(null=True, blank=True)
    imported = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    handed_back_at = models.DateField(null=True, blank=True)
    # When the void went into Snagging (for the nightly email).
    snagging_since = models.DateTimeField(null=True, blank=True)
    # Final check: asked for when every trade is complete, confirmed by a supervisor.
    final_check_requested_at = models.DateTimeField(null=True, blank=True)
    final_checked_at = models.DateTimeField(null=True, blank=True)
    final_checked_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="+")

    class Meta:
        ordering = ["handback_date", "address"]

    def __str__(self):
        return f"{self.address} ({self.simpro})" if self.simpro else self.address

    @property
    def district(self):
        return district_of(self.postcode)

    @property
    def target_date(self):
        return self.handback_date

    @property
    def days_to_handback(self):
        """Whole days until the hand-back date (negative when overdue), or None."""
        if not self.handback_date:
            return None
        return (self.handback_date - timezone.localdate()).days

    @property
    def handback_label(self):
        d = self.days_to_handback
        if d is None:
            return ""
        if d == 0:
            return "Due today"
        if d < 0:
            return f"{-d} day{'s' if d != -1 else ''} overdue"
        return f"{d} day{'s' if d != 1 else ''} left"

    @property
    def days_since_update(self):
        return (timezone.now() - self.last_activity).days

    @property
    def stale_level(self):
        """'' (fine), 'amber' or 'red' when nobody has updated an open void for a while."""
        settings = getattr(self, "_thresholds", None) or VoidsSettings.get()
        if self.on_hold or self.status not in (self.Status.NEW, self.Status.STC, self.Status.IN_PROGRESS,
                                               self.Status.SNAGGING):
            return ""
        days = self.days_since_update
        if days >= settings.stale_red_days:
            return "red"
        if days >= settings.stale_amber_days:
            return "amber"
        return ""



class TradeTask(models.Model):
    class Status(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        STC = "stc", "STC"
        IN_PROGRESS = "in_progress", "In progress"
        SNAGGING = "snagging", "Snagging / follow on"
        COMPLETE = "complete", "Complete"

    void = models.ForeignKey(Void, on_delete=models.CASCADE, related_name="tasks")
    trade = models.ForeignKey(Trade, on_delete=models.PROTECT)
    # A trade on the job with nobody assigned yet is STC (still to have trades assigned).
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STC)
    # Red writing in the tracker: works claimed for payment.
    claimed = models.BooleanField(default=False)
    assignee = models.CharField("who", max_length=100, blank=True)
    date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("void", "trade")]
        ordering = ["trade__position"]

    def __str__(self):
        return f"{self.void} – {self.trade}"

    @property
    def display(self):
        if self.notes:
            return self.notes
        if self.status == self.Status.STC:
            return "STC"
        bits = [self.assignee, self.date.strftime("%d/%m") if self.date else ""]
        return " ".join(b for b in bits if b) or (self.get_status_display() if self.status else "")


class VoidUpdate(models.Model):
    """One entry in a void's job diary."""

    class Kind(models.TextChoices):
        GENERAL = "general", "General"
        VISIT = "visit", "Site visit"
        ISSUE = "issue", "Issue"
        CLIENT = "client", "Client contact"
        SNAG = "snag", "Snag"
        TRADE = "trade", "Trade update"
        STATUS = "status", "Status change"

    WRITTEN_KINDS = [Kind.GENERAL, Kind.VISIT, Kind.ISSUE, Kind.CLIENT, Kind.SNAG]
    EDIT_MINUTES = 15

    void = models.ForeignKey(Void, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GENERAL)
    text = models.TextField()
    edited_at = models.DateTimeField(null=True, blank=True)
    hidden = models.BooleanField(default=False)
    hidden_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.void}: {self.text[:40]}"

    @property
    def is_auto(self):
        return self.kind in (self.Kind.TRADE, self.Kind.STATUS)

    def can_edit(self, user):
        return (not self.is_auto and self.author_id == user.pk and not self.hidden
                and timezone.now() - self.created_at < timezone.timedelta(minutes=self.EDIT_MINUTES))


class VoidImportRun(models.Model):
    """One upload of the void tracker workbook: previewed first, then applied."""

    class Status(models.TextChoices):
        PREVIEW = "preview", "Waiting to be applied"
        APPLIED = "applied", "Applied"
        DISCARDED = "discarded", "Discarded"

    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    # Parsed rows, so "apply" uses exactly what was previewed.
    rows = models.JSONField(default=list)
    report = models.JSONField(default=dict)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
