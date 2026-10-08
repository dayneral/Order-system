from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("staff/failed/", views.failed_list, name="failed"),
    path("staff/failed/<int:email_id>/retry/", views.retry, name="retry"),
]
