"""
Daily data retention job. Render runs it every night; it can also be run by hand:

    python manage.py run_retention            # do it
    python manage.py run_retention --dry-run  # only report what would happen
"""

from django.core.management.base import BaseCommand

from retention import services
from retention.models import RetentionRun


class Command(BaseCommand):
    help = "Anonymise orders 30 days after delivery and delete drafts not saved for 30 days."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be done without changing anything.")
        parser.add_argument("--manual", action="store_true", help="Record the run as run by hand (not the daily job).")

    def handle(self, dry_run=False, manual=False, **options):
        log = services.run(dry_run=dry_run,
                           trigger=RetentionRun.Trigger.MANUAL if manual else RetentionRun.Trigger.SCHEDULED)
        prefix = "DRY RUN, nothing changed: " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}{log.orders_anonymised} order(s) anonymised (delivered on or before "
            f"{log.cutoff_delivery_date:%d %b %Y}), {log.drafts_deleted} old draft(s) deleted."))
        if log.order_numbers:
            self.stdout.write("Orders: " + ", ".join(log.order_numbers))
