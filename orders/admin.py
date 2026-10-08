from django.contrib import admin

from .models import Order, OrderLine


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    can_delete = False
    fields = ("part_no", "name", "measure_type", "quantity", "length_m", "width_m", "area_m2", "unit",
              "trade_price", "line_value", "is_non_stocked", "is_flammable")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only view for checking data. Orders are changed through the ordering screens."""

    list_display = ("order_number", "status", "submitted_at", "requester_name", "job_number", "delivery_date",
                    "order_value")
    list_filter = ("status", "property_type")
    search_fields = ("order_number", "job_number", "requester_name")
    inlines = [OrderLineInline]

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(status=Order.Status.DRAFT)  # drafts are private

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Order._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
