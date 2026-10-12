import io
from datetime import date

import openpyxl
import pytest
from django.urls import reverse
from openpyxl.styles import Font, PatternFill

from accounts.models import User
from voids import importer
from voids.models import Supervisor, Trade, TradeTask, Void

TODAY = date(2026, 10, 12)
GREEN = PatternFill("solid", fgColor="FF00B050")
ORANGE = PatternFill("solid", fgColor="FFFFC000")
YELLOW = PatternFill("solid", fgColor="FFFFFF00")
RED = Font(color="FFFF0000")


def tracker(rows, sheet="Voids", headings=None):
    """A small workbook laid out like the tracker: colour key, notes row, heading row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws["A1"] = "ECC VOID TRACKER"
    for col, (label, fill) in zip("HIL", [("Works Completed", GREEN), ("Works in Progress", ORANGE),
                                          ("Snagging Issued", YELLOW)]):
        ws[f"{col}1"] = label
        ws[f"{col}1"].fill = fill
    ws.append([])
    ws.append(headings or ["Post Code", "Supervisor", "Void Area", "SIMPRO Number", "Expected Completion",
                           "Address", "Access", "Joinery", "Plumbing", "Electrical", "Painting"])
    for r in rows:
        values = [v[0] if isinstance(v, tuple) else v for v in r]
        ws.append(values)
        for i, v in enumerate(r):
            if isinstance(v, tuple):
                cell = ws.cell(ws.max_row, i + 1)
                for style in v[1:]:
                    if isinstance(style, PatternFill):
                        cell.fill = style
                    else:
                        cell.font = style
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROWS = [
    ["EH16 6PR", "Stee L", "SE", 7968, "Plumbing line req", "38 Balmwell Terrace", "Master Key",
     ("P Works 23/09", ORANGE, RED), ("Ben 23/09\nKyle 06/10", GREEN), "STC", ("Not needed",)],
    [None, None, None, None, None, "NEW SYSTEM"],
    ["EH4 4SH", "Andrew", "NW ", 11157, date(2026, 10, 2), "4/6 Muirhouse View", "",
     ("Ryan 22/9", GREEN, RED), ("Kyle", GREEN), ("Elite 15/09 - Rewire FF Done", YELLOW), None],
]


@pytest.fixture
def voids_user(make_user):
    return make_user("sup@bfsuk.org", "Sam Supervisor", role=User.Role.ORDERING)


@pytest.fixture
def voids_client(client, voids_user):
    voids_user.can_use_voids = True
    voids_user.save()
    client.force_login(voids_user)
    return client


def test_parse_reads_text_colours_and_skips_dividers(db):
    rows, report = importer.parse_workbook(tracker(ROWS), today=TODAY)
    assert [r["address"] for r in rows] == ["38 Balmwell Terrace", "4/6 Muirhouse View"]
    first = rows[0]
    assert first["expected_note"] == "Plumbing line req"
    joinery = first["tasks"]["Joinery"]
    assert joinery["status"] == TradeTask.Status.IN_PROGRESS and joinery["claimed"]
    assert joinery["assignee"] == "P Works" and joinery["date"] == "2026-09-23"
    plumbing = first["tasks"]["Plumbing"]
    assert plumbing["status"] == TradeTask.Status.COMPLETE and not plumbing["claimed"]
    assert plumbing["notes"] == "Ben 23/09\nKyle 06/10" and plumbing["date"] == "2026-10-06"
    assert first["tasks"]["Electrical"]["status"] == TradeTask.Status.STC
    assert first["tasks"]["Painting"]["status"] == TradeTask.Status.NOT_REQUIRED
    second = rows[1]
    assert second["expected"] == "2026-10-02" and second["area"] == "NW"
    assert second["tasks"]["Electrical"]["status"] == TradeTask.Status.SNAGGING
    assert second["electrical_flag"]


def test_columns_found_by_heading_not_position(db):
    headings = ["Address", "SIMPRO Number", "Painting", "Supervisor", "Mystery"]
    rows, report = importer.parse_workbook(
        tracker([["1 Test Street", 555, ("Decor8 01/10", GREEN), "Martin", "x"]], headings=headings), today=TODAY)
    assert rows[0]["simpro"] == "555"
    assert rows[0]["tasks"]["Painting"]["status"] == TradeTask.Status.COMPLETE
    assert any("Mystery" in p for p in report["problems"])


def test_apply_creates_then_updates_without_duplicates(db, admin_user):
    run = importer.preview_upload("t.xlsx", tracker(ROWS), admin_user, today=TODAY)
    assert run.report["new"] == 2
    assert importer.apply_run(run, admin_user) == (2, 0)
    v = Void.objects.get(simpro="7968")
    assert v.job_type.name == "Void"
    assert v.supervisor.name == "Stee L" and v.area.code == "SE"
    assert v.status == Void.Status.IN_PROGRESS
    muir = Void.objects.get(simpro="11157")
    assert muir.supervisor.name == "Andrew F"  # matched by import alias
    assert muir.status == Void.Status.SNAGGING  # all complete except a snagging trade
    run2 = importer.preview_upload("t.xlsx", tracker(ROWS), admin_user, today=TODAY)
    assert run2.report["new"] == 0 and run2.report["updated"] == 2
    importer.apply_run(run2, admin_user)
    assert Void.objects.count() == 2


def test_postcode_sets_patch_supervisor_for_new_void(voids_client):
    from voids.models import JobType
    resp = voids_client.post(reverse("voids:new"), {
        "job_type": JobType.objects.get(name="Temp").pk, "address": "9 New Street", "postcode": "eh11 2aa",
        "status": "new"})
    assert resp.status_code == 302
    v = Void.objects.get(address="9 New Street")
    assert v.supervisor.name == "Martin T"
    assert v.status == Void.Status.NEW


def test_grid_cell_edit_updates_status(voids_client):
    from voids.models import JobType
    v = Void.objects.create(job_type=JobType.objects.first(), address="1 Grid Road", status=Void.Status.STC)
    trade = Trade.objects.get(name="Joinery")
    url = reverse("voids:task_cell", args=[v.pk, trade.pk])
    assert b"cellform" in voids_client.get(url, HTTP_HX_REQUEST="true").content
    resp = voids_client.post(url, {"status": "complete", "assignee": "Kev", "date": "2026-10-01"},
                             HTTP_HX_REQUEST="true")
    assert b"t-complete" in resp.content
    v.refresh_from_db()
    assert v.status == Void.Status.COMPLETE


def test_filters_by_job_type_and_electrical_supervisor(voids_client):
    from voids.models import JobType
    temp, void_type = JobType.objects.get(name="Temp"), JobType.objects.get(name="Void")
    Void.objects.create(job_type=temp, address="Temp Flat", status=Void.Status.STC)
    sparky = Void.objects.create(job_type=void_type, address="Sparky Flat", status=Void.Status.STC,
                                 electrical_flag=True)
    resp = voids_client.get(reverse("voids:list"), {"job": temp.pk})
    assert b"Temp Flat" in resp.content and b"Sparky Flat" not in resp.content
    jamie = Supervisor.objects.get(name="Jamie H")
    resp = voids_client.get(reverse("voids:list"), {"supervisor": jamie.pk})
    assert b"Sparky Flat" in resp.content and b"Temp Flat" not in resp.content
    assert sparky in Void.objects.all()


def test_module_access(client, make_user):
    orderer = make_user("o@bfsuk.org", "Olly Orders")
    client.force_login(orderer)
    assert client.get(reverse("voids:list")).status_code == 403
    assert client.get(reverse("orders:list")).status_code == 200
    voids_only = make_user("v@bfsuk.org", "Vic Voids")
    voids_only.can_use_orders, voids_only.can_use_voids = False, True
    voids_only.save()
    client.force_login(voids_only)
    assert client.get(reverse("orders:list")).status_code == 403
    assert client.get(reverse("voids:list")).status_code == 200
    assert client.get(reverse("home"))["Location"] == reverse("voids:list")


def test_admin_switches_module_access(admin_client, make_user):
    u = make_user("x@bfsuk.org", "Xan")
    admin_client.post(reverse("accounts:user_action", args=[u.pk]), {"action": "grant_voids"})
    u.refresh_from_db()
    assert u.can_use_voids


def test_export_and_import_pages(admin_client):
    assert admin_client.get(reverse("voids:export")).status_code == 200
    assert admin_client.get(reverse("voids:import")).status_code == 200


def test_tiles_by_area_and_tile_edit(voids_client):
    from voids.models import Area, JobType
    ne, sw = Area.objects.get(code="NE"), Area.objects.get(code="SW")
    a = Void.objects.create(job_type=JobType.objects.first(), address="North Flat", area=ne, status=Void.Status.STC)
    Void.objects.create(job_type=JobType.objects.first(), address="South Flat", area=sw, status=Void.Status.STC)
    resp = voids_client.get(reverse("voids:list"), {"view": "tiles", "area": ne.pk})
    assert b"North Flat" in resp.content and b"South Flat" not in resp.content
    assert b'class="tiles"' in resp.content
    trade = Trade.objects.get(name="Plumbing")
    url = reverse("voids:task_cell", args=[a.pk, trade.pk]) + "?in=tile"
    resp = voids_client.post(url, {"status": "in_progress", "assignee": "Ben"}, HTTP_HX_REQUEST="true")
    assert b'class="tile' in resp.content and b"dot t-in_progress" in resp.content
    a.refresh_from_db()
    assert a.status == Void.Status.IN_PROGRESS
