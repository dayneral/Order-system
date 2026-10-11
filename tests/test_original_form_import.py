from pathlib import Path

import pytest

from catalogue import importer
from catalogue.models import Item, Section, Subsection

pytestmark = pytest.mark.django_db
CSV = Path(__file__).resolve().parent.parent / "docs" / "original_form_items.csv"


def test_original_form_file_creates_hidden_draft_sections_and_subsections():
    run = importer.preview_upload("original_form_items.csv", CSV.read_bytes(), None)
    assert "Doors (draft)" in run.report["new_sections"]
    assert "Doors (draft) > 35mm" in run.report["new_subsections"]
    importer.apply_run(run, None)

    doors = Section.objects.get(name="Doors (draft)")
    assert not doors.is_active
    assert Subsection.objects.filter(section=doors, name="35mm").exists()
    assert Item.objects.filter(section__name__endswith="(draft)").count() == 133
    item = Item.objects.get(display_name__startswith="381 x 1980 x 35")
    assert item.subsection.name == "35mm" and item.part_no.startswith("TBC-") and item.code_to_confirm
    assert not Item.objects.filter(section__name="Plumbing (draft)", subsection__isnull=False).exists()


def test_subsection_column_never_moves_existing_items():
    existing = Section.objects.get(key="joinery")
    Item.objects.create(part_no="J1", catalogue_name="Stud", display_name="Stud", section=existing)
    data = b"Part No.,Description,Section,Subsection\r\nJ1,Stud,Joinery,Zzz Test Group\r\n"
    importer.apply_run(importer.preview_upload("x.csv", data, None), None)
    assert Item.objects.get(part_no="J1").subsection is None
    assert not Subsection.objects.filter(name="Zzz Test Group").exists()
