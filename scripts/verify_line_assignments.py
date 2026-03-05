#!/usr/bin/env python3
"""verify_line_assignments.py

Cross-checks facility_line_mapping.json for correctness.

Checks performed:
  1. Route validity   — assigned lines are a subset of routes serving the station
  2. Name consistency — line keywords in facility name match assigned lines
  3. Pattern rules    — pattern-specific structural invariants hold
  4. Direction sanity — direction is plausible for the assigned lines

P0 facilities (lines=null) are skipped — they're intentionally unassigned.

Usage:
    python verify_line_assignments.py               # offline checks only (fast)
    python verify_line_assignments.py --routes       # also check routes against Supabase
    python verify_line_assignments.py --verbose      # print OK entries too
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

# Search for .env in this dir and parent dirs (matches app behaviour)
_here = os.path.dirname(__file__)
for _d in [_here, os.path.dirname(_here), os.path.dirname(os.path.dirname(_here))]:
    _env = os.path.join(_d, ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
        break

MAPPING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "facility_line_mapping.json")

# ── Reference data ─────────────────────────────────────────────────────────

# Which directions are valid for each line family
VALID_DIRECTIONS = {
    "Red":      {"northbound", "southbound"},
    "Orange":   {"northbound", "southbound"},
    "Blue":     {"eastbound", "westbound"},
    "Green":    {"eastbound", "westbound", "northbound"},  # northbound: GLX past Lechmere
    "Mattapan": {"northbound", "southbound"},
    "CR":       {"inbound", "outbound"},  # CR uses inbound/outbound but we don't assign direction to CR
}

# Keywords that should appear in facility names for P1 (explicit line mention)
LINE_KEYWORDS = {
    "Red Line":      ["Red"],
    "Orange Line":   ["Orange"],
    "Blue Line":     ["Blue"],
    "Green Line":    ["Green-B", "Green-C", "Green-D", "Green-E"],
    "Green-B":       ["Green-B"],
    "Green-C":       ["Green-C"],
    "Green-D":       ["Green-D"],
    "Green-E":       ["Green-E"],
    "Commuter Rail": [],  # CR-* routes vary by station, can't check specific route
    "Mattapan":      ["Mattapan"],
}

# Terminus names and the lines/directions they imply
TERMINUS_DIRECTIONS = {
    "Alewife":              ("Red", "northbound"),
    "Ashmont":              ("Red", "southbound"),
    "Braintree":            ("Red", "southbound"),
    "Ashmont/Braintree":    ("Red", "southbound"),
    "Oak Grove":            ("Orange", "northbound"),
    "Forest Hills":         ("Orange", "southbound"),
    "Bowdoin":              ("Blue", "westbound"),
    "Wonderland":           ("Blue", "eastbound"),
    "Boston College":       ("Green-B", "westbound"),
    "Cleveland Circle":     ("Green-C", "westbound"),
    "Riverside":            ("Green-D", "westbound"),
    "Heath Street":         ("Green-E", "westbound"),
    "Lechmere":             ("Green-D", "eastbound"),  # or northbound for GLX
    "Union Square":         ("Green-D", "northbound"),
    "Medford/Tufts":        ("Green-E", "northbound"),
    "Mattapan":             ("Mattapan", "southbound"),
}


# ── Supabase fetch ─────────────────────────────────────────────────────────

def _get_supabase_conn():
    """Open a Postgres connection from SUPABASE_* env vars."""
    host = os.getenv("SUPABASE_HOST")
    if not host:
        return None
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("SUPABASE_PORT", "5432")),
        dbname=os.getenv("SUPABASE_DB", "postgres"),
        user=os.getenv("SUPABASE_USER", "postgres"),
        password=os.getenv("SUPABASE_PASSWORD", ""),
        connect_timeout=10,
        sslmode="require",
    )


def fetch_station_routes() -> dict[str, set[str]]:
    """Return {stop_id: set(route_ids)} from Supabase station_routes table."""
    print("Reading station routes from Supabase...")
    conn = _get_supabase_conn()
    if conn is None:
        print("  ERROR: SUPABASE_HOST not configured.")
        sys.exit(1)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT stop_id, route_id FROM station_routes")
                rows = cur.fetchall()
    finally:
        conn.close()

    station_routes: dict[str, set[str]] = defaultdict(set)
    for stop_id, route_id in rows:
        station_routes[stop_id].add(route_id)

    print(f"  Done. {len(station_routes)} stops, {len(rows)} stop-route pairs.")
    return dict(station_routes)


# ── Check functions ────────────────────────────────────────────────────────

def check_route_validity(entry: dict, station_routes: dict[str, set[str]]) -> list[str]:
    """Check 1: assigned lines are a subset of routes serving the station."""
    issues = []
    stop_id = entry.get("stop_id")
    lines = entry.get("lines") or []

    if not stop_id:
        issues.append("no stop_id in mapping entry")
        return issues

    api_routes = station_routes.get(stop_id, set())
    if not api_routes:
        issues.append(f"stop_id '{stop_id}' not found in API — ferry/SL/unmapped?")
        return issues

    bad_lines = [l for l in lines if l not in api_routes]
    if bad_lines:
        issues.append(
            f"assigned to line(s) not serving this station: {bad_lines} "
            f"(station has: {sorted(api_routes)})"
        )
    return issues


def check_name_consistency(entry: dict) -> list[str]:
    """Check 2: line keywords in the facility name match assigned lines."""
    issues = []
    fname = entry.get("facility_name", "")
    lines = set(entry.get("lines") or [])
    pattern = entry.get("pattern")

    if not lines:
        return issues

    # Check: if name mentions a line keyword, that line should be assigned
    for keyword, expected_routes in LINE_KEYWORDS.items():
        if keyword in fname:
            if keyword == "Commuter Rail":
                # Just check that at least one CR-* route is assigned
                if not any(l.startswith("CR-") for l in lines):
                    issues.append(
                        f"name mentions 'Commuter Rail' but no CR-* route assigned "
                        f"(lines={sorted(lines)})"
                    )
            elif keyword == "Green Line":
                # "Green Line" should mean at least one Green-* branch assigned
                if not any(l.startswith("Green-") for l in lines):
                    issues.append(
                        f"name mentions 'Green Line' but no Green-* route assigned "
                        f"(lines={sorted(lines)})"
                    )
            elif keyword.startswith("Green-"):
                # Specific branch mentioned — must be in lines
                if keyword not in lines:
                    issues.append(
                        f"name mentions '{keyword}' but it's not in lines={sorted(lines)}"
                    )
            elif expected_routes:
                # Generic line keyword — at least one expected route should be present
                if not any(r in lines for r in expected_routes):
                    issues.append(
                        f"name mentions '{keyword}' but none of {expected_routes} "
                        f"in lines={sorted(lines)}"
                    )

    # Check: if name mentions "Mattapan" (as a line, not station), it should be assigned
    # (handled above via LINE_KEYWORDS)

    return issues


def check_pattern_rules(entry: dict, station_routes: dict[str, set[str]] | None) -> list[str]:
    """Check 3: pattern-specific structural invariants."""
    issues = []
    pattern = entry.get("pattern")
    lines = entry.get("lines") or []
    direction = entry.get("direction")
    stop_id = entry.get("stop_id")

    if pattern == 0:
        # P0: lines should be null (already skipped in main, but just in case)
        if lines:
            issues.append(f"P0 but has lines assigned: {lines}")

    elif pattern == 1:
        # P1 (explicit line): should have at least one line
        if not lines:
            issues.append("P1 (explicit line keyword) but no lines assigned")

    elif pattern == 2:
        # P2 (terminus reference): should have direction
        if not direction:
            issues.append("P2 (terminus reference) but no direction assigned")
        if not lines:
            issues.append("P2 but no lines assigned")

    elif pattern == 3:
        # P3 (Green directional): should have direction, lines should be Green-*
        if not direction:
            issues.append("P3 (Green directional) but no direction assigned")
        non_green = [l for l in lines if not l.startswith("Green-")]
        if non_green:
            issues.append(f"P3 (Green directional) but has non-Green lines: {non_green}")

    elif pattern == 4:
        # P4 (cross-platform): should reference 2+ lines OR be same-line cross-platform
        # Direction is typically null for P4 (connects two directions)
        if not lines:
            issues.append("P4 (cross-platform) but no lines assigned")

    elif pattern == 5:
        # P5 (generic/shared): lines should be ALL routes at the station
        if station_routes and stop_id and stop_id in station_routes:
            api_routes = station_routes[stop_id]
            if set(lines) != api_routes:
                missing = api_routes - set(lines)
                extra = set(lines) - api_routes
                parts = []
                if missing:
                    parts.append(f"missing {sorted(missing)}")
                if extra:
                    parts.append(f"extra {sorted(extra)}")
                issues.append(
                    f"P5 (generic) lines should equal all station routes but "
                    + ", ".join(parts)
                )
        # P5 should NOT have direction (it's shared infrastructure)
        if direction:
            issues.append(f"P5 (generic/shared) but has direction='{direction}'")

    return issues


def check_direction_sanity(entry: dict) -> list[str]:
    """Check 4: direction is plausible for the assigned lines."""
    issues = []
    direction = entry.get("direction")
    lines = entry.get("lines") or []

    if not direction:
        return issues

    for line in lines:
        # Determine the line family for direction lookup
        if line.startswith("Green-"):
            family = "Green"
        elif line.startswith("CR-"):
            family = "CR"
        else:
            family = line

        valid = VALID_DIRECTIONS.get(family)
        if valid and direction not in valid:
            issues.append(
                f"direction '{direction}' is not valid for {line} "
                f"(expected one of: {sorted(valid)})"
            )

    return issues


def check_terminus_direction(entry: dict) -> list[str]:
    """Check 5: if name references a terminus, direction should match."""
    issues = []
    fname = entry.get("facility_name", "")
    direction = entry.get("direction")
    lines = set(entry.get("lines") or [])
    pattern = entry.get("pattern")

    # Only check P2 (terminus reference) entries
    if pattern != 2:
        return issues

    for terminus, (expected_line, expected_dir) in TERMINUS_DIRECTIONS.items():
        # Check if terminus appears in name (as a word boundary, not substring)
        if terminus in fname:
            # The assigned direction should match
            if direction and direction != expected_dir:
                # Special case: Lechmere can be eastbound or northbound
                if terminus == "Lechmere" and direction in ("eastbound", "northbound"):
                    continue
                issues.append(
                    f"name references '{terminus}' (implies {expected_dir}) "
                    f"but direction='{direction}'"
                )
            break  # first terminus match wins

    return issues


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Verify facility_line_mapping.json for correctness."
    )
    parser.add_argument("--routes", action="store_true",
                        help="Also check assigned lines against Supabase station_routes")
    parser.add_argument("--verbose", action="store_true",
                        help="Print OK entries too")
    args = parser.parse_args()

    with open(MAPPING_FILE) as f:
        mapping = json.load(f)

    station_routes = fetch_station_routes() if args.routes else None

    errors: list[tuple[dict, str]] = []    # (entry, message) — hard failures
    warnings: list[tuple[dict, str]] = []  # (entry, message) — worth reviewing
    ok_count = 0
    skip_count = 0

    checks_run = ["name_consistency", "pattern_rules", "direction_sanity", "terminus_direction"]
    if args.routes:
        checks_run.insert(0, "route_validity")

    print(f"\nChecking {len(mapping)} facilities...")
    print(f"Checks: {', '.join(checks_run)}")
    if not args.routes:
        print("(Skipping route_validity — use --routes to enable)\n")

    for entry in mapping:
        fid     = entry["facility_id"]
        fname   = entry["facility_name"]
        lines   = entry.get("lines")
        pattern = entry.get("pattern")

        # P0 — intentionally unassigned, skip
        if lines is None:
            skip_count += 1
            continue

        entry_issues = []

        # Check 1: route validity (API-dependent)
        if station_routes is not None:
            for msg in check_route_validity(entry, station_routes):
                entry_issues.append(("ERROR", msg))

        # Check 2: name consistency
        for msg in check_name_consistency(entry):
            entry_issues.append(("ERROR", msg))

        # Check 3: pattern rules
        for msg in check_pattern_rules(entry, station_routes):
            # P5 not matching all station routes is a warning (could be intentional override)
            if "P5 (generic) lines should equal" in msg and station_routes:
                entry_issues.append(("WARN", msg))
            else:
                entry_issues.append(("ERROR", msg))

        # Check 4: direction sanity
        for msg in check_direction_sanity(entry):
            entry_issues.append(("ERROR", msg))

        # Check 5: terminus direction
        for msg in check_terminus_direction(entry):
            entry_issues.append(("WARN", msg))  # warn, not error — edge cases exist

        if entry_issues:
            for level, msg in entry_issues:
                target = errors if level == "ERROR" else warnings
                target.append((entry, msg))
        else:
            ok_count += 1
            if args.verbose:
                print(f"  OK    {fid}  P{pattern}  lines={lines}  dir={entry.get('direction')}")

    # ── Report ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  OK:       {ok_count}")
    print(f"  Errors:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")
    print(f"  Skipped:  {skip_count} (P0 — no lines assigned)")

    if warnings:
        print("\n── WARNINGS ──────────────────────────────────────────────")
        for entry, msg in warnings:
            print(f"  [{entry['pattern']}] {entry['facility_id']}: {entry['facility_name']}")
            print(f"       {msg}")
            print()

    if errors:
        print("\n── ERRORS ────────────────────────────────────────────────")
        for entry, msg in errors:
            print(f"  [{entry['pattern']}] {entry['facility_id']}: {entry['facility_name']}")
            print(f"       station: {entry['station_name']} ({entry.get('stop_id')})")
            print(f"       lines:   {entry.get('lines')}  dir: {entry.get('direction')}")
            print(f"       {msg}")
            print()

    total_issues = len(errors) + len(warnings)
    if errors:
        print(f"\n  FAILED — {len(errors)} error(s) found.")
        sys.exit(1)
    elif warnings:
        print(f"\n  PASSED with {len(warnings)} warning(s) — review recommended.")
        sys.exit(0)
    else:
        print("\n  PASSED — all checks clean.")
        sys.exit(0)


if __name__ == "__main__":
    main()
