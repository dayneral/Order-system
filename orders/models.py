"""
Orders and order lines.

Orders have a stable UUID (for a future job management system to refer to)
and, once submitted, a human order number BFS-YYYY-NNNNNN.

Each line keeps a copy of the item's details and trade price at the time it
was ordered, so later catalogue changes never alter past orders.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalogue.models import MeasureType


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        CANCELLED = "cancelled", "Cancelled"

    class PropertyType(models.TextChoices):
        VOID = "void", "Void"
        OCCUPIED = "occupied", "Occupied"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # --- Personal data: cleared by the retention job -----------------------
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="orders")
    requester_name = models.CharField(max_length=150, blank=True)
    job_number = models.CharField(max_length=30, blank=True)
    property_address = models.TextField(blank=True)

    # Normalised job number used for the one-order-per-job rule.
    job_number_key = models.CharField(max_length=30, blank=True, db_index=True)
    # Replaces the requester once personal data is removed.
    anonymous_id = models.CharField(max_length=20, blank=True)

    property_type = models.CharField(max_length=10, choices=PropertyType.choices, blank=True)
    delivery_date = models.DateField(null=True, blank=True)
    special_instructions = models.TextField(blank=True)
    order_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True, help_text="The order date.")
    amended_at = models.DateTimeField(null=True, blank=True)
    amendment_pending = models.BooleanField(
        default=False, help_text="Changed since stores were last sent the order.")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    anonymised_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            # One active order per job. Drafts, cancelled and anonymised orders don't count.
            models.UniqueConstraint(
                fields=["job_number_key"],
                condition=Q(status="submitted") & ~Q(job_number_key=""),
                name="one_active_order_per_job",
            ),
        ]

    def __str__(self):
        return self.order_number or f"Draft {str(self.id)[:8]}"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def is_anonymised(self):
        return self.anonymised_at is not None

    @property
    def requester_display(self):
        return self.anonymous_id if self.is_anonymised else self.requester_name

    @property
    def has_estimates(self):
        return any(line.is_estimate for line in self.lines.all())


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("catalogue.Item", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    position = models.PositiveIntegerField(default=0)
    is_non_stocked = models.BooleanField(default=False)

    # Copied from the item when ordered.
    part_no = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=300)
    section_name = models.CharField(max_length=60, blank=True)
    measure_type = models.CharField(max_length=10, choices=MeasureType.choices, default=MeasureType.EACH)
    unit = models.CharField(max_length=40, default="each")
    pack_size = models.PositiveIntegerField(default=1)
    trade_price = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    catalogue_length_m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    roll_width_m = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    is_flammable = models.BooleanField(default=False)

    # What was asked for.
    quantity = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    length_m = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    width_m = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    area_m2 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    line_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_estimate = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.part_no or 'NON-STOCKED'} {self.name}"

    @property
    def measurement_text(self):
        """Plain description of what was ordered, as shown on the order and to stores."""
        if self.measure_type == MeasureType.AREA:
            return f"{self.length_m}m × {self.width_m}m = {self.area_m2} m²"
        if self.measure_type == MeasureType.CUT_TO_ORDER:
            return f"cut to {self.length_m}m"
        if self.measure_type == MeasureType.LINEAR:
            return f"{self.length_m}m"
        qty = self.quantity.normalize() if self.quantity is not None else ""
        qty = f"{qty:f}" if qty != "" else ""
        if self.measure_type == MeasureType.PACK:
            return f"{qty} pack{'s' if self.quantity != 1 else ''} of {self.pack_size}"
        return f"{qty} {self.unit}"


class OrderNumberCounter(models.Model):
    """Last order number used in each year. Numbers never reset within a year."""

    year = models.PositiveIntegerField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)
