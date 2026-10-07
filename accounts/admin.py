from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Read-mostly view. Approvals and role changes are done in Admin > Users,
    so they are recorded in the audit history."""

    list_display = ("full_name", "email", "role", "status", "date_joined", "last_login")
    list_filter = ("role", "status")
    search_fields = ("full_name", "email")
    fields = ("full_name", "email", "role", "status", "date_joined", "approved_at", "approved_by", "last_login")
    readonly_fields = ("email", "role", "status", "date_joined", "approved_at", "approved_by", "last_login")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
