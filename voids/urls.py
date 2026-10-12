from django.urls import path

from . import views

app_name = "voids"

urlpatterns = [
    path("", views.void_list, name="list"),
    path("export/", views.export, name="export"),
    path("new/", views.void_new, name="new"),
    path("<int:void_id>/", views.void_detail, name="detail"),
    path("<int:void_id>/edit/", views.void_edit, name="edit"),
    path("<int:void_id>/trade/<int:trade_id>/", views.task_cell, name="task_cell"),
    path("import/", views.import_start, name="import"),
    path("import/<int:run_id>/", views.import_detail, name="import_detail"),
    path("import/<int:run_id>/apply/", views.import_apply, name="import_apply"),
    path("import/<int:run_id>/discard/", views.import_discard, name="import_discard"),
]
