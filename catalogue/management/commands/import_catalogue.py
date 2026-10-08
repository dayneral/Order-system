"""
Import the catalogue from the command line.

    python manage.py import_catalogue path/to/catalogue.xlsx           # preview only
    python manage.py import_catalogue path/to/catalogue.xlsx --apply   # preview and apply
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from catalogue import importer
from catalogue.reader import SpreadsheetError


class Command(BaseCommand):
    help = "Preview (and optionally apply) a catalogue import from a .xlsx or .csv file."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--apply", action="store_true", help="Apply the changes after showing the report.")
        parser.add_argument("--user", help="Email of the admin to record as doing the import.")

    def handle(self, path, apply=False, user=None, **options):
        file = Path(path)
        if not file.exists():
            raise CommandError(f"File not found: {file}")
        actor = None
        if user:
            actor = User.objects.filter(email__iexact=user).first()
            if actor is None:
                raise CommandError(f"No user with email {user}")
        try:
            run = importer.preview_upload(file.name, file.read_bytes(), actor)
        except SpreadsheetError as exc:
            raise CommandError(str(exc)) from exc

        report = run.report
        if apply:
            report = importer.apply_run(run, actor)
        self.print_report(report)
        if apply:
            self.stdout.write(self.style.SUCCESS("Import applied."))
        else:
            self.stdout.write(self.style.WARNING(
                f"Preview only: nothing changed. Re-run with --apply, or apply import #{run.pk} on the website."))

    def print_report(self, r):
        out = self.stdout.write
        out("Summary: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in r["summary"].items()))
        sections = [
            ("Temporary codes", "temp_codes", lambda x: f"{x['original']} -> {x['new']} ({x['reason']}) {x['name']}"),
            ("Incomplete (not orderable until completed)", "incomplete",
             lambda x: f"{x['part_no']} {x['name']}: missing {x['missing']} [{x['where']}]"),
            ("Sell price below trade price (review only)", "sell_below_trade",
             lambda x: f"{x['part_no']} {x['name']}: trade {x['trade']}, sell {x['sell']}"),
            ("Names still garbled after repair", "garbled_names", lambda x: f"{x['part_no']} {x['name']}"),
            ("Will be deactivated (no longer in file)", "removals", lambda x: f"{x['part_no']} {x['name']}"),
            ("Setup notes", "setup_notes", lambda x: f"{x['part_no']} {x['name']}: {x['note']}"),
            ("Rows with no section (placed in Miscellaneous)", "no_section", lambda x: f"{x['part_no']} {x['name']} [{x['where']}]"),
            ("Skipped", "skipped", lambda x: f"{x['where']}: {x['reason']}"),
        ]
        for title, key, fmt in sections:
            if r.get(key):
                out(f"\n{title}:")
                for entry in r[key]:
                    out("  " + fmt(entry))
        if r.get("ignored_columns"):
            out("\nIgnored columns: " + ", ".join(r["ignored_columns"]))
