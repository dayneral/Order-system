"""
Sending order emails to stores.

If sending fails the order is still saved: the failure is recorded, the
user is warned, and admins can retry from Admin > Failed emails.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from . import documents
from .models import OrderEmail

logger = logging.getLogger(__name__)


class CannotRetry(Exception):
    pass


def last_sent_snapshot(order, before=None):
    """The order as stores last received it."""
    emails = OrderEmail.objects.filter(order=order, status=OrderEmail.Status.SENT).exclude(snapshot={})
    if before is not None:
        emails = emails.filter(created_at__lt=before)
    latest = emails.order_by("-created_at").first()
    return latest.snapshot if latest else None


def _build_message(order, kind, previous=None):
    reply_to = [order.owner.email] if order.owner_id and order.owner and order.owner.email else None
    message = EmailMultiAlternatives(
        subject=documents.subject(order, kind, previous),
        body=documents.render_text(order, kind, previous),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.STORES_EMAIL],
        reply_to=reply_to,
    )
    message.attach_alternative(documents.render_html(order, kind, previous), "text/html")
    return message


def _attempt(order, kind, previous=None):
    """Try to send. Returns (ok, error text)."""
    try:
        _build_message(order, kind, previous).send(fail_silently=False)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure must not lose the order
        logger.exception("Order email failed: %s %s", kind, order.order_number)
        return False, f"{type(exc).__name__}: {exc}"[:2000]


def _resolve_earlier_failures(order, kind):
    """A later successful email of the same kind supersedes earlier failures."""
    OrderEmail.objects.filter(order=order, kind=kind, status=OrderEmail.Status.FAILED).update(
        status=OrderEmail.Status.RESOLVED)


def send_order_email(order, kind):
    previous = last_sent_snapshot(order) if kind == "amended" else None
    ok, error = _attempt(order, kind, previous)
    log = OrderEmail.objects.create(
        order=order, kind=kind, recipient=settings.STORES_EMAIL, subject=documents.subject(order, kind, previous),
        status=OrderEmail.Status.SENT if ok else OrderEmail.Status.FAILED,
        last_error=error, sent_at=timezone.now() if ok else None, snapshot=documents.snapshot(order),
    )
    if ok:
        _resolve_earlier_failures(order, kind)
    return log


def retry(log):
    """Re-send a failed email using the order as it is now."""
    order = log.order
    if log.status != OrderEmail.Status.FAILED:
        raise CannotRetry("This email is not waiting for a retry.")
    previous = last_sent_snapshot(order, before=log.created_at) if log.kind == "amended" else None
    ok, error = _attempt(order, log.kind, previous)
    log.attempts += 1
    log.snapshot = documents.snapshot(order)
    log.subject = documents.subject(order, log.kind, previous)
    if ok:
        log.status = OrderEmail.Status.SENT
        log.sent_at = timezone.now()
        log.last_error = ""
        log.save()
        _resolve_earlier_failures(order, log.kind)
    else:
        log.last_error = error
        log.save()
    return ok
