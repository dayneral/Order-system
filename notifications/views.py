from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from audit.models import record

from . import sending
from .models import OrderEmail


@admin_required
def failed_list(request):
    return render(request, "notifications/failed_list.html", {
        "failed": OrderEmail.objects.filter(status=OrderEmail.Status.FAILED).select_related("order"),
        "recent": OrderEmail.objects.exclude(status=OrderEmail.Status.FAILED).select_related("order")[:30],
    })


@admin_required
@require_POST
def retry(request, email_id):
    log = get_object_or_404(OrderEmail, pk=email_id)
    try:
        ok = sending.retry(log)
    except sending.CannotRetry as exc:
        messages.error(request, str(exc))
        return redirect("notifications:failed")
    record(request.user, "email.retry", log.order, f"Retried {log.get_kind_display().lower()} email for "
           f"{log.order.order_number}: {'sent' if ok else 'failed again'}")
    if ok:
        messages.success(request, f"Email for {log.order.order_number} sent to {log.recipient}.")
    else:
        messages.error(request, f"Sending failed again: {log.last_error}")
    return redirect("notifications:failed")
