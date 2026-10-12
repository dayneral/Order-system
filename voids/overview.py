"""Management overview: the whole picture of open voids, with every number linking to the list."""

from datetime import timedelta
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Area, JobType, Supervisor, Trade, TradeTask, Void, VoidsSettings

S = Void.Status
T = TradeTask.Status


def list_link(**params):
    # Reset every list filter, then apply these (the list remembers filters, so be explicit).
    base = {"q": "", "job": "", "area": "", "supervisor": "", "status": "", "trade": "", "due": "", "flag": "",
            "sort": "handback", "view": "tiles"}
    base.update({k: v for k, v in params.items() if v is not None})
    return reverse("voids:list") + "?" + urlencode(base)


def measures(voids, th, today):
    soon = today + timedelta(days=th.handback_soon_days)
    stale_cut = timezone.now() - timedelta(days=th.stale_filter_days)
    open_v = [v for v in voids if v.status != S.HANDED_BACK]
    return {
        "open": len(open_v),
        "new": sum(v.status == S.NEW for v in open_v),
        "stc": sum(v.status == S.STC for v in open_v),
        "in_progress": sum(v.status == S.IN_PROGRESS for v in open_v),
        "snagging": sum(v.status == S.SNAGGING for v in open_v),
        "final_check": sum(v.status == S.COMPLETE for v in open_v),
        "issues": sum(v.issue_flag for v in open_v),
        "overdue": sum(bool(v.handback_date and v.handback_date < today) for v in open_v),
        "due_soon": sum(bool(v.handback_date and today <= v.handback_date <= soon) for v in open_v),
        "stale": sum(bool(not v.on_hold and v.last_activity < stale_cut and v.status in
                          (S.NEW, S.STC, S.IN_PROGRESS, S.SNAGGING)) for v in open_v),
        "on_hold": sum(v.on_hold for v in open_v),
    }


COLUMNS = [  # (key, heading, list filters)
    ("open", "Open", {}),
    ("new", "Needs visit", {"status": S.NEW}),
    ("stc", "STC", {"status": S.STC}),
    ("in_progress", "In progress", {"status": S.IN_PROGRESS}),
    ("snagging", "Snagging", {"status": S.SNAGGING}),
    ("final_check", "Final check", {"status": S.COMPLETE}),
    ("overdue", "Overdue", {"due": "overdue"}),
    ("due_soon", "Due soon", {"due": "week"}),
    ("issues", "Issues", {"flag": "issue"}),
    ("stale", "No update", {"flag": "stale"}),
]


def build(job=None):
    today = timezone.localdate()
    th = VoidsSettings.get()
    qs = Void.objects.select_related("supervisor", "area", "job_type").prefetch_related("tasks__trade")
    if job:
        qs = qs.filter(job_type=job)
    voids = list(qs)
    for v in voids:
        v._thresholds = th
    jobq = str(job.pk) if job else ""
    open_v = [v for v in voids if v.status != S.HANDED_BACK]
    totals = measures(voids, th, today)

    def row(label, subset, **filters):
        m = measures(subset, th, today)
        return {"label": label, "cells": [{"n": m[k], "url": list_link(job=jobq, **filters, **f), "key": k}
                                          for k, _, f in COLUMNS]}

    sup_rows = [row(s.name + (f" ({s.trade.name})" if s.trade_id else ""),
                    [v for v in voids if services.supervises(s, v)], supervisor=str(s.pk))
                for s in Supervisor.objects.filter(active=True).select_related("trade")]
    no_sup = [v for v in voids if not v.supervisor_id]
    area_rows = [row(a.code, [v for v in voids if v.area_id == a.pk], area=str(a.pk)) for a in Area.objects.all()]
    no_area = [v for v in voids if not v.area_id]
    # Biggest number per column, for shading the cells.
    col_max = {k: max([c["n"] for r in sup_rows + area_rows for c in r["cells"] if c["key"] == k] + [1])
               for k, _, _ in COLUMNS}

    trades = []
    for t in Trade.objects.filter(active=True):
        tasks = [x for v in open_v for x in v.tasks.all() if x.trade_id == t.pk]
        counts = {"stc": sum(x.status == T.STC for x in tasks),
                  "booked": sum(x.status == T.BOOKED for x in tasks),
                  "in_progress": sum(x.status == T.IN_PROGRESS for x in tasks),
                  "snagging": sum(x.status == T.SNAGGING for x in tasks)}
        waiting = sum(counts.values())
        if waiting:
            trades.append({"trade": t, "counts": counts, "total": waiting,
                           "url": list_link(job=jobq, trade=str(t.pk))})
    trades.sort(key=lambda r: -r["total"])
    trade_max = max([r["total"] for r in trades] + [1])
    for r in trades:
        r["segments"] = [(k, n, 100 * n / trade_max) for k, n in r["counts"].items() if n]

    # Hand-backs over the next two weeks, and everything overdue.
    days = []
    for i in range(14):
        d = today + timedelta(days=i)
        due = [v for v in open_v if v.handback_date == d]
        days.append({"date": d, "voids": due, "weekend": d.weekday() >= 5})
    day_max = max([len(d["voids"]) for d in days] + [1])
    overdue = sorted([v for v in open_v if v.handback_date and v.handback_date < today], key=lambda v: v.handback_date)

    month_ago = today - timedelta(days=30)
    done = [v for v in voids if v.status == S.HANDED_BACK and v.handed_back_at and v.handed_back_at >= month_ago]
    dated = [v for v in done if v.handback_date]
    on_time = sum(v.handed_back_at <= v.handback_date for v in dated)

    return {
        "totals": totals, "columns": COLUMNS, "sup_rows": sup_rows, "area_rows": area_rows, "col_max": col_max,
        "no_sup": len([v for v in no_sup if v.status != S.HANDED_BACK]),
        "no_area": len([v for v in no_area if v.status != S.HANDED_BACK]),
        "trades": trades, "days": days, "day_max": day_max, "overdue": overdue[:15], "overdue_count": len(overdue),
        "done_30": len(done), "on_time": on_time, "dated": len(dated),
        "on_time_pct": round(100 * on_time / len(dated)) if dated else None,
        "th": th, "today": today, "job": job, "job_types": JobType.objects.all(),
        "links": {k: list_link(job=jobq, **f) for k, _, f in COLUMNS} | {
            "on_hold": list_link(job=jobq, flag="on_hold"), "completed": list_link(job=jobq, status=S.HANDED_BACK)},
    }
