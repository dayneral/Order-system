"""
The nightly supervisor email: each supervisor gets their open voids that need attention,
with snagging at the top and emphasised. Sent by `python manage.py send_void_digest`.
"""

from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Supervisor, TradeTask, Void, VoidsSettings
from .notify import site_url
from .services import supervises


def snag_trades_for(sup, void, trade_sup_trades):
    """Snagging trades this supervisor should chase. A trade with its own trade supervisor (e.g.
    Electrical → Jamie) goes to that person in every area, not to the void's patch supervisor."""
    snags = [t for t in void.tasks.all() if t.status == TradeTask.Status.SNAGGING]
    if sup.trade_id:
        return [t.trade.name for t in snags if t.trade_id == sup.trade_id]
    return [t.trade.name for t in snags if t.trade_id not in trade_sup_trades]


def sections_for(sup, voids, th, today, trade_sup_trades=None):
    if trade_sup_trades is None:
        trade_sup_trades = set(Supervisor.objects.filter(active=True, trade__isnull=False)
                               .values_list("trade_id", flat=True))
    mine = [v for v in voids if supervises(sup, v)]
    for v in mine:
        v._thresholds = th
        v.link = site_url(reverse("voids:detail", args=[v.pk]))
        v.snag_trades = snag_trades_for(sup, v, trade_sup_trades)
        v.snag_days = (timezone.now() - v.snagging_since).days if v.snagging_since else None
    soon = today + timedelta(days=th.handback_soon_days)
    stale_cutoff = timezone.now() - timedelta(days=th.stale_filter_days)
    snagging = sorted([v for v in mine if v.snag_trades],
                      key=lambda v: v.snagging_since or timezone.now())
    return {
        "snagging": snagging,
        "issues": [v for v in mine if v.issue_flag],
        "final_check": [v for v in mine if v.status == Void.Status.COMPLETE],
        "needs_visit": [v for v in mine if v.status == Void.Status.NEW],
        "handbacks": sorted([v for v in mine if v.handback_date and v.handback_date <= soon],
                            key=lambda v: v.handback_date),
        "stale": sorted([v for v in mine if not v.on_hold and v.last_activity < stale_cutoff
                         and v.status in (Void.Status.NEW, Void.Status.STC, Void.Status.IN_PROGRESS,
                                          Void.Status.SNAGGING)], key=lambda v: v.last_activity),
    }


def recipient(sup):
    return sup.email or (sup.user.email if sup.user_id else "")


def build(sup, sections, today):
    ctx = {"sup": sup, "s": sections, "today": today, "list_url": site_url(reverse("voids:list")),
           "th": VoidsSettings.get()}
    subject = f"Voids update {today:%a %d %b}"
    if sections["snagging"]:
        subject += f" – {len(sections['snagging'])} snagging"
    text = render_to_string("voids/email/digest.txt", ctx)
    html = render_to_string("voids/email/digest.html", ctx)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient(sup)])
    msg.attach_alternative(html, "text/html")
    return msg


def send_all(dry_run=False, only=None):
    """Returns a list of (supervisor, email, counts, sent?) for the report."""
    today = timezone.localdate()
    th = VoidsSettings.get()
    voids = list(Void.objects.exclude(status=Void.Status.HANDED_BACK)
                 .select_related("supervisor", "area", "job_type")
                 .prefetch_related("tasks__trade"))
    report = []
    trade_sup_trades = set(Supervisor.objects.filter(active=True, trade__isnull=False).values_list("trade_id", flat=True))
    for sup in Supervisor.objects.filter(active=True).select_related("trade", "user"):
        if only and sup.name.lower() != only.lower():
            continue
        email = recipient(sup)
        sections = sections_for(sup, voids, th, today, trade_sup_trades)
        counts = {k: len(v) for k, v in sections.items()}
        if not email or not any(counts.values()):
            report.append((sup, email, counts, False))
            continue
        if not dry_run:
            build(sup, sections, today).send()
        report.append((sup, email, counts, not dry_run))
    return report
