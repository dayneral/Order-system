"""
BFS request (Oct 2026):
- Starting subsections for Electrical, Plumbing and Joinery.
- A new Kitchens section (Wall Units, Base Units, Worktops, Splashbacks, Trims)
  replaces Bathrooms & Kitchens.
- Bathroom items move to Plumbing > Sanitary Wear; the sink to Kitchens;
  worktop items from Joinery to Kitchens. Moved items are section-locked so
  re-imports keep them where they are. Every move is written to the audit log.
Items are never deleted. Codes not present are skipped.
"""

from django.db import migrations

SUBSECTIONS = {
    "electrical": ["Sockets and Switches", "Smoke Alarms", "Fans", "Back Boxes", "Cable"],
    "plumbing": ["Sanitary Wear", "Wet Floors", "Showers and Accessories", "Other Common Items",
                 "Fixtures and Fittings"],
    "joinery": ["Internal Doors and Ironmongery", "Sheet Products", "Wet Wall and Trims", "Timber"],
    "kitchens": ["Wall Units", "Base Units", "Worktops", "Splashbacks", "Trims"],
}

# part number -> (section key, subsection name or None)
MOVES = {code: ("plumbing", "Sanitary Wear") for code in
         ["BK0002", "BK0003", "BK0004", "BK0005", "BK0006", "BK0007", "BK0008", "BK0009", "BK0010", "BK0011", "BK0012"]}
MOVES.update({
    "BK0001": ("kitchens", None),
    "JOI080": ("kitchens", "Worktops"),
    "JOI088": ("kitchens", "Splashbacks"),
    "JOI081": ("kitchens", "Trims"),
    "JOI083": ("kitchens", "Trims"),
    "JOI091": ("kitchens", "Trims"),
})


def forwards(apps, schema_editor):
    Section = apps.get_model("catalogue", "Section")
    Subsection = apps.get_model("catalogue", "Subsection")
    Item = apps.get_model("catalogue", "Item")
    AuditEntry = apps.get_model("audit", "AuditEntry")

    # Remember which file each imported item came from (see Item.import_section). The old
    # Bathrooms & Kitchens file name now matches Kitchens.
    for item in Item.objects.filter(source="import").only("pk", "section_id"):
        Item.objects.filter(pk=item.pk).update(
            import_section="kitchens" if item.section_id == "bathrooms-kitchens" else (item.section_id or ""))

    old = Section.objects.filter(key="bathrooms-kitchens").first()
    Section.objects.get_or_create(key="kitchens", defaults={
        "name": "Kitchens", "sort_order": old.sort_order if old else 2})

    subs = {}
    for key, names in SUBSECTIONS.items():
        if not Section.objects.filter(key=key).exists():
            continue
        for order, name in enumerate(names, start=1):
            subs[(key, name)], _ = Subsection.objects.get_or_create(section_id=key, name=name,
                                                                    defaults={"sort_order": order * 10})

    for item in Item.objects.filter(part_no__in=MOVES):
        key, sub_name = MOVES[item.part_no]
        if not Section.objects.filter(key=key).exists():
            continue
        before = item.section_id
        item.section_id = key
        item.subsection = subs.get((key, sub_name)) if sub_name else None
        item.section_locked = True
        item.save(update_fields=["section", "subsection", "section_locked"])
        AuditEntry.objects.create(
            action="item.edit", target_type="catalogue.item", target_id=str(item.pk),
            summary=f"Moved {item.part_no} to {key}{' > ' + sub_name if sub_name else ''} (Kitchens reorganisation)",
            changes={"section": {"from": before, "to": key}})

    if old is not None:
        remaining = Item.objects.filter(section_id="bathrooms-kitchens")
        count = remaining.count()
        remaining.update(section=None)  # never delete items: they become Unsectioned
        AuditEntry.objects.create(action="section.delete", target_type="catalogue.section",
                                  target_id="bathrooms-kitchens",
                                  summary=f"Removed section Bathrooms & Kitchens (replaced by Kitchens); "
                                          f"{count} other item(s) moved to Unsectioned")
        old.delete()


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0007_subsections"), ("audit", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
