"""
Safe sync from the Excel void tracker.

Each sync compares, field by field, three values: what the tracker said at the last sync (the
baseline), what it says now, and what the app says now.

    sheet changed, app didn't           -> take the sheet's value
    app changed, sheet didn't           -> keep the app's value
    both changed to different values    -> conflict: keep the app's value, list it for review
    void ready for final check / done   -> never changed by the sheet; differences are conflicts

New rows create voids. Rows missing from the sheet never delete anything: the void is flagged.
The app never writes back to the spreadsheet.
"""

import re
from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit.models import record

from . import services
from .importer import find_existing, find_supervisor, parse_workbook
from .models import (Area, JobType, Supervisor, TrackerBaseline, TrackerConflict, TrackerSyncRun, Trade,
                     TradeTask, Void)
from .notify import site_url

FIELD_LABELS = {"address": "Address", "postcode": "Postcode", "area": "Area", "supervisor": "Supervisor",
                "access": "Access", "handback": "Hand-back date", "status_note": "Note", "issue": "Issue flag"}
PROTECTED = (Void.Status.COMPLETE, Void.Status.HANDED_BACK)
MISSING = object()


# --- Snapshots: the same shape for the sheet and the app -------------------------------------

def sheet_snapshot(item, sups, area_codes):
    snap = {
        "address": item["address"][:200],
        "postcode": (item["postcode"] or "")[:10].upper(),
        "access": (item["access"] or "")[:100],
        "handback": item["expected"],
        "status_note": "; ".join(x for x in (item["status_note"], item["expected_note"]) if x)[:300],
        "issue": bool(item["issue_flag"]),
    }
    # Area and supervisor only count when the sheet's value is recognised.
    if item["area"] in area_codes:
        snap["area"] = item["area"]
    sup = find_supervisor(item["supervisor"], sups)
    if sup:
        snap["supervisor"] = sup.name
    for name, t in item["tasks"].items():
        snap[f"trade:{name}"] = {"status": t["status"], "assignee": (t["assignee"] or "")[:100],
                                 "date": t["date"], "claimed": bool(t["claimed"]), "notes": t["notes"] or ""}
    return snap


def app_snapshot(void, trade_names):
    snap = {
        "address": void.address, "postcode": void.postcode, "access": void.access,
        "handback": void.handback_date.isoformat() if void.handback_date else None,
        "status_note": void.status_note, "issue": bool(void.issue_flag),
        "area": void.area.code if void.area_id else None,
        "supervisor": void.supervisor.name if void.supervisor_id else None,
    }
    for t in void.tasks.all():
        snap[f"trade:{t.trade.name}"] = {"status": t.status, "assignee": t.assignee,
                                         "date": t.date.isoformat() if t.date else None,
                                         "claimed": bool(t.claimed), "notes": t.notes}
    return snap


def norm(value):
    if isinstance(value, dict):
        return tuple(sorted((k, norm(v)) for k, v in value.items()))
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().lower()
    return value


def same(a, b):
    return norm(a) == norm(b)


def uk(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else ""


def describe(key, value):
    if value is None or value is MISSING:
        return "— (not on the job)" if key.startswith("trade:") else "—"
    if key.startswith("trade:"):
        st = dict(TradeTask.Status.choices).get(value["status"], value["status"])
        bits = [st, value.get("assignee") or "", uk(value.get("date"))]
        text = " · ".join(b for b in bits if b)
        if value.get("claimed"):
            text += " · claimed"
        if value.get("notes"):
            text += f" · “{value['notes'][:60]}”"
        return text
    if key == "issue":
        return "Yes" if value else "No"
    if key == "handback":
        return uk(value)
    return str(value)


def label_for(key):
    return key.split(":", 1)[1] + " (trade)" if key.startswith("trade:") else FIELD_LABELS.get(key, key)


# --- Applying a sheet value to the app ---------------------------------------------------------

def apply_value(void, key, value):
    if key.startswith("trade:"):
        trade = Trade.objects.get(name=key.split(":", 1)[1])
        if value is None or value is MISSING:
            void.tasks.filter(trade=trade).delete()
        else:
            TradeTask.objects.update_or_create(void=void, trade=trade, defaults={
                "status": value["status"], "assignee": value["assignee"], "claimed": value["claimed"],
                "date": date.fromisoformat(value["date"]) if value["date"] else None, "notes": value["notes"]})
        return
    if key == "area":
        void.area = Area.objects.filter(code=value).first() if value else None
    elif key == "supervisor":
        void.supervisor = Supervisor.objects.filter(name=value).first() if value else None
    elif key == "handback":
        void.handback_date = date.fromisoformat(value) if value else None
    elif key == "issue":
        void.issue_flag = bool(value)
        if value and not void.issue_note:
            void.issue_note = "Issue marked in the tracker"
        if not value:
            void.issue_note = ""
    else:
        setattr(void, key, value or "")
    void.save()


# --- The sync ------------------------------------------------------------------------------------

def open_conflict(void, key, app_val, sheet_val):
    c = TrackerConflict.objects.filter(void=void, key=key, resolution="").first()
    fields = {"label": label_for(key), "app_text": describe(key, app_val)[:500],
              "sheet_text": describe(key, sheet_val)[:500],
              "sheet_value": None if sheet_val is MISSING else sheet_val}
    if c:
        for k, v in fields.items():
            setattr(c, k, v)
        c.save()
    else:
        TrackerConflict.objects.create(void=void, key=key, **fields)


@transaction.atomic
def sync_rows(rows, user=None):
    sups = list(Supervisor.objects.all())
    area_codes = set(Area.objects.values_list("code", flat=True))
    trade_names = set(Trade.objects.values_list("name", flat=True))
    job_types = {j.name: j for j in JobType.objects.all()}
    report = {"created": [], "updated": [], "conflicts": 0, "unchanged": 0, "missing": []}
    seen = set()
    for item in rows:
        sheet = sheet_snapshot(item, sups, area_codes)
        void = find_existing(item)
        if void is None:
            void = Void(job_type=job_types[item["job_type"]], imported=True, simpro=item["simpro"],
                        status=Void.Status.NEW)
            for key in ("address", "postcode", "access", "status_note"):
                setattr(void, key, sheet[key])
            void.handback_date = date.fromisoformat(sheet["handback"]) if sheet["handback"] else None
            void.issue_flag = sheet["issue"]
            void.issue_note = item.get("issue_note", "")[:300] if sheet["issue"] else ""
            if "area" in sheet:
                void.area = Area.objects.get(code=sheet["area"])
            if "supervisor" in sheet:
                void.supervisor = Supervisor.objects.get(name=sheet["supervisor"])
            services.apply_postcode(void)
            void.save()
            for key, value in sheet.items():
                if key.startswith("trade:"):
                    apply_value(void, key, value)
            services.refresh_status(void, None, activity=False)
            TrackerBaseline.objects.create(void=void, data=sheet)
            record(user, "void.sync", void, f"Created {void} from the tracker ({item['sheet']} row {item['row']})")
            report["created"].append(void.address)
            seen.add(void.pk)
            continue

        seen.add(void.pk)
        if void.missing_from_tracker:
            Void.objects.filter(pk=void.pk).update(missing_from_tracker=False)
        base_obj = TrackerBaseline.objects.filter(void=void).first()
        base = base_obj.data if base_obj else None
        app = app_snapshot(void, trade_names)
        keys = set(sheet) | {k for k in app if k.startswith("trade:")} | ({k for k in base if k.startswith("trade:")}
                                                                          if base else set())
        changed, conflicts = [], 0
        for key in sorted(keys):
            if key in ("area", "supervisor") and key not in sheet:
                continue  # the sheet's value wasn't recognised: no information
            s = sheet.get(key)
            a = app.get(key)
            if same(s, a):
                TrackerConflict.objects.filter(void=void, key=key, resolution="").update(
                    resolution=TrackerConflict.Resolution.SUPERSEDED, resolved_at=timezone.now())
                continue
            b = (base or {}).get(key) if base is not None else MISSING
            if b is not MISSING and same(s, b):
                continue  # only the app changed it: keep the app's value
            if b is not MISSING and same(a, b) and void.status not in PROTECTED:
                apply_value(void, key, s)  # only the sheet changed it
                changed.append(label_for(key))
                continue
            open_conflict(void, key, a, s)
            conflicts += 1
        if base_obj:
            base_obj.data = sheet
            base_obj.save()
        else:
            TrackerBaseline.objects.create(void=void, data=sheet)
        if changed:
            services.refresh_status(void, None, activity=False)
            record(user, "void.sync", void, f"Updated from the tracker: {', '.join(changed)}"[:500])
            report["updated"].append(f"{void.address}: {', '.join(changed)}")
        elif not conflicts:
            report["unchanged"] += 1
        report["conflicts"] += conflicts

    synced_types = {item["job_type"] for item in rows}
    for v in Void.objects.filter(imported=True, job_type__name__in=synced_types).exclude(pk__in=seen).exclude(
            status=Void.Status.HANDED_BACK):
        if not v.missing_from_tracker:
            Void.objects.filter(pk=v.pk).update(missing_from_tracker=True)
        report["missing"].append(v.address)
    report["open_conflicts"] = TrackerConflict.objects.filter(resolution="").count()
    return report


def run_sync(data, source, user=None, filename=""):
    """Parse the workbook bytes and sync. Always records a run, even when it fails."""
    run = TrackerSyncRun(source=source, run_by=user if getattr(user, "pk", None) else None)
    try:
        rows, parse_report = parse_workbook(data)
        report = sync_rows(rows, user)
        report["problems"] = parse_report.get("problems", [])
        report["filename"] = filename
        run.ok, run.report = True, report
    except Exception as exc:  # noqa: BLE001 - recorded on the run and emailed
        run.ok, run.error = False, str(exc)[:2000]
    run.save()
    notify_admins(run)
    return run


def notify_admins(run):
    from accounts.models import User
    r = run.report
    if run.ok and not (r.get("created") or r.get("updated") or r.get("conflicts") or r.get("missing")):
        return
    to = list(User.objects.filter(role=User.Role.ADMIN, status=User.Status.APPROVED).values_list("email", flat=True))
    if not to:
        return
    link = site_url(reverse("voids:tracker"))
    if not run.ok:
        subject, body = "Void tracker sync FAILED", f"The tracker sync failed:\n\n{run.error}\n\n{link}"
    else:
        lines = [f"Void tracker sync – {timezone.localtime(run.started_at):%a %d %b %H:%M}", ""]
        if r["conflicts"]:
            lines += [f"⚠ {r['conflicts']} conflict(s) need a decision ({r['open_conflicts']} open in total).", ""]
        if r["created"]:
            lines += [f"New voids ({len(r['created'])}):"] + [f"  - {a}" for a in r["created"]] + [""]
        if r["updated"]:
            lines += [f"Updated from the tracker ({len(r['updated'])}):"] + [f"  - {a}" for a in r["updated"]] + [""]
        if r["missing"]:
            lines += [f"No longer in the tracker ({len(r['missing'])}):"] + [f"  - {a}" for a in r["missing"]] + [""]
        lines.append(f"Review: {link}")
        subject = "Void tracker sync" + (f" – {r['conflicts']} to review" if r["conflicts"] else "")
        body = "\n".join(lines)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    except Exception:  # noqa: BLE001
        pass


@transaction.atomic
def resolve(conflict, user, use_sheet):
    void = conflict.void
    if use_sheet:
        apply_value(void, conflict.key, MISSING if conflict.sheet_value is None and conflict.key.startswith("trade:")
                    else conflict.sheet_value)
        services.refresh_status(void, user)
        record(user, "void.sync", void, f"Used the tracker's value for {conflict.label}: {conflict.sheet_text}"[:500])
    conflict.resolution = (TrackerConflict.Resolution.USED_SHEET if use_sheet
                           else TrackerConflict.Resolution.KEPT_APP)
    conflict.resolved_at, conflict.resolved_by = timezone.now(), user
    conflict.save()
