"""
Data retention (decisions in docs/DECISIONS.md):

- 30 days after an order's requested delivery date, the order is deleted:
  the order, its lines, its history and its email log. Stores keep their own
  copies and metrics under their own process.
- Drafts not saved for 30 days are deleted.
- Every run writes a RetentionRun log entry with the order numbers removed
  (no personal details).
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEntry
from orders.models import Order
from orders.services import DRAFT_RETENTION_DAYS

from .models import RetentionRun

logger = logging.getLogger(__name__)

RETENTION_DAYS_AFTER_DELIVERY = 30


def orders_due(today):
    """Submitted or cancelled orders more than 30 days past delivery."""
    cutoff = today - timedelta(days=RETENTION_DAYS_AFTER_DELIVERY)
    return cutoff, (
        Order.objects.exclude(status=Order.Status.DRAFT)
        .filter(Q(delivery_date__lte=cutoff) | Q(delivery_date__isnull=True, submitted_at__date__lte=cutoff))
    )


def drafts_due(now):
    return Order.objects.filter(status=Order.Status.DRAFT, updated_at__lt=now - timedelta(days=DRAFT_RETENTION_DAYS))


@transaction.atomic
def delete_order_completely(order):
    """Delete an order with everything recorded about it (lines and email log go with it)."""
    AuditEntry.objects.filter(target_type="orders.order", target_id=str(order.pk)).delete()
    order.delete()


def run(today=None, now=None, dry_run=False, trigger=RetentionRun.Trigger.SCHEDULED, run_by=""):
    """Run the retention job once. Safe to run more than once a day."""
    now = now or timezone.now()
    today = today or timezone.localdate(now)
    cutoff, due = orders_due(today)
    log = RetentionRun.objects.create(trigger=trigger, run_by=run_by, dry_run=dry_run, cutoff_delivery_date=cutoff)
    try:
        orders = list(due.order_by("delivery_date", "order_number"))
        drafts = drafts_due(now)
        draft_count = drafts.count()
        if not dry_run:
            for order in orders:
                delete_order_completely(order)
            drafts.delete()
        log.order_numbers = [o.order_number for o in orders]
        log.orders_deleted = len(orders)
        log.drafts_deleted = draft_count
        log.succeeded = True
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise for the cron log
        log.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        log.finished_at = timezone.now()
        log.save()
        logger.info("Retention run %s: %s orders deleted, %s drafts deleted%s",
                    log.pk, log.orders_deleted, log.drafts_deleted, " (dry run)" if dry_run else "")
    return log
