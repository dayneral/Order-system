"""
The material catalogue.

Items are imported from the stores spreadsheet and then tidied by admins.
Part No. (the store code) is the key users and stores see. Import matching
uses a separate, stable `import_key` so that an admin can correct a temporary
code (e.g. ELE118-B -> ELE130) without breaking later re-imports.
"""

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Section(models.Model):
    """Catalogue sections. Admins can rename, reorder, add and remove them.

    The nine original sections are created by a data migration; their keys
    (below) stay the same when they are renamed. Removing a section keeps its
    items: they become "Unsectioned" until moved.
    """

    ADHESIVES = "adhesives"
    BATHROOMS_KITCHENS = "bathrooms-kitchens"  # original section, replaced by KITCHENS (Oct 2026)
    KITCHENS = "kitchens"
    ELECTRICAL = "electrical"
    FLAMMABLE = "flammable"
    FLOORING = "flooring"
    JOINERY = "joinery"
    PLASTERING = "plastering"
    PLUMBING = "plumbing"
    MISC = "miscellaneous"

    # (key, name) in display order.
    FIXED = [
        (ADHESIVES, "Adhesives"),
        (BATHROOMS_KITCHENS, "Bathrooms & Kitchens"),
        (ELECTRICAL, "Electrical"),
        (FLAMMABLE, "Flammable Items"),
        (FLOORING, "Flooring"),
        (JOINERY, "Joinery"),
        (PLASTERING, "Plastering"),
        (PLUMBING, "Plumbing"),
        (MISC, "Miscellaneous"),
    ]

    key = models.SlugField(primary_key=True, max_length=40, editable=False)
    name = models.CharField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField("Order", default=100, help_text="Lower numbers are shown first.")
    is_flammable = models.BooleanField(
        "Flammable store", default=False,
        help_text="Items in this section are flagged FLAMMABLE on orders and in the stores email.")

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.key:
            base = slugify(self.name)[:34] or "section"
            key, n = base, 2
            while Section.objects.filter(key=key).exists():
                key, n = f"{base}-{n}", n + 1
            self.key = key
        super().save(*args, **kwargs)


class Subsection(models.Model):
    """One level of grouping inside a section, e.g. Electrical > Sockets and Switches.

    Removing a subsection keeps its items in the parent section.
    """

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="subsections")
    name = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField("Order", default=100)

    class Meta:
        ordering = ["section__sort_order", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["section", "name"], name="subsection_name_once")]

    def __str__(self):
        return f"{self.section.name} > {self.name}"


class MeasureType(models.TextChoices):
    EACH = "each", "Each"
    PACK = "pack", "Pack"
    AREA = "area", "Area (m²)"
    LINEAR = "linear", "Linear (m)"
    CUT_TO_ORDER = "cut", "Cut to order (m)"
    WHOLE = "whole", "Whole items only"


class Item(models.Model):
    class Source(models.TextChoices):
        IMPORT = "import", "Imported"
        MANUAL = "manual", "Added by admin"

    part_no = models.CharField(
        "Part No.", max_length=40, unique=True,
        help_text="The store code. Temporary codes end in -A, -B and so on until confirmed.",
    )
    original_part_no = models.CharField(
        "Part No. in spreadsheet", max_length=40, blank=True,
        help_text="The code exactly as it appeared in the last import.",
    )
    import_key = models.CharField(
        max_length=60, unique=True, null=True, blank=True, editable=False,
        help_text="Internal: links this item to its spreadsheet row on re-import.",
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL, editable=False)

    catalogue_name = models.CharField(
        "Original name", max_length=300, blank=True,
        help_text="Name from the catalogue spreadsheet. Updated on each import.",
    )
    display_name = models.CharField(
        max_length=300, blank=True,
        help_text="The name users see. Never changed by an import.",
    )
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.SET_NULL, related_name="items",
                                help_text="Blank = Unsectioned (its section was removed).")
    subsection = models.ForeignKey(Subsection, null=True, blank=True, on_delete=models.SET_NULL, related_name="items")
    section_locked = models.BooleanField(
        default=False, editable=False,
        help_text="Section set by an admin: re-imports leave the section alone.")
    import_section = models.CharField(
        max_length=40, blank=True, editable=False,
        help_text="Internal: the section of the file this item was last imported from. A re-import of that "
                  "file can deactivate the item if it is no longer listed, wherever the item now sits.")

    trade_price = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text="Trade price (£) per unit. For packs, the price of one pack. For area items, the price per m².",
    )
    measure_type = models.CharField(max_length=10, choices=MeasureType.choices, default=MeasureType.EACH)
    unit = models.CharField(
        "Unit of measure", max_length=40, default="each",
        help_text='Shown to users next to the quantity, e.g. "each", "pack", "m²", "m", "roll".',
    )
    pack_size = models.PositiveIntegerField(
        default=1, help_text="How many are in one pack (1 if not sold in packs).",
    )
    catalogue_length_m = models.DecimalField(
        "Catalogue length (m)", max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Cut-to-order items only: the length the trade price covers (e.g. 3.0 for worktops).",
    )
    is_active = models.BooleanField("Active", default=True)
    is_flammable = models.BooleanField("Flammable", default=False)
    is_incomplete = models.BooleanField(
        "Incomplete", default=False, editable=False,
        help_text="Missing a name or trade price. Not orderable until completed.",
    )
    code_to_confirm = models.BooleanField(
        "Code to be confirmed", default=False,
        help_text="Temporary code given on import (duplicate or placeholder). Clears when the Part No. is changed.",
    )
    name_check = models.BooleanField(
        "Check name", default=False,
        help_text="The imported name still looks garbled after automatic repair.",
    )
    deactivated_by_import = models.BooleanField(default=False, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section__sort_order", "display_name"]

    def __str__(self):
        return f"{self.part_no} — {self.name}"

    @property
    def name(self):
        return self.display_name or self.catalogue_name

    @property
    def is_orderable(self):
        return self.is_active and not self.is_incomplete

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.catalogue_name
        # A missing price only blocks ordering while order values are in use (settings.ORDER_VALUES_ENABLED).
        self.is_incomplete = not self.name.strip() or (settings.ORDER_VALUES_ENABLED and self.trade_price is None)
        if self.subsection_id and self.subsection.section_id != self.section_id:
            self.section_id = self.subsection.section_id
        if self.section_id and self.section.is_flammable:
            self.is_flammable = True
        super().save(*args, **kwargs)


class ImportRun(models.Model):
    """One upload of the catalogue spreadsheet: previewed first, then applied."""

    class Status(models.TextChoices):
        PREVIEW = "preview", "Waiting to be applied"
        APPLIED = "applied", "Applied"
        DISCARDED = "discarded", "Discarded"

    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    # Parsed spreadsheet rows, so "apply" uses exactly what was previewed.
    rows = models.JSONField(default=list)
    report = models.JSONField(default=dict)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.get_status_display()}, {self.created_at:%d %b %Y %H:%M})"


class LinkedItemRule(models.Model):
    """'When X is added, suggest Y': shown on the order page as soon as X is added.

    With two_way set, adding any of the suggested items also suggests the trigger item.
    """

    trigger = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="link_rules",
                                help_text="When this item is added to an order…")
    suggested_items = models.ManyToManyField(Item, related_name="+",
                                             help_text="…suggest these items.")
    note = models.CharField(max_length=200, blank=True,
                            help_text='Optional reason shown to the user, e.g. "Sealant needs an applicator gun".')
    two_way = models.BooleanField("Suggest both ways", default=False,
                                  help_text="Also suggest the trigger item when a suggested item is added.")
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "linked item rule"
        ordering = ["trigger__part_no"]

    def __str__(self):
        return f"{self.trigger.part_no} → {', '.join(i.part_no for i in self.suggested_items.all())}"


class Kit(models.Model):
    """A named set of items ordered together, e.g. a close-coupled toilet kit.

    Adding an item marked "prompts kit" offers the rest of the kit. Kits can
    also be added in one go from the Kits tab. Each kit item becomes its own
    order line, labelled with the kit name.
    """

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class KitItem(models.Model):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="kit_items")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="kit_memberships")
    quantity = models.PositiveIntegerField(
        default=1, help_text="Default quantity (packs for pack items). Measured items: the user enters measurements.")
    prompts_kit = models.BooleanField(
        default=False, help_text="Adding this item to an order offers the rest of the kit.")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["kit", "item"], name="kit_item_once")]

    def __str__(self):
        return f"{self.kit.name}: {self.item.part_no}"
