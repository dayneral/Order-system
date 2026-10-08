from django.urls import path

from . import views

app_name = "retention"

urlpatterns = [
    path("staff/", views.run_list, name="runs"),
    path("staff/run/", views.run_now, name="run_now"),
]
