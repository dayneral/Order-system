from django.urls import path

from . import views

app_name = "voids"

urlpatterns = [
    path("", views.void_list, name="list"),
    path("overview/", views.overview, name="overview"),
    path("export/", views.export, name="export"),
    path("new/", views.void_new, name="new"),
    path("<int:void_id>/", views.void_detail, name="detail"),
    path("<int:void_id>/edit/", views.void_edit, name="edit"),
    path("<int:void_id>/trade/<int:trade_id>/", views.task_cell, name="task_cell"),
    path("<int:void_id>/trade/<int:trade_id>/status/", views.set_task_status, name="set_task_status"),
    path("<int:void_id>/trade/<int:trade_id>/claim/", views.toggle_claim, name="toggle_claim"),
    path("<int:void_id>/claim-complete/", views.claim_complete, name="claim_complete"),
    path("<int:void_id>/final-check/", views.final_check, name="final_check"),
    path("<int:void_id>/add-trade/", views.add_trade, name="add_trade"),
    path("<int:void_id>/clear-issue/", views.clear_issue, name="clear_issue"),
    path("<int:void_id>/update/", views.add_update, name="add_update"),
    path("updates/<int:update_id>/edit/", views.edit_update, name="edit_update"),
    path("updates/<int:update_id>/hide/", views.hide_update, name="hide_update"),
    path("import/", views.import_start, name="import"),
    path("import/<int:run_id>/", views.import_detail, name="import_detail"),
    path("import/<int:run_id>/apply/", views.import_apply, name="import_apply"),
    path("import/<int:run_id>/discard/", views.import_discard, name="import_discard"),
]
