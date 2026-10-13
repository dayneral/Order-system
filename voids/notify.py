"""Emails from the voids module (email only)."""

import logging
import os

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def site_url(path):
    base = os.environ.get("SITE_URL") or (
        f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}" if os.environ.get("RENDER_EXTERNAL_HOSTNAME") else "")
    return f"{base.rstrip('/')}{path}"


def final_check_recipients(void):
    from .services import trade_supervisors
    people = [void.supervisor] if void.supervisor else []
    people += [s for s in trade_supervisors(void) if s not in people]
    emails = []
    for s in people:
        email = s.email or (s.user.email if s.user_id else "")
        if email and email not in emails:
            emails.append(email)
    return emails


def send_final_check_request(void):
    """Ask the supervisor(s) to final check a void whose trades are all complete.
    Returns the addresses emailed (empty when none are set up or sending failed)."""
    to = final_check_recipients(void)
    if not to:
        return []
    link = site_url(reverse("voids:detail", args=[void.pk]))
    subject = f"Final check needed: {void.address}" + (f" (SIMPRO {void.simpro})" if void.simpro else "")
    body = (f"All trades on {void.address} are marked complete.\n\n"
            f"Please final check the property, then press \"Confirm final check\" on the void page:\n{link}\n\n"
            f"Hand-back date: {void.handback_date:%d %b %Y}\n" if void.handback_date else
            f"All trades on {void.address} are marked complete.\n\n"
            f"Please final check the property, then press \"Confirm final check\" on the void page:\n{link}\n")
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    except Exception:  # noqa: BLE001 - email trouble must not stop the trade update
        logger.exception("Final check email failed for void %s", void.pk)
        return []
    return to
