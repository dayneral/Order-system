"""Admins can rename, add and remove sections; removing a section keeps its items."""

from decimal import Decimal as D

import pytest
from django.urls import reverse

from audit.models import AuditEntry
from catalogue import importer
from catalogue.models import Item, Section
from orders import services

pytestmark = pytest.mark.django_db

CSV = "Part No.,Name,Trade Price\r\n{code},{name},1.00\r\n"


def imp(filename, code="X001", name="Thing"):
    importer.apply_run(importer.preview_upload(filename, CSV.format(code=code, name=name).encode(), None), None)
    return Item.objects.get(part_no=code)


def test_admin_can_add_a_section_and_import_matches_it_by_name(admin_client):
    resp = admin_client.post(reverse("admin:catalogue_section_add"),
                             {"name": "Decorating", "sort_order": 55, "is_flammable": ""})
    assert resp.status_code == 302
    section = Section.objects.get(name="Decorating")
    assert section.key == "decorating"
    assert imp("1a2b3c4d-Decorating.csv").section == section
    assert AuditEntry.objects.filter(action="section.add").exists()


def test_renaming_keeps_items_and_import_recognises_old_and_new_names(admin_client):
    item = imp("Plumbing.csv", "PLU001", "Tap")
    plumbing = Section.objects.get(key=Section.PLUMBING)
    admin_client.post(reverse("admin:catalogue_section_change", args=[plumbing.pk]),
                      {"name": "Plumbing & Heating", "sort_order": plumbing.sort_order})
    plumbing.refresh_from_db()
    item.refresh_from_db()
    assert plumbing.name == "Plumbing & Heating" and item.section == plumbing
    assert imp("Plumbing & Heating.csv", "PLU002").section == plumbing
    assert imp("Plumbing.csv", "PLU003").section == plumbing  # old file names still work
    assert AuditEntry.objects.get(action="section.edit").changes["name"]["to"] == "Plumbing & Heating"


def test_removing_a_section_keeps_its_items_as_unsectioned(admin_client, make_user):
    item = imp("Joinery.csv", "JOI001", "Hinge")
    joinery = Section.objects.get(key=Section.JOINERY)
    admin_client.post(reverse("admin:catalogue_section_delete", args=[joinery.pk]), {"post": "yes"})
    assert not Section.objects.filter(key=Section.JOINERY).exists()
    item.refresh_from_db()
    assert item.section is None and item.is_active and item.is_orderable
    assert "moved to Unsectioned" in AuditEntry.objects.get(action="section.delete").summary

    # Still orderable, shown under "Other" on the order page.
    user = make_user()
    order = services.create_draft(user)
    admin_client.force_login(user)
    page = admin_client.get(reverse("orders:items", args=[order.pk]) + "?section=unsectioned").content.decode()
    assert "JOI001" in page and ">Other<" in page
    line = services.add_item_line(order, user, item, {"quantity": "1"})
    assert line.section_name == ""


def test_import_into_removed_section_falls_back_to_miscellaneous():
    Section.objects.filter(key=Section.JOINERY).delete()
    assert imp("Joinery.csv", "JOI002").section_id == Section.MISC


def test_flammable_flag_follows_the_section_setting():
    store = Section.objects.create(name="Paint store", is_flammable=True)
    item = Item.objects.create(part_no="P1", catalogue_name="Paint", section=store, trade_price=D("1"))
    assert item.is_flammable
    assert Section.objects.get(key=Section.FLAMMABLE).is_flammable  # set by migration


def test_past_orders_keep_their_section_name(make_user):
    item = imp("Adhesives.csv", "ADH001", "Glue")
    user = make_user()
    order = services.create_draft(user)
    line = services.add_item_line(order, user, item, {"quantity": "1"})
    Section.objects.filter(key=Section.ADHESIVES).update(name="Glues")
    line.refresh_from_db()
    assert line.section_name == "Adhesives"


def test_sections_screen_admin_only(client, make_user):
    client.force_login(make_user())
    assert client.get(reverse("admin:catalogue_section_changelist")).status_code == 302
