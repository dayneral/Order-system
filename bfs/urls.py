from django.contrib import admin
from django.urls import include, path

from . import views

admin.site.site_header = "BFS Material Orders: data admin"
admin.site.site_title = "BFS Material Orders"
admin.site.index_title = "Data admin"

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("data-admin/", admin.site.urls),
]
