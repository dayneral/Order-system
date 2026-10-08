"""
Starting values for measure type, unit and pack size of NEW items.

These encode the item setup decisions recorded in the build brief. They only
apply when an item is first imported; after that, admins edit units and pack
sizes in the catalogue screens and re-imports never overwrite them.
"""

import re
from decimal import Decimal

from .models import MeasureType, Section

WORKTOP_LENGTH_M = Decimal("3.00")

# Decisions for specific store codes.
CODE_RULES = {
    "V0002": {"measure_type": MeasureType.EACH, "unit": "2m length"},  # cove former, 2m lengths
    "V0004": {"measure_type": MeasureType.EACH},  # weld rod
    "PLA007": {"measure_type": MeasureType.WHOLE, "unit": "length"},  # angle bead
    "PLA012": {"measure_type": MeasureType.WHOLE, "unit": "length"},  # angle bead
    "PLA015": {"measure_type": MeasureType.WHOLE, "unit": "roll"},  # scrim tape
    "PLU037": {"measure_type": MeasureType.PACK, "pack_size": 8},  # sink clips
    "PLU045": {"measure_type": MeasureType.PACK},
    "PLU088": {"measure_type": MeasureType.PACK},
    "PLU091": {"measure_type": MeasureType.PACK},
    "PLU095": {"measure_type": MeasureType.PACK},
    "PLU099": {"measure_type": MeasureType.PACK},
    "BFS05": {"measure_type": MeasureType.PACK, "pack_size": 80,
              "note": "Wipes set to pack of 80, pending confirmation."},
}

PACK_PATTERNS = [
    re.compile(r"\bper\s+(\d{1,5})\b", re.I),
    re.compile(r"\b(?:pack|box|bag|tub)\s+of\s+(\d{1,5})\b", re.I),
    re.compile(r"\b(\d{1,5})\s*(?:pk|pack|pcs?)\b", re.I),
    re.compile(r"\bx\s*(\d{2,5})\s*$", re.I),
]
AREA_UNITS = {"m2", "m²", "sqm", "sq m", "m sq", "square metre", "square metres", "per m2", "per m²"}


def detect_pack_size(*texts):
    for text in texts:
        for pattern in PACK_PATTERNS:
            match = pattern.search(text or "")
            if match and int(match.group(1)) > 1:
                return int(match.group(1))
    return None


def _clean_unit(unit_text):
    text = (unit_text or "").strip().lower()
    if not text or text in ("ea", "each", "item", "no", "nr", "unit", "1"):
        return "each"
    if text in AREA_UNITS:
        return "m²"
    return text[:40]


def initial_setup(code, name, unit_text, section_key):
    """Return (fields, note) for a newly imported item."""
    unit = _clean_unit(unit_text)
    pack = detect_pack_size(unit_text, name)
    rule = CODE_RULES.get(code)
    lower_name = (name or "").lower()
    note = None

    if rule:
        fields = {"measure_type": rule["measure_type"], "unit": rule.get("unit", unit), "pack_size": 1}
        if rule["measure_type"] == MeasureType.PACK:
            fields["pack_size"] = rule.get("pack_size") or pack or 1
            fields["unit"] = "pack"
            if fields["pack_size"] == 1:
                note = "Sold per pack: please set the pack size."
        note = rule.get("note", note)
        return fields, note

    if "worktop" in lower_name or "splashback" in lower_name:
        return {"measure_type": MeasureType.CUT_TO_ORDER, "unit": "m", "pack_size": 1,
                "catalogue_length_m": WORKTOP_LENGTH_M}, None

    # Vinyl and sheet flooring is cut to the exact size ordered: length x width, priced per m².
    if section_key == Section.FLOORING and (unit == "m²" or "vinyl" in lower_name):
        return {"measure_type": MeasureType.AREA, "unit": "m²", "pack_size": 1}, None

    if pack:
        return {"measure_type": MeasureType.PACK, "unit": "pack", "pack_size": pack}, None

    return {"measure_type": MeasureType.EACH, "unit": unit, "pack_size": 1}, None
