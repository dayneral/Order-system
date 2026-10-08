"""Repeat adds increase the quantity; operative name for occupied properties; order values shelved."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import importer
from catalogue.models import Item, MeasureType, Section
from orders import services
from orders.services import OrderError
from retention import services as retention

pytestmark = pytest.mark.django_db
TOMORROW = timezone.localdate() + timedelta(days=1)


def make(code, price="2.00", **kw):
    return Item.objects.create(part_no=code, catalogue_name=f"Item {code}", section_id=Section.PLUMBING,
                               trade_price=D(price) if price is not None else None, **kw)


# --- Adding the same item again ------------------------------------------------

def test_adding_same_counted_item_increases_quantity(client, make_user):
    user = make_user()
    item = make("PLU1")
    order = services.create_draft(user)
    client.force_login(user)
    url = reverse("orders:line_add", args=[order.pk])
    client.post(url, {"item_id": item.pk, "quantity": "2"}, HTTP_HX_REQUEST="true")
    resp = client.post(url, {"item_id": item.pk, "quantity": "3"}, HTTP_HX_REQUEST="true")
    assert order.lines.count() == 1 and order.lines.get().quantity == D("5")
    assert "already on the order: quantity is now 5 each" in resp.content.decode()


def test_packs_merge_but_measured_pieces_stay_separate(make_user):
    user = make_user()
    pack = make("PLU2", measure_type=MeasureType.PACK, pack_size=100, unit="pack")
    vinyl = make("V1", measure_type=MeasureType.AREA, unit="m²")
    order = services.create_draft(user)
    services.add_item_line(order, user, pack, {"quantity": "1"})
    services.add_item_line(order, user, pack, {"quantity": "2"})
    services.add_item_line(order, user, vinyl, {"length": "3.0", "width": "2.0"})
    services.add_item_line(order, user, vinyl, {"length": "1.5", "width": "2.0"})
    assert order.lines.get(item=pack).quantity == D("3")
    assert order.lines.filter(item=vinyl).count() == 2


def test_merge_on_submitted_order_keeps_original_price_and_is_audited(make_user):
    from audit.models import AuditEntry

    user = make_user()
    item = make("PLU3", price="2.00")
    order = services.create_draft(user, job_number="J1", property_address="1 Road", property_type="void",
                                  delivery_date=TOMORROW)
    services.add_item_line(order, user, item, {"quantity": "1"})
    services.submit(order, user)
    item.trade_price = D("9.99")
    item.save()
    services.add_item_line(order, user, item, {"quantity": "1"})
    line = order.lines.get()
    assert line.quantity == D("2") and line.trade_price == D("2.0000")
    assert AuditEntry.objects.filter(action="order.amend", summary__contains="changed PLU3").exists()


def test_kit_adding_an_item_already_on_order_merges(make_user):
    from catalogue.models import Kit, KitItem

    user = make_user()
    item = make("PLU4")
    kit = Kit.objects.create(name="Basin kit")
    ki = KitItem.objects.create(kit=kit, item=item, quantity=2)
    order = services.create_draft(user)
    services.add_item_line(order, user, item, {"quantity": "1"})
    services.add_kit(order, user, kit, {f"include_{ki.pk}": "1", f"quantity_{ki.pk}": "2"})
    line = order.lines.get()
    assert line.quantity == D("3") and line.kit_name == "Basin kit"


# --- Operative name ---------------------------------------------------------------

def header(**kw):
    return {"job_number": "J9", "property_address": "1 Road", "property_type": "occupied",
            "delivery_date": TOMORROW, **kw}


def test_occupied_property_needs_operative_name(make_user):
    user = make_user()
    order = services.create_draft(user, **header())
    services.add_item_line(order, user, make("PLU5"), {"quantity": "1"})
    with pytest.raises(OrderError, match="operative"):
        services.submit(order, user)
    services.update_header(order, user, header(operative_name="Jo Fitter"))
    services.submit(order, user)
    html = mail.outbox[0].alternatives[0][0]
    assert "Operative" in html and "Jo Fitter" in html and "Jo Fitter" in mail.outbox[0].body


def test_void_property_does_not_need_or_keep_operative_name(make_user):
    user = make_user()
    order = services.create_draft(user)
    services.update_header(order, user, header(property_type="void", operative_name="Jo Fitter"))
    services.add_item_line(order, user, make("PLU6"), {"quantity": "1"})
    services.submit(order, user)
    order.refresh_from_db()
    assert order.operative_name == "" and "Operative" not in mail.outbox[0].body


def test_amending_to_occupied_without_operative_is_blocked(make_user):
    user = make_user()
    order = services.create_draft(user, **header(property_type="void"))
    services.add_item_line(order, user, make("PLU7"), {"quantity": "1"})
    services.submit(order, user)
    with pytest.raises(OrderError, match="operative"):
        services.update_header(order, user, header())


def test_operative_name_removed_by_retention(make_user):
    user = make_user()
    order = services.create_draft(user, **header(operative_name="Jo Fitter"))
    services.add_item_line(order, user, make("PLU8"), {"quantity": "1"})
    services.submit(order, user)
    type(order).objects.filter(pk=order.pk).update(delivery_date=timezone.localdate() - timedelta(days=31))
    retention.run()
    order.refresh_from_db()
    assert order.operative_name == ""


# --- Order values shelved ------------------------------------------------------------

@override_settings(ORDER_VALUES_ENABLED=False)
def test_values_hidden_and_missing_price_does_not_block_ordering(client, make_user):
    user = make_user()
    priced = make("PLU9", price="4.20")
    unpriced = make("PLU10", price=None)
    assert unpriced.is_orderable  # price is information only for now
    order = services.create_draft(user)
    client.force_login(user)
    resp = client.post(reverse("orders:line_add", args=[order.pk]), {"item_id": priced.pk, "quantity": "2"},
                       HTTP_HX_REQUEST="true")
    services.add_item_line(order, user, unpriced, {"quantity": "1"})
    pages = [resp.content.decode(),
             client.get(reverse("orders:edit", args=[order.pk])).content.decode(),
             client.get(reverse("orders:items", args=[order.pk]) + "?section=plumbing").content.decode()]
    for page in pages:
        assert "£" not in page and "Order value" not in page
    services.update_header(order, user, header(property_type="void"))
    services.submit(order, user)
    for page in (client.get(reverse("orders:detail", args=[order.pk])).content.decode(),
                 client.get(reverse("orders:list") + "?view=all").content.decode()):
        assert "£" not in page
    priced.refresh_from_db()
    assert priced.trade_price == D("4.20")  # still stored


@override_settings(ORDER_VALUES_ENABLED=False)
def test_import_does_not_flag_missing_price_as_incomplete_while_values_shelved():
    csv = "Part No.,Name,Trade Price\r\nX1,Thing,\r\nX2,,1.00\r\n".encode()
    run = importer.preview_upload("Plumbing.csv", csv, None)
    assert [x["part_no"] for x in run.report["incomplete"]] == ["X2"]
    importer.apply_run(run, None)
    assert Item.objects.get(part_no="X1").is_orderable and not Item.objects.get(part_no="X2").is_orderable
