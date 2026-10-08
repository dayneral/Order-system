from django.urls import path

from . import views

app_name = "catalogue"

urlpatterns = [
    path("staff/import/", views.import_start, name="import_start"),
    path("staff/import/<int:run_id>/", views.import_detail, name="import_detail"),
    path("staff/import/<int:run_id>/apply/", views.import_apply, name="import_apply"),
    path("staff/import/<int:run_id>/discard/", views.import_discard, name="import_discard"),
]
