"""The daily retention job: anonymise orders 30 days after delivery, delete old drafts, log every run."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEntry
from catalogue.models import Item, Section
from notifications.models import OrderEmail
from orders import services
from orders.models import Order
from retention import services as retention
from retention.models import RetentionRun

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def silicone(db):
    return Item.objects.create(part_no="ADH014", catalogue_name="Silicone", section_id=Section.ADHESIVES,
                               trade_price=D("4.20"))


@pytest.fixture
def place(make_user, silicone):
    user = make_user(full_name="Sam Ordering")

    def _place(job, delivered_days_ago, cancelled=False):
        order = services.create_draft(user, job_number=job, property_address="14 Mill Lane, Leeds",
                                      property_type="void", delivery_date=TODAY + timedelta(days=1),
                                      special_instructions="Key with neighbour at no. 12")
        services.add_item_line(order, user, silicone, {"quantity": "3"})
        services.submit(order, user)
        if cancelled:
            services.cancel(order, user)
        Order.objects.filter(pk=order.pk).update(delivery_date=TODAY - timedelta(days=delivered_days_ago))
        order.refresh_from_db()
        return order
    return _place


def test_personal_data_removed_30_days_after_delivery(place):
    due = place("J-OLD", delivered_days_ago=30)
    not_yet = place("J-RECENT", delivered_days_ago=29)
    log = retention.run()

    due.refresh_from_db()
    assert due.anonymised_at is not None
    assert due.job_number == "" and due.property_address == "" and due.requester_name == ""
    assert due.special_instructions == "" and due.owner is None
    assert due.anonymous_id.startswith("ANON-") and len(due.anonymous_id) == 11

    # Kept for analysis.
    assert due.order_number and due.submitted_at and due.property_type == "void"
    assert due.delivery_date == TODAY - timedelta(days=30) and due.order_value == D("12.60")
    line = due.lines.get()
    assert (line.part_no, line.quantity, line.unit, line.trade_price, line.section_name) == (
        "ADH014", D("3.0"), "each", D("4.2000"), "Adhesives")

    not_yet.refresh_from_db()
    assert not_yet.anonymised_at is None and not_yet.job_number == "J-RECENT"
    assert log.succeeded and log.order_numbers == [due.order_number] and log.orders_anonymised == 1


def test_cancelled_orders_are_anonymised_too(place):
    order = place("J-CANC", delivered_days_ago=45, cancelled=True)
    retention.run()
    order.refresh_from_db()
    assert order.anonymised_at and order.job_number == "" and order.cancelled_by is None


def test_each_order_gets_a_different_anonymous_id(place):
    a, b = place("J-A", 40), place("J-B", 40)
    retention.run()
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.anonymous_id != b.anonymous_id


def test_history_and_email_log_are_scrubbed(place):
    order = place("J-SCRUB", 31)
    assert AuditEntry.objects.filter(target_id=str(order.pk), summary__contains="J-SCRUB").exists()
    assert OrderEmail.objects.filter(order=order, subject__contains="J-SCRUB").exists()
    retention.run()
    entries = AuditEntry.objects.filter(target_type="orders.order", target_id=str(order.pk))
    assert entries.exists()
    for entry in entries:
        assert entry.actor is None and entry.changes == {}
        assert "J-SCRUB" not in entry.summary and "Mill Lane" not in entry.summary
    for email in OrderEmail.objects.filter(order=order):
        assert "J-SCRUB" not in email.subject


def test_no_personal_data_left_anywhere_for_the_order(place):
    order = place("J-SEARCH-999", 60)
    retention.run()
    from django.db import connection

    with connection.cursor() as cursor:
        for table in ("orders_order", "audit_auditentry", "notifications_orderemail"):
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed table names
            dump = str(cursor.fetchall())
            assert "J-SEARCH-999" not in dump and "Mill Lane" not in dump and "neighbour" not in dump


def test_anonymised_job_number_no_longer_blocks_a_new_order(place):
    place("J-REUSE", 35)
    retention.run()
    place("J-REUSE", 0)  # would raise if the old order still held the job


def test_running_twice_is_safe(place):
    order = place("J-TWICE", 40)
    retention.run()
    order.refresh_from_db()
    first_id = order.anonymous_id
    second = retention.run()
    order.refresh_from_db()
    assert order.anonymous_id == first_id and second.orders_anonymised == 0


def test_old_drafts_deleted_recent_drafts_kept(make_user):
    user = make_user()
    old = services.create_draft(user, job_number="DRAFT-OLD")
    edge = services.create_draft(user, job_number="DRAFT-29")
    fresh = services.create_draft(user, job_number="DRAFT-NEW")
    now = timezone.now()
    Order.objects.filter(pk=old.pk).update(updated_at=now - timedelta(days=31))
    Order.objects.filter(pk=edge.pk).update(updated_at=now - timedelta(days=29))
    log = retention.run(now=now)
    assert not Order.objects.filter(pk=old.pk).exists()
    assert Order.objects.filter(pk__in=[edge.pk, fresh.pk]).count() == 2
    assert log.drafts_deleted == 1


def test_dry_run_changes_nothing_but_logs(place, make_user):
    order = place("J-DRY", 40)
    draft = services.create_draft(make_user("d@bfsuk.org", "D"), job_number="OLD")
    Order.objects.filter(pk=draft.pk).update(updated_at=timezone.now() - timedelta(days=40))
    log = retention.run(dry_run=True)
    order.refresh_from_db()
    assert order.anonymised_at is None and Order.objects.filter(pk=draft.pk).exists()
    assert log.dry_run and log.orders_anonymised == 1 and log.drafts_deleted == 1


def test_every_run_is_logged_with_what_was_cleared(place):
    place("J-LOG", 40)
    call_command("run_retention")
    call_command("run_retention", "--manual")
    runs = list(RetentionRun.objects.order_by("started_at"))
    assert [r.trigger for r in runs] == ["scheduled", "manual"]
    assert runs[0].orders_anonymised == 1 and runs[1].orders_anonymised == 0
    assert all(r.succeeded and r.finished_at and r.cutoff_delivery_date == TODAY - timedelta(days=30) for r in runs)


def test_failed_run_is_logged(monkeypatch, place):
    place("J-FAIL", 40)

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(retention, "anonymise_order", boom)
    with pytest.raises(RuntimeError):
        retention.run()
    log = RetentionRun.objects.get()
    assert not log.succeeded and "database went away" in log.error


def test_anonymised_order_shows_in_historic_list_only(client, place):
    order = place("J-HIST", 40)
    retention.run()
    order.refresh_from_db()
    client.force_login(_viewer())
    hist = client.get(reverse("orders:list") + "?view=historic").content.decode()
    assert order.order_number in hist and order.anonymous_id in hist
    assert order.order_number not in client.get(reverse("orders:list") + "?view=all").content.decode()
    detail = client.get(reverse("orders:detail", args=[order.pk])).content.decode()
    assert "Personal details were removed" in detail and "Amend order" not in detail


def _viewer():
    from accounts.models import User

    return User.objects.create_user("viewer@bfsuk.org", "Viewer", "x", status=User.Status.APPROVED)


def test_admin_page_and_run_now(admin_client, place):
    place("J-PAGE", 40)
    page = admin_client.get(reverse("retention:runs")).content.decode()
    assert "Waiting for the next run" in page and "<b>1</b>" in page
    admin_client.post(reverse("retention:run_now"))
    run = RetentionRun.objects.get()
    assert run.trigger == "manual" and run.run_by == "Alex Admin" and run.orders_anonymised == 1


def test_retention_pages_admin_only(client, make_user):
    client.force_login(make_user())
    assert client.get(reverse("retention:runs")).status_code == 403
    assert client.post(reverse("retention:run_now")).status_code == 403
