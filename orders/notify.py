"""
Hooks called when stores need to hear about an order.

Stage 4 connects these to the order email (HTML body + PDF attachment).
Until then they only write a log line.
"""

import logging

logger = logging.getLogger(__name__)


def order_submitted(order):
    logger.info("Order %s submitted (email not yet connected)", order.order_number)


def order_amended(order):
    logger.info("Order %s amended (email not yet connected)", order.order_number)


def order_cancelled(order):
    logger.info("Order %s cancelled (email not yet connected)", order.order_number)
