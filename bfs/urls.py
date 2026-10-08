from django.contrib import admin
from django.urls import include, path

from . import views

admin.site.site_header = "BFS Material Orders: data admin"
admin.site.site_title = "BFS Material Orders"
admin.site.index_title = "Data admin"
admin.site.enable_nav_sidebar = False

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("catalogue/", include("catalogue.urls")),
    path("data-admin/", admin.site.urls),
]
