import io
from datetime import timedelta

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl.styles import Font, PatternFill

from accounts.decorators import admin_required
from audit.models import AuditEntry, record

from . import importer, services
from .forms import FilterForm, TaskForm, UpdateForm, UploadForm, VoidForm
from .models import Area, JobType, VoidsSettings, Trade, TradeTask, Void, VoidImportRun, VoidUpdate

SESSION_KEY = "voids_filters"
VOID_FIELDS = list(VoidForm.Meta.fields)


def with_related(qs):
    return qs.select_related("job_type", "area", "supervisor").prefetch_related(
        Prefetch("tasks", queryset=TradeTask.objects.select_related("trade")),
        Prefetch("updates", queryset=VoidUpdate.objects.filter(hidden=False).select_related("author"),
                 to_attr="visible_updates"))


def filtered_voids(request):
    """Apply the list filters. Filters are remembered for each user in their session."""
    if "clear" in request.GET:
        request.session.pop(SESSION_KEY, None)
        params = QueryDict()
    elif any(k in request.GET for k in FilterForm.base_fields):
        params = request.GET
        request.session[SESSION_KEY] = request.GET.urlencode()
    else:
        params = QueryDict(request.session.get(SESSION_KEY, ""))
    form = FilterForm(params)
    th = VoidsSettings.get()
    form.fields["flag"].choices = [
        (k, f"No update in {th.stale_filter_days}+ days" if k == "stale" else
         f"Due in {th.handback_soon_days} days" if k == "week" else label)
        for k, label in form.fields["flag"].choices]
    form.fields["due"].choices = [
        (k, f"Due in {th.handback_soon_days} days" if k == "week" else label) for k, label in form.fields["due"].choices]
    form.is_valid()
    f = form.cleaned_data if form.is_valid() else {}
    qs = with_related(Void.objects.all())
    today = timezone.localdate()
    if f.get("q"):
        q = f["q"].strip()
        qs = qs.filter(Q(address__icontains=q) | Q(postcode__icontains=q) | Q(simpro__icontains=q))
    if f.get("job"):
        qs = qs.filter(job_type=f["job"])
    if f.get("supervisor"):
        sup = f["supervisor"]
        if sup.trade_id:
            # A trade supervisor (electrical) sees every void needing their trade, plus their own.
            qs = qs.filter(Q(supervisor=sup) | Q(tasks__trade=sup.trade_id, tasks__status__in=OUTSTANDING)
                           | (Q(electrical_flag=True) if sup.trade.name.lower() == "electrical" else Q(pk=None))
                           ).distinct()
        else:
            qs = qs.filter(supervisor=sup)
    status = f.get("status")
    if status == "all":
        pass
    elif status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status__in=Void.OPEN_STATUSES)
    if f.get("trade"):
        qs = qs.filter(tasks__trade_id=f["trade"], tasks__status__in=OUTSTANDING).distinct()
    if f.get("due") == "overdue":
        qs = qs.filter(handback_date__lt=today)
    elif f.get("due") == "week":
        qs = qs.filter(handback_date__gte=today,
                       handback_date__lte=today + timedelta(days=VoidsSettings.get().handback_soon_days))
    elif f.get("due") == "none":
        qs = qs.filter(handback_date__isnull=True)
    if f.get("flag") == "stale":
        qs = qs.filter(last_activity__lt=timezone.now() - timedelta(days=VoidsSettings.get().stale_filter_days),
                       on_hold=False,
                       status__in=STALE_STATUSES)
    elif f.get("flag") == "on_hold":
        qs = qs.filter(on_hold=True)
    elif f.get("flag") == "electrical":
        qs = qs.filter(Q(electrical_flag=True) | Q(tasks__trade__name__iexact="electrical",
                                                     tasks__status__in=OUTSTANDING + [TradeTask.Status.COMPLETE])
                       ).distinct()
    elif f.get("flag") == "issue":
        qs = qs.filter(issue_flag=True)
    sort = request.GET.get("sort") or f.get("sort") or "simpro"
    order = {"stale": ["last_activity"], "recent": ["-last_activity"],
             "expected": ["expected_completion", "handback_date", "address"],
             "start": ["start_date", "expected_completion", "address"],"handback": ["handback_date", "expected_completion", "address"], "address": ["address"],
             "simpro": ["simpro"], "area": ["area__code", "address"], "supervisor": ["supervisor__name", "address"],
             "postcode": ["postcode"], "job": ["job_type__position", "address"]}.get(sort, ["handback_date"])
    qs = qs.order_by(*order)
    voids = list(qs)
    for v in voids:
        v._thresholds = th  # read the warning thresholds once, not once per void
    if sort == "simpro":
        voids.sort(key=services.simpro_number)
    elif sort in ("handback", "expected", "start"):
        # Voids without the date go last.
        field = {"handback": "handback_date", "expected": "expected_completion", "start": "start_date"}[sort]
        voids.sort(key=lambda v: getattr(v, field) is None)
    if f.get("flag") == "late":
        voids = [v for v in voids if v.is_late_risk]
    elif f.get("flag") == "stale_amber":
        voids = [v for v in voids if v.stale_level]
    elif f.get("flag") == "stale_red":
        voids = [v for v in voids if v.stale_level == "red"]
    # Counts for the area buttons are taken before the area filter.
    area_counts = {}
    for v in voids:
        area_counts[v.area_id] = area_counts.get(v.area_id, 0) + 1
    if f.get("area"):
        voids = [v for v in voids if v.area_id == f["area"].pk]
    return form, voids, params, area_counts


STALE_STATUSES = [Void.Status.NEW, Void.Status.STC, Void.Status.IN_PROGRESS, Void.Status.SNAGGING]

OUTSTANDING = [TradeTask.Status.STC, TradeTask.Status.BOOKED, TradeTask.Status.IN_PROGRESS,
               TradeTask.Status.SNAGGING]


def grid_rows(voids, trades):
    today = timezone.localdate()
    th = VoidsSettings.get()
    soon = timedelta(days=th.handback_soon_days)
    for v in voids:
        v._thresholds = th
    rows = []
    for v in voids:
        by_trade = {t.trade_id: t for t in v.tasks.all()}
        cells = [(trade, by_trade.get(trade.pk)) for trade in trades]
        on_job = [(trade, t) for trade, t in cells if t and t.status != TradeTask.Status.NOT_REQUIRED]
        not_needed = [(trade, t) for trade, t in cells if t and t.status == TradeTask.Status.NOT_REQUIRED]
        others = [trade for trade, t in cells if not t]
        needed = [t for _, t in cells if t and t.status != TradeTask.Status.NOT_REQUIRED]
        done = sum(1 for t in needed if t.status == TradeTask.Status.COMPLETE)
        updates = getattr(v, "visible_updates", None)
        if updates is None:
            updates = list(v.updates.filter(hidden=False).select_related("author")[:1])
        rows.append({"void": v, "cells": cells, "done": done, "needed": len(needed),
                     "latest": updates[0] if updates else None, "update_form": UpdateForm(),
                     "on_job": on_job, "not_needed": not_needed, "others": others,
                     "percent": round(100 * done / len(needed)) if needed else 0,
                     "overdue": bool(v.handback_date and v.handback_date < today
                                     and v.status != Void.Status.HANDED_BACK),
                     "this_week": bool(v.handback_date and today <= v.handback_date <= today + soon)})
    return rows


@login_required
def void_list(request):
    form, voids, params, area_counts = filtered_voids(request)
    trades = list(Trade.objects.filter(active=True))
    current_job = params.get("job", "")
    job_links = []
    for jt in [None, *JobType.objects.all()]:
        p = params.copy()
        p["job"] = str(jt.pk) if jt else ""
        job_links.append({"label": jt.name if jt else "All", "query": p.urlencode(),
                          "active": (str(jt.pk) if jt else "") == current_job})
    current_area = params.get("area", "")
    area_links = []
    for a in [None, *Area.objects.all()]:
        p = params.copy()
        p["area"] = str(a.pk) if a else ""
        area_links.append({"label": a.code if a else "All properties",
                           "count": sum(area_counts.values()) if a is None else area_counts.get(a.pk, 0),
                           "query": p.urlencode(), "active": (str(a.pk) if a else "") == current_area})
    unassigned = area_counts.get(None, 0)
    p = params.copy()
    elec_on = params.get("flag") == "electrical"
    p["flag"] = "" if elec_on else "electrical"
    electrical_link = {"query": p.urlencode(), "active": elec_on}
    current_sort = params.get("sort", "")
    sort_options = []
    for value, label in form.fields["sort"].choices:
        p = params.copy()
        p["sort"] = value
        sort_options.append({"label": label, "query": p.urlencode(), "active": value == current_sort})
    view = request.GET.get("view")
    if view in ("tiles", "table"):
        request.session["voids_view"] = view
    view = request.session.get("voids_view", "tiles")
    # Area, job type and trade have their own controls (quick buttons, trade list).
    form.fields["trade"].widget = form.fields["trade"].hidden_widget()
    form.fields["job"].widget = form.fields["job"].hidden_widget()
    form.fields["area"].widget = form.fields["area"].hidden_widget()
    form.fields["sort"].widget = form.fields["sort"].hidden_widget()
    return render(request, "voids/void_list.html", {
        "area_links": area_links, "unassigned": unassigned, "view": view,
        "electrical_link": electrical_link, "sort_options": sort_options,
        "task_statuses": [c for c in TradeTask.Status.choices if c[0]],
        "form": form, "rows": grid_rows(voids, trades), "trades": trades, "count": len(voids),
        "job_links": job_links, "params": params.urlencode(), "trade_filter": params.get("trade", ""),
        "statuses": Void.Status.choices,
    })


@login_required
def void_detail(request, void_id):
    void = get_object_or_404(Void.objects.select_related("job_type", "area", "supervisor"), pk=void_id)
    tasks = {t.trade_id: t for t in void.tasks.select_related("trade")}
    trades = [(trade, tasks.get(trade.pk)) for trade in Trade.objects.filter(active=True)]
    # Trades on the job first, then not required, then the rest.
    trades.sort(key=lambda tt: 2 if tt[1] is None else (1 if tt[1].status == TradeTask.Status.NOT_REQUIRED else 0))
    history = AuditEntry.objects.filter(target_type="voids.void", target_id=str(void.pk)).select_related("actor")[:50]
    updates = void.updates.select_related("author", "hidden_by")
    if not request.user.is_admin:
        updates = updates.filter(hidden=False)
    return render(request, "voids/void_detail.html", {
        "void": void, "trades": trades, "history": history, "updates": updates, "update_form": UpdateForm(),
        "edit_minutes": VoidUpdate.EDIT_MINUTES,
        "edit_cutoff": timezone.now() - timedelta(minutes=VoidUpdate.EDIT_MINUTES),
        "trade_supervisors": services.trade_supervisors(void)})


def _save_void(request, form, void_before=None):
    void = form.save(commit=False)
    services.apply_postcode(void)
    is_new = void.pk is None
    void.save()
    if is_new:
        record(request.user, "void.create", void, f"Created void {void}")
    else:
        record(request.user, "void.edit", void, f"Edited void {void}",
               services.changed_fields(void_before, void, [f if f not in ("job_type", "area", "supervisor")
                                                           else f + "_id" for f in VOID_FIELDS]))
        if void.on_hold != void_before.on_hold:
            services.add_update(void, request.user, VoidUpdate.Kind.STATUS,
                                "Put on hold" if void.on_hold else "Taken off hold")
        elif void.status != void_before.status:
            services.add_update(void, request.user, VoidUpdate.Kind.STATUS, f"Status → {void.get_status_display()}")
    services.refresh_status(void, request.user)
    return void


@login_required
def void_new(request):
    form = VoidForm(request.POST or None, initial={"job_type": JobType.objects.first(), "status": Void.Status.NEW})
    if request.method == "POST" and form.is_valid():
        void = _save_void(request, form)
        messages.success(request, f"Void created: {void}.")
        return redirect("voids:detail", void.pk)
    return render(request, "voids/void_form.html", {"form": form, "void": None})


@login_required
def void_edit(request, void_id):
    void = get_object_or_404(Void, pk=void_id)
    before = Void.objects.get(pk=void.pk)
    form = VoidForm(request.POST or None, instance=void)
    if request.method == "POST" and form.is_valid():
        _save_void(request, form, before)
        messages.success(request, "Saved.")
        return redirect("voids:detail", void.pk)
    return render(request, "voids/void_form.html", {"form": form, "void": void})


@login_required
def task_cell(request, void_id, trade_id):
    """Edit one trade cell in place in the grid (or on the void page without JavaScript)."""
    void = get_object_or_404(Void, pk=void_id)
    trade = get_object_or_404(Trade, pk=trade_id)
    task = TradeTask.objects.filter(void=void, trade=trade).first() or TradeTask(void=void, trade=trade)
    in_grid = request.headers.get("HX-Request") == "true"
    in_tile = in_grid and request.GET.get("in") == "tile"
    if request.method == "POST":
        if "cancel" in request.POST:
            return _cell_response(request, void, trade, in_grid, in_tile)
        if "remove" in request.POST:
            if task.pk:
                task.delete()
                record(request.user, "void.trade", void, f"{trade} removed from the job")
                services.add_update(void, request.user, VoidUpdate.Kind.TRADE, f"{trade} removed from the job")
                services.refresh_status(void, request.user)
            return _cell_response(request, void, trade, in_grid, in_tile)
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            before = TradeTask.objects.filter(pk=task.pk).first()
            task = form.save()
            record(request.user, "void.trade", void, f"{trade}: {task.get_status_display()} {task.display}"[:500],
                   services.changed_fields(before, task, ["status", "assignee", "date", "claimed", "notes"])
                   if before else {})
            changes = services.changed_fields(before, task, ["status", "assignee", "date", "claimed", "notes"]) \
                if before else {"status": {}}
            comment = form.cleaned_data.get("comment", "").strip()
            if changes or comment:
                text = services.trade_update_text(task) if changes else trade.name
                services.add_update(void, request.user, VoidUpdate.Kind.TRADE,
                                    f"{text}: {comment}" if comment else text)
            services.refresh_status(void, request.user)
            return _cell_response(request, void, trade, in_grid, in_tile)
    else:
        form = TaskForm(instance=task)
    if in_grid:
        trades = list(Trade.objects.filter(active=True))
        void = with_related(Void.objects.filter(pk=void.pk)).get()
        tpl = "voids/_tile.html" if in_tile else "voids/_row.html"
        return render(request, tpl, {"row": grid_rows([void], trades)[0], "editing": trade.pk,
                                     "form": form, "open": True})
    return render(request, "voids/task_edit.html", {"form": form, "void": void, "trade": trade, "task": task})


def _cell_response(request, void, trade, in_grid, in_tile=False):
    if not in_grid:
        return redirect("voids:detail", void.pk)
    void = with_related(Void.objects.filter(pk=void.pk)).get()
    trades = list(Trade.objects.filter(active=True))
    tpl = "voids/_tile.html" if in_tile else "voids/_row.html"
    return render(request, tpl, {"row": grid_rows([void], trades)[0], "open": True})


@login_required
@require_POST
def add_trade(request, void_id):
    """Put another trade on the job. It starts as STC until someone is assigned."""
    void = get_object_or_404(Void, pk=void_id)
    trade = get_object_or_404(Trade, pk=request.POST.get("trade") or 0)
    task, created = TradeTask.objects.get_or_create(void=void, trade=trade)
    if not created and task.status == TradeTask.Status.NOT_REQUIRED:
        task.status = TradeTask.Status.STC
        task.save(update_fields=["status"])
        created = True
    if created:
        record(request.user, "void.trade", void, f"{trade} added to the job")
        services.add_update(void, request.user, VoidUpdate.Kind.TRADE, f"{trade} added to the job (STC)")
        services.refresh_status(void, request.user)
    if request.headers.get("HX-Request") == "true":
        v = with_related(Void.objects.filter(pk=void.pk)).get()
        return render(request, "voids/_tile.html", {
            "row": grid_rows([v], list(Trade.objects.filter(active=True)))[0], "open": True})
    return redirect("voids:detail", void.pk)


@login_required
@require_POST
def clear_issue(request, void_id):
    void = get_object_or_404(Void, pk=void_id)
    if void.issue_flag:
        note = void.issue_note
        void.issue_flag, void.issue_note = False, ""
        void.save(update_fields=["issue_flag", "issue_note", "updated_at"])
        services.add_update(void, request.user, VoidUpdate.Kind.STATUS,
                            f"Issue cleared{': ' + note if note else ''}"[:500])
    if request.headers.get("HX-Request") == "true":
        v = with_related(Void.objects.filter(pk=void.pk)).get()
        return render(request, "voids/_tile.html", {
            "row": grid_rows([v], list(Trade.objects.filter(active=True)))[0], "open": True})
    return redirect("voids:detail", void.pk)


@login_required
@require_POST
def add_update(request, void_id):
    """Add a job diary entry, from a tile (returns the tile) or the void page."""
    void = get_object_or_404(Void, pk=void_id)
    form = UpdateForm(request.POST)
    in_tile = request.headers.get("HX-Request") == "true"
    if form.is_valid():
        services.add_update(void, request.user, form.cleaned_data["kind"], form.cleaned_data["text"])
        if not in_tile:
            messages.success(request, "Update added.")
    elif not in_tile:
        messages.error(request, "Write something before adding the update.")
    if in_tile:
        v = with_related(Void.objects.filter(pk=void.pk)).get()
        row = grid_rows([v], list(Trade.objects.filter(active=True)))[0]
        if not form.is_valid():
            row["update_form"] = form
        return render(request, "voids/_tile.html", {"row": row, "open": True})
    return redirect("voids:detail", void.pk)


@login_required
def edit_update(request, update_id):
    upd = get_object_or_404(VoidUpdate, pk=update_id)
    if not upd.can_edit(request.user):
        messages.error(request, f"Updates can only be changed by the person who wrote them, within "
                                f"{VoidUpdate.EDIT_MINUTES} minutes.")
        return redirect("voids:detail", upd.void_id)
    form = UpdateForm(request.POST or None, initial={"kind": upd.kind, "text": upd.text})
    if request.method == "POST" and form.is_valid():
        old = upd.text
        upd.kind, upd.text, upd.edited_at = form.cleaned_data["kind"], form.cleaned_data["text"], timezone.now()
        upd.save()
        record(request.user, "void.update_edit", upd.void, "Edited a diary update",
               {"text": {"from": old, "to": upd.text}})
        return redirect("voids:detail", upd.void_id)
    return render(request, "voids/update_edit.html", {"form": form, "upd": upd})


@admin_required
@require_POST
def hide_update(request, update_id):
    """Admins hide (never delete) a diary entry, e.g. one added to the wrong void."""
    upd = get_object_or_404(VoidUpdate, pk=update_id)
    upd.hidden = not upd.hidden
    upd.hidden_by = request.user if upd.hidden else None
    upd.save(update_fields=["hidden", "hidden_by"])
    record(request.user, "void.update_hide", upd.void,
           f"{'Hid' if upd.hidden else 'Restored'} diary update: {upd.text[:80]}")
    return redirect("voids:detail", upd.void_id)


STATUS_FILLS = {"in_progress": "FFF4B183", "snagging": "FFFFF2A8", "complete": "FFC6E8C6", "new": "FFE3DAF5",
                "handed_back": "FFDDDDDD"}
TASK_FILLS = {"complete": "FFC6E8C6", "in_progress": "FFF4B183", "snagging": "FFFFF2A8",
              "booked": "FFCFE2F3"}


@login_required
def export(request):
    _, voids, _, _ = filtered_voids(request)
    trades = list(Trade.objects.filter(active=True))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Voids"
    heads = ["Job type", "Status", "Post Code", "Supervisor", "Area", "SIMPRO Number", "Address", "Access",
             "Hand-back date", "Expected Completion", "Note"] + [t.name for t in trades] + ["Last Inspection Date"]
    ws.append(heads)
    for c in ws[1]:
        c.font = Font(bold=True)
    for v in voids:
        tasks = {t.trade_id: t for t in v.tasks.all()}
        ws.append([v.job_type.name, v.get_status_display(), v.postcode, str(v.supervisor or ""), str(v.area or ""),
                   v.simpro, v.address, v.access, v.handback_date, v.expected_completion, v.status_note]
                  + [tasks[t.pk].display if t.pk in tasks else "" for t in trades] + [v.last_inspection])
        r = ws.max_row
        if v.status in STATUS_FILLS:
            ws.cell(r, 7).fill = PatternFill("solid", fgColor=STATUS_FILLS[v.status])
        for i, t in enumerate(trades):
            task = tasks.get(t.pk)
            if task:
                c = ws.cell(r, 12 + i)
                if task.status in TASK_FILLS:
                    c.fill = PatternFill("solid", fgColor=TASK_FILLS[task.status])
                if task.claimed:
                    c.font = Font(color="FFFF0000")
    for col, width in zip("ABCDEFGHIJK", [12, 14, 10, 12, 6, 12, 34, 14, 12, 12, 24]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "H2"
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="voids-{timezone.localdate():%Y-%m-%d}.xlsx"'
    return resp


@admin_required
def import_start(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            run = importer.preview_upload(upload.name, upload.read(), request.user)
        except importer.TrackerError as exc:
            form.add_error("file", str(exc))
        else:
            return redirect("voids:import_detail", run.pk)
    return render(request, "voids/import_start.html", {
        "form": form, "runs": VoidImportRun.objects.select_related("uploaded_by")[:15],
        "job_types": JobType.objects.exclude(sheet_name="")})


@admin_required
def import_detail(request, run_id):
    run = get_object_or_404(VoidImportRun, pk=run_id)
    return render(request, "voids/import_report.html", {"run": run, "r": run.report, "rows": run.rows[:500]})


@admin_required
@require_POST
def import_apply(request, run_id):
    run = get_object_or_404(VoidImportRun, pk=run_id)
    try:
        created, updated = importer.apply_run(run, request.user)
    except importer.TrackerError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Import applied: {created} new voids, {updated} updated.")
    return redirect("voids:import_detail", run.pk)


@admin_required
@require_POST
def import_discard(request, run_id):
    run = get_object_or_404(VoidImportRun, pk=run_id, status=VoidImportRun.Status.PREVIEW)
    run.status = VoidImportRun.Status.DISCARDED
    run.save(update_fields=["status"])
    messages.success(request, "Import discarded. Nothing was changed.")
    return redirect("voids:import")
