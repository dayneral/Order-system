from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from audit.models import AuditEntry, record

from .models import ImportRun, Item, Kit, KitItem, LinkedItemRule, MeasureType, Section, Subsection

# Fields an admin can change, with friendly labels for the audit history.
AUDITED_FIELDS = [
    "part_no", "display_name", "catalogue_name", "section", "subsection", "trade_price", "measure_type", "unit", "pack_size",
    "catalogue_length_m", "is_active", "is_flammable", "code_to_confirm", "name_check",
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
            ("nosection", "No section"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "any":
            return queryset.filter(Q(code_to_confirm=True) | Q(is_incomplete=True) | Q(name_check=True)
                                   | Q(section__isnull=True))
        if value == "code":
            return queryset.filter(code_to_confirm=True)
        if value == "incomplete":
            return queryset.filter(is_incomplete=True)
        if value == "name":
            return queryset.filter(name_check=True)
        if value == "nosection":
            return queryset.filter(section__isnull=True)
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
        if measure == MeasureType.CUT_TO_ORDER and not cleaned.get("catalogue_length_m"):
            self.add_error("catalogue_length_m",
                           "Cut-to-order items need the length the trade price covers (e.g. 3.0). "
                           "Flooring cut to size by length and width should use Area (m²).")
        if measure != MeasureType.PACK and cleaned.get("pack_size", 1) > 1:
            self.add_error("measure_type", "Items with a pack size above 1 should use the Pack measure type.")
        sub, section = cleaned.get("subsection"), cleaned.get("section")
        if sub and section and sub.section_id != section.pk:
            self.add_error("subsection", f"{sub.name} belongs to {sub.section.name}, not {section.name}.")
        if not (cleaned.get("display_name") or cleaned.get("catalogue_name")):
            self.add_error("display_name", "Enter a name.")
        return cleaned


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    form = ItemForm
    list_display = ("part_no", "display_name", "original_name", "section", "subsection", "price", "measure_type", "unit",
                    "pack_size", "flags", "is_active")
    list_display_links = ("part_no",)
    list_editable = ("display_name",)
    list_filter = (NeedsAttentionFilter, "section", "subsection", "measure_type", "is_active", "is_flammable", "source")
    search_fields = ("part_no", "original_part_no", "display_name", "catalogue_name")
    list_per_page = 100
    actions = ["move_to_section", "deactivate", "activate", "confirm_codes"]
    readonly_fields = ("original_part_no", "source", "is_incomplete", "created_at", "updated_at", "history")
    fieldsets = (
        ("Code", {"fields": ("part_no", "original_part_no", "code_to_confirm", "source")}),
        ("Names", {"fields": ("display_name", "catalogue_name", "name_check"),
                   "description": "Users see the display name. The original name comes from the catalogue "
                                  "file and is updated by each import; the display name never is."}),
        ("Ordering", {"fields": ("section", "subsection", "trade_price", "measure_type", "unit", "pack_size",
                                 "catalogue_length_m"),
                      "description": "Area (m²): vinyl and other flooring. Users enter the exact length and width "
                                     "to be cut; value = m² × price per m², no waste allowance. "
                                     "Cut to order: worktops and splashbacks. Users enter a length; "
                                     "value = length ÷ catalogue length × price (an estimate)."}),
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

    @admin.display(description="Trade £ (info)", ordering="trade_price")
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
        if change and ("section" in form.changed_data or "subsection" in form.changed_data):
            obj.section_locked = True  # re-imports keep the admin's choice
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

    @admin.action(description="Move to section / subsection…")
    def move_to_section(self, request, queryset):
        from django.template.response import TemplateResponse

        choices = [(f"s:{s.pk}", s.name) for s in Section.objects.all()]
        choices += [(f"u:{u.pk}", f"{u.section.name} > {u.name}") for u in Subsection.objects.select_related("section")]
        choices.sort(key=lambda c: c[1])
        target = request.POST.get("target")
        if "apply" in request.POST and target:
            kind, pk = target.split(":")
            if kind == "u":
                sub = Subsection.objects.select_related("section").get(pk=pk)
                section, label = sub.section, f"{sub.section.name} > {sub.name}"
            else:
                sub, section = None, Section.objects.get(pk=pk)
                label = section.name
            count = 0
            for item in queryset.select_related("section", "subsection"):
                before = f"{item.section.name if item.section else 'Unsectioned'}" + (
                    f" > {item.subsection.name}" if item.subsection else "")
                item.section, item.subsection, item.section_locked = section, sub, True
                item.save()
                record(request.user, "item.edit", item, f"Moved {item.part_no} to {label}",
                       {"section": {"from": before, "to": label}})
                count += 1
            self.message_user(request, f"Moved {count} item(s) to {label}.", messages.SUCCESS)
            return None
        return TemplateResponse(request, "admin/catalogue/move_items.html", {
            **self.admin_site.each_context(request), "title": "Move items", "items": queryset,
            "choices": choices, "opts": self.model._meta,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
        })

    @admin.action(description="Deactivate selected items")
    def deactivate(self, request, queryset):
        self._bulk_set(request, queryset, "is_active", False, "Deactivated")

    @admin.action(description="Activate selected items")
    def activate(self, request, queryset):
        self._bulk_set(request, queryset, "is_active", True, "Activated")

    @admin.action(description="Mark codes as confirmed")
    def confirm_codes(self, request, queryset):
        self._bulk_set(request, queryset, "code_to_confirm", False, "Confirmed code of")


class SubsectionInline(admin.TabularInline):
    model = Subsection
    extra = 2
    fields = ("name", "sort_order")
    verbose_name_plural = "Subsections (removing one keeps its items in this section)"


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    inlines = [SubsectionInline]
    """Rename, reorder, add and remove sections. Removing a section never deletes its items."""

    list_display = ("name", "sort_order", "is_flammable", "item_count", "subsection_list")
    list_editable = ("sort_order",)
    fields = ("name", "sort_order", "is_flammable")
    actions = None

    def get_queryset(self, request):
        from django.db.models import Count
        return super().get_queryset(request).annotate(n_items=Count("items"))

    @admin.display(description="Subsections")
    def subsection_list(self, obj):
        return ", ".join(s.name for s in obj.subsections.all()) or "—"

    @admin.display(description="Items", ordering="n_items")
    def item_count(self, obj):
        return obj.n_items

    def save_model(self, request, obj, form, change):
        before = Section.objects.filter(pk=obj.pk).values("name", "sort_order", "is_flammable").first() if change else None
        super().save_model(request, obj, form, change)
        if before is None:
            record(request.user, "section.add", obj, f"Added section {obj.name}")
        else:
            changes = {k: {"from": str(v), "to": str(getattr(obj, k))} for k, v in before.items() if v != getattr(obj, k)}
            if changes:
                record(request.user, "section.edit", obj, f"Edited section {obj.name}", changes)

    def delete_model(self, request, obj):
        count = obj.items.count()
        name = obj.name
        record(request.user, "section.delete", obj,
               f"Removed section {name}; {count} item(s) moved to Unsectioned")
        super().delete_model(request, obj)  # items are kept: their section becomes blank
        if count:
            self.message_user(request, f"{count} item(s) from {name} are now Unsectioned. Move them in Catalogue "
                                       "(filter: needs attention > No section).", messages.WARNING)

    def render_delete_form(self, request, context):
        obj = context.get("object")
        if obj is not None and obj.items.exists():
            self.message_user(request, f"{obj.items.count()} item(s) in {obj.name} will NOT be deleted. "
                                       "They will become Unsectioned until you move them.", messages.WARNING)
        return super().render_delete_form(request, context)


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


class LinkedItemRuleForm(forms.ModelForm):
    class Meta:
        model = LinkedItemRule
        fields = ["trigger", "suggested_items", "note", "two_way", "is_active"]

    def clean(self):
        cleaned = super().clean()
        trigger, suggested = cleaned.get("trigger"), cleaned.get("suggested_items")
        if trigger and suggested and trigger in suggested:
            self.add_error("suggested_items", "An item cannot be suggested for itself.")
        return cleaned


@admin.register(LinkedItemRule)
class LinkedItemRuleAdmin(admin.ModelAdmin):
    form = LinkedItemRuleForm
    list_display = ("trigger", "suggestions", "two_way", "is_active", "note")
    list_filter = ("is_active", "two_way")
    search_fields = ("trigger__part_no", "trigger__display_name", "suggested_items__part_no",
                     "suggested_items__display_name")
    autocomplete_fields = ("trigger", "suggested_items")

    @admin.display(description="Suggests")
    def suggestions(self, obj):
        return ", ".join(f"{i.part_no} {i.name}" for i in obj.suggested_items.all())

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        record(request.user, "link.save", form.instance, f"Linked items: {form.instance}")


class KitItemInline(admin.TabularInline):
    model = KitItem
    extra = 3
    autocomplete_fields = ("item",)
    fields = ("position", "item", "quantity", "prompts_kit")


@admin.register(Kit)
class KitAdmin(admin.ModelAdmin):
    list_display = ("name", "item_count", "prompted_by", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "kit_items__item__part_no", "kit_items__item__display_name")
    inlines = [KitItemInline]
    fields = ("name", "description", "is_active")

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.kit_items.count()

    @admin.display(description="Prompted when adding")
    def prompted_by(self, obj):
        return ", ".join(ki.item.part_no for ki in obj.kit_items.filter(prompts_kit=True).select_related("item")) or "—"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        record(request.user, "kit.save", form.instance, f"Kit saved: {form.instance.name}")
