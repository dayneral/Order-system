from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from audit.models import AuditEntry
from catalogue.models import Item, Section

from . import services
from .forms import OrderHeaderForm
from .models import Order, OrderLine
from .services import OrderError

ITEM_RESULTS_LIMIT = 150


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _get_order(request, order_id, edit=False):
    order = get_object_or_404(Order, pk=order_id)
    if not services.can_view(order, request.user):
        raise Http404  # drafts of other users are invisible
    if edit and not services.can_edit(order, request.user):
        raise PermissionDenied
    return order


# --- Lists ---------------------------------------------------------------------

VIEWS = {
    "mine": "My Orders",
    "all": "All Orders",
    "historic": "Historic Orders",
}


@login_required
def order_list(request):
    view = request.GET.get("view", "mine")
    if view not in VIEWS:
        view = "mine"
    q = request.GET.get("q", "").strip()

    orders = Order.objects.exclude(status=Order.Status.DRAFT).annotate(line_count=Count("lines"))
    if view == "historic":
        orders = orders.filter(anonymised_at__isnull=False)
        if q:
            orders = orders.filter(Q(order_number__icontains=q) | Q(anonymous_id__icontains=q))
    else:
        orders = orders.filter(anonymised_at__isnull=True)
        if view == "mine":
            orders = orders.filter(owner=request.user)
        if q:
            orders = orders.filter(Q(order_number__icontains=q) | Q(job_number__icontains=q)
                                   | Q(property_address__icontains=q) | Q(requester_name__icontains=q))
    page = Paginator(orders.order_by("-submitted_at"), 50).get_page(request.GET.get("page"))

    drafts = []
    if view == "mine":
        drafts = list(Order.objects.filter(owner=request.user, status=Order.Status.DRAFT)
                      .annotate(line_count=Count("lines")).order_by("-updated_at"))
        for draft in drafts:
            draft.expires = services.draft_expiry(draft)
    return render(request, "orders/order_list.html", {
        "view": view, "views": VIEWS, "q": q, "page": page, "drafts": drafts,
    })


# --- Create and edit -------------------------------------------------------------

@login_required
def order_new(request):
    if request.method == "POST":
        form = OrderHeaderForm(request.POST)
        if form.is_valid():
            order = services.create_draft(request.user, **form.cleaned_data)
            other = services.active_order_for_job(order.job_number_key)
            if other:
                messages.warning(request, services._job_clash_message(other))
            return redirect("orders:edit", order.pk)
    else:
        form = OrderHeaderForm()
    return render(request, "orders/order_new.html", {"form": form})


def _item_browser(request):
    sections = list(Section.objects.all())
    q = request.GET.get("q", "").strip()
    section_key = request.GET.get("section") or (sections[0].key if sections else None)
    items = Item.objects.filter(is_active=True).select_related("section")
    if q:
        terms = q.split()
        for term in terms:
            items = items.filter(Q(part_no__icontains=term) | Q(display_name__icontains=term))
        section_key = None
    else:
        items = items.filter(section_id=section_key)
    items = list(items.order_by("section__sort_order", "display_name")[:ITEM_RESULTS_LIMIT + 1])
    return {
        "sections": sections,
        "current_section": section_key,
        "q": q,
        "items": items[:ITEM_RESULTS_LIMIT],
        "more_items": len(items) > ITEM_RESULTS_LIMIT,
    }


def _lines_context(order, error=None, notice=None):
    return {"order": order, "lines": list(order.lines.all()), "line_error": error, "line_notice": notice}


@login_required
def order_edit(request, order_id):
    order = _get_order(request, order_id, edit=True)
    errors = []
    if request.method == "POST":
        form = OrderHeaderForm(request.POST)
        action = request.POST.get("action", "save")
        if form.is_valid():
            try:
                services.update_header(order, request.user, form.cleaned_data)
                if action == "submit":
                    services.submit(order, request.user)
                    messages.success(request, f"Order {order.order_number} submitted.")
                    return redirect("orders:detail", order.pk)
                if action == "send_amendment":
                    services.finish_amendment(order, request.user)
                    messages.success(request, f"Amended order {order.order_number} sent to stores.")
                    return redirect("orders:detail", order.pk)
                messages.success(request, "Draft saved." if order.is_draft else "Changes saved.")
                return redirect("orders:edit", order.pk)
            except OrderError as exc:
                errors = exc.errors
                order.refresh_from_db()
    else:
        form = OrderHeaderForm(instance=order)
    context = {"order": order, "form": form, "errors": errors, **_item_browser(request), **_lines_context(order)}
    return render(request, "orders/order_edit.html", context)


@login_required
def item_browser(request, order_id):
    order = _get_order(request, order_id, edit=True)
    return render(request, "orders/_items.html", {"order": order, **_item_browser(request)})


def _lines_response(request, order, error=None, notice=None):
    if _is_htmx(request):
        order.refresh_from_db()
        return render(request, "orders/_lines.html", _lines_context(order, error, notice))
    if error:
        messages.error(request, error)
    elif notice:
        messages.success(request, notice)
    return redirect(reverse("orders:edit", args=[order.pk]) + "#lines")


@login_required
@require_POST
def line_add(request, order_id):
    order = _get_order(request, order_id, edit=True)
    item = get_object_or_404(Item, pk=request.POST.get("item_id"))
    try:
        line = services.add_item_line(order, request.user, item, request.POST)
    except OrderError as exc:
        return _lines_response(request, order, error=f"{item.part_no}: {exc}")
    return _lines_response(request, order, notice=f"Added {line.part_no} {line.name}.")


@login_required
@require_POST
def line_add_non_stocked(request, order_id):
    order = _get_order(request, order_id, edit=True)
    try:
        line = services.add_non_stocked_line(order, request.user, request.POST.get("name"),
                                             request.POST.get("unit"), request.POST.get("quantity"))
    except OrderError as exc:
        return _lines_response(request, order, error=str(exc))
    return _lines_response(request, order, notice=f"Added non-stocked item {line.name}.")


@login_required
@require_POST
def line_update(request, order_id, line_id):
    order = _get_order(request, order_id, edit=True)
    line = get_object_or_404(OrderLine, pk=line_id, order=order)
    try:
        services.update_line(line, request.user, request.POST)
    except OrderError as exc:
        return _lines_response(request, order, error=f"{line.part_no or line.name}: {exc}")
    return _lines_response(request, order)


@login_required
@require_POST
def line_remove(request, order_id, line_id):
    order = _get_order(request, order_id, edit=True)
    line = get_object_or_404(OrderLine, pk=line_id, order=order)
    try:
        services.remove_line(line, request.user)
    except OrderError as exc:
        return _lines_response(request, order, error=str(exc))
    return _lines_response(request, order)


# --- View, cancel, delete --------------------------------------------------------------

@login_required
def order_detail(request, order_id):
    order = _get_order(request, order_id)
    history = []
    if not order.is_draft:
        history = AuditEntry.objects.filter(target_type="orders.order", target_id=str(order.pk)).select_related("actor")
    return render(request, "orders/order_detail.html", {
        "order": order,
        "lines": list(order.lines.all()),
        "history": history,
        "can_edit": services.can_edit(order, request.user),
        "can_cancel": services.can_cancel(order, request.user),
    })


@login_required
def order_cancel(request, order_id):
    order = _get_order(request, order_id)
    if not services.can_cancel(order, request.user):
        raise PermissionDenied
    if request.method == "POST":
        services.cancel(order, request.user, request.POST.get("reason", ""))
        messages.success(request, f"Order {order.order_number} cancelled. Stores will be told.")
        return redirect("orders:detail", order.pk)
    return render(request, "orders/order_cancel.html", {"order": order})


@login_required
@require_POST
def draft_delete(request, order_id):
    order = _get_order(request, order_id)
    try:
        services.delete_draft(order, request.user)
    except OrderError as exc:
        messages.error(request, str(exc))
        return redirect("orders:list")
    messages.success(request, "Draft deleted.")
    return redirect("orders:list")


@login_required
def home(request):
    return redirect("orders:list")

