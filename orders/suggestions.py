"""
Prompts shown as soon as an item is added to an order:

- Linked items: "Commonly ordered with X". Rules can be one-way or two-way.
- Kits: if the item is marked "prompts kit" in a kit, offer the rest of the kit.

Items already on the order, inactive items and incomplete items are never suggested.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from catalogue.models import Item, Kit, KitItem, LinkedItemRule
from orders.pricing import WHOLE_NUMBER_TYPES


@dataclass
class KitRow:
    kit_item: KitItem
    item: Item
    available: bool
    on_order: bool

    @property
    def checked(self):
        return self.available and not self.on_order


@dataclass
class KitOffer:
    kit: Kit
    rows: list = field(default_factory=list)

    @property
    def estimated_value(self):
        """Trade value of the kit's count-based items (measured items are priced once measured)."""
        total = Decimal("0.00")
        for row in self.rows:
            if row.available and row.item.measure_type in WHOLE_NUMBER_TYPES and row.item.trade_price is not None:
                total += row.item.trade_price * row.kit_item.quantity
        return total.quantize(Decimal("0.01"))

    @property
    def has_measured_items(self):
        return any(row.item.measure_type not in WHOLE_NUMBER_TYPES for row in self.rows)


def _on_order_ids(order):
    return set(order.lines.exclude(item=None).values_list("item_id", flat=True))


def kit_offer(kit, on_order=frozenset()):
    rows = [KitRow(kit_item=ki, item=ki.item, available=ki.item.is_orderable, on_order=ki.item_id in on_order)
            for ki in kit.kit_items.select_related("item__section")]
    return KitOffer(kit=kit, rows=rows)


def linked_suggestions(order, item):
    """Items to suggest after `item` was added, with any notes from the rules."""
    on_order = _on_order_ids(order)
    suggested, notes = {}, []
    forward = LinkedItemRule.objects.filter(trigger=item, is_active=True).prefetch_related("suggested_items")
    reverse = LinkedItemRule.objects.filter(suggested_items=item, two_way=True, is_active=True).select_related("trigger")
    for rule in forward:
        for other in rule.suggested_items.all():
            suggested.setdefault(other.pk, other)
        if rule.note:
            notes.append(rule.note)
    for rule in reverse:
        suggested.setdefault(rule.trigger.pk, rule.trigger)
        if rule.note:
            notes.append(rule.note)
    items = [i for pk, i in suggested.items() if pk != item.pk and pk not in on_order and i.is_orderable]
    return sorted(items, key=lambda i: i.part_no), list(dict.fromkeys(notes))


def kit_prompts(order, item):
    """Kits offered because `item` was added (only if it is marked 'prompts kit' and the kit has more to add)."""
    on_order = _on_order_ids(order)
    kits = Kit.objects.filter(is_active=True, kit_items__item=item, kit_items__prompts_kit=True).distinct()
    offers = [kit_offer(kit, on_order) for kit in kits]
    return [offer for offer in offers if any(row.checked for row in offer.rows)]


def after_add(order, item):
    items, notes = linked_suggestions(order, item)
    return {"suggest_for": item, "suggested_items": items, "suggest_notes": notes,
            "kit_offers": kit_prompts(order, item)}


def all_kit_offers(order):
    on_order = _on_order_ids(order)
    return [kit_offer(kit, on_order) for kit in Kit.objects.filter(is_active=True).prefetch_related("kit_items")]
