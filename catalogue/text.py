"""
Repair of garbled characters in imported names.

The catalogue has been through Excel/Windows more than once, so UTF-8 text was
sometimes re-read as Windows-1252. That turns "−" into "âˆ’" and "²" into
"Â²". We reverse that, then tidy a few characters that print badly.
"""

import re

# Character sequences that only appear in garbled text.
GARBLE_MARKERS = ("Ã", "Â", "â€", "âˆ", "â„", "�")

# Fallback for strings that cannot be reversed in one go.
KNOWN_FIXES = [
    ("âˆ’", "-"), ("â€“", "–"), ("â€”", "—"), ("â€™", "'"), ("â€˜", "'"),
    ("â€œ", '"'), ("â€\x9d", '"'), ("â€¦", "..."), ("â„¢", "™"),
    ("Ã—", "×"), ("Ã©", "é"), ("Ã¨", "è"), ("Ã¶", "ö"), ("Ã¼", "ü"),
    ("Â²", "²"), ("Â³", "³"), ("Â°", "°"), ("Â½", "½"), ("Â¼", "¼"), ("Â¾", "¾"),
    ("Â±", "±"), ("Â£", "£"), ("Â\xa0", " "), ("Â ", " "),
]

TIDY = {"−": "-", "\xa0": " ", "​": ""}


def looks_garbled(text):
    return any(marker in text for marker in GARBLE_MARKERS)


def repair_text(text):
    """Return text with common encoding damage repaired and whitespace tidied."""
    if not text:
        return ""
    text = str(text)
    for _ in range(3):  # some names were double-garbled
        if not looks_garbled(text):
            break
        try:
            fixed = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            fixed = text
            for bad, good in KNOWN_FIXES:
                fixed = fixed.replace(bad, good)
        if fixed == text:
            break
        text = fixed
    for bad, good in TIDY.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text).strip()
