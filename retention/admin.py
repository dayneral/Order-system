from django.contrib import admin

from .models import RetentionRun


@admin.register(RetentionRun)
class RetentionRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "trigger", "dry_run", "orders_deleted", "drafts_deleted", "succeeded")
    readonly_fields = [f.name for f in RetentionRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
