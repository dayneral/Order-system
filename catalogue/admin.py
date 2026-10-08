from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from audit.models import AuditEntry, record

from .models import ImportRun, Item, MeasureType, Section

# Fields an admin can change, with friendly labels for the audit history.
AUDITED_FIELDS = [
    "part_no", "display_name", "catalogue_name", "section", "trade_price", "measure_type", "unit", "pack_size",
    "catalogue_length_m", "roll_width_m", "is_active", "is_flammable", "code_to_confirm", "name_check",
]


class NeedsAttentionFilter(admin.SimpleListFilter):
    title = "needs attention"
    parameter_name = "attention"

    def lookups(self, request, model_admin):
        return [
            ("any", "Anything to check"),
            ("code", "Code to be confirmed"),
            ("incomplete", "Incomplete"),
            ("name", "Check name"),
            ("pack", "Pack with pack size 1"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "any":
            return queryset.filter(Q(code_to_confirm=True) | Q(is_incomplete=True) | Q(name_check=True))
        if value == "code":
            return queryset.filter(code_to_confirm=True)
        if value == "incomplete":
            return queryset.filter(is_incomplete=True)
        if value == "name":
            return queryset.filter(name_check=True)
        if value == "pack":
            return queryset.filter(measure_type=MeasureType.PACK, pack_size__lte=1)
        return queryset


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = "__all__"

    def clean_pack_size(self):
        value = self.cleaned_data["pack_size"]
        if value < 1:
            raise forms.ValidationError("Pack size must be at least 1.")
        return value

    def clean(self):
        cleaned = super().clean()
        measure = cleaned.get("measure_type")
        if cleaned.get("catalogue_length_m") and cleaned.get("roll_width_m"):
            self.add_error("roll_width_m", "Set either a catalogue length (worktops) or a roll width (vinyl), not both.")
        if measure != MeasureType.PACK and cleaned.get("pack_size", 1) > 1:
            self.add_error("measure_type", "Items with a pack size above 1 should use the Pack measure type.")
        if not (cleaned.get("display_name") or cleaned.get("catalogue_name")):
            self.add_error("display_name", "Enter a name.")
        return cleaned


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    form = ItemForm
    list_display = ("part_no", "display_name", "original_name", "section", "price", "measure_type", "unit",
                    "pack_size", "flags", "is_active")
    list_display_links = ("part_no",)
    list_editable = ("display_name",)
    list_filter = (NeedsAttentionFilter, "section", "measure_type", "is_active", "is_flammable", "source")
    search_fields = ("part_no", "original_part_no", "display_name", "catalogue_name")
    list_per_page = 100
    actions = ["deactivate", "activate", "confirm_codes"]
    readonly_fields = ("original_part_no", "source", "is_incomplete", "created_at", "updated_at", "history")
    fieldsets = (
        ("Code", {"fields": ("part_no", "original_part_no", "code_to_confirm", "source")}),
        ("Names", {"fields": ("display_name", "catalogue_name", "name_check"),
                   "description": "Users see the display name. The original name comes from the catalogue "
                                  "file and is updated by each import; the display name never is."}),
        ("Ordering", {"fields": ("section", "trade_price", "measure_type", "unit", "pack_size",
                                 "catalogue_length_m", "roll_width_m"),
                      "description": "Cut to order: worktops and splashbacks use the catalogue length "
                                     "(price × length ÷ catalogue length). Vinyl cut from a roll uses the roll "
                                     "width (length × roll width × price per m²)."}),
        ("Status", {"fields": ("is_active", "is_flammable", "is_incomplete", "created_at", "updated_at")}),
        ("History", {"fields": ("history",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj is not None and obj.source == Item.Source.IMPORT:
            fields.append("catalogue_name")
        return fields

    @admin.display(description="Original name", ordering="catalogue_name")
    def original_name(self, obj):
        if obj.catalogue_name == obj.display_name:
            return format_html('<span style="color:#888">(same)</span>')
        return obj.catalogue_name

    @admin.display(description="Trade £", ordering="trade_price")
    def price(self, obj):
        return "—" if obj.trade_price is None else f"{obj.trade_price:.2f}"

    @admin.display(description="Flags")
    def flags(self, obj):
        tags = []
        if obj.code_to_confirm:
            tags.append(("#9a6a00", "CODE TO CONFIRM"))
        if obj.is_incomplete:
            tags.append(("#b42318", "INCOMPLETE"))
        if obj.name_check:
            tags.append(("#b42318", "CHECK NAME"))
        if obj.is_flammable:
            tags.append(("#c2410c", "FLAMMABLE"))
        return format_html_join(
            "<br>", '<b style="color:{};font-size:11px;white-space:nowrap">{}</b>', tags)

    @admin.display(description="Change history")
    def history(self, obj):
        if obj is None or obj.pk is None:
            return "-"
        entries = AuditEntry.objects.filter(target_type="catalogue.item", target_id=str(obj.pk)).select_related("actor")[:50]
        if not entries:
            return "No changes recorded yet."
        rows = format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            ((f"{timezone.localtime(e.timestamp):%d %b %Y %H:%M}", e.actor.full_name if e.actor else "System", e.summary,
              "; ".join(f"{k}: {v.get('from')} → {v.get('to')}" for k, v in e.changes.items()))
             for e in entries),
        )
        return format_html('<table><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr>{}</table>', rows)

    def save_model(self, request, obj, form, change):
        if change and "part_no" in form.changed_data and "code_to_confirm" not in form.changed_data:
            obj.code_to_confirm = False  # a new code has been entered, so it is now confirmed
        if not change:
            obj.source = Item.Source.MANUAL
        old = Item.objects.get(pk=obj.pk) if change else None
        super().save_model(request, obj, form, change)
        if old is None:
            record(request.user, "item.create", obj, f"Added item {obj.part_no} {obj.name}")
            return
        changes = {}
        for name in AUDITED_FIELDS:
            before, after = getattr(old, name), getattr(obj, name)
            if before != after:
                label = Item._meta.get_field(name).verbose_name
                changes[str(label)] = {"from": str(before) if before is not None else None,
                                       "to": str(after) if after is not None else None}
        if changes:
            record(request.user, "item.edit", obj, f"Edited {obj.part_no}: {', '.join(changes)}", changes)

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead, so past orders keep their items

    def _bulk_set(self, request, queryset, field, value, verb):
        count = 0
        for item in queryset.exclude(**{field: value}):
            setattr(item, field, value)
            if field == "is_active":
                item.deactivated_by_import = False
            item.save()
            record(request.user, "item.edit", item, f"{verb} {item.part_no}", {field: {"from": not value, "to": value}})
            count += 1
        self.message_user(request, f"{verb} {count} item(s).", messages.SUCCESS)

    @admin.action(description="Deactivate selected items")
    def deactivate(self, request, queryset):
        self._bulk_set(request, queryset, "is_active", False, "Deactivated")

    @admin.action(description="Activate selected items")
    def activate(self, request, queryset):
        self._bulk_set(request, queryset, "is_active", True, "Activated")

    @admin.action(description="Mark codes as confirmed")
    def confirm_codes(self, request, queryset):
        self._bulk_set(request, queryset, "code_to_confirm", False, "Confirmed code of")


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("sort_order", "name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "filename", "status", "uploaded_by", "applied_at", "report_link")
    fields = ("created_at", "filename", "status", "uploaded_by", "applied_at", "applied_by", "report_link")
    readonly_fields = fields

    @admin.display(description="Report")
    def report_link(self, obj):
        return format_html('<a href="{}">Open report</a>', reverse("catalogue:import_detail", args=[obj.pk]))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
