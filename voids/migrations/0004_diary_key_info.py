import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def start_activity(apps, schema_editor):
    Void = apps.get_model("voids", "Void")
    for v in Void.objects.all():
        v.last_activity = v.updated_at or v.created_at
        v.save(update_fields=["last_activity"])


class Migration(migrations.Migration):
    dependencies = [
        ("voids", "0003_trade_short_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField("void", "notes", "key_info"),
        migrations.AlterField("void", "key_info", models.TextField(
            "key info", blank=True,
            help_text="Important information shown prominently: key safe numbers, warnings, hazards.")),
        migrations.AddField("void", "on_hold", models.BooleanField(default=False, help_text=(
            "Paused (e.g. waiting on an asbestos survey). Not flagged as needing an update."))),
        migrations.AddField("void", "last_activity", models.DateTimeField(
            db_index=True, default=django.utils.timezone.now)),
        migrations.RunPython(start_activity, migrations.RunPython.noop),
        migrations.CreateModel(
            name="VoidUpdate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("kind", models.CharField(choices=[("general", "General"), ("visit", "Site visit"), ("issue", "Issue"),
                                                   ("client", "Client contact"), ("snag", "Snag"),
                                                   ("trade", "Trade update"), ("status", "Status change")],
                                          default="general", max_length=10)),
                ("text", models.TextField()),
                ("edited_at", models.DateTimeField(blank=True, null=True)),
                ("hidden", models.BooleanField(default=False)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                             related_name="+", to=settings.AUTH_USER_MODEL)),
                ("hidden_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                related_name="+", to=settings.AUTH_USER_MODEL)),
                ("void", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="updates",
                                           to="voids.void")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
