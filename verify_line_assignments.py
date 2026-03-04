#!/usr/bin/env python3
"""verify_line_assignments.py

Cross-checks facility_line_mapping.json against the live MBTA API.

For every facility, verifies that its assigned lines[] are a subset of
the routes actually serving that station. Flags any facility assigned to
a line that doesn't serve its stop_id.

P0 facilities (lines=null) are skipped — they're intentionally unassigned.

Usage:
    python verify_line_assignments.py
    python verify_line_assignments.py --verbose   # also print OK entries
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv

# Search for .env in this dir and parent dirs (matches app behaviour)
for _d in [os.path.dirname(__file__), os.path.dirname(os.path.dirname(__file__))]:
    _env = os.path.join(_d, ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
        break

BASE_URL = "https://api-v3.mbta.com"
API_KEY  = os.getenv("MBTA_API_KEY")
headers  = {"x-api-key": API_KEY} if API_KEY else {}

MAPPING_FILE = os.path.join(os.path.dirname(__file__), "facility_line_mapping.json")


def fetch_station_routes() -> dict[str, set[str]]:
    """Return {stop_id: set(route_ids)} for all rapid-transit + CR routes."""
    print("Fetching all routes from MBTA API (types 0,1,2)...")
    resp = requests.get(
        f"{BASE_URL}/routes",
        headers=headers,
        params={"filter[type]": "0,1,2"},
        timeout=30,
    )
    resp.raise_for_status()
    routes = resp.json().get("data", [])
    print(f"  {len(routes)} routes found. Fetching stops per route...")

    station_routes: dict[str, set[str]] = defaultdict(set)
    for i, route in enumerate(routes):
        route_id = route["id"]
        stops_resp = requests.get(
            f"{BASE_URL}/stops",
            headers=headers,
            params={"filter[route]": route_id},
            timeout=30,
        )
        stops_resp.raise_for_status()
        for stop in stops_resp.json().get("data", []):
            station_routes[stop["id"]].add(route_id)
        if (i + 1) % 10 == 0:
            print(f"  ... {i+1}/{len(routes)} routes fetched")

    print(f"  Done. {len(station_routes)} stops mapped.")
    return dict(station_routes)


def normalize_line(line: str) -> str:
    """Normalize a line ID so CR-* matches any CR route at that stop."""
    return line  # kept as-is; comparison is exact match against API route IDs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Print OK entries too")
    args = parser.parse_args()

    with open(MAPPING_FILE) as f:
        mapping = json.load(f)

    station_routes = fetch_station_routes()

    errors   = []
    warnings = []
    ok_count = 0
    skip_count = 0

    print(f"\nChecking {len(mapping)} facilities...")

    for entry in mapping:
        fid      = entry["facility_id"]
        fname    = entry["facility_name"]
        stop_id  = entry.get("stop_id")
        lines    = entry.get("lines")
        pattern  = entry.get("pattern")

        # P0 — intentionally unassigned, skip
        if lines is None:
            skip_count += 1
            continue

        if not stop_id:
            warnings.append(f"  WARN  {fid}: no stop_id in mapping entry")
            continue

        api_routes = station_routes.get(stop_id, set())

        if not api_routes:
            warnings.append(
                f"  WARN  {fid} [{entry['station_name']}]: "
                f"stop_id '{stop_id}' not found in API — ferry/SL station?"
            )
            continue

        bad_lines = [l for l in lines if l not in api_routes]

        if bad_lines:
            errors.append({
                "facility_id":   fid,
                "facility_name": fname,
                "station_name":  entry["station_name"],
                "stop_id":       stop_id,
                "pattern":       pattern,
                "assigned":      lines,
                "api_routes":    sorted(api_routes),
                "bad_lines":     bad_lines,
            })
        else:
            ok_count += 1
            if args.verbose:
                print(f"  OK    {fid}  lines={lines}")

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  OK:      {ok_count}")
    print(f"  Errors:  {len(errors)}")
    print(f"  Warnings:{len(warnings)}")
    print(f"  Skipped: {skip_count} (P0 — no lines assigned)")

    if warnings:
        print("\n── WARNINGS ──────────────────────────────────────────────")
        for w in warnings:
            print(w)

    if errors:
        print("\n── ERRORS ────────────────────────────────────────────────")
        print("  Facilities assigned to a line not serving their stop:\n")
        for e in errors:
            print(f"  [{e['pattern']}] {e['facility_id']}: {e['facility_name']}")
            print(f"       station:    {e['station_name']} ({e['stop_id']})")
            print(f"       assigned:   {e['assigned']}")
            print(f"       bad lines:  {e['bad_lines']}")
            print(f"       api routes: {e['api_routes']}")
            print()
        sys.exit(1)
    else:
        print("\n  All assigned lines are valid routes at their stations.")
        sys.exit(0)


if __name__ == "__main__":
    main()
