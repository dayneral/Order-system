"""
Sync the voids from the tracker spreadsheet on OneDrive (Render runs it nightly).

    python manage.py sync_void_tracker
    python manage.py sync_void_tracker --file "Void Tracker.xlsx"   # from a local copy instead
"""

from django.core.management.base import BaseCommand, CommandError

from voids import onedrive
from voids.models import TrackerSyncRun
from voids.sync import run_sync


class Command(BaseCommand):
    help = "Sync voids from the tracker spreadsheet (safe: conflicts are listed, never overwritten)."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Use this .xlsx file instead of downloading from OneDrive.")

    def handle(self, file=None, **options):
        if file:
            with open(file, "rb") as fh:
                name, data, source = file, fh.read(), TrackerSyncRun.Source.UPLOAD
        else:
            if not onedrive.configured():
                self.stdout.write("Tracker sync not set up (no OneDrive settings) – skipped.")
                return
            try:
                name, data = onedrive.download()
            except onedrive.OneDriveError as exc:
                run = TrackerSyncRun.objects.create(source=TrackerSyncRun.Source.ONEDRIVE, ok=False, error=str(exc))
                from voids.sync import notify_admins
                notify_admins(run)
                raise CommandError(str(exc))
            source = TrackerSyncRun.Source.ONEDRIVE
        run = run_sync(data, source, filename=name)
        if not run.ok:
            raise CommandError(run.error)
        r = run.report
        self.stdout.write(self.style.SUCCESS(
            f"Tracker synced: {len(r['created'])} new, {len(r['updated'])} updated, {r['unchanged']} unchanged, "
            f"{r['conflicts']} conflict(s), {len(r['missing'])} no longer in the tracker."))
