from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required

from . import services
from .models import RetentionRun


@admin_required
def run_list(request):
    today = services.timezone.localdate()
    cutoff, due = services.orders_due(today)
    return render(request, "retention/run_list.html", {
        "runs": RetentionRun.objects.all()[:60],
        "due_now": due.count(),
        "drafts_due_now": services.drafts_due(services.timezone.now()).count(),
        "cutoff": cutoff,
    })


@admin_required
@require_POST
def run_now(request):
    log = services.run(trigger=RetentionRun.Trigger.MANUAL, run_by=request.user.full_name)
    messages.success(request, f"Retention run complete: {log.orders_anonymised} order(s) anonymised, "
                              f"{log.drafts_deleted} old draft(s) deleted.")
    return redirect("retention:runs")
