from django.contrib import admin

from .models import OrderEmail


@admin.register(OrderEmail)
class OrderEmailAdmin(admin.ModelAdmin):
    list_display = ("created_at", "subject", "kind", "status", "attempts", "sent_at")
    list_filter = ("status", "kind")
    search_fields = ("subject",)
    readonly_fields = [f.name for f in OrderEmail._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
