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
from .models import Area, JobType, Supervisor, VoidsSettings, Trade, TradeTask, Void, VoidImportRun, VoidUpdate

SESSION_KEY = "voids_filters"
VOID_FIELDS = list(VoidForm.Meta.fields)


def with_related(qs):
    return qs.select_related("job_type", "area", "supervisor", "final_checked_by").prefetch_related(
        Prefetch("tasks", queryset=TradeTask.objects.select_related("trade", "updated_by")),
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
        params = QueryDict(request.session.get(SESSION_KEY, ""), mutable=True)
        own = getattr(request.user, "supervisor", None) if SESSION_KEY not in request.session else None
        if own is not None:
            params["supervisor"] = str(own.pk)  # a supervisor starts on their own voids
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
    if f.get("job") and f.get("flag") != "electrical":  # Electrical covers every job type
        qs = qs.filter(job_type=f["job"])
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
             "start": ["start_date", "handback_date", "address"],"handback": ["handback_date", "address"], "address": ["address"],
             "simpro": ["simpro"], "area": ["area__code", "address"], "supervisor": ["supervisor__name", "address"],
             "postcode": ["postcode"], "job": ["job_type__position", "address"]}.get(sort, ["handback_date"])
    qs = qs.order_by(*order)
    voids = list(qs)
    for v in voids:
        v._thresholds = th  # read the warning thresholds once, not once per void
    if sort == "simpro":
        voids.sort(key=services.simpro_number)
    elif sort in ("handback", "start"):
        # Voids without the date go last.
        field = {"handback": "handback_date", "start": "start_date"}[sort]
        voids.sort(key=lambda v: getattr(v, field) is None)
    if f.get("flag") == "stale_amber":
        voids = [v for v in voids if v.stale_level]
    elif f.get("flag") == "stale_red":
        voids = [v for v in voids if v.stale_level == "red"]
    # Area and supervisor buttons show counts, so those two filters are applied here:
    # each set of counts respects the other filter but not its own.
    sups = list(Supervisor.objects.filter(active=True).select_related("trade"))
    area = f.get("area")
    sup = f.get("supervisor")
    # Area and supervisor are alternative ways in: a view is by one or the other, never both.
    area_counts = {}
    for v in voids:
        area_counts[v.area_id] = area_counts.get(v.area_id, 0) + 1
    sup_counts = {s.pk: sum(1 for v in voids if services.supervises(s, v)) for s in sups}
    sup_counts[None] = len(voids)
    if sup:
        voids = [v for v in voids if services.supervises(sup, v)]
    elif area:
        voids = [v for v in voids if v.area_id == area.pk]
    return form, voids, params, {"areas": area_counts, "sups": sup_counts, "sup_list": sups}


# The status buttons on each trade, in workflow order.
QUICK_STATUSES = [(TradeTask.Status.STC, "STC"),
                  (TradeTask.Status.IN_PROGRESS, "In progress"), (TradeTask.Status.SNAGGING, "Snagging"),
                  (TradeTask.Status.COMPLETE, "Complete")]

STALE_STATUSES = [Void.Status.NEW, Void.Status.STC, Void.Status.IN_PROGRESS, Void.Status.SNAGGING]

OUTSTANDING = [TradeTask.Status.STC, TradeTask.Status.IN_PROGRESS,
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
                     "can_sign_off": services.can_sign_off(v),
                     "status_choices": QUICK_STATUSES,
                     "electrical": next((t for trade, t in on_job if trade.name.lower() == "electrical"), None),
                     "claimable": sum(1 for _, t in on_job if t.status == TradeTask.Status.COMPLETE and not t.claimed),
                     "percent": round(100 * done / len(needed)) if needed else 0,
                     "overdue": bool(v.handback_date and v.handback_date < today
                                     and v.status != Void.Status.HANDED_BACK),
                     "this_week": bool(v.handback_date and today <= v.handback_date <= today + soon)})
    return rows


@login_required
def void_list(request):
    form, voids, params, counts = filtered_voids(request)
    area_counts = counts["areas"]
    trades = list(Trade.objects.filter(active=True))
    current_job = params.get("job", "")
    job_links = []
    for jt in [None, *JobType.objects.all()]:
        p = params.copy()
        p["job"] = str(jt.pk) if jt else ""
        if p.get("flag") == "electrical":
            p["flag"] = ""  # Electrical is its own button across all job types
        job_links.append({"label": jt.name if jt else "All", "query": p.urlencode(),
                          "active": (str(jt.pk) if jt else "") == current_job
                          and params.get("flag") != "electrical"})
    current_sup = params.get("supervisor", "")
    current_area = "" if current_sup else params.get("area", "")
    area_links = []
    for a in [None, *Area.objects.all()]:
        p = params.copy()
        p["area"] = str(a.pk) if a else ""
        p["supervisor"] = ""  # by area OR by supervisor, not both
        area_links.append({"label": a.code if a else "All properties",
                           "count": sum(area_counts.values()) if a is None else area_counts.get(a.pk, 0),
                           "query": p.urlencode(),
                           "active": (str(a.pk) if a else "") == current_area and (a or not current_sup)})
    unassigned = area_counts.get(None, 0)
    sup_links = []
    for sp in counts["sup_list"]:
        p = params.copy()
        p["supervisor"] = str(sp.pk)
        p["area"] = ""
        sup_links.append({"label": (f"{sp.name} ({sp.trade.name})" if sp and sp.trade_id else sp.name) if sp
                          else "All supervisors", "count": counts["sups"].get(sp.pk, 0),
                          "query": p.urlencode(), "active": str(sp.pk) == current_sup})
    p = params.copy()
    elec_on = params.get("flag") == "electrical"
    p["flag"] = "" if elec_on else "electrical"
    p["job"] = ""
    electrical_link = {"query": p.urlencode(), "active": elec_on}
    p = params.copy()
    done_on = params.get("status") == Void.Status.HANDED_BACK
    p["status"] = "" if done_on else Void.Status.HANDED_BACK
    completed_link = {"query": p.urlencode(), "active": done_on}
    current_sort = params.get("sort", "")
    sort_toggle = []
    for value, label in (("", "Job number"), ("handback", "Hand-back date")):
        p = params.copy()
        p["sort"] = value
        sort_toggle.append({"label": label, "query": p.urlencode(), "active": value == current_sort})
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
    form.fields["supervisor"].widget = form.fields["supervisor"].hidden_widget()
    return render(request, "voids/void_list.html", {
        "area_links": area_links, "unassigned": unassigned, "view": view,
        "electrical_link": electrical_link, "sort_options": sort_options, "sort_toggle": sort_toggle, "sup_links": sup_links,
        "completed_link": completed_link, "electrical_mode": params.get("flag") == "electrical",
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
        form = TaskForm(request.POST, instance=task, auto_id=_task_ids(void, trade))
        if form.is_valid():
            before = TradeTask.objects.filter(pk=task.pk).first()
            task = form.save(commit=False)
            if before and form.cleaned_data.get("date") == before.date:
                services.auto_date(task, before.status)  # unless a date was typed in
            fields = ["status", "assignee", "date", "claimed", "notes"]
            changes = services.changed_fields(before, task, fields) if before else {"status": {}}
            comment = form.cleaned_data.get("comment", "").strip()
            if changes or comment:
                task.updated_by, task.updated_at = request.user, timezone.now()
                task.save()
                record(request.user, "void.trade", void, f"{trade}: {task.get_status_display()} {task.display}"[:500],
                       changes)
                services.log_trade_change(void, request.user, task, comment)
                # A trade update by a supervisor means they have been on top of the job: record it as the
                # last inspection.
                if "status" in changes or not before:
                    Void.objects.filter(pk=void.pk).update(last_inspection=timezone.localdate())
                services.refresh_status(void, request.user)
            if "autosave" in request.POST and in_tile:
                # Saved as you go: keep the editor open with the saved values.
                return _tile(request, void, editing=trade.pk,
                             form=TaskForm(instance=task, auto_id=_task_ids(void, trade)), saved=bool(changes or comment))
            return _cell_response(request, void, trade, in_grid, in_tile)
    else:
        form = TaskForm(instance=task, auto_id=_task_ids(void, trade))
    if in_tile:
        return _tile(request, void, editing=trade.pk, form=form)
    if in_grid:
        trades = list(Trade.objects.filter(active=True))
        void = with_related(Void.objects.filter(pk=void.pk)).get()
        return render(request, "voids/_row.html", {"row": grid_rows([void], trades)[0], "editing": trade.pk,
                                                    "form": form, "open": True})
    return render(request, "voids/task_edit.html", {"form": form, "void": void, "trade": trade, "task": task})


def _task_ids(void, trade):
    # Stable ids so the cursor stays in the same box after each automatic save.
    return f"t{void.pk}-{trade.pk}-%s"


def _tile(request, void, **extra):
    v = with_related(Void.objects.filter(pk=void.pk)).get()
    row = grid_rows([v], list(Trade.objects.filter(active=True)))[0]
    saved = QueryDict(request.session.get(SESSION_KEY, ""))
    return render(request, "voids/_tile.html", {"row": row, "open": True,
                                                "electrical_mode": saved.get("flag") == "electrical", **extra})


@login_required
@require_POST
def set_task_status(request, void_id, trade_id):
    """One tap on a trade's status button."""
    void = get_object_or_404(Void, pk=void_id)
    task = get_object_or_404(TradeTask, void=void, trade_id=trade_id)
    status = request.POST.get("status")
    if status == TradeTask.Status.IN_PROGRESS and not task.assignee:
        # Ask who is doing it before setting In progress: open the editor with the status chosen.
        form = TaskForm(instance=task, initial={"status": status, "date": timezone.localdate()},
                        auto_id=_task_ids(void, task.trade))
        return _tile(request, void, editing=task.trade_id, form=form, ask_who=True)
    if status in TradeTask.Status.values and status != task.status:
        old = task.status
        task.status = status
        services.auto_date(task, old)
        task.updated_by, task.updated_at = request.user, timezone.now()
        task.save(update_fields=["status", "date", "updated_by", "updated_at"])
        record(request.user, "void.trade", void, f"{task.trade}: {task.get_status_display()}",
               {"status": {"from": old, "to": status}})
        services.log_trade_change(void, request.user, task, "")
        Void.objects.filter(pk=void.pk).update(last_inspection=timezone.localdate())
        services.refresh_status(void, request.user)
    return _after_action(request, void)


@login_required
@require_POST
def toggle_claim(request, void_id, trade_id):
    """One click: mark a trade claimed for payment (or undo it)."""
    void = get_object_or_404(Void, pk=void_id)
    task = get_object_or_404(TradeTask, void=void, trade_id=trade_id)
    task.claimed = not task.claimed
    task.updated_by, task.updated_at = request.user, timezone.now()
    task.save(update_fields=["claimed", "updated_by", "updated_at"])
    record(request.user, "void.trade", void, f"{task.trade}: {'claimed' if task.claimed else 'claim removed'}",
           {"claimed": {"from": not task.claimed, "to": task.claimed}})
    services.log_trade_change(void, request.user, task, "")
    return _after_action(request, void)


@login_required
@require_POST
def claim_complete(request, void_id):
    """Mark every complete trade on the job as claimed."""
    void = get_object_or_404(Void, pk=void_id)
    names = []
    for task in void.tasks.select_related("trade").filter(status=TradeTask.Status.COMPLETE, claimed=False):
        task.claimed = True
        task.updated_by, task.updated_at = request.user, timezone.now()
        task.save(update_fields=["claimed", "updated_by", "updated_at"])
        names.append(task.trade.name)
    if names:
        record(request.user, "void.trade", void, f"Claimed: {', '.join(names)}")
        services.add_update(void, request.user, VoidUpdate.Kind.TRADE, f"Claimed: {', '.join(names)}")
    return _after_action(request, void)


@login_required
@require_POST
def final_check(request, void_id):
    void = get_object_or_404(Void, pk=void_id)
    if "reopen" in request.POST:
        services.reopen(void, request.user)
        messages.success(request, f"{void.address} reopened.")
    elif void.status != Void.Status.HANDED_BACK:
        try:
            services.confirm_final_check(void, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Final check confirmed: {void.address} moved to Completed.")
    return _after_action(request, void)


def _after_action(request, void):
    if request.headers.get("HX-Request") == "true":
        return _tile(request, void)
    return redirect("voids:detail", void.pk)


def _cell_response(request, void, trade, in_grid, in_tile=False):
    if not in_grid:
        return redirect("voids:detail", void.pk)
    if in_tile:
        return _tile(request, void)
    void = with_related(Void.objects.filter(pk=void.pk)).get()
    trades = list(Trade.objects.filter(active=True))
    return render(request, "voids/_row.html", {"row": grid_rows([void], trades)[0], "open": True})


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
    return _after_action(request, void)


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
    return _after_action(request, void)


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
        return _tile(request, void, **({} if form.is_valid() else {"update_form_errors": form}))
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
              }


@login_required
def export(request):
    _, voids, _, _ = filtered_voids(request)
    trades = list(Trade.objects.filter(active=True))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Voids"
    heads = ["Job type", "Status", "Post Code", "Supervisor", "Area", "SIMPRO Number", "Address", "Access",
             "Hand-back date", "Note"] + [t.name for t in trades] + ["Last Inspection Date"]
    ws.append(heads)
    for c in ws[1]:
        c.font = Font(bold=True)
    for v in voids:
        tasks = {t.trade_id: t for t in v.tasks.all()}
        ws.append([v.job_type.name, v.get_status_display(), v.postcode, str(v.supervisor or ""), str(v.area or ""),
                   v.simpro, v.address, v.access, v.handback_date, v.status_note]
                  + [tasks[t.pk].display if t.pk in tasks else "" for t in trades] + [v.last_inspection])
        r = ws.max_row
        if v.status in STATUS_FILLS:
            ws.cell(r, 7).fill = PatternFill("solid", fgColor=STATUS_FILLS[v.status])
        for i, t in enumerate(trades):
            task = tasks.get(t.pk)
            if task:
                c = ws.cell(r, 11 + i)
                if task.status in TASK_FILLS:
                    c.fill = PatternFill("solid", fgColor=TASK_FILLS[task.status])
                if task.claimed:
                    c.font = Font(color="FFFF0000")
    for col, width in zip("ABCDEFGHIJ", [12, 14, 10, 12, 6, 12, 34, 14, 12, 24]):
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


@login_required
def overview(request):
    from .overview import build
    job = JobType.objects.filter(pk=request.GET.get("job") or 0).first()
    return render(request, "voids/overview.html", build(job))
