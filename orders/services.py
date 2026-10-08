"""
Order rules: drafts, lines, submit, amend and cancel.

Kept separate from the views so the rules are easy to test and can be reused
by the future API for the job management system.
"""

import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from audit.models import record
from catalogue.models import Item, MeasureType

from . import notify
from .models import Order, OrderLine, OrderNumberCounter
from .pricing import LENGTH_TYPES, TENTH, WHOLE_NUMBER_TYPES, order_total, price_line

logger = logging.getLogger(__name__)

DRAFT_RETENTION_DAYS = 30
DRAFT_WARNING_DAYS = 5
MAX_QUANTITY = 9999
MAX_METRES = Decimal("100")


class OrderError(Exception):
    """A rule was broken. The message is shown to the user."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or [message]


# --- Permissions -------------------------------------------------------------

def can_view(order, user):
    if order.is_draft:
        return order.owner_id == user.pk
    return user.is_active  # any approved user can see All Orders


def can_edit(order, user):
    if order.is_draft:
        return order.owner_id == user.pk
    if order.status == Order.Status.SUBMITTED:
        return order.owner_id == user.pk or user.is_admin
    return False


def can_cancel(order, user):
    return order.status == Order.Status.SUBMITTED and (order.owner_id == user.pk or user.is_admin)


# --- Helpers ---------------------------------------------------------------------

def normalise_job_number(value):
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def parse_whole_number(value, label="Quantity"):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise OrderError(f"{label}: enter a whole number.")
    if number != number.to_integral_value():
        raise OrderError(f"{label}: enter a whole number.")
    if number < 1 or number > MAX_QUANTITY:
        raise OrderError(f"{label}: enter a number from 1 to {MAX_QUANTITY}.")
    return number


def parse_metres(value, label="Length"):
    try:
        metres = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise OrderError(f"{label}: enter a number of metres, e.g. 2.4")
    if metres <= 0 or metres > MAX_METRES:
        raise OrderError(f"{label}: enter between 0.1 and {MAX_METRES} metres.")
    if metres != metres.quantize(TENTH):
        raise OrderError(f"{label}: enter metres to the nearest 0.1m, e.g. 2.4")
    return metres.quantize(TENTH)


def parse_measurement(measure_type, data):
    """Read the quantity or measurement for a line from submitted form data."""
    if measure_type in WHOLE_NUMBER_TYPES:
        return {"quantity": parse_whole_number(data.get("quantity", "")), "length_m": None, "width_m": None}
    if measure_type == MeasureType.AREA:
        return {"quantity": None, "length_m": parse_metres(data.get("length", ""), "Length"),
                "width_m": parse_metres(data.get("width", ""), "Width")}
    if measure_type in LENGTH_TYPES:
        return {"quantity": None, "length_m": parse_metres(data.get("length", ""), "Length"), "width_m": None}
    raise OrderError("This item cannot be ordered: unknown measure type.")


def _copy_item(line, item):
    line.item = item
    line.part_no = item.part_no
    line.name = item.name
    line.section_name = item.section.name if item.section else ""
    line.measure_type = item.measure_type
    line.unit = item.unit
    line.pack_size = item.pack_size
    line.trade_price = item.trade_price
    line.catalogue_length_m = item.catalogue_length_m
    line.is_flammable = item.is_flammable


def _price(line):
    if line.is_non_stocked:
        line.line_value, line.area_m2, line.is_estimate = None, None, False
        return
    result = price_line(line.measure_type, line.trade_price, quantity=line.quantity, length=line.length_m,
                        width=line.width_m, catalogue_length=line.catalogue_length_m)
    line.line_value, line.area_m2, line.is_estimate = result.value, result.area_m2, result.is_estimate


def recalculate_total(order):
    order.order_value = order_total(order.lines.values_list("line_value", flat=True))
    order.save(update_fields=["order_value", "updated_at"])


def _touch(order, actor, summary, changes=None):
    """Note a change. Changes to a submitted order are amendments: audited and flagged for re-sending."""
    if order.status == Order.Status.SUBMITTED:
        order.amended_at = timezone.now()
        order.amendment_pending = True
        order.save(update_fields=["amended_at", "amendment_pending", "updated_at"])
        record(actor, "order.amend", order, f"{order.order_number}: {summary}", changes)
    else:
        order.save(update_fields=["updated_at"])


def _require_editable(order, user):
    if not can_edit(order, user):
        raise OrderError("You cannot change this order.")


# --- Drafts and header ------------------------------------------------------------

HEADER_FIELDS = ["job_number", "property_address", "property_type", "operative_name", "delivery_date",
                 "special_instructions"]


def create_draft(user, **header):
    order = Order(owner=user, requester_name=user.full_name)
    for name in HEADER_FIELDS:
        if name in header:
            setattr(order, name, header[name])
    order.job_number = (order.job_number or "").strip()
    order.job_number_key = normalise_job_number(order.job_number)
    order.save()
    return order


def header_errors(order, today=None):
    """Problems that stop an order being submitted (or an amendment being saved)."""
    today = today or timezone.localdate()
    errors = []
    if not order.job_number.strip():
        errors.append("Enter the job number.")
    if not order.property_address.strip():
        errors.append("Enter the property address.")
    if order.property_type not in Order.PropertyType.values:
        errors.append("Choose the property type: Void or Occupied.")
    elif order.property_type == Order.PropertyType.OCCUPIED and not order.operative_name.strip():
        errors.append("Enter the operative's name: it is required for occupied properties.")
    if order.delivery_date is None:
        errors.append("Enter the requested delivery date.")
    return errors


def active_order_for_job(job_number_key, exclude=None):
    if not job_number_key:
        return None
    qs = Order.objects.filter(status=Order.Status.SUBMITTED, job_number_key=job_number_key)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.first()


def _job_clash_message(other):
    return (f"Job {other.job_number} already has an active order, {other.order_number}, placed by "
            f"{other.requester_name} on {timezone.localtime(other.submitted_at):%d %b %Y}. "
            "Amend that order, or cancel it before placing a new one.")


def update_header(order, user, data, today=None):
    """Save header fields. Drafts accept partial details; submitted orders must stay complete."""
    _require_editable(order, user)
    today = today or timezone.localdate()
    old = {name: getattr(order, name) for name in HEADER_FIELDS}
    for name in HEADER_FIELDS:
        if name in data:
            setattr(order, name, data[name] if data[name] is not None else ("" if name != "delivery_date" else None))
    order.job_number = order.job_number.strip()
    order.job_number_key = normalise_job_number(order.job_number)
    order.operative_name = order.operative_name.strip() if order.property_type == Order.PropertyType.OCCUPIED else ""

    if order.delivery_date and order.delivery_date != old["delivery_date"] and order.delivery_date < today:
        raise OrderError("The delivery date cannot be in the past.")
    if order.status == Order.Status.SUBMITTED:
        errors = header_errors(order, today)
        if errors:
            raise OrderError(errors[0], errors)
        other = active_order_for_job(order.job_number_key, exclude=order)
        if other:
            raise OrderError(_job_clash_message(other))

    changes = {name: {"from": str(old[name] or ""), "to": str(getattr(order, name) or "")}
               for name in HEADER_FIELDS if old[name] != getattr(order, name)}
    try:
        with transaction.atomic():
            order.save()
    except IntegrityError:
        raise OrderError(_job_clash_message(active_order_for_job(order.job_number_key, exclude=order)))
    if changes:
        _touch(order, user, "changed " + ", ".join(k.replace("_", " ") for k in changes), changes)
    return order


def started_drafts(user):
    """The user's drafts that have something in them (an untouched empty draft is not listed)."""
    return (Order.objects.filter(owner=user, status=Order.Status.DRAFT)
            .exclude(job_number="", property_address="", lines__isnull=True))


def empty_draft_for(user):
    """An untouched draft to reuse when the user starts a new order."""
    return (Order.objects.filter(owner=user, status=Order.Status.DRAFT, job_number="", property_address="",
                                 property_type="", delivery_date__isnull=True, special_instructions="",
                                 lines__isnull=True)
            .order_by("-updated_at").first())


def delete_draft(order, user):
    if not (order.is_draft and order.owner_id == user.pk):
        raise OrderError("Only your own drafts can be deleted.")
    order.delete()


def drafts_due_for_deletion(user, now=None):
    """The user's drafts that will be deleted within DRAFT_WARNING_DAYS."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=DRAFT_RETENTION_DAYS - DRAFT_WARNING_DAYS)
    return started_drafts(user).filter(updated_at__lte=cutoff).order_by("updated_at")


def draft_expiry(order):
    return order.updated_at + timedelta(days=DRAFT_RETENTION_DAYS)


# --- Lines ----------------------------------------------------------------------

def _next_position(order):
    last = order.lines.order_by("-position").values_list("position", flat=True).first()
    return (last or 0) + 1


def add_item_line(order, user, item, data, kit_name="", audit=True):
    _require_editable(order, user)
    if not item.is_active:
        raise OrderError(f"{item.part_no} is no longer available.")
    if item.is_incomplete:
        raise OrderError(f"{item.part_no} cannot be ordered yet: its catalogue details are incomplete.")
    measurement = parse_measurement(item.measure_type, data)

    # Counted items already on the order: increase that line rather than adding another.
    # (Measured items stay separate: each line is a different piece to cut.)
    if item.measure_type in WHOLE_NUMBER_TYPES:
        existing = order.lines.filter(item=item, is_non_stocked=False, measure_type=item.measure_type).first()
        if existing is not None:
            before = existing.measurement_text
            new_quantity = existing.quantity + measurement["quantity"]
            if new_quantity > MAX_QUANTITY:
                raise OrderError(f"Quantity: the total would be more than {MAX_QUANTITY}.")
            existing.quantity = new_quantity
            if kit_name and not existing.kit_name:
                existing.kit_name = kit_name
            _price(existing)  # keeps the price the line was first added at
            existing.save()
            recalculate_total(order)
            if audit:
                _touch(order, user, f"changed {existing.part_no} {existing.name}",
                       {"quantity": {"from": before, "to": existing.measurement_text}})
            existing.merged = True
            return existing

    line = OrderLine(order=order, position=_next_position(order), kit_name=kit_name, **measurement)
    _copy_item(line, item)
    _price(line)
    line.save()
    recalculate_total(order)
    if audit:
        _touch(order, user, f"added {line.part_no} {line.name} ({line.measurement_text})")
    return line


def add_kit(order, user, kit, data):
    """Add the ticked items of a kit. Every ticked item is checked before anything is added.

    Form fields per kit item (id = KitItem id): include_<id>, and quantity_<id>
    or length_<id> / width_<id> depending on the item's measure type.
    """
    _require_editable(order, user)
    chosen, errors = [], []
    for kit_item in kit.kit_items.select_related("item__section"):
        if not data.get(f"include_{kit_item.pk}"):
            continue
        item = kit_item.item
        if not item.is_orderable:
            errors.append(f"{item.part_no} {item.name} is not available.")
            continue
        fields = {name: data.get(f"{name}_{kit_item.pk}", "") for name in ("quantity", "length", "width")}
        try:
            chosen.append((item, parse_measurement(item.measure_type, fields)))
        except OrderError as exc:
            errors.append(f"{item.part_no} {item.name}: {exc}")
    if errors:
        raise OrderError(errors[0], errors)
    if not chosen:
        raise OrderError("Tick at least one item to add.")
    with transaction.atomic():
        lines = [add_item_line(order, user, item, {
            "quantity": m["quantity"], "length": m["length_m"], "width": m["width_m"]}, kit_name=kit.name, audit=False)
            for item, m in chosen]
        # Label lines already on the order that belong to this kit (e.g. the item that prompted it).
        order.lines.filter(item__in=kit.kit_items.values("item"), kit_name="").update(kit_name=kit.name)
    _touch(order, user, f"added kit {kit.name}: " + ", ".join(f"{l.part_no} ({l.measurement_text})" for l in lines))
    return lines


def add_non_stocked_line(order, user, name, unit, quantity):
    _require_editable(order, user)
    name, unit = (name or "").strip(), (unit or "").strip()
    if not name:
        raise OrderError("Non-stocked item: enter a name.")
    if not unit:
        raise OrderError("Non-stocked item: enter a unit (e.g. each, box, m).")
    try:
        qty = Decimal(str(quantity).strip())
    except (InvalidOperation, ValueError):
        raise OrderError("Non-stocked item: enter a quantity.")
    if qty <= 0 or qty > MAX_QUANTITY or qty != qty.quantize(TENTH):
        raise OrderError("Non-stocked item: enter a quantity above 0 (up to 1 decimal place).")
    line = OrderLine(order=order, position=_next_position(order), is_non_stocked=True, name=name[:300],
                     unit=unit[:40], measure_type=MeasureType.EACH, quantity=qty)
    line.save()
    recalculate_total(order)
    _touch(order, user, f"added NON-STOCKED {line.name} ({line.measurement_text})")
    return line


def update_line(line, user, data):
    order = line.order
    _require_editable(order, user)
    before = line.measurement_text
    if line.is_non_stocked:
        try:
            qty = Decimal(str(data.get("quantity", "")).strip())
        except (InvalidOperation, ValueError):
            raise OrderError("Enter a quantity.")
        if qty <= 0 or qty > MAX_QUANTITY or qty != qty.quantize(TENTH):
            raise OrderError("Enter a quantity above 0 (up to 1 decimal place).")
        line.quantity = qty
    else:
        for attr, value in parse_measurement(line.measure_type, data).items():
            setattr(line, attr, value)
    _price(line)  # keeps the price the line was ordered at
    line.save()
    recalculate_total(order)
    if before != line.measurement_text:
        _touch(order, user, f"changed {line.part_no or 'NON-STOCKED'} {line.name}",
               {"quantity": {"from": before, "to": line.measurement_text}})
    return line


def remove_line(line, user):
    order = line.order
    _require_editable(order, user)
    if order.status == Order.Status.SUBMITTED and order.lines.count() == 1:
        raise OrderError("A submitted order must keep at least one item. Cancel the order instead.")
    description = f"{line.part_no or 'NON-STOCKED'} {line.name} ({line.measurement_text})"
    line.delete()
    recalculate_total(order)
    _touch(order, user, f"removed {description}")


# --- Submit, amend, cancel -------------------------------------------------------

def _next_order_number(year):
    OrderNumberCounter.objects.get_or_create(year=year)
    OrderNumberCounter.objects.filter(year=year).update(last_number=F("last_number") + 1)
    number = OrderNumberCounter.objects.get(year=year).last_number
    return f"BFS-{year}-{number:06d}"


def submit(order, user, today=None):
    """Turn a draft into a submitted order. Raises OrderError listing every problem."""
    if not (order.is_draft and order.owner_id == user.pk):
        raise OrderError("Only your own draft orders can be submitted.")
    today = today or timezone.localdate()
    errors = header_errors(order, today)
    if order.delivery_date and order.delivery_date < today:
        errors.append("The delivery date cannot be in the past.")
    lines = list(order.lines.select_related("item__section"))
    if not lines:
        errors.append("Add at least one item.")
    for line in lines:
        if line.is_non_stocked:
            continue
        item = line.item
        if item is None or not item.is_active:
            errors.append(f"{line.part_no} {line.name} is no longer available. Remove it to continue.")
        elif item.is_incomplete:
            errors.append(f"{line.part_no} {line.name} cannot be ordered: its details are incomplete.")
        elif item.measure_type != line.measure_type:
            errors.append(f"{line.part_no} {line.name} is now ordered differently ({item.get_measure_type_display()}). "
                          "Remove it and add it again.")
    other = active_order_for_job(order.job_number_key)
    if other:
        errors.append(_job_clash_message(other))
    if errors:
        raise OrderError(errors[0], errors)

    try:
        with transaction.atomic():
            # Fix prices and details as they are right now.
            for line in lines:
                if not line.is_non_stocked:
                    _copy_item(line, line.item)
                    _price(line)
                    line.save()
            order.submitted_at = timezone.now()
            order.status = Order.Status.SUBMITTED
            order.requester_name = user.full_name
            order.order_number = _next_order_number(timezone.localtime(order.submitted_at).year)
            order.order_value = order_total(line.line_value for line in lines)
            order.save()
    except IntegrityError:
        order.refresh_from_db()
        raise OrderError(_job_clash_message(active_order_for_job(order.job_number_key)))
    record(user, "order.submit", order, f"Submitted order {order.order_number} for job {order.job_number}")
    notify.order_submitted(order)
    return order


def admin_delete(order, user, reason):
    """Remove an order entered by mistake, so it does not skew the records.

    An order stores may act on is cancelled first, so they receive a CANCELLED email.
    The deletion itself is logged with the order number, who, when and why.
    """
    from audit.models import AuditEntry
    from retention.services import delete_order_completely

    if not user.is_admin:
        raise OrderError("Only admins can delete orders.")
    if order.is_draft:
        raise OrderError("Drafts are deleted by their owner.")
    reason = (reason or "").strip()
    if not reason:
        raise OrderError("Enter a reason for deleting the order.")
    if order.status == Order.Status.SUBMITTED:
        cancel(order, user, f"Deleted by admin: {reason}")
    number, pk = order.order_number, order.pk
    delete_order_completely(order)
    AuditEntry.objects.create(actor=user, action="order.delete", target_type="orders.order", target_id=str(pk),
                              summary=f"Deleted order {number}: {reason}"[:500])
    return number


def finish_amendment(order, user):
    """The user has finished amending: send the updated order to stores."""
    _require_editable(order, user)
    if order.status != Order.Status.SUBMITTED or not order.amendment_pending:
        raise OrderError("There are no changes to send.")
    errors = header_errors(order)
    if not order.lines.exists():
        errors.append("Add at least one item.")
    if errors:
        raise OrderError(errors[0], errors)
    order.amendment_pending = False
    order.save(update_fields=["amendment_pending", "updated_at"])
    record(user, "order.amend_send", order, f"Amended order {order.order_number} sent to stores")
    notify.order_amended(order)
    return order


def cancel(order, user, reason=""):
    if not can_cancel(order, user):
        raise OrderError("You cannot cancel this order.")
    order.status = Order.Status.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancelled_by = user
    order.amendment_pending = False
    order.save()
    summary = f"Cancelled order {order.order_number}"
    record(user, "order.cancel", order, summary + (f": {reason.strip()}" if reason.strip() else ""))
    notify.order_cancelled(order)
    return order
