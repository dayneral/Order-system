"""
The order document: one template used for the email body and the printable
page, so the two always match. (A PDF attachment was dropped at BFS's request:
the email itself is printed.)

Stores never see prices or order values.
"""

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from catalogue.models import MeasureType

LABELS = {"submitted": "", "amended": "AMENDED", "cancelled": "CANCELLED"}


def item_description(line):
    """'ADH014 — Multi-purpose silicone' style text, with cut instructions."""
    if line.measure_type == MeasureType.CUT_TO_ORDER:
        return f"{line.name}, cut to {line.length_m}m"
    return line.name


def quantity_text(line):
    if line.measure_type == MeasureType.AREA:
        return f"Cut to {line.length_m}m × {line.width_m}m = {line.area_m2} m²"
    if line.measure_type == MeasureType.CUT_TO_ORDER:
        return f"1 piece, {line.length_m}m"
    if line.measure_type == MeasureType.LINEAR:
        return f"{line.length_m}m"
    return line.measurement_text


def document_context(order, kind="submitted"):
    lines = []
    for line in order.lines.all():
        lines.append({
            "code": "NON-STOCKED" if line.is_non_stocked else line.part_no,
            "is_non_stocked": line.is_non_stocked,
            "description": item_description(line),
            "quantity": quantity_text(line),
            "section": line.section_name,
            "is_flammable": line.is_flammable,
        })
    return {
        "order": order,
        "kind": kind,
        "label": LABELS.get(kind, ""),
        "lines": lines,
        "has_flammable": any(line["is_flammable"] for line in lines),
        "has_non_stocked": any(line["is_non_stocked"] for line in lines),
        "generated_at": timezone.localtime(),
        "stores_email": settings.STORES_EMAIL,
    }


def subject(order, kind="submitted"):
    label = LABELS.get(kind, "")
    delivery = f"{order.delivery_date:%d %b %Y}" if order.delivery_date else "no date"
    text = f"Material order {order.order_number} - Job {order.job_number} - deliver {delivery}"
    return f"{label}: {text}" if label else text


def render_html(order, kind="submitted"):
    return render_to_string("notifications/order_document.html", document_context(order, kind))


def render_to_string_for_print(order, kind, back_url):
    return render_to_string("notifications/order_document.html",
                            {**document_context(order, kind), "show_toolbar": True,
                             "back_url": back_url})


def render_text(order, kind="submitted"):
    return render_to_string("notifications/order_document.txt", document_context(order, kind))
