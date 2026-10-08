from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("new/", views.order_new, name="new"),
    path("<uuid:order_id>/", views.order_detail, name="detail"),
    path("<uuid:order_id>/edit/", views.order_edit, name="edit"),
    path("<uuid:order_id>/items/", views.item_browser, name="items"),
    path("<uuid:order_id>/lines/add/", views.line_add, name="line_add"),
    path("<uuid:order_id>/kits/<int:kit_id>/add/", views.kit_add, name="kit_add"),
    path("<uuid:order_id>/lines/non-stocked/", views.line_add_non_stocked, name="line_add_non_stocked"),
    path("<uuid:order_id>/lines/<int:line_id>/", views.line_update, name="line_update"),
    path("<uuid:order_id>/lines/<int:line_id>/remove/", views.line_remove, name="line_remove"),
    path("<uuid:order_id>/print/", views.order_print, name="print"),
    path("<uuid:order_id>/cancel/", views.order_cancel, name="cancel"),
    path("<uuid:order_id>/delete-order/", views.order_delete, name="delete"),
    path("<uuid:order_id>/delete/", views.draft_delete, name="draft_delete"),
]
