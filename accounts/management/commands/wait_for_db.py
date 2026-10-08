"""
Wait until the database accepts connections (used before migrations on Render,
where a new database can take a few minutes to start).

    python manage.py wait_for_db --timeout 300
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Wait until the database is reachable."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=300, help="Seconds to keep trying (default 300).")

    def handle(self, timeout=300, **options):
        deadline = time.monotonic() + timeout
        while True:
            try:
                connection.ensure_connection()
                self.stdout.write(self.style.SUCCESS("Database is available."))
                return
            except OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise CommandError(f"Database still not reachable after {timeout}s: {exc}") from exc
                self.stdout.write("Database not ready yet, retrying in 5 seconds...")
                connection.close()
                time.sleep(5)
