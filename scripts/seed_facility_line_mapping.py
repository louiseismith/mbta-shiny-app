#!/usr/bin/env python3
"""seed_facility_line_mapping.py

Classifies all MBTA accessibility facilities by line(s) and direction,
using encoded MBTA topology knowledge. Outputs a JSON file for review
before uploading to Supabase.

Usage:
    python seed_facility_line_mapping.py              # writes facility_line_mapping.json
    python seed_facility_line_mapping.py --upload      # also writes to Supabase
"""

import argparse
import json
import os
import re
import psycopg2
from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# MBTA Topology Knowledge
# =============================================================================

# Terminus → (line, direction)
# Used for P2 (terminus reference) classification
TERMINUS_MAP = {
    # Red Line (N/S)
    "Alewife":           ("Red", "northbound"),
    "Ashmont":           ("Red", "southbound"),
    "Braintree":         ("Red", "southbound"),
    "Ashmont/Braintree": ("Red", "southbound"),
    # Orange Line (N/S)
    "Oak Grove":         ("Orange", "northbound"),
    "Forest Hills":      ("Orange", "southbound"),
    # Blue Line (E/W)
    "Bowdoin":           ("Blue", "westbound"),
    "Wonderland":        ("Blue", "eastbound"),
    # Green Line branch termini (all westbound/outbound)
    "Boston College":    ("Green-B", "westbound"),
    "Cleveland Circle":  ("Green-C", "westbound"),
    "Riverside":         ("Green-D", "westbound"),
    # Green Line northern terminus (eastbound = inbound in Green Line convention)
    "Lechmere":          ("Green-E", "eastbound"),
    # Mattapan Trolley
    "Mattapan":          ("Mattapan", "southbound"),
}

# Green Line directional labels → direction
# "Park Street & North" variants = eastbound (inbound toward downtown)
# "Copley & West" / "Kenmore & West" variants = westbound (outbound)
GREEN_EASTBOUND_LABELS = [
    "Park Street & North",
    "Park St & North",
    "Government Center & North",
    "North Station & North",
    "Lechmere & North",
    "Union Square, Medford/Tufts",  # Green-D/E northbound extension
]
GREEN_WESTBOUND_LABELS = [
    "Copley & West",
    "Kenmore & West",
    "Heath Street",        # Green-E southern terminus (westbound/outbound)
    "Westbound",           # explicit directional
]

# Explicit line keywords → route IDs (P1)
LINE_KEYWORDS = {
    "Orange Line":    ["Orange"],
    "Red Line":       ["Red"],
    "Blue Line":      ["Blue"],
    "Green Line":     "GREEN",        # sentinel — expand to station's Green branches
    "Commuter Rail":  "CR",           # sentinel — expand to station's CR routes
    "Mattapan Line":  ["Mattapan"],
    "Silver Line":    "SL",           # sentinel — not in station_routes but noted
}


# =============================================================================
# Classification Logic
# =============================================================================

def get_green_routes(routes):
    """Return the Green Line route IDs from the station's route list."""
    return [r for r in routes if r.startswith("Green-")]


def get_cr_routes(routes):
    """Return the Commuter Rail route IDs from the station's route list."""
    return [r for r in routes if r.startswith("CR-")]


def classify_facility(facility):
    """Classify a single facility. Returns dict with lines, direction, pattern, reasoning."""
    name = facility["facility_name"]
    routes = facility["routes"]
    station = facility["station_name"]

    # -------------------------------------------------------------------------
    # No routes available — can't classify
    # -------------------------------------------------------------------------
    if not routes:
        return {
            "lines": None,
            "direction": None,
            "pattern": 0,
            "reasoning": "No transit routes mapped to this station.",
        }

    # -------------------------------------------------------------------------
    # Single-route station — all facilities serve that route
    # -------------------------------------------------------------------------
    if len(routes) == 1:
        direction = _extract_direction_single_route(name, routes[0])
        pattern = _detect_pattern(name, routes)
        return {
            "lines": routes,
            "direction": direction,
            "pattern": pattern if pattern != 0 else 5,
            "reasoning": f"Single-route station ({routes[0]}); direction from facility name."
                         if direction else
                         f"Single-route station ({routes[0]}).",
        }

    # -------------------------------------------------------------------------
    # Multi-route station — need to determine which line(s)
    # -------------------------------------------------------------------------

    # Check for explicit line keywords (P1) — before generic check so that
    # "Commuter Rail track 2 to lobby" is P1 (CR only), not P5 (all routes)
    p1_result = _check_explicit_line(name, routes)
    if p1_result:
        return p1_result

    # Check for ramp access or generic infrastructure (P5)
    if _is_generic(name):
        return {
            "lines": routes,
            "direction": None,
            "pattern": 5,
            "reasoning": "Generic shared infrastructure (ramp, busway, lobby-to-street, passageway).",
        }

    # Check for Green Line directional labels (P3) — before P4 so that
    # "Copley & West platform to lobby" is P3 (westbound), not P4
    p3_result = _check_green_directional(name, routes)
    if p3_result:
        return p3_result

    # Check for cross-platform connector (P4) — two terminus references
    p4_result = _check_cross_platform(name, routes)
    if p4_result:
        return p4_result

    # Check for single terminus reference (P2)
    p2_result = _check_terminus_reference(name, routes)
    if p2_result:
        return p2_result

    # Check for combined platform labels (e.g. "Forest Hills, Copley & West platform")
    combined_result = _check_combined_platform(name, routes)
    if combined_result:
        return combined_result

    # Fallback — assume shared infrastructure
    return {
        "lines": routes,
        "direction": None,
        "pattern": 5,
        "reasoning": "No line-specific or directional keywords found; treating as shared.",
    }


def _is_generic(name):
    """Check if facility name indicates generic/shared infrastructure."""
    name_lower = name.lower()
    # Ramp access
    if "ramp access" in name_lower:
        return True
    # Mobile lift with no directional info
    if "mobile lift" in name_lower and "(" not in name:
        return True
    # Lobby/passageway/busway to street (no platform reference)
    paren = _get_paren_content(name)
    if paren:
        paren_lower = paren.lower()
        # No "platform" reference and connects lobby/street/busway/passageway
        if "platform" not in paren_lower:
            generic_terms = ["lobby", "street", "busway", "passageway", "parking",
                             "concourse", "square", "avenue", "financial",
                             "csa area", "td garden"]
            if any(t in paren_lower for t in generic_terms):
                # But check it's not a terminus reference (e.g. "Oak Grove" in the text)
                has_terminus = any(t.lower() in paren_lower for t in TERMINUS_MAP)
                has_green = any(g.lower() in paren_lower for g in
                                GREEN_EASTBOUND_LABELS + GREEN_WESTBOUND_LABELS)
                if not has_terminus and not has_green:
                    return True
    return False


def _get_paren_content(name):
    """Extract content inside parentheses."""
    m = re.search(r'\((.+)\)', name)
    return m.group(1) if m else None


def _extract_direction_single_route(name, route_id):
    """For single-route stations, extract direction from terminus references."""
    paren = _get_paren_content(name)
    if not paren:
        return None

    for terminus, (line, direction) in TERMINUS_MAP.items():
        if terminus.lower() in paren.lower():
            # Verify the terminus line matches the station's route
            if line == route_id or (line == "Red" and route_id == "Red"):
                return direction

    # Check explicit directional words
    paren_lower = paren.lower()
    for label in GREEN_EASTBOUND_LABELS:
        if label.lower() in paren_lower:
            return "eastbound"
    for label in GREEN_WESTBOUND_LABELS:
        if label.lower() in paren_lower:
            return "westbound"

    if "northbound" in paren_lower:
        return "northbound"
    if "southbound" in paren_lower:
        return "southbound"
    if "eastbound" in paren_lower:
        return "eastbound"
    if "westbound" in paren_lower:
        return "westbound"

    return None


def _detect_pattern(name, routes):
    """Detect the naming pattern for a facility."""
    paren = _get_paren_content(name)
    if not paren:
        if "ramp access" in name.lower():
            return 5
        if "mobile lift" in name.lower():
            return 5
        return 0

    paren_lower = paren.lower()

    # P1: Explicit line keywords
    for kw in LINE_KEYWORDS:
        if kw.lower() in name.lower():
            return 1

    # P3: Green Line directional
    for label in GREEN_EASTBOUND_LABELS + GREEN_WESTBOUND_LABELS:
        if label.lower() in paren_lower:
            return 3

    # P2: Terminus reference
    for terminus in TERMINUS_MAP:
        if terminus.lower() in paren_lower:
            return 2

    # P5: Generic
    if "platform" not in paren_lower:
        return 5

    return 0


def _check_explicit_line(name, routes):
    """P1: Check for explicit line name keywords."""
    name_lower = name.lower()
    matched_lines = set()
    found_keywords = []

    for keyword, line_ids in LINE_KEYWORDS.items():
        if keyword.lower() in name_lower:
            found_keywords.append(keyword)
            if line_ids == "GREEN":
                matched_lines.update(get_green_routes(routes))
            elif line_ids == "CR":
                matched_lines.update(get_cr_routes(routes))
            elif line_ids == "SL":
                # Silver Line not in station_routes; note but don't add
                pass
            else:
                matched_lines.update(lid for lid in line_ids if lid in routes)

    if not matched_lines:
        return None

    # Direction: if P1 and the facility also has a directional reference, capture it
    direction = None
    paren = _get_paren_content(name)
    if paren:
        paren_lower = paren.lower()
        # Check for terminus in the parenthetical
        for terminus, (line, d) in TERMINUS_MAP.items():
            if terminus.lower() in paren_lower and line in matched_lines:
                direction = d
                break

    return {
        "lines": sorted(matched_lines),
        "direction": direction,
        "pattern": 1,
        "reasoning": f"Explicit line keyword(s): {', '.join(found_keywords)}.",
    }


def _check_cross_platform(name, routes):
    """P4: Check for cross-platform connectors (two terminus/line references)."""
    paren = _get_paren_content(name)
    if not paren:
        return None

    # Find all terminus references
    found_termini = []
    for terminus, (line, direction) in TERMINUS_MAP.items():
        if terminus.lower() in paren.lower():
            found_termini.append((terminus, line, direction))

    # Also check Green Line directional labels
    green_refs = []
    for label in GREEN_EASTBOUND_LABELS:
        if label.lower() in paren.lower():
            green_refs.append(("eastbound", label))
    for label in GREEN_WESTBOUND_LABELS:
        if label.lower() in paren.lower():
            green_refs.append(("westbound", label))

    # Cross-platform: references to two different lines/directions
    all_lines = set()
    for _, line, _ in found_termini:
        all_lines.add(line)
    if green_refs:
        all_lines.update(get_green_routes(routes))

    # Need at least 2 distinct line references or " to " connecting two named endpoints
    if " to " in paren:
        parts = paren.split(" to ", 1)
        left_termini = [(t, l, d) for t, l, d in found_termini if t.lower() in parts[0].lower()]
        right_termini = [(t, l, d) for t, l, d in found_termini if t.lower() in parts[1].lower()]

        # Also check Green refs on each side
        left_green = [g for g in green_refs if g[1].lower() in parts[0].lower()]
        right_green = [g for g in green_refs if g[1].lower() in parts[1].lower()]

        left_lines = set()
        for _, l, _ in left_termini:
            left_lines.add(l)
        if left_green:
            left_lines.update(get_green_routes(routes))

        right_lines = set()
        for _, l, _ in right_termini:
            right_lines.add(l)
        if right_green:
            right_lines.update(get_green_routes(routes))

        if left_lines and right_lines and left_lines != right_lines:
            # True cross-platform: connects two different lines
            combined = left_lines | right_lines
            return {
                "lines": sorted(lid for lid in combined if lid in routes),
                "direction": None,
                "pattern": 4,
                "reasoning": f"Cross-platform connector linking {', '.join(sorted(left_lines))} and {', '.join(sorted(right_lines))}.",
            }

        if left_lines and right_lines and left_lines == right_lines:
            # Same line, different directions (e.g. "Alewife platform to Ashmont platform")
            # This is still P4 but within one line
            line = list(left_lines)[0]
            return {
                "lines": [line] if line in routes else sorted(lid for lid in left_lines if lid in routes),
                "direction": None,
                "pattern": 4,
                "reasoning": f"Cross-platform connector between two {line} platforms.",
            }

    # Multiple terminus references on the same side (e.g. "Forest Hills, Wonderland platforms")
    if len(all_lines) >= 2:
        # Check if this is a single endpoint listing (not "X to Y")
        # e.g. "Oak Grove platform to Forest Hills, Wonderland platforms"
        return {
            "lines": sorted(lid for lid in all_lines if lid in routes),
            "direction": None,
            "pattern": 4,
            "reasoning": f"References multiple lines: {', '.join(sorted(all_lines))}.",
        }

    return None


def _check_green_directional(name, routes):
    """P3: Check for Green Line directional labels."""
    paren = _get_paren_content(name)
    text = paren if paren else name

    green_routes = get_green_routes(routes)
    if not green_routes:
        return None

    for label in GREEN_EASTBOUND_LABELS:
        if label.lower() in text.lower():
            return {
                "lines": green_routes,
                "direction": "eastbound",
                "pattern": 3,
                "reasoning": f"Green Line eastbound label: '{label}'.",
            }

    for label in GREEN_WESTBOUND_LABELS:
        if label.lower() in text.lower():
            return {
                "lines": green_routes,
                "direction": "westbound",
                "pattern": 3,
                "reasoning": f"Green Line westbound label: '{label}'.",
            }

    return None


def _check_terminus_reference(name, routes):
    """P2: Check for a single terminus reference."""
    paren = _get_paren_content(name)
    if not paren:
        return None

    paren_lower = paren.lower()

    for terminus, (line, direction) in TERMINUS_MAP.items():
        if terminus.lower() in paren_lower:
            if line in routes:
                return {
                    "lines": [line],
                    "direction": direction,
                    "pattern": 2,
                    "reasoning": f"Terminus reference: '{terminus}' = {line} {direction}.",
                }
            # Special case: "Braintree branch" at a station without explicit "Red"
            # but with Red Line (Braintree is a Red Line terminus)
            if line == "Red" and "Red" in routes:
                return {
                    "lines": ["Red"],
                    "direction": direction,
                    "pattern": 2,
                    "reasoning": f"Terminus reference: '{terminus}' = Red Line {direction}.",
                }

    return None


def _check_combined_platform(name, routes):
    """Handle combined platform labels like 'Forest Hills, Copley & West platform'."""
    paren = _get_paren_content(name)
    if not paren:
        return None

    paren_lower = paren.lower()

    # North Station pattern: "Forest Hills, Copley & West platform" or
    # "Lobby to Forest Hills, Copley & West platform"
    matched_lines = set()
    directions = set()

    # Check Orange/Red/Blue terminus references
    for terminus, (line, direction) in TERMINUS_MAP.items():
        if terminus.lower() in paren_lower and line in routes:
            matched_lines.add(line)
            directions.add(direction)

    # Check Green Line directional labels
    for label in GREEN_EASTBOUND_LABELS:
        if label.lower() in paren_lower:
            matched_lines.update(get_green_routes(routes))
            directions.add("eastbound")
    for label in GREEN_WESTBOUND_LABELS:
        if label.lower() in paren_lower:
            matched_lines.update(get_green_routes(routes))
            directions.add("westbound")

    if matched_lines:
        # If all directions are the same, use that direction
        direction = list(directions)[0] if len(directions) == 1 else None
        pattern = 2 if len(matched_lines) == 1 else 4
        if any(label.lower() in paren_lower for label in
               GREEN_EASTBOUND_LABELS + GREEN_WESTBOUND_LABELS):
            pattern = 3 if len(matched_lines) <= len(get_green_routes(routes)) else 4

        return {
            "lines": sorted(lid for lid in matched_lines if lid in routes),
            "direction": direction,
            "pattern": pattern,
            "reasoning": f"Combined platform reference serving {', '.join(sorted(matched_lines))}.",
        }

    return None


# =============================================================================
# Special Case Overrides
# =============================================================================
# Some facilities have names that don't fit neatly into pattern rules.
# These are manually classified based on MBTA knowledge.

MANUAL_OVERRIDES = {
    # South Station: "Airport/Design Center/Chelsea platform" = Silver Line,
    # but Silver Line is not in station_routes. These facilities connect
    # Red Line platforms to Silver Line platforms.
    "918": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont/Braintree platform (Red southbound) to Silver Line platform.",
    },
    "901": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont/Braintree platform (Red southbound) to Silver Line drop-off.",
    },
    "919": {
        "lines": ["Red"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Alewife platform (Red northbound) to Silver Line platform.",
    },
    "927": {
        "lines": ["Red"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Alewife platform (Red northbound) to Silver Line drop-off.",
    },

    # South Station: "South Station platform" = Commuter Rail platform
    "399": {
        "lines": None,  # will be filled with CR routes
        "direction": None,
        "pattern": 1,
        "reasoning": "South Station platform = Commuter Rail platform.",
        "_use_cr": True,
    },
    "419": {
        "lines": None,
        "direction": None,
        "pattern": 1,
        "reasoning": "Lobby to South Station platform = Commuter Rail platform.",
        "_use_cr": True,
    },
    "400": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 4,
        "reasoning": "Ashmont/Braintree platform (Red southbound) to Commuter Rail platform.",
        # Serves Red because it's on the Red platform side
    },
    "420": {
        "lines": ["Red"],
        "direction": "northbound",
        "pattern": 4,
        "reasoning": "Alewife platform (Red northbound) to Commuter Rail platform.",
    },

    # Government Center: "Copley & West, Blue Line platforms to street"
    # Serves both Green westbound AND Blue — it's a combined exit
    "445": {
        "lines": None,  # filled dynamically
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined exit serving Green Line westbound and Blue Line platforms.",
        "_use_green_and_blue": True,
    },

    # State: complex multi-level station connecting Orange and Blue
    # "Oak Grove platform to Forest Hills, Wonderland platforms" — elevator
    # connecting Orange northbound platform to Orange southbound + Blue eastbound
    "802": {
        "lines": ["Blue", "Orange"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Connects Orange NB platform to Orange SB and Blue EB platforms.",
    },
    "967": {
        "lines": ["Blue", "Orange"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Connects Orange NB platform to Orange SB and Blue EB platforms.",
    },
    # "Oak Grove platform to Wonderland platform" — connects Orange NB to Blue EB
    "501": {
        "lines": ["Blue", "Orange"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Escalator connecting Orange NB platform to Blue EB platform.",
    },
    # "Oak Grove platform to City Hall Plaza" — Orange northbound to street
    "374": {
        "lines": ["Orange"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Oak Grove platform (Orange northbound) to street.",
    },
    # "Oak Grove platform to Old State House lobby"
    "6": {
        "lines": ["Orange"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Oak Grove platform (Orange northbound) to street exit.",
    },

    # Ashmont: "Ashmont platform to Mattapan Line lobby, busway" — this is the
    # platform where trains terminate (Red southbound) AND Mattapan trolley departs
    "970": {
        "lines": ["Mattapan", "Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont terminus platform serves Red southbound and Mattapan trolley.",
    },
    "437": {
        "lines": ["Mattapan", "Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont terminus platform serves Red southbound and Mattapan trolley.",
    },
    "438": {
        "lines": ["Mattapan", "Red"],
        "direction": "southbound",
        "pattern": 1,
        "reasoning": "Ashmont platform to Mattapan Line — explicit Mattapan Line reference, serves Red SB + Mattapan.",
    },

    # Ashmont: "Alewife platform to Mattapan Line lobby" — Red northbound platform,
    # but exits to Mattapan Line lobby (transfer point)
    "969": {
        "lines": ["Mattapan", "Red"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Cross-platform connector: Red northbound (Alewife) platform to Mattapan Line lobby.",
    },

    # Ashmont mobile lift (Mattapan Line) — serves Mattapan trolley specifically
    "portlift-asmnl": {
        "lines": ["Mattapan"],
        "direction": None,
        "pattern": 1,
        "reasoning": "Explicit Mattapan Line reference.",
    },

    # Government Center: "North Station & North platform" — Green Line eastbound
    # equivalent (Green Line's inbound direction goes toward North Station and north)
    "444": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'North Station & North' = Green Line eastbound/inbound platform.",
        "_use_green": True,
    },

    # Lechmere: mobile lifts with directional info
    "portlift-lech-1": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound/outbound.",
        "_use_green": True,
    },
    "portlift-lech-0": {
        "lines": None,
        "direction": "northbound",
        "pattern": 3,
        "reasoning": "'Union Square, Medford/Tufts' = Green Line northbound extension.",
        "_use_green": True,
    },

    # Science Park/West End: "Copley & West platform" and "Lechmere & North platform"
    "980": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound/outbound.",
        "_use_green": True,
    },
    "981": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Lechmere & North' = Green Line eastbound/inbound.",
        "_use_green": True,
    },
    "portlift-spmnl": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Lechmere & North' = Green Line eastbound/inbound.",
        "_use_green": True,
    },

    # Haymarket mobile lifts
    "portlift-haecl-1": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound/outbound.",
        "_use_green": True,
    },
    "portlift-haecl-0": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'North Station & North' = Green Line eastbound/inbound.",
        "_use_green": True,
    },

    # North Station mobile lifts
    "portlift-north-1": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound/outbound.",
        "_use_green": True,
    },
    "portlift-north-0": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Lechmere & North' = Green Line eastbound/inbound.",
        "_use_green": True,
    },

    # Downtown Crossing: "Ashmont/Braintree platform to Oak Grove platform" (escalator)
    # Connects Red southbound to Orange northbound
    "331": {
        "lines": ["Orange", "Red"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Escalator connecting Red SB platform to Orange NB platform.",
    },
    "332": {
        "lines": ["Orange", "Red"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Escalator connecting Red NB platform to Orange NB platform.",
    },

    # Park Street: complex multi-line station
    # "Red Line center platform" references — Red serves both directions from center
    "808": {
        "lines": ["Red"],
        "direction": None,
        "pattern": 1,
        "reasoning": "Red Line center platform to Green Line platform — Red Line side.",
    },
    "979": {
        "lines": ["Red"],
        "direction": None,
        "pattern": 1,
        "reasoning": "Red Line center platform to Green Line westbound platform.",
    },
    # "Green Line underpass to Government Center & North platform"
    "812": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Government Center & North' = Green Line eastbound platform.",
        "_use_green": True,
    },
    # "Green Line underpass to Copley & West platform"
    "823": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound platform.",
        "_use_green": True,
    },
    # "Government Center & North lobby to Tremont Street" — shared lobby exit
    "804": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Lobby to street exit — shared infrastructure.",
        "_use_all": True,
    },
    # "Copley & West platform to Boston Common" — Green westbound to street
    "978": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound platform to street.",
        "_use_green": True,
    },
    # Escalators at Park Street
    "320": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Government Center & North' = Green Line eastbound platform.",
        "_use_green": True,
    },
    "373": {
        "lines": ["Red"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Alewife platform = Red Line northbound.",
    },
    "375": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont/Braintree platform = Red Line southbound.",
    },

    # Park Street mobile lifts — no directional info, shared
    "portlift-pktrm-0": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Mobile lift with no directional info — shared station infrastructure.",
        "_use_all": True,
    },
    "portlift-pktrm-1": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Mobile lift with no directional info — shared station infrastructure.",
        "_use_all": True,
    },

    # Government Center mobile lift — no directional info
    "portlift-gover": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Mobile lift with no directional info — shared station infrastructure.",
        "_use_all": True,
    },

    # Kenmore mobile lift — no directional info
    "portlift-kencl": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Mobile lift with no directional info — shared station infrastructure.",
        "_use_all": True,
    },

    # Copley mobile lifts — have directional info in parens
    "portlift-coecl-1": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Kenmore & West, Heath Street' = Green Line westbound platform.",
        "_use_green": True,
    },
    "portlift-coecl-0": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Park Street & North' = Green Line eastbound platform.",
        "_use_green": True,
    },

    # Arlington mobile lifts
    "portlift-armnl-1": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Copley & West' = Green Line westbound platform.",
        "_use_green": True,
    },
    "portlift-armnl-0": {
        "lines": None,
        "direction": "eastbound",
        "pattern": 3,
        "reasoning": "'Park Street & North' = Green Line eastbound platform.",
        "_use_green": True,
    },

    # Hynes Convention Center — only has one escalator, westbound
    "315": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Kenmore & West' = Green Line westbound platform.",
        "_use_green": True,
    },

    # Copley escalator — westbound
    "439": {
        "lines": None,
        "direction": "westbound",
        "pattern": 3,
        "reasoning": "'Kenmore & West, Heath Street' = Green Line westbound lobby to street.",
        "_use_green": True,
    },

    # Porter: escalators between Alewife and Ashmont/Braintree platforms
    # These are within-station Red Line connections (between the two platform levels)
    "506": {
        "lines": ["Red"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Connects Alewife (NB) platform to Ashmont/Braintree (SB) platform within Red Line.",
    },
    "507": {
        "lines": ["Red"],
        "direction": None,
        "pattern": 4,
        "reasoning": "Connects Alewife (NB) platform to Ashmont/Braintree (SB) platform within Red Line.",
    },

    # JFK/UMass escalator — "Ashmont platform to Columbia Road"
    "322": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont platform = Red Line Ashmont branch, southbound.",
    },

    # North Station: "Lobby to Forest Hills, Copley & West platform" — combined
    # Orange southbound + Green westbound platform
    "910": {
        "lines": None,
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined Orange SB (Forest Hills) and Green WB (Copley & West) platform.",
        "_use_orange_green": True,
    },
    "302": {
        "lines": None,
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined Orange SB (Forest Hills) and Green WB (Copley & West) platform.",
        "_use_orange_green": True,
    },
    "303": {
        "lines": None,
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined Orange SB (Forest Hills) and Green WB (Copley & West) platform.",
        "_use_orange_green": True,
    },
    "391": {
        "lines": None,
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined Orange SB (Forest Hills) and Green WB (Copley & West) platform.",
        "_use_orange_green": True,
    },
    "392": {
        "lines": None,
        "direction": None,
        "pattern": 4,
        "reasoning": "Combined Orange SB (Forest Hills) and Green WB (Copley & West) platform.",
        "_use_orange_green": True,
    },

    # Haymarket: "Underpass to Green Line platforms, busway"
    "906": {
        "lines": None,
        "direction": None,
        "pattern": 1,
        "reasoning": "Explicit 'Green Line platforms' reference.",
        "_use_green": True,
    },
    # Haymarket: "Green Line lobby to busway, Sudbury Street, New Chardon Street"
    "908": {
        "lines": None,
        "direction": None,
        "pattern": 1,
        "reasoning": "Green Line lobby to street — serves Green Line passengers.",
        "_use_green": True,
    },
    "313": {
        "lines": None,
        "direction": None,
        "pattern": 1,
        "reasoning": "Green Line lobby to street — serves Green Line passengers.",
        "_use_green": True,
    },

    # South Station: "Red Line, Silver Line SL1/SL2/SL3 lobby to Commuter Rail lobby"
    "6476": {
        "lines": None,
        "direction": None,
        "pattern": 1,
        "reasoning": "Connects Red Line/Silver Line lobby to Commuter Rail lobby — serves all lines.",
        "_use_all": True,
    },

    # South Station: generic lobbies and concourses
    "926": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Subway lobby to street — shared infrastructure.",
        "_use_all": True,
    },
    "949": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Lobby to street — shared infrastructure.",
        "_use_all": True,
    },
    "386": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "1 Financial Center to lobby — shared building access.",
        "_use_all": True,
    },
    "387": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Lobby to 1 Financial Center — shared building access.",
        "_use_all": True,
    },
    "388": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Lobby to CSA area — shared infrastructure.",
        "_use_all": True,
    },
    "411": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Airport/Design Center (Silver Line) platform to lobby — treated as shared.",
        "_use_all": True,
    },
    "424": {
        "lines": None,
        "direction": None,
        "pattern": 5,
        "reasoning": "Lobby to street — shared infrastructure.",
        "_use_all": True,
    },
    # "Ashmont/Braintree platform to CSA area"
    "389": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont/Braintree platform = Red Line southbound.",
    },
    # "Ashmont/Braintree platform to Airport/Design Center platform" — Red SB to Silver Line
    "390": {
        "lines": ["Red"],
        "direction": "southbound",
        "pattern": 2,
        "reasoning": "Ashmont/Braintree platform = Red Line southbound side.",
    },
    # "Alewife platform to Airport/Design Center platform" — Red NB to Silver Line
    "398": {
        "lines": ["Red"],
        "direction": "northbound",
        "pattern": 2,
        "reasoning": "Alewife platform = Red Line northbound side.",
    },
}


def resolve_override(override, routes):
    """Fill in dynamic line lists for manual overrides."""
    result = dict(override)

    if result.pop("_use_cr", False):
        result["lines"] = sorted(get_cr_routes(routes))
    if result.pop("_use_green", False):
        result["lines"] = sorted(get_green_routes(routes))
    if result.pop("_use_all", False):
        result["lines"] = sorted(routes)
    if result.pop("_use_green_and_blue", False):
        result["lines"] = sorted(get_green_routes(routes) + ["Blue"])
    if result.pop("_use_orange_green", False):
        result["lines"] = sorted(["Orange"] + get_green_routes(routes))

    return result


# =============================================================================
# Main
# =============================================================================

def classify_all(facilities):
    """Classify all facilities. Returns list of result dicts."""
    results = []
    for f in facilities:
        fid = f["facility_id"]

        # Check manual overrides first
        if fid in MANUAL_OVERRIDES:
            classification = resolve_override(MANUAL_OVERRIDES[fid], f["routes"])
        else:
            classification = classify_facility(f)

        results.append({
            "facility_id": fid,
            "facility_name": f["facility_name"],
            "facility_type": f["facility_type"],
            "station_name": f["station_name"],
            "stop_id": f["stop_id"],
            "lines": classification["lines"],
            "direction": classification["direction"],
            "pattern": classification["pattern"],
            "source": "claude",
            "reasoning": classification["reasoning"],
        })
    return results


def upload_to_supabase(results):
    """Write classification results to Supabase facility_line_mapping table."""
    host = os.getenv("SUPABASE_HOST")
    if not host:
        print("ERROR: SUPABASE_* env vars not set.")
        return False

    conn = psycopg2.connect(
        host=host,
        port=int(os.getenv("SUPABASE_PORT", "5432")),
        dbname=os.getenv("SUPABASE_DB", "postgres"),
        user=os.getenv("SUPABASE_USER", "postgres"),
        password=os.getenv("SUPABASE_PASSWORD", ""),
        connect_timeout=10,
        sslmode="require",
    )

    try:
        with conn:
            with conn.cursor() as cur:
                # Create table if it doesn't exist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS facility_line_mapping (
                        facility_id   TEXT PRIMARY KEY,
                        facility_name TEXT NOT NULL,
                        station_name  TEXT NOT NULL,
                        lines         TEXT[],
                        direction     TEXT,
                        pattern       SMALLINT,
                        source        TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT now(),
                        updated_at    TIMESTAMPTZ DEFAULT now()
                    )
                """)

                # Upsert (don't overwrite manual overrides)
                for r in results:
                    cur.execute("""
                        INSERT INTO facility_line_mapping
                            (facility_id, facility_name, station_name, lines,
                             direction, pattern, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (facility_id) DO UPDATE SET
                            facility_name = EXCLUDED.facility_name,
                            station_name = EXCLUDED.station_name,
                            lines = EXCLUDED.lines,
                            direction = EXCLUDED.direction,
                            pattern = EXCLUDED.pattern,
                            source = EXCLUDED.source,
                            updated_at = now()
                        WHERE facility_line_mapping.source != 'manual'
                    """, (
                        r["facility_id"],
                        r["facility_name"],
                        r["station_name"],
                        r["lines"],
                        r["direction"],
                        r["pattern"],
                        r["source"],
                    ))

        print(f"Uploaded {len(results)} rows to facility_line_mapping.")
        return True
    finally:
        conn.close()


def print_summary(results):
    """Print classification summary."""
    from collections import Counter

    pattern_counts = Counter(r["pattern"] for r in results)
    direction_counts = Counter(r["direction"] for r in results)

    pattern_labels = {
        0: "Unknown",
        1: "Explicit line name",
        2: "Terminus reference",
        3: "Green Line directional",
        4: "Cross-platform connector",
        5: "Generic/shared",
    }

    print(f"\nTotal facilities: {len(results)}")
    print(f"\nBy pattern:")
    for p in sorted(pattern_counts):
        print(f"  P{p} ({pattern_labels.get(p, '?'):<25}): {pattern_counts[p]:>3}")

    print(f"\nBy direction:")
    for d in sorted(direction_counts, key=lambda x: x or ""):
        print(f"  {str(d):<12}: {direction_counts[d]:>3}")

    # Facilities with no lines
    no_lines = [r for r in results if not r["lines"]]
    if no_lines:
        print(f"\nWARNING: {len(no_lines)} facilities with no lines:")
        for r in no_lines:
            print(f"  [{r['facility_id']}] {r['facility_name']}")


def main():
    parser = argparse.ArgumentParser(description="Classify MBTA facilities by line and direction.")
    parser.add_argument("--upload", action="store_true", help="Also upload to Supabase")
    _data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    parser.add_argument("--input", default=os.path.join(_data_dir, "seed_facilities_input.json"),
                        help="Input JSON file (default: ../data/seed_facilities_input.json)")
    parser.add_argument("--output", default=os.path.join(_data_dir, "facility_line_mapping.json"),
                        help="Output JSON file (default: ../data/facility_line_mapping.json)")
    args = parser.parse_args()

    # Load facilities
    with open(args.input) as f:
        facilities = json.load(f)
    print(f"Loaded {len(facilities)} facilities from {args.input}")

    # Classify
    results = classify_all(facilities)

    # Save to JSON
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {len(results)} classifications to {args.output}")

    # Summary
    print_summary(results)

    # Upload if requested
    if args.upload:
        upload_to_supabase(results)


if __name__ == "__main__":
    main()
