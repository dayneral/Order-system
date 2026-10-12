"""
Nightly supervisor email (Render runs it after the retention job). By hand:

    python manage.py send_void_digest            # send
    python manage.py send_void_digest --dry-run  # only report who would get what
"""

from django.core.management.base import BaseCommand

from voids.digest import send_all


class Command(BaseCommand):
    help = "Email each supervisor their voids needing attention, snagging first."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--supervisor", help="Only this supervisor (by name).")

    def handle(self, dry_run=False, supervisor=None, **options):
        for sup, email, counts, sent in send_all(dry_run=dry_run, only=supervisor):
            summary = ", ".join(f"{k} {n}" for k, n in counts.items() if n) or "nothing to report"
            status = "sent" if sent else ("no email address" if not email else
                                          ("not sent (dry run)" if dry_run and any(counts.values()) else "skipped"))
            self.stdout.write(f"{sup.name} <{email or '-'}>: {summary} – {status}")
