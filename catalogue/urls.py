from django.urls import path

from . import sorting, views

app_name = "catalogue"

urlpatterns = [
    path("staff/sort/", sorting.sort_page, name="sort"),
    path("staff/sort/move/", sorting.move, name="sort_move"),
    path("staff/sort/undo/", sorting.undo, name="sort_undo"),
    path("staff/sort/<slug:section_key>/subsections/add/", sorting.add_subsection, name="sort_add_subsection"),
    path("staff/sort/<slug:section_key>/subsections/order/", sorting.reorder_subsections, name="sort_reorder"),
    path("staff/sort/subsection/<int:sub_id>/rename/", sorting.rename_subsection, name="sort_rename"),
    path("staff/sort/<slug:section_key>/kits/add/", sorting.add_kit, name="sort_add_kit"),
    path("staff/import/", views.import_start, name="import_start"),
    path("staff/import/<int:run_id>/", views.import_detail, name="import_detail"),
    path("staff/import/<int:run_id>/apply/", views.import_apply, name="import_apply"),
    path("staff/import/<int:run_id>/discard/", views.import_discard, name="import_discard"),
]
