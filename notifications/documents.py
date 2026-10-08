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


HEADER_LABELS = {
    "job_number": "Job number", "property_address": "Property address", "property_type": "Property type",
    "operative_name": "Operative", "delivery_date": "Delivery date", "special_instructions": "Special instructions",
}


def _line_dict(line):
    return {
        "key": str(line.pk),
        "code": "NON-STOCKED" if line.is_non_stocked else line.part_no,
        "is_non_stocked": line.is_non_stocked,
        "description": item_description(line),
        "quantity": quantity_text(line),
        "section": line.section_name,
        "is_flammable": line.is_flammable,
        "kit_name": line.kit_name,
    }


def _header_dict(order):
    return {
        "job_number": order.job_number,
        "property_address": order.property_address,
        "property_type": order.get_property_type_display(),
        "operative_name": order.operative_name,
        "delivery_date": f"{order.delivery_date:%A %d %B %Y}" if order.delivery_date else "",
        "special_instructions": order.special_instructions,
    }


def snapshot(order):
    """What stores were sent, for comparing with the next version."""
    return {"header": _header_dict(order), "lines": {d["key"]: d for d in map(_line_dict, order.lines.all())}}


def document_context(order, kind="submitted", previous=None):
    """previous: the snapshot from the last email stores received. For AMENDED emails the
    changes since then are marked: new, changed (with the old value) and removed lines,
    and changed job details."""
    lines = [_line_dict(line) for line in order.lines.all()]
    changes = None
    if kind == "amended" and previous:
        old_lines = previous.get("lines", {})
        for line in lines:
            old = old_lines.get(line["key"])
            if old is None:
                line["change"] = "new"
            elif (old["quantity"], old["description"]) != (line["quantity"], line["description"]):
                line["change"] = "changed"
                line["was"] = old["quantity"] if old["description"] == line["description"] else \
                    f'{old["description"]}, {old["quantity"]}'
        current_keys = {line["key"] for line in lines}
        removed = [dict(old, change="removed") for key, old in old_lines.items() if key not in current_keys]
        old_header = previous.get("header", {})
        new_header = _header_dict(order)
        header_changes = {field: old_header.get(field, "") for field in HEADER_LABELS
                          if old_header.get(field, "") != new_header[field]}
        changes = {
            "new": sum(1 for line in lines if line.get("change") == "new"),
            "changed": sum(1 for line in lines if line.get("change") == "changed"),
            "removed": len(removed),
            "header": [{"field": f, "label": HEADER_LABELS[f], "was": old or "(blank)"} for f, old in header_changes.items()],
            "header_fields": set(header_changes),
        }
        lines = lines + removed
    return {
        "order": order,
        "kind": kind,
        "label": LABELS.get(kind, ""),
        "lines": lines,
        "groups": group_by_section(lines),
        "changes": changes,
        "line_count": sum(1 for line in lines if line.get("change") != "removed"),
        "has_flammable": any(line["is_flammable"] for line in lines if line.get("change") != "removed"),
        "has_non_stocked": any(line["is_non_stocked"] for line in lines if line.get("change") != "removed"),
        "generated_at": timezone.localtime(),
        "stores_email": settings.STORES_EMAIL,
    }


def group_by_section(lines):
    """Lines grouped under section headings for stores, in catalogue section order."""
    from catalogue.models import Section

    order = {name: index for index, name in enumerate(Section.objects.values_list("name", flat=True))}
    groups = {}
    for line in lines:
        name = line["section"] or ("Non-stocked" if line["is_non_stocked"] else "Other")
        groups.setdefault(name, []).append(line)
    ranked = sorted(groups.items(), key=lambda g: (order.get(g[0], len(order) + (g[0] == "Non-stocked")),))
    number = 0
    for _, group in ranked:
        group.sort(key=lambda line: line.get("change") == "removed")  # removed lines last in their section
        for line in group:
            if line.get("change") == "removed":
                line["number"] = ""
                continue
            number += 1
            line["number"] = number  # numbered top to bottom as stores read it
    return [{"section": name, "lines": group} for name, group in ranked]


def subject(order, kind="submitted"):
    label = LABELS.get(kind, "")
    delivery = f"{order.delivery_date:%d %b %Y}" if order.delivery_date else "no date"
    text = f"Material order {order.order_number} - Job {order.job_number} - deliver {delivery}"
    return f"{label}: {text}" if label else text


def render_html(order, kind="submitted", previous=None):
    return render_to_string("notifications/order_document.html", document_context(order, kind, previous))


def render_to_string_for_print(order, kind, back_url, previous=None):
    return render_to_string("notifications/order_document.html",
                            {**document_context(order, kind, previous), "show_toolbar": True,
                             "back_url": back_url})


def render_text(order, kind="submitted", previous=None):
    return render_to_string("notifications/order_document.txt", document_context(order, kind, previous))
