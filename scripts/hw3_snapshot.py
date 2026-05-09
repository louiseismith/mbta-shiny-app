"""hw3_snapshot.py

One-time script: fetch live MBTA data and save 6 fixture stations to
data/hw3_fixtures.json. Run once from the homework/shiny_app/ directory:

    python scripts/hw3_snapshot.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict

from dotenv import load_dotenv

from modules.mbta_api import (
    extract_facility_ids_from_alert,
    fetch_accessibility_alerts,
    fetch_facilities,
    fetch_route_alerts,
)

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_PATH = os.path.join(DATA_DIR, "hw3_fixtures.json")


def build_facilities_and_stops():
    print("Fetching facilities...")
    facilities_data = fetch_facilities()
    print("Fetching accessibility alerts...")
    alerts_data = fetch_accessibility_alerts()

    stops_lookup = {}
    for included in facilities_data.get("included", []):
        if included["type"] == "stop":
            attrs = included.get("attributes", {})
            stops_lookup[included["id"]] = {
                "name": attrs.get("name", "Unknown"),
                "wheelchair_boarding": attrs.get("wheelchair_boarding", 0),
            }

    facilities = {}
    for item in facilities_data.get("data", []):
        fid = item["id"]
        attrs = item["attributes"]
        stop_id = item.get("relationships", {}).get("stop", {}).get("data", {}).get("id")
        facilities[fid] = {
            "id": fid,
            "type": attrs.get("type"),
            "name": attrs.get("long_name"),
            "short_name": attrs.get("short_name"),
            "stop_id": stop_id,
            "station_name": stops_lookup.get(stop_id, {}).get("name", "Unknown"),
            "status": "operational",
            "alert": None,
        }

    for alert in alerts_data.get("data", []):
        alert_attrs = alert["attributes"]
        fids = extract_facility_ids_from_alert(alert)
        active_periods = alert_attrs.get("active_period", [])
        outage_start = active_periods[0].get("start") if active_periods else None
        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "outage_start": outage_start,
        }
        for fid in fids:
            if fid in facilities:
                facilities[fid]["status"] = "out_of_service"
                facilities[fid]["alert"] = alert_summary

    print(f"  {len(facilities)} facilities across {len(stops_lookup)} stops")
    return facilities, stops_lookup


def categorize_stations(facilities, stops_lookup):
    station_facilities = defaultdict(list)
    for f in facilities.values():
        if f["stop_id"]:
            station_facilities[f["stop_id"]].append(f)

    categories = defaultdict(list)
    for stop_id, facs in station_facilities.items():
        wb = stops_lookup.get(stop_id, {}).get("wheelchair_boarding", 0)
        if wb == 2:
            categories["fully_inaccessible"].append(stop_id)
            continue
        out_count = sum(1 for f in facs if f["status"] == "out_of_service")
        if out_count == 0:
            categories["clean_or_alert"].append(stop_id)
        elif out_count == 1:
            categories["single_outage"].append(stop_id)
        else:
            categories["multiple_outages"].append(stop_id)

    return dict(categories), station_facilities


def fetch_alerts_for_candidates(categories):
    candidates = set()
    for stop_ids in categories.values():
        candidates.update(stop_ids[:15])

    print(f"Fetching service alerts for {len(candidates)} candidate stations...")
    service_alerts_map = {}
    for i, sid in enumerate(candidates, 1):
        try:
            service_alerts_map[sid] = fetch_route_alerts(sid)
        except Exception:
            service_alerts_map[sid] = []
        if i % 10 == 0:
            print(f"  {i}/{len(candidates)} done")
    return service_alerts_map


def select_fixtures(categories, service_alerts_map, station_facilities, stops_lookup):
    selected = {}

    # Fully inaccessible
    for sid in categories.get("fully_inaccessible", []):
        selected["fully_inaccessible"] = sid
        break

    # Multiple outages + service alert (combined)
    for sid in categories.get("multiple_outages", []):
        if service_alerts_map.get(sid):
            selected["combined"] = sid
            break

    # Multiple outages only
    for sid in categories.get("multiple_outages", []):
        if sid != selected.get("combined"):
            selected["multiple_outages"] = sid
            break

    # Single outage only (no service alert)
    for sid in categories.get("single_outage", []):
        if not service_alerts_map.get(sid):
            selected["single_outage"] = sid
            break

    # Service alert only (no outages)
    for sid in categories.get("clean_or_alert", []):
        if service_alerts_map.get(sid):
            selected["service_alert_only"] = sid
            break

    # Clean (no outages, no alerts)
    for sid in categories.get("clean_or_alert", []):
        if not service_alerts_map.get(sid) and sid != selected.get("service_alert_only"):
            selected["clean"] = sid
            break

    # Fallback: combined from single_outage if needed
    if "combined" not in selected:
        for sid in categories.get("single_outage", []):
            if service_alerts_map.get(sid):
                selected["combined"] = sid
                break

    fixtures = []
    for case, stop_id in selected.items():
        name = stops_lookup.get(stop_id, {}).get("name", stop_id)
        wb = stops_lookup.get(stop_id, {}).get("wheelchair_boarding", 0)
        facs = station_facilities.get(stop_id, [])
        alerts = service_alerts_map.get(stop_id, [])
        out = sum(1 for f in facs if f["status"] == "out_of_service")
        print(f"  [{case}] {name} ({stop_id}) — {out} outage(s), {len(alerts)} service alert(s)")
        fixtures.append({
            "case": case,
            "station_id": stop_id,
            "station_name": name,
            "wheelchair_boarding": wb,
            "facilities": facs,
            "service_alerts": alerts,
        })
    return fixtures


def main():
    facilities, stops_lookup = build_facilities_and_stops()
    categories, station_facilities = categorize_stations(facilities, stops_lookup)

    print(f"\nStation pool: {sum(len(v) for v in categories.values())} total")
    for case, ids in categories.items():
        print(f"  {case}: {len(ids)}")

    service_alerts_map = fetch_alerts_for_candidates(categories)

    print("\nSelected fixtures:")
    fixtures = select_fixtures(categories, service_alerts_map, station_facilities, stops_lookup)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(fixtures, f, indent=2)
    print(f"\nSaved {len(fixtures)} fixtures → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
