import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def merge_dates(apps, schema_editor):
    """Expected completion and hand-back are the same date: keep one."""
    Void = apps.get_model("voids", "Void")
    for v in Void.objects.filter(handback_date__isnull=True, expected_completion__isnull=False):
        v.handback_date = v.expected_completion
        v.save(update_fields=["handback_date"])


class Migration(migrations.Migration):
    dependencies = [
        ("voids", "0006_warning_thresholds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(merge_dates, migrations.RunPython.noop),
        migrations.RemoveField("void", "expected_completion"),
        migrations.AlterField("void", "handback_date", models.DateField("hand-back date", null=True, blank=True)),
        migrations.AlterField("void", "status", models.CharField(
            choices=[("new", "New: needs visit"), ("stc", "STC"), ("in_progress", "In progress"),
                     ("snagging", "Snagging"), ("complete", "Ready for final check"), ("handed_back", "Completed")],
            db_index=True, default="new", max_length=20)),
        migrations.AddField("void", "final_check_requested_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("void", "final_checked_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("void", "final_checked_by", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
            to=settings.AUTH_USER_MODEL)),
        migrations.AddField("tradetask", "updated_by", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
            to=settings.AUTH_USER_MODEL)),
        migrations.AddField("tradetask", "updated_at", models.DateTimeField(blank=True, null=True)),
    ]
