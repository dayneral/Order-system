"""Keep each kit's automatic linked-item rules in step with the kit.

Every item in a kit gets a rule suggesting the kit's other items, so someone
ordering part of a kit is reminded of the rest. The rules are managed only
through the kit (they are hidden from the Linked items screen).
"""

from .models import LinkedItemRule


def sync_kit_rules(kit):
    items = [ki.item for ki in kit.kit_items.select_related("item")]
    ids = {item.pk for item in items}
    kit.link_rules.exclude(trigger_id__in=ids).delete()
    if len(items) < 2:
        kit.link_rules.all().delete()
        return
    for item in items:
        rule, _ = LinkedItemRule.objects.get_or_create(kit=kit, trigger=item)
        rule.suggested_items.set([other for other in items if other.pk != item.pk])
        rule.is_active = kit.is_active
        rule.two_way = False
        rule.note = f"Part of the {kit.name}"
        rule.save()
