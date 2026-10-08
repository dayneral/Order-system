from django.db import migrations

SECTIONS = [
    ("adhesives", "Adhesives"),
    ("bathrooms-kitchens", "Bathrooms & Kitchens"),
    ("electrical", "Electrical"),
    ("flammable", "Flammable Items"),
    ("flooring", "Flooring"),
    ("joinery", "Joinery"),
    ("plastering", "Plastering"),
    ("plumbing", "Plumbing"),
    ("miscellaneous", "Miscellaneous"),
]


def create_sections(apps, schema_editor):
    Section = apps.get_model("catalogue", "Section")
    for order, (key, name) in enumerate(SECTIONS, start=1):
        Section.objects.update_or_create(key=key, defaults={"name": name, "sort_order": order})


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0001_initial")]
    operations = [migrations.RunPython(create_sections, migrations.RunPython.noop)]
