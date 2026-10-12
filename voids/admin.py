from django.contrib import admin

from .models import VoidsSettings, Area, JobType, PostcodeDistrict, Supervisor, Trade


@admin.register(JobType)
class JobTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "sheet_name", "position"]
    list_editable = ["sheet_name", "position"]


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ["name", "short_name", "position", "active"]
    list_editable = ["short_name", "position", "active"]


class DistrictInline(admin.TabularInline):
    model = PostcodeDistrict
    fields = ["district", "area"]
    extra = 1


@admin.register(Supervisor)
class SupervisorAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "trade", "patch", "active"]
    inlines = [DistrictInline]

    @admin.display(description="Postcode districts")
    def patch(self, obj):
        return ", ".join(d.district for d in obj.districts.all())


@admin.register(PostcodeDistrict)
class PostcodeDistrictAdmin(admin.ModelAdmin):
    list_display = ["district", "area", "supervisor"]
    list_editable = ["area", "supervisor"]
    list_filter = ["area", "supervisor"]


@admin.register(VoidsSettings)
class VoidsSettingsAdmin(admin.ModelAdmin):
    """A single settings row: the list opens it straight away."""

    def has_add_permission(self, request):
        return not VoidsSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        obj = VoidsSettings.get()
        return redirect("admin:voids_voidssettings_change", obj.pk)
