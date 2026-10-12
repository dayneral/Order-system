"""
Voids management: properties being turned round between tenancies.

One Void per property job, with one TradeTask per trade. Job type (Void, Temp,
Concierge Station…) is a label used for filtering, not a separate workflow.
"""

import re

from django.db import models
from django.utils import timezone


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
        COMPLETE = "complete", "Complete"
        HANDED_BACK = "handed_back", "Handed back"

    OPEN_STATUSES = [Status.NEW, Status.STC, Status.IN_PROGRESS, Status.SNAGGING, Status.COMPLETE]

    job_type = models.ForeignKey(JobType, on_delete=models.PROTECT)
    simpro = models.CharField("SIMPRO number", max_length=60, blank=True, db_index=True)
    address = models.CharField(max_length=200)
    postcode = models.CharField(max_length=10, blank=True)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL)
    supervisor = models.ForeignKey(Supervisor, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="voids")
    access = models.CharField(max_length=100, blank=True)
    keys_received = models.DateField(null=True, blank=True)
    handback_date = models.DateField("client hand-back date", null=True, blank=True)
    expected_completion = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    # When set, the status is not worked out from the trades.
    status_fixed = models.BooleanField("status set by hand", default=False)
    electrical_flag = models.BooleanField(default=False)
    issue_flag = models.BooleanField("issue", default=False)
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

    class Meta:
        ordering = ["handback_date", "address"]

    def __str__(self):
        return f"{self.address} ({self.simpro})" if self.simpro else self.address

    @property
    def district(self):
        return district_of(self.postcode)

    @property
    def target_date(self):
        return self.handback_date or self.expected_completion

    @property
    def days_since_update(self):
        return (timezone.now() - self.last_activity).days

    @property
    def stale_level(self):
        """'' (fine), 'amber' or 'red' when nobody has updated an open void for a while."""
        from django.conf import settings
        if self.on_hold or self.status not in (self.Status.NEW, self.Status.STC, self.Status.IN_PROGRESS,
                                               self.Status.SNAGGING):
            return ""
        days = self.days_since_update
        if days >= settings.VOIDS_STALE_RED_DAYS:
            return "red"
        if days >= settings.VOIDS_STALE_AMBER_DAYS:
            return "amber"
        return ""

    @property
    def is_late_risk(self):
        """Expected completion is after the client's hand-back date."""
        return bool(self.handback_date and self.expected_completion and self.expected_completion > self.handback_date)


class TradeTask(models.Model):
    class Status(models.TextChoices):
        NONE = "", "—"
        NOT_REQUIRED = "not_required", "Not required"
        STC = "stc", "STC"
        BOOKED = "booked", "Booked / ordered"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        SNAGGING = "snagging", "Snagging / follow on"
        ISSUE = "issue", "Issue"
        COMPLETE = "complete", "Complete"

    void = models.ForeignKey(Void, on_delete=models.CASCADE, related_name="tasks")
    trade = models.ForeignKey(Trade, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, blank=True, default="")
    # Red writing in the tracker: works claimed for payment.
    claimed = models.BooleanField(default=False)
    assignee = models.CharField("who", max_length=100, blank=True)
    date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

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
