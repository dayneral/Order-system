"""Active/inactive sections, kits inside sections with automatic linked rules, and the Sort catalogue page."""

import json
from decimal import Decimal as D

import pytest
from django.urls import reverse

from catalogue.kits import sync_kit_rules
from catalogue.models import Item, Kit, KitItem, LinkedItemRule, Section, Subsection
from orders import services, suggestions

pytestmark = pytest.mark.django_db


def make(code, section=Section.PLUMBING, sub=None):
    return Item.objects.create(part_no=code, catalogue_name=f"Item {code}", section_id=section, subsection=sub,
                               trade_price=D("1"))


def items_page(client, order, query):
    return client.get(reverse("orders:items", args=[order.pk]) + query).content.decode()


# --- Active / inactive sections ----------------------------------------------------------

def test_inactive_section_hidden_from_users_but_shown_to_admins(client, make_user, admin_user):
    Section.objects.filter(key=Section.FLOORING).update(is_active=False)
    make("V1", Section.FLOORING)
    user = make_user()
    order = services.create_draft(user)
    client.force_login(user)
    page = items_page(client, order, "?section=adhesives")
    assert ">Flooring" not in page
    assert "Item V1" not in items_page(client, order, "?q=V1")  # search too
    assert "Item V1" not in items_page(client, order, "?section=flooring")
    assert "Item V1" not in items_page(client, order, "?section=hidden")
    assert "Hidden" not in page
    client.force_login(admin_user)
    admin_order = services.create_draft(admin_user)
    page = items_page(client, admin_order, "?section=adhesives")
    assert ">Flooring" not in page and "?section=hidden" in page and "V1" not in page
    page = items_page(client, admin_order, "?section=hidden")
    assert "Flooring" in page and "V1" in page


def test_hidden_subsection_listed_in_admin_hidden_tab(client, admin_user):
    sub = Subsection.objects.get(section_id=Section.PLUMBING, name="Wet Floors")
    sub.is_active = False
    sub.save()
    make("PLU9", Section.PLUMBING, sub)
    client.force_login(admin_user)
    order = services.create_draft(admin_user)
    assert "PLU9" not in items_page(client, order, "?section=plumbing&sub=all")
    page = items_page(client, order, f"?section=hidden&sub={sub.pk}")
    assert "Plumbing › Wet Floors" in page and "PLU9" in page


def test_inactive_subsection_hidden_from_users(client, make_user):
    sub = Subsection.objects.get(section_id=Section.PLUMBING, name="Wet Floors")
    sub.is_active = False
    sub.save()
    make("PLU9", Section.PLUMBING, sub)
    user = make_user()
    order = services.create_draft(user)
    client.force_login(user)
    page = items_page(client, order, "?section=plumbing&sub=all")
    assert "Wet Floors" not in page and "PLU9" not in page


# --- Kits inside sections and their linked rules ----------------------------------------

def kit_with(*items, prompt_first=True, section=Section.PLUMBING):
    kit = Kit.objects.create(name="WC kit", section_id=section)
    for pos, item in enumerate(items):
        KitItem.objects.create(kit=kit, item=item, quantity=1, prompts_kit=prompt_first and pos == 0, position=pos)
    sync_kit_rules(kit)
    return kit


def test_kit_creates_linked_rules_for_each_item_and_stays_in_step():
    pan, cistern, seat = make("P1"), make("P2"), make("P3")
    kit = kit_with(pan, cistern, seat)
    rules = {r.trigger.part_no: sorted(i.part_no for i in r.suggested_items.all()) for r in kit.link_rules.all()}
    assert rules == {"P1": ["P2", "P3"], "P2": ["P1", "P3"], "P3": ["P1", "P2"]}
    KitItem.objects.filter(kit=kit, item=seat).delete()
    sync_kit_rules(kit)
    assert sorted(r.trigger.part_no for r in kit.link_rules.all()) == ["P1", "P2"]
    kit.is_active = False
    kit.save()
    sync_kit_rules(kit)
    assert not kit.link_rules.filter(is_active=True).exists()


def test_prompting_item_gets_kit_prompt_others_get_linked_suggestions(make_user):
    pan, cistern, seat = make("P1"), make("P2"), make("P3")
    kit_with(pan, cistern, seat)
    user = make_user()
    order = services.create_draft(user)
    services.add_item_line(order, user, pan, {"quantity": "1"})
    assert suggestions.linked_suggestions(order, pan)[0] == []  # kit prompt covers it
    assert suggestions.kit_prompts(order, pan)
    order2 = services.create_draft(user)
    services.add_item_line(order2, user, seat, {"quantity": "1"})
    items, notes = suggestions.linked_suggestions(order2, seat)
    assert [i.part_no for i in items] == ["P1", "P2"] and notes == ["Part of the WC kit"]


def test_kit_rules_hidden_from_linked_items_screen(admin_client):
    kit_with(make("P1"), make("P2"))
    LinkedItemRule.objects.create(trigger=make("P3")).suggested_items.set([Item.objects.get(part_no="P1")])
    page = admin_client.get(reverse("admin:catalogue_linkeditemrule_changelist")).content.decode()
    assert "1 linked item rule" in page


def test_kit_saved_in_admin_syncs_rules(admin_client):
    a, b = make("P1"), make("P2")
    data = {"name": "Basin kit", "description": "", "section": Section.PLUMBING, "subsection": "", "is_active": "on",
            "kit_items-TOTAL_FORMS": "2", "kit_items-INITIAL_FORMS": "0", "kit_items-MIN_NUM_FORMS": "0",
            "kit_items-MAX_NUM_FORMS": "1000",
            "kit_items-0-item": a.pk, "kit_items-0-quantity": "1", "kit_items-0-position": "0",
            "kit_items-1-item": b.pk, "kit_items-1-quantity": "1", "kit_items-1-position": "1"}
    resp = admin_client.post(reverse("admin:catalogue_kit_add"), data)
    assert resp.status_code == 302, resp.content.decode()[:500]
    assert Kit.objects.get(name="Basin kit").link_rules.count() == 2


# --- Sort catalogue page -----------------------------------------------------------------

def post_json(client, name, body, *args):
    return client.post(reverse(f"catalogue:{name}", args=args), json.dumps(body), content_type="application/json")


def test_sort_page_lists_unsorted_items(admin_client):
    sub = Subsection.objects.get(section_id=Section.ELECTRICAL, name="Fans")
    make("E1", Section.ELECTRICAL)
    make("E2", Section.ELECTRICAL, sub)
    page = admin_client.get(reverse("catalogue:sort") + "?section=electrical").content.decode()
    assert 'data-id="' in page and "E1" in page and "E2" not in page
    page = admin_client.get(reverse("catalogue:sort") + "?section=electrical&show=all").content.decode()
    assert "E2" in page


def test_drop_items_on_subsection_then_undo(admin_client):
    a, b = make("E1", Section.ELECTRICAL), make("E2", Section.ELECTRICAL)
    fans = Subsection.objects.get(section_id=Section.ELECTRICAL, name="Fans")
    res = post_json(admin_client, "sort_move", {"items": [a.pk, b.pk], "target": f"sub:{fans.pk}"}).json()
    assert res["ok"] and res["moved"] == [a.pk, b.pk]
    for item in (a, b):
        item.refresh_from_db()
        assert item.subsection == fans and item.section_locked
    assert post_json(admin_client, "sort_undo", {}).json()["ok"]
    a.refresh_from_db()
    assert a.subsection is None and not a.section_locked


def test_drop_items_on_another_section_and_on_a_kit(admin_client):
    a = make("E1", Section.ELECTRICAL)
    post_json(admin_client, "sort_move", {"items": [a.pk], "target": "sec:plumbing"})
    a.refresh_from_db()
    assert a.section_id == Section.PLUMBING and a.subsection is None
    kit = Kit.objects.create(name="Kit", section_id=Section.PLUMBING)
    b = make("P5")
    res = post_json(admin_client, "sort_move", {"items": [a.pk, b.pk], "target": f"kit:{kit.pk}"}).json()
    assert res["kit_count"] == 2 and kit.link_rules.count() == 2
    post_json(admin_client, "sort_undo", {})
    assert kit.kit_items.count() == 0 and kit.link_rules.count() == 0


def test_add_rename_and_reorder_subsections_and_create_kit(admin_client):
    admin_client.post(reverse("catalogue:sort_add_subsection", args=["joinery"]), {"name": "Screws and Fixings"})
    sub = Subsection.objects.get(section_id=Section.JOINERY, name="Screws and Fixings")
    assert post_json(admin_client, "sort_rename", {"name": "Fixings"}, sub.pk).json()["ok"]
    sub.refresh_from_db()
    assert sub.name == "Fixings"
    ids = list(Subsection.objects.filter(section_id=Section.JOINERY).values_list("pk", flat=True))
    post_json(admin_client, "sort_reorder", {"ids": [sub.pk] + [i for i in ids if i != sub.pk]}, "joinery")
    assert Subsection.objects.filter(section_id=Section.JOINERY).order_by("sort_order").first() == sub
    admin_client.post(reverse("catalogue:sort_add_kit", args=["joinery"]), {"name": "Door kit"})
    assert Kit.objects.get(name="Door kit").section_id == Section.JOINERY


def test_sort_page_admin_only(client, make_user):
    client.force_login(make_user())
    assert client.get(reverse("catalogue:sort")).status_code == 403
    assert post_json(client, "sort_move", {"items": [1], "target": "sec:plumbing"}).status_code == 403
