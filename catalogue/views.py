from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from audit.models import record

from . import importer
from .models import ImportRun, Item
from .reader import SpreadsheetError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UploadForm(forms.Form):
    file = forms.FileField(label="Catalogue file (.xlsx or .csv)")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError("Please choose a .xlsx or .csv file.")
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("The file is larger than 10 MB.")
        return upload


@admin_required
def import_start(request):
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                run = importer.preview_upload(upload.name, upload.read(), request.user)
            except SpreadsheetError as exc:
                form.add_error("file", str(exc))
            else:
                return redirect("catalogue:import_detail", run.pk)
    else:
        form = UploadForm()
    attention = {
        "code": Item.objects.filter(code_to_confirm=True).count(),
        "incomplete": Item.objects.filter(is_incomplete=True).count(),
        "name": Item.objects.filter(name_check=True).count(),
    }
    return render(request, "catalogue/import_start.html", {
        "form": form,
        "runs": ImportRun.objects.select_related("uploaded_by")[:15],
        "item_count": Item.objects.filter(is_active=True).count(),
        "attention": attention,
    })


@admin_required
def import_detail(request, run_id):
    run = get_object_or_404(ImportRun, pk=run_id)
    return render(request, "catalogue/import_report.html", {"run": run, "r": run.report})


@admin_required
@require_POST
def import_apply(request, run_id):
    run = get_object_or_404(ImportRun, pk=run_id)
    try:
        report = importer.apply_run(run, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        s = report["summary"]
        messages.success(request, f"Import applied: {s['additions']} added, {s['updates']} updated, "
                                  f"{s['removals']} deactivated.")
    return redirect("catalogue:import_detail", run.pk)


@admin_required
@require_POST
def import_discard(request, run_id):
    run = get_object_or_404(ImportRun, pk=run_id, status=ImportRun.Status.PREVIEW)
    run.status = ImportRun.Status.DISCARDED
    run.save(update_fields=["status"])
    record(request.user, "catalogue.import_discard", run, f"Discarded catalogue import preview {run.filename}")
    messages.info(request, "Import discarded. Nothing was changed.")
    return redirect("catalogue:import_start")
