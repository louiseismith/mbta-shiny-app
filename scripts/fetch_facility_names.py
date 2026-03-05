# fetch_facility_names.py
# One-off script to explore MBTA facility naming patterns for benchmark construction.
# Fetches all facilities from the MBTA API, joins with station_routes from Supabase,
# and prints facilities at multi-line stations — where line interpretation is non-trivial.

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.mbta_api import fetch_facilities
from modules.db import _read_station_routes_from_db

def main():
    print("Fetching facilities from MBTA API...")
    raw = fetch_facilities()

    # Build stop name lookup from included stops
    stops = {}
    for item in raw.get("included", []):
        if item["type"] == "stop":
            stops[item["id"]] = item["attributes"].get("name", "Unknown")

    # Collect elevators and escalators (the ones worth interpreting)
    facilities = []
    for item in raw.get("data", []):
        ftype = item["attributes"].get("type")
        if ftype not in ("ELEVATOR", "ESCALATOR"):
            continue
        stop_id = item.get("relationships", {}).get("stop", {}).get("data", {}).get("id")
        facilities.append({
            "facility_id": item["id"],
            "type": ftype,
            "name": item["attributes"].get("long_name", ""),
            "short_name": item["attributes"].get("short_name", ""),
            "stop_id": stop_id,
            "station_name": stops.get(stop_id, "Unknown"),
        })

    print(f"Found {len(facilities)} elevators/escalators.\n")

    # Load station routes from Supabase
    print("Reading station routes from Supabase...")
    station_routes = _read_station_routes_from_db()

    # Join: attach route IDs to each facility
    for f in facilities:
        routes = station_routes.get(f["stop_id"], [])
        f["route_ids"] = sorted({r["id"] for r in routes if r.get("route_type") in (0, 1, 2)})

    # Group by station, count distinct lines
    from collections import defaultdict
    by_station = defaultdict(list)
    for f in facilities:
        by_station[f["stop_id"]].append(f)

    # Print: multi-line stations first (most interesting for interpretation)
    print(f"{'FACILITY NAME':<55} {'STATION':<30} {'LINES'}")
    print("-" * 110)

    multi = [(sid, fs) for sid, fs in by_station.items() if len({r for f in fs for r in f["route_ids"]}) > 1]
    single = [(sid, fs) for sid, fs in by_station.items() if len({r for f in fs for r in f["route_ids"]}) <= 1]

    print(f"\n=== MULTI-LINE STATIONS ({len(multi)} stations) ===\n")
    for sid, fs in sorted(multi, key=lambda x: x[1][0]["station_name"]):
        for f in sorted(fs, key=lambda x: x["name"]):
            lines = ", ".join(f["route_ids"]) if f["route_ids"] else "(unknown)"
            print(f"{f['name']:<55} {f['station_name']:<30} {lines}")
        print()

    print(f"\n=== SINGLE-LINE STATIONS ({len(single)} stations) ===\n")
    for sid, fs in sorted(single, key=lambda x: x[1][0]["station_name"]):
        for f in sorted(fs, key=lambda x: x["name"]):
            lines = ", ".join(f["route_ids"]) if f["route_ids"] else "(unknown)"
            print(f"{f['name']:<55} {f['station_name']:<30} {lines}")
        print()

if __name__ == "__main__":
    main()
