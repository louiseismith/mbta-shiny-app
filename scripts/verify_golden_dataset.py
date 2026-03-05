# verify_golden_dataset.py
# Fetches official MBTA route direction/terminus data and cross-references it
# against our golden dataset to verify labels.

import requests
import os
from dotenv import load_dotenv
from facility_interpreter_golden import GOLDEN

load_dotenv()

BASE_URL = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")
headers = {"x-api-key": API_KEY} if API_KEY else {}


def fetch_route_directions():
    """Fetch all rapid-transit + commuter rail routes with their direction destinations."""
    resp = requests.get(
        f"{BASE_URL}/routes",
        headers=headers,
        params={"filter[type]": "0,1,2"},
    )
    resp.raise_for_status()

    routes = {}
    for r in resp.json().get("data", []):
        rid = r["id"]
        attrs = r["attributes"]
        routes[rid] = {
            "name": attrs.get("long_name") or attrs.get("short_name"),
            "direction_names": attrs.get("direction_names", []),        # e.g. ["Outbound", "Inbound"]
            "direction_destinations": attrs.get("direction_destinations", []),  # e.g. ["Alewife", "Ashmont/Braintree"]
        }
    return routes


def build_terminus_lookup(routes):
    """Build a lookup: terminus keyword → list of (route_id, direction_index, direction_name).

    Splits multi-destination strings like "Ashmont/Braintree" into individual keywords.
    """
    lookup = {}
    for rid, info in routes.items():
        for direction_idx, destination in enumerate(info["direction_destinations"]):
            if not destination:
                continue
            # Split "Ashmont/Braintree" → ["Ashmont", "Braintree"]
            keywords = [d.strip() for d in destination.replace("/", "|").replace(",", "|").split("|")]
            for keyword in keywords:
                if not keyword or keyword.lower() in ("n/a", "varies"):
                    continue
                key = keyword.lower()
                if key not in lookup:
                    lookup[key] = []
                lookup[key].append({
                    "route_id": rid,
                    "route_name": info["name"],
                    "direction_idx": direction_idx,
                    "direction_name": info["direction_names"][direction_idx] if direction_idx < len(info["direction_names"]) else "?",
                    "destination": destination,
                })
    return lookup


def check_case(case, terminus_lookup):
    """Check a single golden dataset case against the terminus lookup.

    Returns a dict with: matched_termini, inferred_lines, inferred_direction, issues
    """
    name = case["facility_name"].lower()
    issues = []
    matched = []

    for keyword, entries in terminus_lookup.items():
        if keyword in name:
            for entry in entries:
                # Only flag if the route is actually one of the station's routes
                if entry["route_id"] in case["routes"]:
                    matched.append((keyword, entry))

    # Deduplicate by (keyword, route_id) — same keyword can appear multiple times
    seen = set()
    unique_matched = []
    for keyword, entry in matched:
        key = (keyword, entry["route_id"], entry["direction_idx"])
        if key not in seen:
            seen.add(key)
            unique_matched.append((keyword, entry))

    inferred_lines = sorted({e["route_id"] for _, e in unique_matched})
    inferred_directions = {e["direction_name"].lower() for _, e in unique_matched}
    inferred_direction = inferred_directions.pop() if len(inferred_directions) == 1 else None

    # Check against expected
    expected_lines = sorted(case["expected_lines"])
    if inferred_lines and inferred_lines != expected_lines:
        issues.append(f"Line mismatch: inferred {inferred_lines}, expected {expected_lines}")

    expected_dir = case["expected_direction"]
    if inferred_direction and expected_dir and inferred_direction != expected_dir.lower():
        issues.append(f"Direction mismatch: inferred '{inferred_direction}', expected '{expected_dir}'")

    return {
        "matched_termini": unique_matched,
        "inferred_lines": inferred_lines,
        "inferred_direction": inferred_direction,
        "issues": issues,
    }


def main():
    print("Fetching route direction data from MBTA API...")
    routes = fetch_route_directions()

    print(f"\nFound {len(routes)} routes. Direction destinations:\n")
    print(f"  {'ROUTE':<30} {'DIR 0':<25} {'DIR 1'}")
    print("  " + "-" * 75)
    for rid, info in sorted(routes.items()):
        d = info["direction_destinations"]
        n = info["direction_names"]
        d0 = f"{n[0]}: {d[0]}" if d else ""
        d1 = f"{n[1]}: {d[1]}" if len(d) > 1 else ""
        print(f"  {rid:<30} {d0:<25} {d1}")

    terminus_lookup = build_terminus_lookup(routes)

    print(f"\n\n{'='*80}")
    print("GOLDEN DATASET VERIFICATION")
    print(f"{'='*80}\n")

    pattern_labels = {
        1: "Explicit line name",
        2: "Terminus reference",
        3: "Green Line directional",
        4: "Cross-platform connector",
        5: "Generic/shared",
    }

    issues_found = 0
    no_match = 0

    for i, case in enumerate(GOLDEN, 1):
        result = check_case(case, terminus_lookup)
        status = "OK"
        if result["issues"]:
            status = "ISSUE"
            issues_found += 1
        elif not result["matched_termini"] and case["pattern"] in (2, 3):
            status = "NO MATCH"
            no_match += 1

        pattern_label = pattern_labels[case["pattern"]]
        print(f"[{i:02d}] {status} | Pattern {case['pattern']} ({pattern_label})")
        print(f"      {case['facility_name']}")
        print(f"      Station: {case['station_name']} | Routes: {', '.join(case['routes'])}")
        print(f"      Expected: lines={case['expected_lines']}, direction={case['expected_direction']}")

        if result["matched_termini"]:
            for keyword, entry in result["matched_termini"]:
                print(f"      API match: '{keyword}' → {entry['route_id']} {entry['direction_name']} toward {entry['destination']}")
        elif case["pattern"] in (2, 3):
            print(f"      API match: (none — no terminus keyword found in facility name)")

        if result["issues"]:
            for issue in result["issues"]:
                print(f"      *** {issue}")

        print()

    print(f"{'='*80}")
    print(f"Summary: {len(GOLDEN)} cases | {issues_found} issues | {no_match} unmatched Pattern 2/3 cases")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
