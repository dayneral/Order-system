from types import SimpleNamespace
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from audit.models import AuditEntry
from catalogue.models import Item, Kit, Section, Subsection

from . import services, suggestions
from notifications import documents
from notifications.models import OrderEmail

from .forms import OrderHeaderForm
from .models import Order, OrderLine
from .services import OrderError

ITEM_RESULTS_LIMIT = 150


def _report_email(request, order, success_text):
    """Tell the user whether stores were emailed. The order is saved either way."""
    email = order.emails.order_by("-created_at").first()
    if email is None or email.status == OrderEmail.Status.SENT:
        messages.success(request, success_text)
    else:
        messages.warning(
            request,
            f"{order.order_number} has been saved, but the email to stores could NOT be sent. "
            "It is listed for an admin to resend. If it is urgent, phone stores, or print this order "
            "and pass it on.",
        )


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
}


@login_required
def order_list(request):
    view = request.GET.get("view", "mine")
    if view not in VIEWS:
        view = "mine"
    q = request.GET.get("q", "").strip()

    orders = Order.objects.exclude(status=Order.Status.DRAFT).annotate(line_count=Count("lines"))
    if view == "mine":
        orders = orders.filter(owner=request.user)
    if q:
        orders = orders.filter(Q(order_number__icontains=q) | Q(job_number__icontains=q)
                               | Q(property_address__icontains=q) | Q(requester_name__icontains=q))
    page = Paginator(orders.order_by("-submitted_at"), 50).get_page(request.GET.get("page"))

    drafts = []
    if view == "mine":
        drafts = list(services.started_drafts(request.user).annotate(line_count=Count("lines"))
                      .order_by("-updated_at"))
        for draft in drafts:
            draft.expires = services.draft_expiry(draft)
    return render(request, "orders/order_list.html", {
        "view": view, "views": VIEWS, "q": q, "page": page, "drafts": drafts,
    })


# --- Create and edit -------------------------------------------------------------

@login_required
def order_new(request):
    """Open the order page straight away. Reuses the user's untouched empty draft, if any."""
    order = services.empty_draft_for(request.user) or services.create_draft(request.user)
    return redirect("orders:edit", order.pk)


def _item_browser(request, order):
    """Item list for the order page.

    Inactive sections and subsections (and their items) are left out of the normal
    tabs and search. Admins get one extra "Hidden" tab listing them, with each hidden
    section / subsection as a sub-tab. A section opens on its first subsection;
    "All" is the last option. Kits are listed inside their section/subsection.
    """
    admin = request.user.is_admin
    sections = list(Section.objects.filter(is_active=True))
    visible = Q(section__isnull=True) | Q(section__is_active=True)
    visible &= Q(subsection__isnull=True) | Q(subsection__is_active=True)
    all_items = Item.objects.filter(is_active=True).select_related("section", "subsection")
    all_kits = Kit.objects.filter(is_active=True)
    items, kits = all_items.filter(visible), all_kits.filter(visible)
    hidden_subs = []
    if admin:
        hidden_subs = [SimpleNamespace(pk=f"s-{sec.key}", name=sec.name, is_active=True)
                       for sec in Section.objects.filter(is_active=False)]
        hidden_subs += [SimpleNamespace(pk=sub.pk, name=f"{sub.section.name} › {sub.name}", is_active=True)
                        for sub in Subsection.objects.filter(is_active=False, section__is_active=True)
                        .select_related("section")]

    q = request.GET.get("q", "").strip()
    section_key = request.GET.get("section") or (sections[0].key if sections else None)
    subsections, current_sub = [], ""
    if q:
        for term in q.split():
            items = items.filter(Q(part_no__icontains=term) | Q(display_name__icontains=term))
            kits = kits.filter(name__icontains=term)
        section_key = None
    elif section_key == "unsectioned":
        items = items.filter(section__isnull=True)
        kits = kits.filter(section__isnull=True)
    elif section_key == "hidden" and admin:
        items, kits = all_items.exclude(visible), all_kits.exclude(visible)
        subsections = hidden_subs
        current_sub = request.GET.get("sub") or (str(subsections[0].pk) if subsections else "all")
        if current_sub.startswith("s-"):
            items = items.filter(section_id=current_sub[2:])
            kits = kits.filter(section_id=current_sub[2:])
        elif current_sub.isdigit():
            items = items.filter(subsection_id=int(current_sub))
            kits = kits.filter(subsection_id=int(current_sub))
    else:
        items = items.filter(section_id=section_key)
        kits = kits.filter(section_id=section_key)
        subsections = list(Subsection.objects.filter(section_id=section_key, is_active=True))
        current_sub = request.GET.get("sub") or (str(subsections[0].pk) if subsections else "all")
        if current_sub != "all" and current_sub.isdigit():
            items = items.filter(subsection_id=int(current_sub))
            kits = kits.filter(subsection_id=int(current_sub))
    items = list(items.order_by("section__sort_order", "display_name")[:ITEM_RESULTS_LIMIT + 1])
    on_order = set(order.lines.exclude(item=None).values_list("item_id", flat=True))
    return {
        "sections": sections,
        "has_hidden": bool(hidden_subs),
        "has_unsectioned": Item.objects.filter(is_active=True, section__isnull=True).exists(),
        "subsections": subsections,
        "current_sub": current_sub,
        "current_section": section_key,
        "q": q,
        "items": items[:ITEM_RESULTS_LIMIT],
        "more_items": len(items) > ITEM_RESULTS_LIMIT,
        "kit_offers": [suggestions.kit_offer(kit, on_order) for kit in kits.prefetch_related("kit_items")],
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
                    _report_email(request, order, f"Order {order.order_number} submitted and sent to stores.")
                    return redirect("orders:detail", order.pk)
                if action == "send_amendment":
                    services.finish_amendment(order, request.user)
                    _report_email(request, order, f"Amended order {order.order_number} sent to stores.")
                    return redirect("orders:detail", order.pk)
                messages.success(request, "Draft saved." if order.is_draft else "Changes saved.")
                if order.is_draft:
                    other = services.active_order_for_job(order.job_number_key)
                    if other:
                        messages.warning(request, services._job_clash_message(other))
                return redirect("orders:edit", order.pk)
            except OrderError as exc:
                errors = exc.errors
                order.refresh_from_db()
    else:
        form = OrderHeaderForm(instance=order)
    context = {"order": order, "form": form, "errors": errors, **_item_browser(request, order), **_lines_context(order)}
    return render(request, "orders/order_edit.html", context)


@login_required
def item_browser(request, order_id):
    order = _get_order(request, order_id, edit=True)
    return render(request, "orders/_items.html", {"order": order, **_item_browser(request, order)})


def _lines_response(request, order, error=None, notice=None, extra=None):
    if _is_htmx(request):
        order.refresh_from_db()
        return render(request, "orders/_lines.html", {**_lines_context(order, error, notice), **(extra or {})})
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
    notice = (f"{line.part_no} {line.name} was already on the order: quantity is now {line.measurement_text}."
              if getattr(line, "merged", False) else f"Added {line.part_no} {line.name}.")
    return _lines_response(request, order, notice=notice,
                           extra=suggestions.after_add(order, item))


@login_required
@require_POST
def kit_add(request, order_id, kit_id):
    order = _get_order(request, order_id, edit=True)
    kit = get_object_or_404(Kit, pk=kit_id, is_active=True)
    try:
        lines = services.add_kit(order, request.user, kit, request.POST)
    except OrderError as exc:
        return _lines_response(request, order, error=f"{kit.name}: " + "; ".join(exc.errors))
    return _lines_response(request, order, notice=f"Added {len(lines)} item{'s' if len(lines) != 1 else ''} "
                                                  f"from {kit.name}.")


@login_required
@require_POST
def line_add_non_stocked(request, order_id):
    order = _get_order(request, order_id, edit=True)
    try:
        line = services.add_non_stocked_line(order, request.user, request.POST.get("name"),
                                             "", request.POST.get("quantity"))
    except OrderError as exc:
        return _lines_response(request, order, error=str(exc))
    return _lines_response(request, order, notice=f"Added other item {line.name}.")


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
        "email_failed": order.emails.filter(status=OrderEmail.Status.FAILED).exists(),
    })


@login_required
def order_cancel(request, order_id):
    order = _get_order(request, order_id)
    if not services.can_cancel(order, request.user):
        raise PermissionDenied
    if request.method == "POST":
        services.cancel(order, request.user, request.POST.get("reason", ""))
        _report_email(request, order, f"Order {order.order_number} cancelled. Stores have been sent a CANCELLED notice.")
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
    user = request.user
    if user.is_authenticated and not user.can_order and user.can_voids:
        return redirect("voids:list")
    return redirect("orders:list")



@login_required
def order_print(request, order_id):
    """Print-friendly page with the same content as the stores email."""
    order = _get_order(request, order_id)
    kind = "cancelled" if order.status == Order.Status.CANCELLED else ("amended" if order.amended_at else "submitted")
    from notifications.sending import last_sent_snapshot

    previous = None
    if kind == "amended":
        # Changes compared with the version stores had before the latest amendment email.
        latest = order.emails.filter(kind="amended", status="sent").order_by("-created_at").first()
        previous = last_sent_snapshot(order, before=latest.created_at if latest else None)
    html = documents.render_to_string_for_print(order, kind, back_url=reverse("orders:detail", args=[order.pk]),
                                                previous=previous)
    return HttpResponse(html)


@login_required
def order_delete(request, order_id):
    """Admins only: delete an order entered by mistake."""
    if not request.user.is_admin:
        raise PermissionDenied
    order = _get_order(request, order_id)
    error = None
    if request.method == "POST":
        was_active = order.status == Order.Status.SUBMITTED
        try:
            number = services.admin_delete(order, request.user, request.POST.get("reason", ""))
        except OrderError as exc:
            error = str(exc)
        else:
            messages.success(request, f"Order {number} deleted." + (
                " Stores have been sent a CANCELLED notice." if was_active else ""))
            return redirect(reverse("orders:list") + "?view=all")
    return render(request, "orders/order_delete.html", {"order": order, "error": error})
