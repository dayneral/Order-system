from django.db import migrations

JOB_TYPES = [("Void", "Voids"), ("Temp", "TEMP"), ("Concierge Station", "")]
AREAS = [("NE", "North East"), ("NW", "North West"), ("SE", "South East"), ("SW", "South West")]
TRADES = [
    ("Clear Out", ""), ("Joinery", ""), ("Composite Door / Windows", "Composite Door / New windows\nComposite Door"),
    ("T71", ""), ("T77", ""), ("Plumbing", ""), ("Plasterer", "Plastering"), ("Electrical", ""),
    ("Glazing", ""), ("Painting", "Decoration\nDecorating"), ("Vinyl / Flooring", "Flooring\nVinyl\nCarpets / Vinyl"),
    ("Sparkle Clean", "Clean"),
]
SUPERVISORS = [
    ("Stee L", "Stee", "EH1 EH2 EH8 EH9 EH13 EH16 EH17 EH19 EH20"),
    ("Martin T", "Martin", "EH3 EH11 EH12 EH14 EH28"),
    ("Andrew F", "Andrew", "EH4 EH5 EH6 EH7 EH15 EH29 EH30"),
]


def seed(apps, schema_editor):
    JobType = apps.get_model("voids", "JobType")
    Area = apps.get_model("voids", "Area")
    Trade = apps.get_model("voids", "Trade")
    Supervisor = apps.get_model("voids", "Supervisor")
    District = apps.get_model("voids", "PostcodeDistrict")
    for i, (name, sheet) in enumerate(JOB_TYPES):
        JobType.objects.get_or_create(name=name, defaults={"sheet_name": sheet, "position": i})
    for code, name in AREAS:
        Area.objects.get_or_create(code=code, defaults={"name": name})
    for i, (name, aliases) in enumerate(TRADES):
        Trade.objects.get_or_create(name=name, defaults={"position": i * 10, "import_aliases": aliases})
    for name, alias, districts in SUPERVISORS:
        sup, _ = Supervisor.objects.get_or_create(name=name, defaults={"import_aliases": alias})
        for d in districts.split():
            District.objects.get_or_create(district=d, defaults={"supervisor": sup})
    Supervisor.objects.get_or_create(name="Jamie H", defaults={
        "import_aliases": "Jamie", "trade": Trade.objects.get(name="Electrical")})


class Migration(migrations.Migration):
    dependencies = [("voids", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
