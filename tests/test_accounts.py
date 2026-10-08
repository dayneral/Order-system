"""Sign-in, the approval flow, roles and admin-triggered password reset."""

import pytest
from django.core import mail
from django.urls import reverse

from accounts import services
from accounts.models import User
from audit.models import AuditEntry

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


def sign_in(client, email, password=PASSWORD):
    return client.post(reverse("accounts:login"), {"username": email, "password": password})


# --- Registration and approval ---------------------------------------------

def test_registration_creates_pending_ordering_user_and_notifies_admins(client, admin_user):
    resp = client.post(reverse("accounts:register"), {
        "full_name": "Jo Bloggs", "email": "Jo.Bloggs@BFSUK.org",
        "password1": "Plaster-Board-77", "password2": "Plaster-Board-77",
    })
    assert resp.status_code == 302
    user = User.objects.get(email="jo.bloggs@bfsuk.org")
    assert user.status == User.Status.PENDING
    assert user.role == User.Role.ORDERING
    assert not user.is_active
    assert user.password != "Plaster-Board-77"  # stored hashed
    assert user.check_password("Plaster-Board-77")
    assert mail.outbox and admin_user.email in mail.outbox[0].to


def test_registration_cannot_choose_admin_role(client):
    client.post(reverse("accounts:register"), {
        "full_name": "Sneaky", "email": "sneaky@bfsuk.org", "role": "admin", "status": "approved",
        "password1": "Plaster-Board-77", "password2": "Plaster-Board-77",
    })
    user = User.objects.get(email="sneaky@bfsuk.org")
    assert user.role == User.Role.ORDERING and user.status == User.Status.PENDING


def test_duplicate_email_rejected_case_insensitively(client, make_user):
    make_user("taken@bfsuk.org")
    resp = client.post(reverse("accounts:register"), {
        "full_name": "Other", "email": "TAKEN@bfsuk.org",
        "password1": "Plaster-Board-77", "password2": "Plaster-Board-77",
    })
    assert resp.status_code == 200
    assert "already exists" in resp.content.decode()


def test_pending_user_cannot_sign_in_and_is_told_why(client, make_user):
    make_user("new@bfsuk.org", status=User.Status.PENDING)
    resp = sign_in(client, "new@bfsuk.org")
    assert resp.status_code == 200
    assert "waiting for an admin" in resp.content.decode()
    assert "_auth_user_id" not in client.session


def test_pending_user_with_wrong_password_gets_generic_message(client, make_user):
    make_user("new@bfsuk.org", status=User.Status.PENDING)
    resp = sign_in(client, "new@bfsuk.org", "wrong-password")
    body = resp.content.decode()
    assert "not recognised" in body and "waiting for an admin" not in body


def test_approval_lets_user_sign_in_and_is_audited(client, admin_client, admin_user, make_user):
    user = make_user("new@bfsuk.org", status=User.Status.PENDING)
    admin_client.post(reverse("accounts:user_action", args=[user.pk]), {"action": "approve"})
    user.refresh_from_db()
    assert user.status == User.Status.APPROVED and user.is_active
    assert user.approved_by == admin_user and user.approved_at
    entry = AuditEntry.objects.get(action="account.approve")
    assert entry.actor == admin_user and entry.target_id == str(user.pk)

    admin_client.logout()
    resp = sign_in(client, "NEW@bfsuk.org")  # email is case insensitive
    assert resp.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_rejected_and_disabled_users_cannot_sign_in(client, make_user):
    make_user("rej@bfsuk.org", status=User.Status.REJECTED)
    make_user("dis@bfsuk.org", status=User.Status.DISABLED)
    for email in ("rej@bfsuk.org", "dis@bfsuk.org"):
        resp = sign_in(client, email)
        assert "cannot sign in" in resp.content.decode()
        assert "_auth_user_id" not in client.session


def test_disabling_a_user_ends_their_session(client, admin_user, make_user):
    user = make_user()
    client.force_login(user)
    assert client.get(reverse("orders:list")).status_code == 200
    services.disable(user, admin_user)
    resp = client.get(reverse("orders:list"))
    assert resp.status_code == 302 and reverse("accounts:login") in resp.url


# --- Roles ------------------------------------------------------------------

def test_ordering_user_cannot_reach_admin_pages(client, make_user):
    user = make_user()
    client.force_login(user)
    assert client.get(reverse("accounts:user_list")).status_code == 403
    pending = make_user("p@bfsuk.org", status=User.Status.PENDING)
    assert client.post(reverse("accounts:user_action", args=[pending.pk]), {"action": "approve"}).status_code == 403
    pending.refresh_from_db()
    assert pending.status == User.Status.PENDING
    # Built-in data admin is closed too.
    assert client.get(reverse("admin:index")).status_code == 302


def test_admin_can_reach_admin_pages(admin_client):
    assert admin_client.get(reverse("accounts:user_list")).status_code == 200
    assert admin_client.get(reverse("admin:index")).status_code == 200


def test_anonymous_redirected_to_sign_in(client):
    resp = client.get(reverse("home"))
    assert resp.status_code == 302 and reverse("accounts:login") in resp.url


def test_role_change_and_self_protection(admin_user, make_user):
    user = make_user()
    services.set_role(user, User.Role.ADMIN, admin_user)
    user.refresh_from_db()
    assert user.is_admin
    with pytest.raises(services.AccountActionError):
        services.set_role(admin_user, User.Role.ORDERING, admin_user)
    with pytest.raises(services.AccountActionError):
        services.disable(admin_user, admin_user)


def test_pending_user_is_not_admin_even_with_admin_role(make_user):
    user = make_user(role=User.Role.ADMIN, status=User.Status.PENDING)
    assert not user.is_admin and not user.is_staff


# --- Password reset -----------------------------------------------------------

def test_admin_triggered_password_reset(client, admin_client, make_user):
    user = make_user()
    admin_client.post(reverse("accounts:user_action", args=[user.pk]), {"action": "send_reset"})
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [user.email]
    link = next(line for line in mail.outbox[0].body.splitlines() if "/password/reset/" in line).strip()
    assert AuditEntry.objects.filter(action="account.password_reset").exists()

    admin_client.logout()
    path = link.split("testserver", 1)[1]
    resp = client.get(path)  # Django swaps the token into the session and redirects
    assert resp.status_code == 302
    resp = client.post(resp.url, {"new_password1": "Fresh-Grout-2026", "new_password2": "Fresh-Grout-2026"})
    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Fresh-Grout-2026")
    # Link cannot be reused.
    assert "Link not valid" in client.get(path, follow=True).content.decode()


def test_reset_link_shown_to_admin_if_email_fails(admin_client, make_user, settings, monkeypatch):
    user = make_user()

    def boom(*args, **kwargs):
        raise OSError("SMTP down")

    monkeypatch.setattr(services, "send_mail", boom)
    resp = admin_client.post(reverse("accounts:user_action", args=[user.pk]), {"action": "send_reset"}, follow=True)
    body = resp.content.decode()
    assert "could not be sent" in body and "/password/reset/" in body


def test_self_service_reset_only_for_approved_accounts(client, make_user):
    make_user("pending@bfsuk.org", status=User.Status.PENDING)
    make_user("ok@bfsuk.org")
    client.post(reverse("accounts:password_reset"), {"email": "pending@bfsuk.org"})
    client.post(reverse("accounts:password_reset"), {"email": "ok@bfsuk.org"})
    assert [m.to for m in mail.outbox] == [["ok@bfsuk.org"]]
