"""
Data retention (decisions in docs/DECISIONS.md):

- 30 days after an order's requested delivery date, its personal data is
  removed: requester name (replaced by a random anonymous ID, different for
  every order), link to the user account, job number, property address,
  operative name and special instructions. The same details are removed from the order's
  history and email log.
- Kept for analysis: order number, order date, items, quantities, units,
  prices, sections, property type, delivery date and order value.
- Drafts not saved for 30 days are deleted.
- Every run writes a RetentionRun log entry recording what was cleared.

Applies only to this app's records. Stores keep their own copies under
their own process.
"""

import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEntry
from notifications.models import OrderEmail
from orders.models import Order
from orders.services import DRAFT_RETENTION_DAYS

from .models import RetentionRun

logger = logging.getLogger(__name__)

RETENTION_DAYS_AFTER_DELIVERY = 30
ANON_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def new_anonymous_id():
    while True:
        candidate = "ANON-" + "".join(secrets.choice(ANON_ALPHABET) for _ in range(6))
        if not Order.objects.filter(anonymous_id=candidate).exists():
            return candidate


def orders_due(today):
    """Submitted or cancelled orders more than 30 days past delivery and not yet anonymised."""
    cutoff = today - timedelta(days=RETENTION_DAYS_AFTER_DELIVERY)
    return cutoff, (
        Order.objects.exclude(status=Order.Status.DRAFT)
        .filter(anonymised_at__isnull=True)
        .filter(Q(delivery_date__lte=cutoff) | Q(delivery_date__isnull=True, submitted_at__date__lte=cutoff))
    )


def drafts_due(now):
    return Order.objects.filter(status=Order.Status.DRAFT, updated_at__lt=now - timedelta(days=DRAFT_RETENTION_DAYS))


@transaction.atomic
def anonymise_order(order, now=None):
    """Remove personal data from one order and everything recorded about it."""
    now = now or timezone.now()
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.anonymised_at:
        return order
    order.anonymous_id = new_anonymous_id()
    order.requester_name = ""
    order.owner = None
    order.job_number = ""
    order.job_number_key = ""
    order.property_address = ""
    order.special_instructions = ""
    order.operative_name = ""
    order.cancelled_by = None
    order.amendment_pending = False
    order.anonymised_at = now
    order.save()

    # History: keep what happened and when, drop who and the details.
    for entry in AuditEntry.objects.filter(target_type="orders.order", target_id=str(order.pk)):
        entry.actor = None
        entry.summary = f"{entry.action.replace('order.', '').replace('_', ' ').capitalize()} {order.order_number}"
        entry.changes = {}
        entry.save(update_fields=["actor", "summary", "changes"])

    # Email log subjects contain the job number.
    for email in OrderEmail.objects.filter(order=order):
        email.subject = f"{email.get_kind_display()}: {order.order_number}"
        email.last_error = ""
        if email.status == OrderEmail.Status.FAILED:
            email.status = OrderEmail.Status.RESOLVED  # can no longer be sent
        email.save(update_fields=["subject", "last_error", "status"])
    return order


def run(today=None, now=None, dry_run=False, trigger=RetentionRun.Trigger.SCHEDULED, run_by=""):
    """Run the retention job once. Safe to run more than once a day."""
    now = now or timezone.now()
    today = today or timezone.localdate(now)
    cutoff, due = orders_due(today)
    log = RetentionRun.objects.create(trigger=trigger, run_by=run_by, dry_run=dry_run, cutoff_delivery_date=cutoff)
    try:
        numbers = list(due.order_by("delivery_date", "order_number").values_list("order_number", flat=True))
        drafts = drafts_due(now)
        draft_count = drafts.count()
        if not dry_run:
            for order in due.order_by("delivery_date"):
                anonymise_order(order, now)
            drafts.delete()
        log.order_numbers = numbers
        log.orders_anonymised = len(numbers)
        log.drafts_deleted = draft_count
        log.succeeded = True
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise for the cron log
        log.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        log.finished_at = timezone.now()
        log.save()
        logger.info("Retention run %s: %s orders anonymised, %s drafts deleted%s",
                    log.pk, log.orders_anonymised, log.drafts_deleted, " (dry run)" if dry_run else "")
    return log
