"""
Order line pricing. Pure functions, no database, so they are easy to test.

Rules (build brief section 5, plus decisions in docs/DECISIONS.md):
- Each, pack and whole items: quantity × trade price (for packs, the number
  of packs × price per pack).
- Area (vinyl and other flooring): the user gives the exact length and width
  that stores will cut. m² = length × width, to 2 decimal places;
  value = m² × price per m². No waste allowance.
- Cut to order (worktops, splashbacks): requested length ÷ catalogue length
  × trade price. Shown as an estimate. If no catalogue length is set, the
  price is treated as per metre.
- Linear: length × price per metre.
- Each line is rounded to the penny (half up). Non-stocked lines have no value.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from catalogue.models import MeasureType

PENNY = Decimal("0.01")
TENTH = Decimal("0.1")
ONE = Decimal("1")

WHOLE_NUMBER_TYPES = {MeasureType.EACH, MeasureType.PACK, MeasureType.WHOLE}
LENGTH_TYPES = {MeasureType.CUT_TO_ORDER, MeasureType.LINEAR}


@dataclass
class LinePrice:
    value: Decimal | None
    area_m2: Decimal | None = None
    is_estimate: bool = False


def money(value):
    return Decimal(value).quantize(PENNY, rounding=ROUND_HALF_UP)


def area_m2(length, width):
    return (Decimal(length) * Decimal(width)).quantize(PENNY, rounding=ROUND_HALF_UP)


def price_line(measure_type, trade_price, quantity=None, length=None, width=None,
               catalogue_length=None):
    """Work out one line's value. Returns a LinePrice."""
    area = area_m2(length, width) if measure_type == MeasureType.AREA else None
    if trade_price is None:
        return LinePrice(value=None, area_m2=area)
    price = Decimal(trade_price)

    if measure_type in WHOLE_NUMBER_TYPES:
        return LinePrice(money(Decimal(quantity) * price))
    if measure_type == MeasureType.AREA:
        return LinePrice(money(area * price), area_m2=area)
    if measure_type == MeasureType.CUT_TO_ORDER:
        length = Decimal(length)
        per = Decimal(catalogue_length) if catalogue_length else ONE
        return LinePrice(money(length * price / per), is_estimate=True)
    if measure_type == MeasureType.LINEAR:
        return LinePrice(money(Decimal(length) * price))
    raise ValueError(f"Unknown measure type {measure_type!r}")


def order_total(line_values):
    """Sum of line values, ignoring lines with no value (non-stocked items)."""
    return sum((v for v in line_values if v is not None), Decimal("0.00"))
