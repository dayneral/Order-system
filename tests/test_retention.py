"""The daily retention job: delete orders 30 days after delivery, delete old drafts, log every run."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core.management import call_command
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEntry
from catalogue.models import Item, Section
from notifications.models import OrderEmail
from orders import services
from orders.models import Order, OrderLine
from retention import services as retention
from retention.models import RetentionRun

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def place(make_user, db):
    user = make_user(full_name="Sam Ordering")
    item = Item.objects.create(part_no="ADH014", catalogue_name="Silicone", section_id=Section.ADHESIVES,
                               trade_price=D("4.20"))

    def _place(job, delivered_days_ago, cancelled=False):
        order = services.create_draft(user, job_number=job, property_address="14 Mill Lane, Leeds",
                                      property_type="occupied", operative_name="Jo Fitter",
                                      delivery_date=TODAY + timedelta(days=1),
                                      special_instructions="Key with neighbour at no. 12")
        services.add_item_line(order, user, item, {"quantity": "3"})
        services.submit(order, user)
        if cancelled:
            services.cancel(order, user)
        Order.objects.filter(pk=order.pk).update(delivery_date=TODAY - timedelta(days=delivered_days_ago))
        order.refresh_from_db()
        return order
    return _place


def test_orders_deleted_30_days_after_delivery(place):
    due = place("J-OLD", delivered_days_ago=30)
    not_yet = place("J-RECENT", delivered_days_ago=29)
    log = retention.run()
    assert not Order.objects.filter(pk=due.pk).exists()
    assert not OrderLine.objects.filter(order_id=due.pk).exists()
    assert not OrderEmail.objects.filter(order_id=due.pk).exists()
    assert not AuditEntry.objects.filter(target_type="orders.order", target_id=str(due.pk)).exists()
    assert Order.objects.filter(pk=not_yet.pk).exists()
    assert log.succeeded and log.order_numbers == [due.order_number] and log.orders_deleted == 1


def test_cancelled_orders_are_deleted_too(place):
    order = place("J-CANC", delivered_days_ago=45, cancelled=True)
    retention.run()
    assert not Order.objects.filter(pk=order.pk).exists()


def test_nothing_personal_left_anywhere(place):
    place("J-SEARCH-999", 60)
    retention.run()
    with connection.cursor() as cursor:
        for table in ("orders_order", "orders_orderline", "audit_auditentry", "notifications_orderemail",
                      "retention_retentionrun"):
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed table names
            dump = str(cursor.fetchall())
            for text in ("J-SEARCH-999", "Mill Lane", "neighbour", "Jo Fitter"):
                assert text not in dump, (table, text)


def test_job_number_free_again_after_deletion(place):
    place("J-REUSE", 35)
    retention.run()
    place("J-REUSE", 0)


def test_running_twice_is_safe(place):
    place("J-TWICE", 40)
    retention.run()
    assert retention.run().orders_deleted == 0


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
    assert Order.objects.filter(pk__in=[order.pk, draft.pk]).count() == 2
    assert log.dry_run and log.orders_deleted == 1 and log.drafts_deleted == 1


def test_every_run_is_logged(place):
    place("J-LOG", 40)
    call_command("run_retention")
    call_command("run_retention", "--manual")
    runs = list(RetentionRun.objects.order_by("started_at"))
    assert [r.trigger for r in runs] == ["scheduled", "manual"]
    assert runs[0].orders_deleted == 1 and runs[1].orders_deleted == 0
    assert all(r.succeeded and r.finished_at and r.cutoff_delivery_date == TODAY - timedelta(days=30) for r in runs)


def test_failed_run_is_logged(monkeypatch, place):
    place("J-FAIL", 40)

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(retention, "delete_order_completely", boom)
    with pytest.raises(RuntimeError):
        retention.run()
    log = RetentionRun.objects.get()
    assert not log.succeeded and "database went away" in log.error


def test_admin_page_and_run_now(admin_client, place):
    place("J-PAGE", 40)
    page = admin_client.get(reverse("retention:runs")).content.decode()
    assert "Waiting for the next run" in page and "<b>1</b>" in page
    admin_client.post(reverse("retention:run_now"))
    run = RetentionRun.objects.get()
    assert run.trigger == "manual" and run.run_by == "Alex Admin" and run.orders_deleted == 1


def test_retention_pages_admin_only(client, make_user):
    client.force_login(make_user())
    assert client.get(reverse("retention:runs")).status_code == 403
    assert client.post(reverse("retention:run_now")).status_code == 403
