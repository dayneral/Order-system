"""
Catalogue import: works out what an upload would change (the plan and
report), then applies it.

Rules (from the build brief):
- Part No. is the key. Re-importing updates existing items.
- Duplicate Part No.: the first keeps the code; later ones get a temporary
  suffix (ELE118-B, ELE118-C...) and are flagged "code to be confirmed".
- Placeholder codes (e.g. PLU0) all get temporary codes (PLU0-A, PLU0-B...).
- Rows missing a name or trade price are imported but flagged incomplete.
- Sell price is never stored; where it is below trade price the row is listed
  for review.
- Garbled characters are repaired; names still garbled are listed.
- Display names, units, pack sizes and measure types are never overwritten
  on re-import.
- Items no longer in the file are deactivated (never deleted), but only for
  sections the file contains, so a single-section file is safe to import.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product
from string import ascii_uppercase

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEntry, record

from .models import ImportRun, Item, Section
from .reader import SourceRow
from .setup_rules import initial_setup
from .text import looks_garbled, repair_text

PLACEHOLDER_RE = re.compile(r"^[A-Z]+0+$")
NO_CODE = "NOCODE"


def _suffixes():
    yield from ascii_uppercase
    for a, b in product(ascii_uppercase, repeat=2):
        yield a + b


def _money(value):
    return None if value is None else f"{Decimal(value):.2f}"


@dataclass
class Entry:
    row: SourceRow
    code: str  # code as in the file (or NOCODE)
    key: str  # stable import key, e.g. ELE118#2
    occurrence: int
    placeholder: bool
    name: str
    trade_price: Decimal | None
    section_key: str
    item: Item | None = None
    part_no: str = ""
    needs_temp: bool = False
    changes: dict = field(default_factory=dict)


@dataclass
class Plan:
    entries: list
    removals: list
    report: dict


def _temp_code(code, start_index, used):
    for index, suffix in enumerate(_suffixes()):
        if index < start_index:
            continue
        candidate = f"{code}-{suffix}"
        if candidate not in used:
            return candidate
    raise ValueError(f"Ran out of temporary codes for {code}")


def build_plan(rows, ignored_columns=(), skipped=(), unknown_sections=()):
    sections = {s.key: s for s in Section.objects.all()}
    default_section = Section.MISC if Section.MISC in sections else None
    items = list(Item.objects.select_related("section"))
    by_key = {i.import_key: i for i in items if i.import_key}
    by_code = {i.part_no: i for i in items}
    used_codes = set(by_code)

    report = {
        "summary": {}, "additions": [], "updates": [], "removals": [], "temp_codes": [],
        "incomplete": [], "sell_below_trade": [], "repaired_names": [], "garbled_names": [],
        "no_section": [], "unknown_sections": list(unknown_sections), "setup_notes": [],
        "skipped": list(skipped), "ignored_columns": list(ignored_columns), "sections_in_file": [],
    }

    # 1. Work out each row's code, stable key and cleaned values.
    counts = defaultdict(int)
    entries = []
    for row in rows:
        code = row.part_no or NO_CODE
        counts[code] += 1
        name = repair_text(row.name)
        where = f"{row.sheet} row {row.row_number}"
        if name != (row.name or "").strip():
            report["repaired_names"].append({"part_no": code, "from": row.name, "to": name})
        if row.section is None:
            report["no_section"].append({"part_no": code, "name": name, "where": where,
                                         "section_text": row.section_text})
        entries.append(Entry(
            row=row, code=code, key=f"{code}#{counts[code]}", occurrence=counts[code],
            placeholder=code == NO_CODE or bool(PLACEHOLDER_RE.match(code)),
            name=name,
            trade_price=Decimal(row.trade_price) if row.trade_price is not None else None,
            section_key=row.section if row.section in sections else default_section,
        ))
    incoming_keys = {e.key for e in entries}

    # 2. Match rows to existing items: by import key first, then by Part No.
    claimed = set()
    for e in entries:
        e.needs_temp = e.placeholder or e.occurrence > 1
        item = by_key.get(e.key)
        if item is not None and item.pk in claimed:
            item = None
        if item is None and not e.needs_temp:
            candidate = by_code.get(e.code)
            if (candidate is not None and candidate.pk not in claimed
                    and (candidate.import_key is None or candidate.import_key not in incoming_keys)):
                item = candidate
        if item is not None:
            claimed.add(item.pk)
        e.item = item

    # 3. Codes for new items. Duplicates continue the lettering from the
    #    occurrence (2nd = -B), placeholders start at -A.
    for e in entries:
        if e.item is not None:
            e.part_no = e.item.part_no
            continue
        if not e.needs_temp and e.code not in used_codes:
            e.part_no = e.code
        else:
            e.part_no = _temp_code(e.code, e.occurrence - 1, used_codes)
            reason = ("no Part No. in file" if e.code == NO_CODE
                      else "placeholder code" if e.placeholder
                      else "duplicate Part No." if e.occurrence > 1
                      else "Part No. already used by another item")
            e.needs_temp = True
            report["temp_codes"].append({"original": e.row.part_no or "(blank)", "new": e.part_no,
                                         "name": e.name, "reason": reason})
        used_codes.add(e.part_no)

    # 4. Per-row checks and changes.
    for e in entries:
        row = e.row
        where = f"{row.sheet} row {row.row_number}"
        missing = [label for label, ok in (("name", e.name), ("trade price", e.trade_price is not None)) if not ok]
        if missing:
            report["incomplete"].append({"part_no": e.part_no, "name": e.name or "(no name)", "where": where,
                                         "missing": ", ".join(missing), "price_text": row.price_text})
        if row.sell_price is not None and e.trade_price is not None and Decimal(row.sell_price) < e.trade_price:
            report["sell_below_trade"].append({"part_no": e.part_no, "name": e.name,
                                               "trade": _money(e.trade_price), "sell": _money(row.sell_price)})
        if looks_garbled(e.name):
            report["garbled_names"].append({"part_no": e.part_no, "name": e.name})

        if e.item is None:
            fields, note = initial_setup(e.code, e.name, row.unit, e.section_key)
            e.changes = fields
            if note:
                report["setup_notes"].append({"part_no": e.part_no, "name": e.name, "note": note})
            report["additions"].append({
                "part_no": e.part_no, "name": e.name or "(no name)",
                "section": sections[e.section_key].name if e.section_key else "Unsectioned",
                "price": _money(e.trade_price), "measure": fields["measure_type"], "unit": fields["unit"],
                "pack_size": fields["pack_size"],
            })
            continue

        item = e.item
        new_values = {
            "catalogue_name": e.name,
            "trade_price": e.trade_price,
            "section_id": e.section_key,
            "original_part_no": row.part_no,
        }
        changes = {}
        for attr, new in new_values.items():
            old = getattr(item, attr)
            if attr == "trade_price":
                same = (old is None and new is None) or (old is not None and new is not None and Decimal(old) == new)
            else:
                same = old == new
            if not same:
                changes[attr] = {"from": old, "to": new}
        if not item.is_active and item.deactivated_by_import:
            changes["is_active"] = {"from": False, "to": True}
        if row.flammable and not item.is_flammable:
            changes["is_flammable"] = {"from": False, "to": True}
        e.changes = changes
        if changes:
            report["updates"].append({
                "part_no": item.part_no, "name": item.name,
                "changes": {_LABELS.get(k, k): _readable(k, v, sections) for k, v in changes.items()},
            })

    # 5. Items that have dropped out of the file (only in sections the file covers).
    file_sections = {e.section_key for e in entries}
    report["sections_in_file"] = [s.name for k, s in sections.items() if k in file_sections]
    removals = [
        i for i in items
        if i.source == Item.Source.IMPORT and i.is_active and i.pk not in claimed and i.section_id in file_sections
    ]
    for i in removals:
        report["removals"].append({"part_no": i.part_no, "name": i.name,
                                   "section": i.section.name if i.section else "Unsectioned"})

    report["summary"] = {
        "rows": len(entries),
        "additions": len(report["additions"]),
        "updates": len(report["updates"]),
        "unchanged": sum(1 for e in entries if e.item is not None and not e.changes),
        "removals": len(removals),
        "temp_codes": len(report["temp_codes"]),
        "incomplete": len(report["incomplete"]),
        "sell_below_trade": len(report["sell_below_trade"]),
        "repaired_names": len(report["repaired_names"]),
        "garbled_names": len(report["garbled_names"]),
        "skipped": len(report["skipped"]),
    }
    return Plan(entries=entries, removals=removals, report=report)


_LABELS = {"catalogue_name": "Original name", "trade_price": "Trade price", "section_id": "Section",
           "original_part_no": "Part No. in spreadsheet", "is_active": "Active", "is_flammable": "Flammable"}


def _readable(attr, change, sections):
    def fmt(value):
        if attr == "trade_price":
            return _money(value) if value is not None else "(none)"
        if attr == "section_id":
            return sections[value].name if value in sections else (value or "Unsectioned")
        return value
    return {"from": fmt(change["from"]), "to": fmt(change["to"])}


def _jsonable(changes, sections):
    return {k: _readable(k, v, sections) for k, v in changes.items()}


@transaction.atomic
def apply_plan(plan, actor, run=None):
    """Write the plan to the database and record it in the audit history."""
    sections = {s.key: s for s in Section.objects.all()}
    audit = []
    now = timezone.now()

    for e in plan.entries:
        flammable = e.row.flammable or bool(e.section_key and sections[e.section_key].is_flammable)
        if e.item is None:
            item = Item(
                part_no=e.part_no, original_part_no=e.row.part_no, import_key=e.key, source=Item.Source.IMPORT,
                catalogue_name=e.name, display_name=e.name, section_id=e.section_key, trade_price=e.trade_price,
                is_flammable=flammable, code_to_confirm=e.needs_temp, name_check=looks_garbled(e.name),
                **e.changes,
            )
            item.save()
            audit.append(AuditEntry(actor=actor, action="item.import_add", target_type="catalogue.item",
                                    target_id=str(item.pk), summary=f"Imported new item {item.part_no} {item.name}"))
            continue

        item = e.item
        item.import_key = e.key
        item.source = Item.Source.IMPORT
        item.name_check = looks_garbled(e.name)
        if e.changes:
            for attr, change in e.changes.items():
                setattr(item, attr, change["to"])
            if "is_active" in e.changes:
                item.deactivated_by_import = False
            audit.append(AuditEntry(actor=actor, action="item.import_update", target_type="catalogue.item",
                                    target_id=str(item.pk), summary=f"Import updated {item.part_no}",
                                    changes=_jsonable(e.changes, sections)))
        item.save()

    for item in plan.removals:
        item.is_active = False
        item.deactivated_by_import = True
        item.save(update_fields=["is_active", "deactivated_by_import", "updated_at"])
        audit.append(AuditEntry(actor=actor, action="item.import_deactivate", target_type="catalogue.item",
                                target_id=str(item.pk),
                                summary=f"Deactivated {item.part_no}: no longer in the catalogue file"))

    AuditEntry.objects.bulk_create(audit)
    if run is not None:
        run.status = ImportRun.Status.APPLIED
        run.applied_at = now
        run.applied_by = actor
        run.report = plan.report
        run.save()
        s = plan.report["summary"]
        record(actor, "catalogue.import", run,
               f"Applied catalogue import {run.filename}: {s['additions']} added, {s['updates']} updated, "
               f"{s['removals']} deactivated")
    return plan.report


def preview_upload(filename, data, actor):
    """Read an uploaded file and save an ImportRun holding its preview report."""
    from .reader import read_spreadsheet

    result = read_spreadsheet(filename, data)
    plan = build_plan(result.rows, result.ignored_columns, result.skipped, result.unknown_sections)
    return ImportRun.objects.create(
        uploaded_by=actor, filename=filename[:255],
        rows=[r.to_dict() for r in result.rows],
        report={**plan.report, "_meta": {"ignored_columns": result.ignored_columns, "skipped": result.skipped,
                                         "unknown_sections": result.unknown_sections}},
    )


def apply_run(run, actor):
    """Re-plan from the stored rows (in case the catalogue changed since preview) and apply."""
    if run.status != ImportRun.Status.PREVIEW:
        raise ValueError("This import has already been applied or discarded.")
    meta = run.report.get("_meta", {})
    rows = [SourceRow.from_dict(r) for r in run.rows]
    plan = build_plan(rows, meta.get("ignored_columns", []), meta.get("skipped", []), meta.get("unknown_sections", []))
    plan.report["_meta"] = meta
    return apply_plan(plan, actor, run)
