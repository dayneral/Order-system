"""Order email to stores: content, failures and retry, print view."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Item, MeasureType, Section
from notifications import sending
from notifications.models import OrderEmail
from orders import services

pytestmark = pytest.mark.django_db

TOMORROW = timezone.localdate() + timedelta(days=1)


@pytest.fixture
def order(make_user):
    user = make_user(full_name="Sam Ordering")
    make = lambda code, name, section, **kw: Item.objects.create(  # noqa: E731
        part_no=code, catalogue_name=name, section_id=section, trade_price=D("10.00"), **kw)
    silicone = make("ADH014", "Multi-purpose silicone 270ml white", Section.ADHESIVES)
    worktop = make("BK010", "Black granite bullnose worktop", Section.BATHROOMS_KITCHENS,
                   measure_type=MeasureType.CUT_TO_ORDER, catalogue_length_m=D("3.0"), unit="m")
    vinyl = make("V0010", "Safety vinyl grey", Section.FLOORING, measure_type=MeasureType.AREA, unit="m²")
    spirit = make("FLA001", "White spirit 1L", Section.FLAMMABLE)
    screws = make("JOI050", "Screws M4 x 50mm", Section.JOINERY, measure_type=MeasureType.PACK, pack_size=100)
    o = services.create_draft(user, job_number="J-24017", property_address="14 Mill Lane\nLeeds",
                              property_type="occupied", delivery_date=TOMORROW,
                              special_instructions="Side gate code 1234")
    services.add_item_line(o, user, silicone, {"quantity": "3"})
    services.add_item_line(o, user, worktop, {"length": "1.9"})
    services.add_item_line(o, user, vinyl, {"length": "4.5", "width": "2.0"})
    services.add_item_line(o, user, spirit, {"quantity": "2"})
    services.add_item_line(o, user, screws, {"quantity": "2"})
    services.add_non_stocked_line(o, user, "Brass kick plate", "each", "1")
    return o


def test_submit_emails_stores_with_html_body_and_no_attachment(order, settings):
    services.submit(order, order.owner)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == [settings.STORES_EMAIL] == ["stores@bfsuk.org"]
    assert "materialorders@bfsuk.org" in msg.from_email
    assert msg.reply_to == [order.owner.email]
    assert msg.subject.startswith(f"Material order {order.order_number} - Job J-24017")
    html = msg.alternatives[0][0]
    for expected in (order.order_number, "Sam Ordering", "J-24017", "14 Mill Lane", "Occupied",
                     "Side gate code 1234", "ADH014</span>&nbsp;&mdash;&nbsp;Multi-purpose silicone 270ml white",
                     "Black granite bullnose worktop, cut to 1.9m", "Cut to 4.5m × 2.0m = 9.00 m²",
                     "[FLAMMABLE]", "NON-STOCKED", "Brass kick plate", "2 packs of 100"):
        assert expected in html, expected
    assert msg.attachments == []
    for expected in (order.order_number, "J-24017", "cut to 1.9m", "9.00 m²", "[FLAMMABLE]", "NON-STOCKED"):
        assert expected in msg.body, expected
    assert OrderEmail.objects.get().status == OrderEmail.Status.SENT


def test_no_prices_or_totals_go_to_stores(order):
    services.submit(order, order.owner)
    msg = mail.outbox[0]
    everything = msg.body + msg.alternatives[0][0]
    assert "£" not in everything and "10.00" not in everything and str(order.order_value) not in everything
    assert "value" not in everything.lower() and "price" not in everything.lower()


def test_amended_and_cancelled_emails(order):
    user = order.owner
    services.submit(order, user)
    services.update_line(order.lines.first(), user, {"quantity": "5"})
    services.finish_amendment(order, user)
    amended = mail.outbox[-1]
    assert amended.subject.startswith("AMENDED: ") and "AMENDED" in amended.alternatives[0][0]
    assert "5 each" in amended.body

    services.cancel(order, user)
    cancelled = mail.outbox[-1]
    assert cancelled.subject.startswith("CANCELLED: ") and "do not pick or deliver" in cancelled.body
    assert [e.kind for e in OrderEmail.objects.order_by("created_at")] == ["submitted", "amended", "cancelled"]


def test_send_failure_keeps_order_warns_user_and_lists_for_retry(order, admin_client, monkeypatch):
    from django.test import Client

    client = Client()
    def broken(*args, **kwargs):
        raise ConnectionRefusedError("SMTP server not reachable")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", broken)
    client.force_login(order.owner)
    data = {"job_number": "J-24017", "property_address": "14 Mill Lane", "property_type": "occupied",
            "delivery_date": TOMORROW.isoformat(), "action": "submit"}
    resp = client.post(reverse("orders:edit", args=[order.pk]), data, follow=True)
    order.refresh_from_db()
    assert order.order_number and order.status == "submitted"  # saved regardless
    page = resp.content.decode()
    assert "could NOT be sent" in page
    log = OrderEmail.objects.get()
    assert log.status == OrderEmail.Status.FAILED and "SMTP server not reachable" in log.last_error

    failed_page = admin_client.get(reverse("notifications:failed")).content.decode()
    assert order.order_number in failed_page and "Retry" in failed_page

    # Retry still failing.
    admin_client.post(reverse("notifications:retry", args=[log.pk]))
    log.refresh_from_db()
    assert log.status == OrderEmail.Status.FAILED and log.attempts == 2

    # Mail back up: retry succeeds.
    monkeypatch.undo()
    admin_client.post(reverse("notifications:retry", args=[log.pk]))
    log.refresh_from_db()
    assert log.status == OrderEmail.Status.SENT and log.attempts == 3
    assert len(mail.outbox) == 1


def test_failed_count_badge_for_admins(order, admin_client, monkeypatch):
    monkeypatch.setattr("django.core.mail.EmailMessage.send", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    services.submit(order, order.owner)
    assert "1 failed" in admin_client.get(reverse("orders:list")).content.decode()


def test_later_success_resolves_earlier_failure(order, monkeypatch):
    monkeypatch.setattr("django.core.mail.EmailMessage.send", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    services.submit(order, order.owner)
    monkeypatch.undo()
    sending.send_order_email(order, "submitted")
    assert list(OrderEmail.objects.values_list("status", flat=True).order_by("created_at")) == ["resolved", "sent"]


def test_cannot_retry_after_anonymisation(order, monkeypatch):
    monkeypatch.setattr("django.core.mail.EmailMessage.send", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    services.submit(order, order.owner)
    order.anonymised_at = timezone.now()
    order.save()
    with pytest.raises(sending.CannotRetry):
        sending.retry(OrderEmail.objects.get())


def test_print_view(client, order, make_user):
    services.submit(order, order.owner)
    client.force_login(make_user("viewer@bfsuk.org", "Viewer"))
    page = client.get(reverse("orders:print", args=[order.pk])).content.decode()
    assert "window.print()" in page and order.order_number in page and "cut to 1.9m" in page and "£" not in page


def test_print_view_matches_email_content(order):
    from notifications import documents

    services.submit(order, order.owner)
    email_html = mail.outbox[0].alternatives[0][0]
    print_html = documents.render_to_string_for_print(order, "submitted", back_url="/")
    strip = lambda h: h.split('<div class="doc">')[1].split("Generated")[0]  # noqa: E731
    assert strip(email_html) == strip(print_html)
