"""Admin delete of mistaken orders; AMENDED emails show what changed."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEntry
from catalogue.models import Item, MeasureType, Section
from notifications.models import OrderEmail
from orders import services
from orders.models import Order
from orders.services import OrderError

pytestmark = pytest.mark.django_db
TOMORROW = timezone.localdate() + timedelta(days=1)


@pytest.fixture
def setup(make_user):
    user = make_user()
    mk = lambda code, **kw: Item.objects.create(part_no=code, catalogue_name=f"Item {code}",  # noqa: E731
                                                 section_id=Section.PLUMBING, trade_price=D("1"), **kw)
    items = {"a": mk("PLU1"), "b": mk("PLU2"), "c": mk("PLU3"),
             "top": mk("JOI80", measure_type=MeasureType.CUT_TO_ORDER, catalogue_length_m=D("3"), unit="m")}
    order = services.create_draft(user, job_number="J1", property_address="1 Road", property_type="void",
                                  delivery_date=TOMORROW)
    services.add_item_line(order, user, items["a"], {"quantity": "2"})
    services.add_item_line(order, user, items["b"], {"quantity": "1"})
    services.add_item_line(order, user, items["top"], {"length": "1.9"})
    services.submit(order, user)
    return user, items, order


# --- Admin delete -------------------------------------------------------------------

def test_admin_deletes_active_order_and_stores_get_cancelled_notice(admin_client, admin_user, setup):
    _, _, order = setup
    url = reverse("orders:delete", args=[order.pk])
    page = admin_client.get(url).content.decode()
    assert "stores will be sent a <b>CANCELLED</b> notice" in page
    resp = admin_client.post(url, {"reason": "Duplicate of BFS-2026-000099"})
    assert resp.status_code == 302
    assert not Order.objects.filter(pk=order.pk).exists()
    assert mail.outbox[-1].subject.startswith("CANCELLED: ")
    entry = AuditEntry.objects.get(action="order.delete")
    assert entry.actor == admin_user and order.order_number in entry.summary and "Duplicate" in entry.summary
    assert AuditEntry.objects.filter(target_id=str(order.pk)).count() == 1  # only the deletion record remains


def test_deleting_cancelled_order_sends_nothing(admin_user, setup):
    user, _, order = setup
    services.cancel(order, user)
    sent = len(mail.outbox)
    services.admin_delete(order, admin_user, "Entered by mistake")
    assert len(mail.outbox) == sent and not Order.objects.filter(pk=order.pk).exists()


def test_only_admins_can_delete_and_a_reason_is_required(client, admin_user, setup):
    user, _, order = setup
    client.force_login(user)
    assert client.get(reverse("orders:delete", args=[order.pk])).status_code == 403
    with pytest.raises(OrderError):
        services.admin_delete(order, user, "mine")
    with pytest.raises(OrderError, match="reason"):
        services.admin_delete(order, admin_user, "  ")
    assert Order.objects.filter(pk=order.pk).exists()


# --- Amended emails show changes -----------------------------------------------------

def test_amended_email_marks_new_changed_and_removed_lines_and_header(setup):
    user, items, order = setup
    lines = {l.part_no: l for l in order.lines.all()}
    services.update_line(lines["PLU1"], user, {"quantity": "5"})
    services.update_line(lines["JOI80"], user, {"length": "2.4"})
    services.remove_line(lines["PLU2"], user)
    services.add_item_line(order, user, items["c"], {"quantity": "1"})
    services.update_header(order, user, {"job_number": "J1", "property_address": "1 Road", "property_type": "void",
                                         "delivery_date": TOMORROW + timedelta(days=3)})
    services.finish_amendment(order, user)
    msg = mail.outbox[-1]
    html, text = msg.alternatives[0][0], msg.body
    assert "Changes since the last version" in html
    assert "1 NEW" in html and "2 CHANGED" in html and "1 REMOVED" in html
    assert 'class="line-new"' in html and 'class="line-removed"' in html and 'class="line-changed"' in html
    assert "was: 2 each" in html and "was: Item JOI80, cut to 1.9m" in html
    assert "do not supply" in html and "Item PLU2" in html
    assert "Delivery date (was:" in html and 'class="chg"' in html
    assert "[NEW] PLU3" in text and "[CHANGED] PLU1" in text and "REMOVED - do not supply: PLU2" in text


def test_second_amendment_compares_with_the_first_amendment(setup):
    user, items, order = setup
    services.add_item_line(order, user, items["c"], {"quantity": "1"})
    services.finish_amendment(order, user)
    line = order.lines.get(part_no="PLU3")
    services.update_line(line, user, {"quantity": "4"})
    services.finish_amendment(order, user)
    html = mail.outbox[-1].alternatives[0][0]
    assert "1 CHANGED" in html and "NEW</span>" not in html.split("Changes since")[1].split("</div>")[0]


def test_failed_amendment_retry_still_shows_changes(setup, monkeypatch):
    from notifications import sending

    user, items, order = setup
    services.add_item_line(order, user, items["c"], {"quantity": "1"})
    monkeypatch.setattr("django.core.mail.EmailMessage.send", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    services.finish_amendment(order, user)
    monkeypatch.undo()
    sending.retry(OrderEmail.objects.get(kind="amended"))
    assert "1 NEW" in mail.outbox[-1].alternatives[0][0]


def test_print_view_of_amended_order_shows_changes(client, setup):
    user, items, order = setup
    services.add_item_line(order, user, items["c"], {"quantity": "1"})
    services.finish_amendment(order, user)
    client.force_login(user)
    page = client.get(reverse("orders:print", args=[order.pk])).content.decode()
    assert "Changes since the last version" in page and "1 NEW" in page
