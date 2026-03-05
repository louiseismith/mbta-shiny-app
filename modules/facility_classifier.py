"""facility_classifier.py

Rule-based classifier for MBTA accessibility facility naming patterns.
Shared between the app backend and the scraper's new-facility handler.

Pattern codes:
    1 — explicit line keyword ("Red Line", "Orange Line", etc.)
    2 — terminus reference ("Alewife platform", "Ashmont platform", etc.)
    3 — Green Line directional jargon ("Copley & West", "Park Street & North")
    4 — cross-platform connector ("X platform to Y platform")
    5 — generic / shared infrastructure (ramp, lobby, busway, etc.)
    0 — unknown / hard to classify
"""

_LINE_KEYWORDS = {"Orange Line", "Red Line", "Blue Line", "Green Line", "Commuter Rail"}
_TERMINUS_NAMES = {
    "Alewife", "Ashmont", "Braintree", "Oak Grove", "Forest Hills",
    "Bowdoin", "Wonderland", "Charles/MGH", "Mattapan", "Riverside",
    "Heath Street", "Cleveland Circle", "Boston College",
}
_GREEN_JARGON = {"Park Street & North", "Copley & West", "Kenmore & West"}


def classify_pattern(facility_name: str) -> int:
    """Classify an MBTA facility name into a naming pattern (0–5)."""
    name = facility_name

    if any(k in name for k in _LINE_KEYWORDS):
        return 1

    if "platform" in name and "platform" in name.split(" to ", 1)[-1]:
        return 4

    if "platform" not in name:
        return 5

    if any(t in name for t in _GREEN_JARGON):
        return 3

    if any(t in name for t in _TERMINUS_NAMES):
        return 2

    return 0
