"""
Hooks called when stores need to hear about an order. Each sends the order
email (print-friendly HTML body) and returns the OrderEmail record, so
the caller can warn the user if sending failed.
"""

from notifications.sending import send_order_email


def order_submitted(order):
    return send_order_email(order, "submitted")


def order_amended(order):
    return send_order_email(order, "amended")


def order_cancelled(order):
    return send_order_email(order, "cancelled")
