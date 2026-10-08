"""Line values and order totals for every measure type (build brief section 5)."""

from decimal import Decimal as D

import pytest

from catalogue.models import MeasureType as M
from orders.pricing import area_m2, order_total, price_line


def test_each():
    assert price_line(M.EACH, D("4.20"), quantity=3).value == D("12.60")


def test_whole_items():
    assert price_line(M.WHOLE, D("1.55"), quantity=4).value == D("6.20")


def test_pack_is_number_of_packs_times_price_per_pack():
    # "Screws M4 x 50mm, per 100": pack size 100, price per pack; 2 packs = 2 x price.
    result = price_line(M.PACK, D("3.49"), quantity=2)
    assert result.value == D("6.98") and not result.is_estimate


def test_area_from_length_and_width():
    result = price_line(M.AREA, D("18.50"), length=D("3.2"), width=D("2.0"))
    assert result.area_m2 == D("6.40")
    assert result.value == D("118.40")


def test_area_rounded_to_two_places_then_line_to_the_penny():
    assert area_m2(D("2.3"), D("1.7")) == D("3.91")
    result = price_line(M.AREA, D("12.345"), length=D("2.3"), width=D("1.7"))
    assert result.value == D("48.27")  # 3.91 x 12.345 = 48.26895 -> 48.27


def test_no_waste_allowance_on_area():
    assert price_line(M.AREA, D("10"), length=D("1.0"), width=D("1.0")).value == D("10.00")


def test_cut_to_order_worktop_is_pro_rata_estimate():
    # 1.9m of a worktop sold per 3.0m length at £90: 1.9 / 3.0 x 90 = 57.00
    result = price_line(M.CUT_TO_ORDER, D("90.00"), length=D("1.9"), catalogue_length=D("3.0"))
    assert result.value == D("57.00") and result.is_estimate


def test_cut_to_order_pro_rata_rounding():
    # 1.0 / 3.0 x 100 = 33.333... -> 33.33
    assert price_line(M.CUT_TO_ORDER, D("100"), length=D("1.0"), catalogue_length=D("3.0")).value == D("33.33")


def test_cut_to_order_vinyl_uses_roll_width_and_price_per_m2():
    # 4.5m off a 2m roll at £18.50/m2 = 9 m2 x 18.50 = 166.50
    result = price_line(M.CUT_TO_ORDER, D("18.50"), length=D("4.5"), roll_width=D("2.0"))
    assert result.value == D("166.50") and result.is_estimate


def test_cut_to_order_vinyl_without_roll_width_is_length_times_price():
    assert price_line(M.CUT_TO_ORDER, D("18.50"), length=D("4.5")).value == D("83.25")


def test_linear():
    assert price_line(M.LINEAR, D("2.10"), length=D("2.5")).value == D("5.25")


def test_no_price_means_no_value():
    assert price_line(M.EACH, None, quantity=2).value is None


def test_half_pennies_round_up():
    assert price_line(M.EACH, D("0.125"), quantity=1).value == D("0.13")


def test_order_total_excludes_non_stocked_lines():
    assert order_total([D("12.60"), None, D("57.00"), D("0.40")]) == D("70.00")
    assert order_total([]) == D("0.00")


@pytest.mark.parametrize("measure", [M.EACH, M.PACK, M.WHOLE])
def test_whole_number_types_scale_linearly(measure):
    one = price_line(measure, D("7.77"), quantity=1).value
    assert price_line(measure, D("7.77"), quantity=10).value == one * 10
