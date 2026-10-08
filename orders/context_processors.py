from django.conf import settings


def order_values(request):
    """show_values: whether prices and order values appear on screen (settings.ORDER_VALUES_ENABLED)."""
    return {"show_values": settings.ORDER_VALUES_ENABLED}
