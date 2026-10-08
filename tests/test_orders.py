"""Ordering: drafts, lines, submit, one order per job, amend, cancel, lists."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEntry
from catalogue.models import Item, MeasureType, Section
from orders import services
from orders.models import Order, OrderLine
from orders.services import OrderError

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db

TOMORROW = timezone.localdate() + timedelta(days=1)


@pytest.fixture
def items(db):
    def make(code, name, price, section=Section.ADHESIVES, **kw):
        return Item.objects.create(part_no=code, catalogue_name=name, section_id=section,
                                   trade_price=D(str(price)) if price is not None else None, **kw)
    return {
        "silicone": make("ADH014", "Multi-purpose silicone 270ml white", "4.20"),
        "screws": make("JOI050", "Screws M4 x 50mm, per 100", "3.49", Section.JOINERY,
                       measure_type=MeasureType.PACK, pack_size=100, unit="pack"),
        "vinyl": make("V0010", "Safety vinyl grey", "18.50", Section.FLOORING, measure_type=MeasureType.AREA, unit="m²"),
        "worktop": make("BK010", "Black granite bullnose worktop", "90.00", Section.KITCHENS,
                        measure_type=MeasureType.CUT_TO_ORDER, unit="m", catalogue_length_m=D("3.0")),
        "spirit": make("FLA001", "White spirit 1L", "3.00", Section.FLAMMABLE),
        "incomplete": make("PLA001", "Bonding plaster", None, Section.PLASTERING),
    }


def full_header(job="J1001"):
    return {"job_number": job, "property_address": "1 High Street, Leeds", "property_type": "void",
            "delivery_date": TOMORROW, "special_instructions": ""}


def ready_draft(user, items, job="J1001"):
    order = services.create_draft(user, **full_header(job))
    services.add_item_line(order, user, items["silicone"], {"quantity": "2"})
    return order


# --- Lines and drafts -----------------------------------------------------------

def test_lines_for_each_measure_type_and_order_value(make_user, items):
    user = make_user()
    order = services.create_draft(user)
    services.add_item_line(order, user, items["silicone"], {"quantity": "3"})  # 12.60
    services.add_item_line(order, user, items["screws"], {"quantity": "2"})  # 6.98
    area = services.add_item_line(order, user, items["vinyl"], {"length": "3.2", "width": "2.0"})  # 6.40 m2 -> 118.40
    cut = services.add_item_line(order, user, items["worktop"], {"length": "1.9"})  # 57.00 estimate
    services.add_non_stocked_line(order, user, "Special hinge", "each", "4")
    order.refresh_from_db()
    assert area.area_m2 == D("6.40") and area.measurement_text == "cut to 3.2m × 2.0m = 6.40 m²"
    assert cut.is_estimate and cut.measurement_text == "cut to 1.9m"
    assert order.order_value == D("194.98")
    assert order.lines.get(is_non_stocked=True).line_value is None


def test_measurement_validation(make_user, items):
    user = make_user()
    order = services.create_draft(user)
    for item, data in [
        (items["silicone"], {"quantity": "1.5"}),
        (items["silicone"], {"quantity": "0"}),
        (items["vinyl"], {"length": "3.25", "width": "2"}),  # not to 0.1m
        (items["vinyl"], {"length": "3"}),  # width missing
        (items["worktop"], {"length": "-1"}),
    ]:
        with pytest.raises(OrderError):
            services.add_item_line(order, user, item, data)
    assert order.lines.count() == 0


def test_incomplete_and_inactive_items_cannot_be_ordered(make_user, items):
    user = make_user()
    order = services.create_draft(user)
    with pytest.raises(OrderError, match="incomplete"):
        services.add_item_line(order, user, items["incomplete"], {"quantity": "1"})
    items["silicone"].is_active = False
    items["silicone"].save()
    with pytest.raises(OrderError, match="no longer available"):
        services.add_item_line(order, user, items["silicone"], {"quantity": "1"})


def test_temporary_code_items_are_orderable(make_user, items):
    user = make_user()
    temp = Item.objects.create(part_no="PLU0-A", catalogue_name="Unknown fitting", section_id=Section.PLUMBING,
                               trade_price=1, code_to_confirm=True)
    line = services.add_item_line(services.create_draft(user), user, temp, {"quantity": "1"})
    assert line.part_no == "PLU0-A"


def test_flammable_items_are_flagged_on_the_line(make_user, items):
    user = make_user()
    line = services.add_item_line(services.create_draft(user), user, items["spirit"], {"quantity": "1"})
    assert line.is_flammable


def test_drafts_are_private(client, make_user, items):
    owner, other = make_user(), make_user("other@bfsuk.org", "Pat Other")
    order = ready_draft(owner, items)
    client.force_login(other)
    assert client.get(reverse("orders:detail", args=[order.pk])).status_code == 404
    assert client.get(reverse("orders:edit", args=[order.pk])).status_code == 404
    assert "J1001" not in client.get(reverse("orders:list") + "?view=all").content.decode()


def test_new_order_opens_the_order_page_directly_and_reuses_an_empty_draft(client, make_user, items):
    user = make_user()
    client.force_login(user)
    resp = client.get(reverse("orders:new"))
    order = Order.objects.get()
    assert resp.url == reverse("orders:edit", args=[order.pk]) and order.is_draft and order.owner == user
    client.get(reverse("orders:new"))
    assert Order.objects.count() == 1  # untouched draft reused, not duplicated
    assert "No drafts" in client.get(reverse("orders:list")).content.decode()  # empty draft not listed
    services.add_item_line(order, user, items["silicone"], {"quantity": "1"})
    client.get(reverse("orders:new"))
    assert Order.objects.count() == 2  # a started draft is kept; a fresh one is made


def test_draft_can_be_saved_incomplete_and_resumed(client, make_user, items):
    user = make_user()
    client.force_login(user)
    client.get(reverse("orders:new"))
    order = Order.objects.get()
    resp = client.post(reverse("orders:edit", args=[order.pk]), {"job_number": "J77", "action": "save"})
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.is_draft and order.job_number == "J77" and order.owner == user
    page = client.get(reverse("orders:list")).content.decode()
    assert "J77" in page and "Continue" in page


# --- Submit ------------------------------------------------------------------------

def test_submit_sets_number_date_requester_and_fixes_prices(make_user, items):
    user = make_user(full_name="Sam Ordering")
    order = ready_draft(user, items)
    items["silicone"].trade_price = D("5.00")  # price changed while drafting
    items["silicone"].save()
    services.submit(order, user)
    order.refresh_from_db()
    year = timezone.localdate().year
    assert order.status == Order.Status.SUBMITTED
    assert order.order_number == f"BFS-{year}-000001"
    assert order.submitted_at is not None and order.requester_name == "Sam Ordering"
    line = order.lines.get()
    assert line.trade_price == D("5.00") and order.order_value == D("10.00")

    # Later price changes do not alter the submitted order.
    items["silicone"].trade_price = D("9.99")
    items["silicone"].save()
    line.refresh_from_db()
    assert line.trade_price == D("5.00")
    assert AuditEntry.objects.filter(action="order.submit", target_id=str(order.pk)).exists()


def test_order_numbers_are_sequential(make_user, items):
    user = make_user()
    numbers = []
    for job in ("A1", "A2", "A3"):
        order = ready_draft(user, items, job)
        services.submit(order, user)
        numbers.append(order.order_number)
    year = timezone.localdate().year
    assert numbers == [f"BFS-{year}-000001", f"BFS-{year}-000002", f"BFS-{year}-000003"]


def test_submit_requires_header_and_items(make_user, items):
    user = make_user()
    order = services.create_draft(user)
    with pytest.raises(OrderError) as exc:
        services.submit(order, user)
    text = " ".join(exc.value.errors)
    for expected in ("job number", "property address", "property type", "delivery date", "at least one item"):
        assert expected in text
    assert Order.objects.get().is_draft


def test_delivery_date_cannot_be_in_the_past(make_user, items):
    user = make_user()
    order = ready_draft(user, items)
    with pytest.raises(OrderError, match="past"):
        services.update_header(order, user, {"delivery_date": timezone.localdate() - timedelta(days=1)})
    order = ready_draft(user, items, "J2")
    Order.objects.filter(pk=order.pk).update(delivery_date=timezone.localdate() - timedelta(days=1))
    order.refresh_from_db()
    with pytest.raises(OrderError, match="past"):
        services.submit(order, user)


def test_submit_blocked_if_item_became_unavailable(make_user, items):
    user = make_user()
    order = ready_draft(user, items)
    items["silicone"].is_active = False
    items["silicone"].save()
    with pytest.raises(OrderError, match="no longer available"):
        services.submit(order, user)


def test_only_owner_submits_their_draft(make_user, items, admin_user):
    order = ready_draft(make_user(), items)
    with pytest.raises(OrderError):
        services.submit(order, admin_user)


# --- One order per job --------------------------------------------------------------

def test_second_active_order_for_same_job_is_blocked(make_user, items):
    first_user, second_user = make_user(), make_user("b@bfsuk.org", "Bo Second")
    first = ready_draft(first_user, items, "J-500")
    services.submit(first, first_user)
    second = ready_draft(second_user, items, " j-500 ")  # same job, different spacing and case
    with pytest.raises(OrderError) as exc:
        services.submit(second, second_user)
    assert first.order_number in exc.value.errors[-1]
    second.refresh_from_db()
    assert second.is_draft


def test_drafts_do_not_block_a_job(make_user, items):
    user = make_user()
    ready_draft(user, items, "J9")
    other = ready_draft(user, items, "J9")
    services.submit(other, user)  # fine: the first is only a draft


def test_job_can_be_reordered_after_cancellation(make_user, items):
    user = make_user()
    first = ready_draft(user, items, "J-600")
    services.submit(first, user)
    services.cancel(first, user)
    second = ready_draft(user, items, "J-600")
    services.submit(second, user)
    assert second.status == Order.Status.SUBMITTED


def test_database_enforces_one_active_order_per_job(make_user, items):
    from django.db import IntegrityError, transaction

    user = make_user()
    services.submit(ready_draft(user, items, "J-700"), user)
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.create(owner=user, job_number="J-700", job_number_key="J-700", status=Order.Status.SUBMITTED,
                             order_number="BFS-TEST-1")


def test_amending_job_number_to_one_with_active_order_is_blocked(make_user, items):
    user = make_user()
    services.submit(ready_draft(user, items, "J-1"), user)
    second = ready_draft(user, items, "J-2")
    services.submit(second, user)
    with pytest.raises(OrderError, match="already has an active order"):
        services.update_header(second, user, {"job_number": "J-1"})


def test_draft_saved_with_job_that_has_active_order_shows_warning(client, make_user, items):
    user = make_user()
    services.submit(ready_draft(user, items, "J-800"), user)
    client.force_login(user)
    draft = services.create_draft(user)
    resp = client.post(reverse("orders:edit", args=[draft.pk]), {"job_number": "J-800", "action": "save"})
    assert any("already has an active order" in str(m) for m in get_messages(resp.wsgi_request))


# --- Amend and cancel ---------------------------------------------------------------

def test_owner_amends_submitted_order_with_audit_and_resend_flag(make_user, items):
    user = make_user()
    order = ready_draft(user, items)
    services.submit(order, user)
    line = order.lines.get()
    services.update_line(line, user, {"quantity": "5"})
    services.add_item_line(order, user, items["screws"], {"quantity": "1"})
    services.update_header(order, user, {**full_header(), "delivery_date": TOMORROW + timedelta(days=2),
                                         "special_instructions": "Leave with site manager"})
    order.refresh_from_db()
    assert order.amendment_pending and order.amended_at
    line.refresh_from_db()
    assert line.trade_price == D("4.20")  # keeps price it was ordered at
    assert order.order_value == D("24.49")  # 5 x 4.20 + 3.49
    entries = AuditEntry.objects.filter(action="order.amend", target_id=str(order.pk))
    assert entries.count() == 3
    assert all(e.actor == user for e in entries)
    services.finish_amendment(order, user)
    order.refresh_from_db()
    assert not order.amendment_pending


def test_admin_can_amend_and_cancel_but_other_users_cannot(make_user, items, admin_user):
    owner, other = make_user(), make_user("o@bfsuk.org", "Other")
    order = ready_draft(owner, items)
    services.submit(order, owner)
    assert services.can_edit(order, admin_user) and services.can_cancel(order, admin_user)
    assert not services.can_edit(order, other) and not services.can_cancel(order, other)
    with pytest.raises(OrderError):
        services.update_line(order.lines.get(), other, {"quantity": "9"})
    services.cancel(order, admin_user, "Duplicate")
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED and order.cancelled_by == admin_user
    assert AuditEntry.objects.get(action="order.cancel").summary.endswith("Duplicate")
    assert not services.can_edit(order, owner)


def test_submitted_order_must_keep_one_item(make_user, items):
    user = make_user()
    order = ready_draft(user, items)
    services.submit(order, user)
    with pytest.raises(OrderError, match="at least one item"):
        services.remove_line(order.lines.get(), user)


def test_anonymised_orders_cannot_be_changed(make_user, items, admin_user):
    user = make_user()
    order = ready_draft(user, items)
    services.submit(order, user)
    order.anonymised_at = timezone.now()
    order.save()
    assert not services.can_edit(order, admin_user) and not services.can_cancel(order, admin_user)


# --- Lists --------------------------------------------------------------------------

def test_my_all_and_historic_order_lists(client, make_user, items):
    me, colleague = make_user(), make_user("c@bfsuk.org", "Col League")
    mine = ready_draft(me, items, "MINE-1")
    services.submit(mine, me)
    theirs = ready_draft(colleague, items, "THEIRS-1")
    services.submit(theirs, colleague)
    old = ready_draft(colleague, items, "OLD-1")
    services.submit(old, colleague)
    Order.objects.filter(pk=old.pk).update(anonymised_at=timezone.now(), job_number="", job_number_key="",
                                           property_address="", requester_name="", owner=None,
                                           anonymous_id="ANON-7Q2K")
    client.force_login(me)
    my_page = client.get(reverse("orders:list") + "?view=mine").content.decode()
    all_page = client.get(reverse("orders:list") + "?view=all").content.decode()
    hist_page = client.get(reverse("orders:list") + "?view=historic").content.decode()
    assert "MINE-1" in my_page and "THEIRS-1" not in my_page
    assert "MINE-1" in all_page and "THEIRS-1" in all_page and old.order_number not in all_page
    assert old.order_number in hist_page and "ANON-7Q2K" in hist_page and "MINE-1" not in hist_page
    # Any user can open another user's submitted order, but not change it.
    detail = client.get(reverse("orders:detail", args=[theirs.pk])).content.decode()
    assert "THEIRS-1" in detail and "Amend order" not in detail
    assert client.get(reverse("orders:edit", args=[theirs.pk])).status_code == 403


# --- Old drafts --------------------------------------------------------------------

def test_sign_in_warns_about_drafts_due_for_deletion(client, make_user, items):
    user = make_user()
    old = services.create_draft(user, job_number="OLD-DRAFT")
    fresh = services.create_draft(user, job_number="NEW-DRAFT")
    Order.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=27))
    assert list(services.drafts_due_for_deletion(user)) == [Order.objects.get(pk=old.pk)]
    resp = client.post(reverse("accounts:login"), {"username": user.email, "password": PASSWORD}, follow=True)
    text = resp.content.decode()
    assert "will be deleted soon" in text and "OLD-DRAFT" in text and "NEW-DRAFT" not in text.split("deleted soon")[1].split("</div>")[0]
    assert fresh.pk


# --- Website flow -------------------------------------------------------------------

def test_full_order_through_the_website(client, make_user, items):
    user = make_user()
    client.force_login(user)
    client.get(reverse("orders:new"))
    order = Order.objects.get()
    edit = reverse("orders:edit", args=[order.pk])
    assert client.get(edit).status_code == 200
    assert "ADH014" in client.get(reverse("orders:items", args=[order.pk]) + "?q=silicone").content.decode()

    resp = client.post(reverse("orders:line_add", args=[order.pk]),
                       {"item_id": items["vinyl"].pk, "length": "2.5", "width": "2.0"}, HTTP_HX_REQUEST="true")
    assert "5.00 m²" in resp.content.decode() and "£92.50" in resp.content.decode()
    resp = client.post(reverse("orders:line_add", args=[order.pk]),
                       {"item_id": items["silicone"].pk, "quantity": "0"}, HTTP_HX_REQUEST="true")
    assert "msg-error" in resp.content.decode()
    client.post(reverse("orders:line_add_non_stocked", args=[order.pk]),
                {"name": "Odd bracket", "unit": "each", "quantity": "2"}, HTTP_HX_REQUEST="true")
    line = order.lines.get(is_non_stocked=False)
    client.post(reverse("orders:line_update", args=[order.pk, line.pk]), {"length": "3.0", "width": "2.0"},
                HTTP_HX_REQUEST="true")

    data = {**full_header("WEB-1"), "delivery_date": TOMORROW.isoformat(), "action": "submit"}
    resp = client.post(edit, data)
    order.refresh_from_db()
    assert resp.status_code == 302 and order.status == Order.Status.SUBMITTED
    assert order.order_value == D("111.00")
    detail = client.get(reverse("orders:detail", args=[order.pk])).content.decode()
    assert "NON-STOCKED" in detail and "Odd bracket" in detail and order.order_number in detail


def test_submit_errors_shown_on_page(client, make_user, items):
    user = make_user()
    client.force_login(user)
    order = services.create_draft(user)
    resp = client.post(reverse("orders:edit", args=[order.pk]), {"action": "submit"})
    page = resp.content.decode()
    assert resp.status_code == 200 and "could not be submitted" in page and "Add at least one item" in page
