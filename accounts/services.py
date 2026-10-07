"""
Account approval and admin actions.

Kept separate from the views so the rules can be tested directly and reused
by a future API.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from audit.models import record

from .models import User

logger = logging.getLogger(__name__)


class AccountActionError(Exception):
    pass


def _set_status(user, status, actor, action, summary):
    old = user.status
    user.status = status
    fields = ["status"]
    if status == User.Status.APPROVED:
        user.approved_at = timezone.now()
        user.approved_by = actor
        fields += ["approved_at", "approved_by"]
    user.save(update_fields=fields)
    record(actor, action, user, summary, {"status": {"from": old, "to": status}})


def _guard_self(user, actor, verb):
    if user.pk == actor.pk:
        raise AccountActionError(f"You cannot {verb} your own account.")


def approve(user, actor):
    if user.status == User.Status.APPROVED:
        raise AccountActionError(f"{user.full_name} is already approved.")
    _set_status(user, User.Status.APPROVED, actor, "account.approve", f"Approved account for {user.full_name}")


def reject(user, actor):
    if user.status != User.Status.PENDING:
        raise AccountActionError("Only pending accounts can be rejected.")
    _set_status(user, User.Status.REJECTED, actor, "account.reject", f"Rejected account request from {user.full_name}")


def disable(user, actor):
    _guard_self(user, actor, "disable")
    if user.status != User.Status.APPROVED:
        raise AccountActionError("Only approved accounts can be disabled.")
    _set_status(user, User.Status.DISABLED, actor, "account.disable", f"Disabled account for {user.full_name}")


def set_role(user, role, actor):
    if role not in User.Role.values:
        raise AccountActionError("Unknown role.")
    if role != User.Role.ADMIN:
        _guard_self(user, actor, "remove admin rights from")
    if user.role == role:
        return
    old = user.role
    user.role = role
    user.save(update_fields=["role"])
    record(actor, "account.role", user, f"Changed role of {user.full_name} to {user.get_role_display()}",
           {"role": {"from": old, "to": role}})


def password_reset_link(user, request):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    return request.build_absolute_uri(path)


def send_password_reset(user, actor, request):
    """Email the user a one-time reset link.

    Returns (link, sent). If the email cannot be sent the link is returned so
    the admin can pass it on another way.
    """
    if not user.is_active:
        raise AccountActionError("Password resets can only be sent to approved accounts.")
    link = password_reset_link(user, request)
    body = (
        f"Hello {user.get_short_name()},\n\n"
        "An admin has asked for your BFS Material Orders password to be reset.\n"
        f"Use this link to choose a new password (valid for 3 days):\n\n{link}\n\n"
        "If you did not expect this email, please contact your manager.\n"
    )
    sent = True
    try:
        send_mail("BFS Material Orders: reset your password", body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:  # noqa: BLE001 - any SMTP failure is reported to the admin
        logger.exception("Could not send password reset email to %s", user.email)
        sent = False
    record(actor, "account.password_reset", user, f"Password reset link {'sent' if sent else 'created'} for {user.full_name}")
    return link, sent


def notify_admins_of_signup(user):
    admins = list(User.objects.filter(role=User.Role.ADMIN, is_active=True).values_list("email", flat=True))
    if not admins:
        return
    try:
        send_mail(
            "BFS Material Orders: new account waiting for approval",
            f"{user.full_name} ({user.email}) has asked for an account. "
            "Sign in and open Admin > Users to approve or reject it.",
            settings.DEFAULT_FROM_EMAIL,
            admins,
        )
    except Exception:  # noqa: BLE001 - sign-up must still succeed
        logger.exception("Could not notify admins of new sign-up %s", user.email)
