"""Subsections, bulk move, imports keeping manual moves, and the stores email grouped by section."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from catalogue import importer
from catalogue.models import Item, Section, Subsection
from orders import services

pytestmark = pytest.mark.django_db


def make(code, name, section, sub=None, price="1.00"):
    return Item.objects.create(part_no=code, catalogue_name=name, section_id=section, subsection=sub,
                               trade_price=D(price))


def test_starting_subsections_and_kitchens_section_exist():
    names = lambda key: list(Subsection.objects.filter(section_id=key).values_list("name", flat=True))  # noqa: E731
    assert names(Section.ELECTRICAL) == ["Sockets and Switches", "Smoke Alarms", "Fans", "Back Boxes", "Cable"]
    assert names(Section.PLUMBING) == ["Sanitary Wear", "Wet Floors", "Showers and Accessories",
                                       "Other Common Items", "Fixtures and Fittings"]
    assert names(Section.JOINERY) == ["Internal Doors and Ironmongery", "Sheet Products", "Wet Wall and Trims", "Timber"]
    assert names(Section.KITCHENS) == ["Wall Units", "Base Units", "Worktops", "Splashbacks", "Trims"]
    assert not Section.objects.filter(key=Section.BATHROOMS_KITCHENS).exists()
    assert Section.objects.get(key=Section.KITCHENS).name == "Kitchens"


def test_order_page_filters_by_subsection(client, make_user):
    sockets = Subsection.objects.get(section_id=Section.ELECTRICAL, name="Sockets and Switches")
    make("ELE010", "Socket", Section.ELECTRICAL, sockets)
    make("ELE031", "Smoke alarm", Section.ELECTRICAL)
    user = make_user()
    client.force_login(user)
    order = services.create_draft(user)
    url = reverse("orders:items", args=[order.pk])
    page = client.get(url + "?section=electrical").content.decode()
    assert "Sockets and Switches" in page and "ELE010" in page and "ELE031" in page
    page = client.get(url + f"?section=electrical&sub={sockets.pk}").content.decode()
    assert "ELE010" in page and "ELE031" not in page


def test_subsection_sets_its_section_and_mismatch_is_rejected_in_admin(admin_client):
    sanitary = Subsection.objects.get(section_id=Section.PLUMBING, name="Sanitary Wear")
    item = make("X1", "Basin", Section.MISC)
    item.subsection = sanitary
    item.save()
    assert item.section_id == Section.PLUMBING
    url = reverse("admin:catalogue_item_change", args=[item.pk])
    form = admin_client.get(url).context["adminform"].form
    data = {k: v for k, v in form.initial.items() if v is not None and k in form.fields}
    data.update({"section": Section.ELECTRICAL, "subsection": sanitary.pk, "is_active": "on"})
    for key in ("is_flammable", "code_to_confirm", "name_check"):
        data.pop(key, None)
    resp = admin_client.post(url, data)
    assert resp.status_code == 200 and "belongs to Plumbing" in resp.content.decode()


def test_bulk_move_to_subsection(admin_client):
    a, b = make("J1", "Door", Section.JOINERY), make("J2", "Handle", Section.JOINERY)
    doors = Subsection.objects.get(section_id=Section.JOINERY, name="Internal Doors and Ironmongery")
    url = reverse("admin:catalogue_item_changelist")
    resp = admin_client.post(url, {"action": "move_to_section", ACTION_CHECKBOX_NAME: [a.pk, b.pk]})
    assert "Move these 2 items" in resp.content.decode()
    admin_client.post(url, {"action": "move_to_section", ACTION_CHECKBOX_NAME: [a.pk, b.pk],
                            "target": f"u:{doors.pk}", "apply": "1"})
    for item in (a, b):
        item.refresh_from_db()
        assert item.subsection == doors and item.section_locked


def test_reimport_keeps_manual_moves_and_does_not_deactivate_moved_items():
    csv = "Part No.,Name,Trade Price\r\nJOI080,Worktop 3m,90.00\r\nJOI001,Hinge,1.00\r\n".encode()
    importer.apply_run(importer.preview_upload("Joinery.csv", csv, None), None)
    worktop = Item.objects.get(part_no="JOI080")
    worktop.section_id = Section.KITCHENS
    worktop.subsection = Subsection.objects.get(section_id=Section.KITCHENS, name="Worktops")
    worktop.section_locked = True
    worktop.save()
    # A Kitchens file that doesn't list the worktop must not deactivate it…
    kitchens = "Part No.,Name,Trade Price\r\nBK0001,Sink,54.51\r\n".encode()
    run = importer.preview_upload("Bathrooms__Kitchens.csv", kitchens, None)
    assert run.report["removals"] == []
    importer.apply_run(run, None)
    # …and re-importing Joinery leaves it in Kitchens.
    run = importer.preview_upload("Joinery.csv", csv, None)
    assert run.report["summary"]["updates"] == 0
    importer.apply_run(run, None)
    worktop.refresh_from_db()
    assert worktop.is_active and worktop.section_id == Section.KITCHENS and worktop.subsection.name == "Worktops"


def test_removing_a_subsection_keeps_items_in_the_section():
    fans = Subsection.objects.get(section_id=Section.ELECTRICAL, name="Fans")
    item = make("E1", "Fan", Section.ELECTRICAL, fans)
    fans.delete()
    item.refresh_from_db()
    assert item.section_id == Section.ELECTRICAL and item.subsection is None and item.is_orderable


def test_stores_email_is_grouped_by_section_in_catalogue_order(make_user):
    user = make_user()
    order = services.create_draft(user, job_number="J1", property_address="1 Road", property_type="void",
                                  delivery_date=timezone.localdate() + timedelta(days=1))
    for item in (make("JOI1", "Hinge", Section.JOINERY), make("ELE1", "Socket", Section.ELECTRICAL),
                 make("ADH1", "Glue", Section.ADHESIVES), make("ELE2", "Switch", Section.ELECTRICAL)):
        services.add_item_line(order, user, item, {"quantity": "1"})
    services.add_non_stocked_line(order, user, "Odd bracket", "each", "1")
    services.submit(order, user)
    msg = mail.outbox[0]
    html, text = msg.alternatives[0][0], msg.body
    positions = [html.index(f">{name}<") for name in ("Adhesives", "Electrical", "Joinery", "Non-stocked")]
    assert positions == sorted(positions)
    assert text.index("-- ADHESIVES --") < text.index("-- ELECTRICAL --") < text.index("-- JOINERY --")
    assert "1. ADH1" in text and "2. ELE1" in text and "3. ELE2" in text and "4. JOI1" in text
