"""
Sort catalogue: a drag-and-drop admin page for placing items into
subsections, other sections and kits. Every move saves straight away, is
recorded in the audit history, is kept on re-import, and can be undone.
"""

import json

from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from audit.models import record

from .kits import sync_kit_rules
from .models import Item, Kit, KitItem, Section, Subsection

LIST_LIMIT = 600


def _place(item):
    return {"section": item.section_id, "subsection": item.subsection_id, "locked": item.section_locked}


def _label(section, sub):
    if section is None:
        return "Unsectioned"
    return f"{section.name} > {sub.name}" if sub else section.name


@admin_required
def sort_page(request):
    sections = list(Section.objects.all())
    if not sections:
        return render(request, "catalogue/sort.html", {"sections": []})
    key = request.GET.get("section") or sections[0].key
    section = get_object_or_404(Section, pk=key)
    show = request.GET.get("show", "unsorted")
    q = request.GET.get("q", "").strip()
    items = Item.objects.filter(section=section).select_related("subsection").order_by("display_name")
    if show == "unsorted":
        items = items.filter(subsection__isnull=True)
    for term in q.split():
        items = items.filter(Q(part_no__icontains=term) | Q(display_name__icontains=term))
    total = items.count()
    subsections = (Subsection.objects.filter(section=section)
                   .annotate(n=Count("items")).order_by("sort_order", "name"))
    return render(request, "catalogue/sort.html", {
        "sections": sections,
        "section": section,
        "show": show,
        "q": q,
        "items": items[:LIST_LIMIT],
        "total": total,
        "limited": total > LIST_LIMIT,
        "subsections": subsections,
        "no_sub_count": Item.objects.filter(section=section, subsection__isnull=True).count(),
        "other_sections": [s for s in sections if s.pk != section.pk],
        "kits": Kit.objects.filter(section=section).annotate(n=Count("kit_items")).order_by("name"),
        "can_undo": bool(request.session.get("sort_undo")),
    })


@admin_required
@require_POST
def move(request):
    """Move items to a subsection / section, or add them to a kit. Body: {"items": [...], "target": "..."}."""
    data = json.loads(request.body or "{}")
    ids = [int(i) for i in data.get("items", []) if str(i).isdigit()]
    target = data.get("target", "")
    items = list(Item.objects.filter(pk__in=ids).select_related("section", "subsection"))
    if not items:
        return JsonResponse({"ok": False, "message": "No items selected."}, status=400)
    kind, _, value = target.partition(":")

    with transaction.atomic():
        if kind == "kit":
            kit = get_object_or_404(Kit, pk=value)
            next_pos = (kit.kit_items.aggregate(m=Max("position"))["m"] or 0) + 1
            created = []
            for item in items:
                kit_item, new = KitItem.objects.get_or_create(kit=kit, item=item,
                                                              defaults={"quantity": 1, "position": next_pos})
                if new:
                    created.append(kit_item.pk)
                    next_pos += 1
            sync_kit_rules(kit)
            record(request.user, "kit.save", kit, f"Added {len(created)} item(s) to kit {kit.name} (Sort catalogue)")
            request.session["sort_undo"] = {"kind": "kit", "kit": kit.pk, "kit_items": created}
            return JsonResponse({"ok": True, "message": f"Added {len(created)} item(s) to {kit.name}.",
                                 "moved": [], "kit": kit.pk, "kit_count": kit.kit_items.count()})

        if kind == "sub":
            sub = get_object_or_404(Subsection.objects.select_related("section"), pk=value)
            section = sub.section
        elif kind == "sec":
            section, sub = get_object_or_404(Section, pk=value), None
        else:
            return JsonResponse({"ok": False, "message": "Unknown target."}, status=400)

        undo = []
        label = _label(section, sub)
        for item in items:
            before = _label(item.section, item.subsection)
            undo.append({"id": item.pk, **_place(item)})
            item.section, item.subsection, item.section_locked = section, sub, True
            item.save()
            record(request.user, "item.edit", item, f"Moved {item.part_no} to {label} (Sort catalogue)",
                   {"section": {"from": before, "to": label}})
        request.session["sort_undo"] = {"kind": "move", "items": undo}
    return JsonResponse({"ok": True, "message": f"Moved {len(items)} item(s) to {label}.",
                         "moved": [i.pk for i in items], "section": section.pk,
                         "subsection": sub.pk if sub else None, "label": sub.name if sub else "No subsection"})


@admin_required
@require_POST
def undo(request):
    last = request.session.pop("sort_undo", None)
    if not last:
        return JsonResponse({"ok": False, "message": "Nothing to undo."}, status=400)
    with transaction.atomic():
        if last["kind"] == "kit":
            kit = Kit.objects.filter(pk=last["kit"]).first()
            KitItem.objects.filter(pk__in=last["kit_items"]).delete()
            if kit:
                sync_kit_rules(kit)
                record(request.user, "kit.save", kit, f"Undid adding {len(last['kit_items'])} item(s) to {kit.name}")
        else:
            for entry in last["items"]:
                item = Item.objects.filter(pk=entry["id"]).first()
                if item is None:
                    continue
                item.section_id, item.subsection_id, item.section_locked = (
                    entry["section"], entry["subsection"], entry["locked"])
                item.save()
                record(request.user, "item.edit", item, f"Undid move of {item.part_no} (Sort catalogue)")
    return JsonResponse({"ok": True, "message": "Last change undone."})


@admin_required
@require_POST
def add_subsection(request, section_key):
    section = get_object_or_404(Section, pk=section_key)
    name = request.POST.get("name", "").strip()[:60]
    if name and not section.subsections.filter(name__iexact=name).exists():
        top = section.subsections.aggregate(m=Max("sort_order"))["m"] or 0
        Subsection.objects.create(section=section, name=name, sort_order=top + 10)
        record(request.user, "section.edit", section, f"Added subsection {section.name} > {name}")
    return redirect(reverse("catalogue:sort") + f"?section={section.pk}")


@admin_required
@require_POST
def rename_subsection(request, sub_id):
    sub = get_object_or_404(Subsection, pk=sub_id)
    name = json.loads(request.body or "{}").get("name", "").strip()[:60]
    if not name:
        return JsonResponse({"ok": False, "message": "Enter a name."}, status=400)
    if sub.section.subsections.filter(name__iexact=name).exclude(pk=sub.pk).exists():
        return JsonResponse({"ok": False, "message": "That name is already used in this section."}, status=400)
    old = sub.name
    sub.name = name
    sub.save(update_fields=["name"])
    record(request.user, "section.edit", sub.section, f"Renamed subsection {old} to {name}")
    return JsonResponse({"ok": True, "message": f"Renamed to {name}."})


@admin_required
@require_POST
def reorder_subsections(request, section_key):
    section = get_object_or_404(Section, pk=section_key)
    ids = [int(i) for i in json.loads(request.body or "{}").get("ids", []) if str(i).isdigit()]
    for position, pk in enumerate(ids, start=1):
        Subsection.objects.filter(pk=pk, section=section).update(sort_order=position * 10)
    record(request.user, "section.edit", section, f"Reordered subsections of {section.name}")
    return JsonResponse({"ok": True, "message": "Order saved."})


@admin_required
@require_POST
def add_kit(request, section_key):
    section = get_object_or_404(Section, pk=section_key)
    name = request.POST.get("name", "").strip()[:120]
    if name and not Kit.objects.filter(name__iexact=name).exists():
        kit = Kit.objects.create(name=name, section=section)
        record(request.user, "kit.save", kit, f"Created kit {name} in {section.name} (Sort catalogue)")
    return redirect(reverse("catalogue:sort") + f"?section={section.pk}")
