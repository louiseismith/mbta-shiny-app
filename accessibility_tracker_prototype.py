import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BASE_URL = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")

headers = {}
if API_KEY:
    headers["x-api-key"] = API_KEY


def fetch_facilities():
    """Fetch all elevators and escalators from MBTA."""
    response = requests.get(
        f"{BASE_URL}/facilities",
        headers=headers,
        params={
            "filter[type]": "ELEVATOR,ESCALATOR",
            "include": "stop"  # Include stop data for station names
        }
    )
    response.raise_for_status()
    return response.json()


def fetch_accessibility_alerts():
    """Fetch current alerts affecting wheelchair users (elevator/escalator outages)."""
    response = requests.get(
        f"{BASE_URL}/alerts",
        headers=headers,
        params={
            "filter[activity]": "USING_WHEELCHAIR"
        }
    )
    response.raise_for_status()
    return response.json()


def extract_facility_ids_from_alert(alert):
    """Extract facility IDs from an alert's informed_entity list."""
    facility_ids = []
    informed_entities = alert.get("attributes", {}).get("informed_entity", [])
    for entity in informed_entities:
        if "facility" in entity:
            facility_ids.append(entity["facility"])
    return facility_ids


def build_accessibility_status():
    """
    Combine facilities and alerts to build a status dashboard.
    Returns a dict of facilities with their current status.
    """
    # Fetch data
    facilities_data = fetch_facilities()
    alerts_data = fetch_accessibility_alerts()

    # Build lookup of included stops (for station names)
    stops_lookup = {}
    for included in facilities_data.get("included", []):
        if included["type"] == "stop":
            stops_lookup[included["id"]] = included["attributes"].get("name", "Unknown")

    # Build facility inventory
    facilities = {}
    for item in facilities_data.get("data", []):
        facility_id = item["id"]
        attrs = item["attributes"]
        stop_id = item.get("relationships", {}).get("stop", {}).get("data", {}).get("id")

        facilities[facility_id] = {
            "id": facility_id,
            "type": attrs.get("type"),  # ELEVATOR or ESCALATOR
            "name": attrs.get("long_name"),
            "short_name": attrs.get("short_name"),
            "stop_id": stop_id,
            "station_name": stops_lookup.get(stop_id, "Unknown"),
            "status": "operational",  # Default, will update if alert exists
            "alert": None
        }

    # Mark facilities with active alerts
    for alert in alerts_data.get("data", []):
        alert_attrs = alert["attributes"]
        affected_facility_ids = extract_facility_ids_from_alert(alert)

        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "severity": alert_attrs.get("severity"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
            "active_period": alert_attrs.get("active_period", [])
        }

        for facility_id in affected_facility_ids:
            if facility_id in facilities:
                facilities[facility_id]["status"] = "out_of_service"
                facilities[facility_id]["alert"] = alert_summary

    return facilities


def get_data_for_app():
    """
    Return facilities and stations for the Shiny app (map + station details).
    Stations include id, name, lat, lon, and outage counts for map styling.
    """
    facilities_data = fetch_facilities()
    alerts_data = fetch_accessibility_alerts()

    # Build lookup of included stops (name + coordinates for map)
    stops_lookup = {}
    for included in facilities_data.get("included", []):
        if included["type"] == "stop":
            attrs = included.get("attributes", {})
            stops_lookup[included["id"]] = {
                "name": attrs.get("name", "Unknown"),
                "latitude": attrs.get("latitude"),
                "longitude": attrs.get("longitude"),
            }

    # Build facility inventory (same logic as build_accessibility_status)
    facilities = {}
    for item in facilities_data.get("data", []):
        facility_id = item["id"]
        attrs = item["attributes"]
        stop_id = item.get("relationships", {}).get("stop", {}).get("data", {}).get("id")

        facilities[facility_id] = {
            "id": facility_id,
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
        affected_facility_ids = extract_facility_ids_from_alert(alert)
        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "severity": alert_attrs.get("severity"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
            "active_period": alert_attrs.get("active_period", []),
        }
        for facility_id in affected_facility_ids:
            if facility_id in facilities:
                facilities[facility_id]["status"] = "out_of_service"
                facilities[facility_id]["alert"] = alert_summary

    # Build stations list with coordinates and counts (for map markers)
    station_counts = {}
    for f in facilities.values():
        sid = f["stop_id"]
        if sid not in station_counts:
            station_counts[sid] = {"operational": 0, "out_of_service": 0}
        if f["status"] == "operational":
            station_counts[sid]["operational"] += 1
        else:
            station_counts[sid]["out_of_service"] += 1

    stations = []
    for stop_id, info in stops_lookup.items():
        lat, lon = info.get("latitude"), info.get("longitude")
        if lat is None or lon is None:
            continue
        counts = station_counts.get(stop_id, {"operational": 0, "out_of_service": 0})
        stations.append({
            "id": stop_id,
            "name": info["name"],
            "lat": lat,
            "lon": lon,
            "n_operational": counts["operational"],
            "n_out_of_service": counts["out_of_service"],
        })

    return {"facilities": facilities, "stations": stations}


def summarize_status(facilities):
    """Print a summary of accessibility status."""
    total = len(facilities)
    operational = sum(1 for f in facilities.values() if f["status"] == "operational")
    out_of_service = total - operational

    print("=" * 60)
    print("MBTA ACCESSIBILITY STATUS")
    print("=" * 60)
    print(f"Total facilities: {total}")
    print(f"Operational: {operational}")
    print(f"Out of service: {out_of_service}")
    print("=" * 60)

    # Group outages by station
    outages_by_station = {}
    for f in facilities.values():
        if f["status"] == "out_of_service":
            station = f["station_name"]
            if station not in outages_by_station:
                outages_by_station[station] = []
            outages_by_station[station].append(f)

    if outages_by_station:
        print("\nCURRENT OUTAGES:")
        print("-" * 60)
        for station, outages in sorted(outages_by_station.items()):
            print(f"\n{station}:")
            for f in outages:
                severity = f["alert"]["severity"] if f["alert"] else "?"
                print(f"  [{severity}] {f['type']}: {f['short_name']}")
                if f["alert"]:
                    print(f"      Cause: {f['alert']['cause']}")
    else:
        print("\nNo current outages!")

    return outages_by_station


if __name__ == "__main__":
    print("Fetching MBTA accessibility data...\n")
    facilities = build_accessibility_status()
    outages = summarize_status(facilities)

    # The facilities dict can be used for:
    # - Shiny dashboard display
    # - JSON API response
    # - Database storage for historical tracking
