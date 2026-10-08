"""Linked-item suggestions and kits."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Item, Kit, KitItem, LinkedItemRule, MeasureType, Section
from orders import services, suggestions
from orders.services import OrderError

pytestmark = pytest.mark.django_db


def make(code, name, price="1.00", section=Section.PLUMBING, **kw):
    return Item.objects.create(part_no=code, catalogue_name=name, section_id=section,
                               trade_price=D(price) if price is not None else None, **kw)


@pytest.fixture
def cat(db):
    return {
        "sealant": make("ADH014", "Silicone sealant", "4.20", Section.ADHESIVES),
        "gun": make("ADH090", "Sealant gun", "6.00", Section.ADHESIVES),
        "nozzle": make("ADH091", "Spare nozzles", "1.00", Section.ADHESIVES),
        "pan": make("BK100", "Close-coupled WC pan", "55.00", Section.KITCHENS),
        "cistern": make("BK101", "Close-coupled cistern", "45.00", Section.KITCHENS),
        "seat": make("BK102", "Toilet seat", "12.00", Section.KITCHENS),
        "connector": make("PLU200", "Pan connector", "4.50"),
        "valve": make("PLU201", "Isolation valve", "2.25"),
        "hose": make("PLU202", "Flexi connector", None),  # incomplete: not orderable
        "lino": make("V0010", "Safety vinyl", "18.50", Section.FLOORING, measure_type=MeasureType.AREA, unit="m²"),
    }


@pytest.fixture
def toilet_kit(cat):
    kit = Kit.objects.create(name="Close-coupled toilet kit", description="Everything for a standard WC")
    for pos, (key, qty, prompts) in enumerate([("pan", 1, True), ("cistern", 1, False), ("seat", 1, False),
                                               ("connector", 1, False), ("valve", 2, False), ("hose", 1, False)]):
        KitItem.objects.create(kit=kit, item=cat[key], quantity=qty, prompts_kit=prompts, position=pos)
    return kit


@pytest.fixture
def order(make_user):
    user = make_user()
    return services.create_draft(user)


def rule(trigger, *suggested, two_way=False, note="", active=True):
    r = LinkedItemRule.objects.create(trigger=trigger, two_way=two_way, note=note, is_active=active)
    r.suggested_items.set(suggested)
    return r


# --- Linked items ----------------------------------------------------------------

def test_adding_trigger_suggests_linked_items(order, cat):
    rule(cat["sealant"], cat["gun"], cat["nozzle"], note="Sealant needs an applicator gun")
    services.add_item_line(order, order.owner, cat["sealant"], {"quantity": "2"})
    items, notes = suggestions.linked_suggestions(order, cat["sealant"])
    assert [i.part_no for i in items] == ["ADH090", "ADH091"]
    assert notes == ["Sealant needs an applicator gun"]


def test_one_way_rule_does_not_suggest_in_reverse(order, cat):
    rule(cat["sealant"], cat["gun"])
    services.add_item_line(order, order.owner, cat["gun"], {"quantity": "1"})
    assert suggestions.linked_suggestions(order, cat["gun"])[0] == []


def test_two_way_rule_suggests_in_reverse(order, cat):
    rule(cat["sealant"], cat["gun"], two_way=True)
    services.add_item_line(order, order.owner, cat["gun"], {"quantity": "1"})
    assert suggestions.linked_suggestions(order, cat["gun"])[0] == [cat["sealant"]]


def test_suggestions_skip_items_on_order_unavailable_items_and_inactive_rules(order, cat):
    rule(cat["sealant"], cat["gun"], cat["nozzle"], cat["hose"])
    cat["nozzle"].is_active = False
    cat["nozzle"].save()
    services.add_item_line(order, order.owner, cat["gun"], {"quantity": "1"})
    services.add_item_line(order, order.owner, cat["sealant"], {"quantity": "1"})
    assert suggestions.linked_suggestions(order, cat["sealant"])[0] == []  # gun on order, nozzle inactive, hose incomplete
    rule(cat["pan"], cat["seat"], active=False)
    assert suggestions.linked_suggestions(order, cat["pan"])[0] == []


def test_prompt_shown_as_soon_as_item_added_and_suggestion_can_be_added(client, order, cat):
    rule(cat["sealant"], cat["gun"], note="Sealant needs an applicator gun")
    client.force_login(order.owner)
    resp = client.post(reverse("orders:line_add", args=[order.pk]),
                       {"item_id": cat["sealant"].pk, "quantity": "2"}, HTTP_HX_REQUEST="true")
    page = resp.content.decode()
    assert "Commonly ordered with ADH014" in page and "Sealant gun" in page and "applicator gun" in page
    resp = client.post(reverse("orders:line_add", args=[order.pk]),
                       {"item_id": cat["gun"].pk, "quantity": "1"}, HTTP_HX_REQUEST="true")
    assert "Commonly ordered with" not in resp.content.decode()
    assert order.lines.count() == 2


# --- Kits ---------------------------------------------------------------------------

def test_adding_prompting_item_offers_the_rest_of_the_kit(order, cat, toilet_kit):
    services.add_item_line(order, order.owner, cat["pan"], {"quantity": "1"})
    offers = suggestions.kit_prompts(order, cat["pan"])
    assert [o.kit for o in offers] == [toilet_kit]
    rows = {r.item.part_no: r for r in offers[0].rows}
    assert rows["BK100"].on_order and not rows["BK100"].checked
    assert rows["BK101"].checked and rows["PLU201"].kit_item.quantity == 2
    assert not rows["PLU202"].available and not rows["PLU202"].checked


def test_non_prompting_kit_item_does_not_offer_the_kit(order, cat, toilet_kit):
    services.add_item_line(order, order.owner, cat["seat"], {"quantity": "1"})
    assert suggestions.kit_prompts(order, cat["seat"]) == []


def test_inactive_kit_is_not_offered(order, cat, toilet_kit):
    toilet_kit.is_active = False
    toilet_kit.save()
    services.add_item_line(order, order.owner, cat["pan"], {"quantity": "1"})
    assert suggestions.kit_prompts(order, cat["pan"]) == []


def kit_data(kit, **overrides):
    data = {}
    for ki in kit.kit_items.all():
        if ki.item.is_orderable:
            data[f"include_{ki.pk}"] = "1"
            data[f"quantity_{ki.pk}"] = str(ki.quantity)
    data.update(overrides)
    return data


def test_add_kit_adds_ticked_items_as_normal_lines_labelled_with_kit(order, cat, toilet_kit):
    seat = toilet_kit.kit_items.get(item=cat["seat"])
    valve = toilet_kit.kit_items.get(item=cat["valve"])
    data = kit_data(toilet_kit, **{f"quantity_{valve.pk}": "3"})
    del data[f"include_{seat.pk}"]  # untick the seat
    lines = services.add_kit(order, order.owner, toilet_kit, data)
    assert [l.part_no for l in lines] == ["BK100", "BK101", "PLU200", "PLU201"]
    assert all(l.kit_name == "Close-coupled toilet kit" for l in lines)
    assert order.lines.get(part_no="PLU201").quantity == D("3")
    order.refresh_from_db()
    assert order.order_value == D("55.00") + D("45.00") + D("4.50") + D("6.75")


def test_add_kit_is_all_or_nothing_on_bad_input(order, cat, toilet_kit):
    valve = toilet_kit.kit_items.get(item=cat["valve"])
    with pytest.raises(OrderError, match="PLU201"):
        services.add_kit(order, order.owner, toilet_kit, kit_data(toilet_kit, **{f"quantity_{valve.pk}": "0"}))
    assert order.lines.count() == 0
    with pytest.raises(OrderError, match="Tick at least one"):
        services.add_kit(order, order.owner, toilet_kit, {})


def test_kit_with_measured_item_needs_measurements(order, cat):
    kit = Kit.objects.create(name="Floor kit")
    ki = KitItem.objects.create(kit=kit, item=cat["lino"], quantity=1)
    with pytest.raises(OrderError, match="Length"):
        services.add_kit(order, order.owner, kit, {f"include_{ki.pk}": "1"})
    services.add_kit(order, order.owner, kit, {f"include_{ki.pk}": "1", f"length_{ki.pk}": "3.0", f"width_{ki.pk}": "2.0"})
    assert order.lines.get().area_m2 == D("6.00")


def test_kits_tab_shows_estimated_value(client, order, cat, toilet_kit):
    client.force_login(order.owner)
    page = client.get(reverse("orders:items", args=[order.pk]) + "?section=kits").content.decode()
    assert "Close-coupled toilet kit" in page and "Add selected" in page
    # 55 + 45 + 12 + 4.50 + 2 x 2.25 (hose is incomplete, so excluded)
    assert "Est. £121.00" in page


def test_kit_prompt_and_add_through_website(client, order, cat, toilet_kit):
    client.force_login(order.owner)
    resp = client.post(reverse("orders:line_add", args=[order.pk]), {"item_id": cat["pan"].pk, "quantity": "1"},
                       HTTP_HX_REQUEST="true")
    page = resp.content.decode()
    assert "is part of the Close-coupled toilet kit" in page
    data = kit_data(toilet_kit)
    del data[f"include_{toilet_kit.kit_items.get(item=cat['pan']).pk}"]  # pan already added
    resp = client.post(reverse("orders:kit_add", args=[order.pk, toilet_kit.pk]), data, HTTP_HX_REQUEST="true")
    page = resp.content.decode()
    assert "Added 4 items from Close-coupled toilet kit" in page and "KIT: Close-coupled toilet kit" in page
    assert order.lines.count() == 5
    assert order.lines.get(part_no="BK100").kit_name == "Close-coupled toilet kit"  # prompting line labelled too


def test_kit_label_appears_in_stores_email(order, cat, toilet_kit):
    services.add_kit(order, order.owner, toilet_kit, kit_data(toilet_kit))
    services.update_header(order, order.owner, {"job_number": "J-KIT", "property_address": "1 Road",
                                                "property_type": "void",
                                                "delivery_date": timezone.localdate() + timedelta(days=1)})
    services.submit(order, order.owner)
    msg = mail.outbox[0]
    assert "[KIT: Close-coupled toilet kit]" in msg.alternatives[0][0]
    assert "[KIT: Close-coupled toilet kit]" in msg.body


# --- Admin --------------------------------------------------------------------------

def test_admin_screens(admin_client, cat, toilet_kit):
    rule(cat["sealant"], cat["gun"])
    assert admin_client.get(reverse("admin:catalogue_kit_changelist")).status_code == 200
    assert admin_client.get(reverse("admin:catalogue_kit_change", args=[toilet_kit.pk])).status_code == 200
    assert admin_client.get(reverse("admin:catalogue_linkeditemrule_changelist")).status_code == 200
    assert admin_client.get(reverse("admin:catalogue_linkeditemrule_add")).status_code == 200


def test_rule_cannot_suggest_its_own_trigger(admin_client, cat):
    resp = admin_client.post(reverse("admin:catalogue_linkeditemrule_add"), {
        "trigger": cat["sealant"].pk, "suggested_items": [cat["sealant"].pk], "note": "", "is_active": "on"})
    assert resp.status_code == 200 and "cannot be suggested for itself" in resp.content.decode()
