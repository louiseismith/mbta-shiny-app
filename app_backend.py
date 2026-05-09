"""app_backend.py

Orchestration layer for the MBTA Accessibility Tracker Shiny app.
Imports from sub-modules and exposes get_data_for_app() to R via reticulate.

Sub-modules (in modules/):
    db.py                — Supabase read functions
    mbta_api.py          — MBTA V3 API fetch functions
    ai_report.py         — Ollama AI report generation
    facility_classifier.py — Rule-based facility name pattern classifier
"""

import requests
from dotenv import load_dotenv

from modules.db import (
    _get_supabase_conn,
    _log_facility_statuses,
    _read_facility_line_mapping_from_db,
    _read_route_shapes_from_db,
    _read_station_routes_from_db,
    fetch_outage_history,
)
from modules.mbta_api import (
    BASE_URL,
    _build_service_alerts_by_stop,
    extract_facility_ids_from_alert,
    fetch_accessibility_alerts,
    fetch_connecting_routes,
    fetch_facilities,
    fetch_route_alerts,
    fetch_route_shapes,
    headers,
)
from modules.ai_report import (
    _build_station_prompt,
    _format_duration,
    _query_ollama,
    generate_station_report,
)
from modules.facility_classifier import classify_pattern

load_dotenv()


def get_data_for_app():
    """
    Return facilities, stations, and cached reference data for the Shiny app.
    Stations include id, name, lat, lon, and outage counts for map styling.
    """
    facilities_data = fetch_facilities()
    alerts_data = fetch_accessibility_alerts()

    # Build lookup of included stops (name + coordinates + wheelchair_boarding for map)
    stops_lookup = {}
    for included in facilities_data.get("included", []):
        if included["type"] == "stop":
            attrs = included.get("attributes", {})
            stops_lookup[included["id"]] = {
                "name": attrs.get("name", "Unknown"),
                "latitude": attrs.get("latitude"),
                "longitude": attrs.get("longitude"),
                "wheelchair_boarding": attrs.get("wheelchair_boarding", 0),
            }

    # Build facility inventory
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
        active_periods = alert_attrs.get("active_period", [])
        outage_start = active_periods[0].get("start") if active_periods else None
        outage_end = active_periods[0].get("end") if active_periods else None

        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
            "outage_start": outage_start,
            "outage_end": outage_end,
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
            "wheelchair_boarding": info.get("wheelchair_boarding", 0),
        })

    try:
        _log_facility_statuses(facilities)
    except Exception:
        pass  # Never let logging break the app

    station_routes = {}
    try:
        station_routes = _read_station_routes_from_db()
    except Exception:
        pass  # Missing station_routes degrades trip checker but doesn't break the app

    route_shapes = {}
    try:
        route_shapes = _read_route_shapes_from_db()
    except Exception:
        pass  # Missing route_shapes degrades to live API fetch on trip check; doesn't break the app

    facility_line_mapping = {}
    try:
        facility_line_mapping = _read_facility_line_mapping_from_db()
    except Exception:
        pass  # Missing mapping degrades to no per-line badges; doesn't break the app

    # Prefetch all service alerts in one call using the cached route list
    service_alerts = {}
    if station_routes:
        try:
            all_route_ids = list({r["id"] for routes in station_routes.values() for r in routes})
            resp = requests.get(
                f"{BASE_URL}/alerts",
                headers=headers,
                params={"filter[route]": ",".join(all_route_ids)},
            )
            resp.raise_for_status()
            service_alerts = _build_service_alerts_by_stop(
                resp.json().get("data", []), station_routes
            )
        except Exception:
            pass  # Degrades to no service alerts in AI report; doesn't break the app

    return {
        "facilities": facilities,
        "stations": stations,
        "station_routes": station_routes,
        "route_shapes": route_shapes,
        "service_alerts": service_alerts,
        "facility_line_mapping": facility_line_mapping,
    }
