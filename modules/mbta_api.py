"""mbta_api.py

MBTA V3 API fetch functions for the accessibility tracker.
All functions are stateless — they take parameters and return data.
"""

import os

import polyline
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")
headers = {}
if API_KEY:
    headers["x-api-key"] = API_KEY


def fetch_facilities():
    """Fetch elevators, escalators, ramps, and portable lifts from MBTA."""
    response = requests.get(
        f"{BASE_URL}/facilities",
        headers=headers,
        params={
            "filter[type]": "ELEVATOR,ESCALATOR,RAMP,PORTABLE_BOARDING_LIFT",
            "include": "stop"  # Include stop data for station names
        }
    )
    response.raise_for_status()
    return response.json()


def fetch_accessibility_alerts():
    """Fetch current alerts affecting elevator/escalator users."""
    response = requests.get(
        f"{BASE_URL}/alerts",
        headers=headers,
        params={
            "filter[activity]": "USING_WHEELCHAIR,USING_ESCALATOR"
        }
    )
    response.raise_for_status()
    return response.json()


def fetch_route_alerts(stop_id, route_ids=None):
    """Fetch all non-facility alerts for the routes serving a station.

    Returns a list of alert dicts (header, effect, description) for
    service-level alerts (shuttles, suspensions, etc.) that affect routes
    passing through the given stop.  Facility-specific alerts (elevator/
    escalator closures) are excluded since they are already tracked separately.

    If route_ids is provided, skips the /routes API call (use when the
    station_routes cache is available).
    """
    if route_ids is None:
        # Discover which routes serve this stop via API
        routes_resp = requests.get(
            f"{BASE_URL}/routes",
            headers=headers,
            params={"filter[stop]": stop_id},
        )
        routes_resp.raise_for_status()
        route_ids = [r["id"] for r in routes_resp.json().get("data", [])]
    if not route_ids:
        return []

    # Fetch alerts for those routes
    alerts_resp = requests.get(
        f"{BASE_URL}/alerts",
        headers=headers,
        params={"filter[route]": ",".join(route_ids)},
    )
    alerts_resp.raise_for_status()

    keep_effects = {
        "SHUTTLE", "SUSPENSION", "DETOUR", "SERVICE_CHANGE",
        "STOP_CLOSURE", "STOP_MOVE", "STATION_ISSUE",
    }
    service_alerts = []
    for alert in alerts_resp.json().get("data", []):
        attr = alert["attributes"]
        effect = attr.get("effect", "")
        if effect not in keep_effects:
            continue
        header = attr.get("header", "")
        if effect == "STATION_ISSUE":
            entities = attr.get("informed_entity", [])
            stop_ids = {e.get("stop") for e in entities if "stop" in e}
            if stop_id not in stop_ids:
                continue
        service_alerts.append({
            "header": header,
            "effect": effect,
            "description": (attr.get("description") or "").strip(),
        })
    return service_alerts


def fetch_connecting_routes(stop_a, stop_b, route_types=(0, 1, 2), station_routes=None):
    """Find rapid-transit and commuter-rail routes serving both stop_a and stop_b.

    If station_routes dict is provided (pre-loaded from Supabase), uses local set
    intersection — no API calls.  Falls back to live API calls if not provided,
    which is the path used by LLM function-calling tools in Module 08.

    Returns a list of route dicts (id, name, color, text_color, route_type) sorted
    by route_type then id.  Buses (type 3) and ferries (type 4) are excluded by
    default so trip results stay focused on scheduled rail service.
    """
    if station_routes is not None:
        def filter_routes(stop_id):
            return {
                r["id"]: r for r in station_routes.get(stop_id, [])
                if r.get("route_type") in route_types
            }
        routes_a = filter_routes(stop_a)
        routes_b = filter_routes(stop_b)
        shared_ids = set(routes_a.keys()) & set(routes_b.keys())
        result = [routes_a[rid] for rid in shared_ids]
        result.sort(key=lambda r: (r.get("route_type", 0), r["id"]))
        return result

    # Fall back to live API calls (used by LLM function-calling tools in Module 08)
    def routes_for_stop(stop_id):
        resp = requests.get(
            f"{BASE_URL}/routes",
            headers=headers,
            params={"filter[stop]": stop_id, "filter[type]": ",".join(str(t) for t in route_types)},
        )
        resp.raise_for_status()
        return {r["id"]: r for r in resp.json().get("data", [])}

    routes_a = routes_for_stop(stop_a)
    routes_b = routes_for_stop(stop_b)

    shared_ids = set(routes_a.keys()) & set(routes_b.keys())
    result = []
    for rid in shared_ids:
        r = routes_a[rid]
        attrs = r["attributes"]
        result.append({
            "id": rid,
            "name": attrs.get("long_name") or attrs.get("short_name"),
            "color": attrs.get("color"),
            "text_color": attrs.get("text_color"),
            "route_type": attrs.get("type"),
        })
    result.sort(key=lambda r: (r["route_type"], r["id"]))
    return result


def fetch_route_shapes(route_ids):
    """Fetch and decode shape geometry for given route IDs.

    Returns dict: {route_id: [[(lat, lon), ...], ...]}
    Each route maps to a list of polylines (one per shape, e.g. branches/directions).
    Only canonical revenue shapes (IDs starting with "canonical-") are included.
    Returns empty dict on error or if route_ids is empty.
    """
    if not route_ids:
        return {}
    result = {}
    for rid in route_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/shapes",
                headers=headers,
                params={"filter[route]": rid},
            )
            resp.raise_for_status()
            shapes = []
            for shape in resp.json().get("data", []):
                priority = shape["attributes"].get("priority")
                if isinstance(priority, int) and priority < 0:
                    continue
                shape_id = shape.get("id", "")
                if not shape_id.startswith("canonical-"):
                    continue
                encoded = shape["attributes"].get("polyline", "")
                if encoded:
                    shapes.append(polyline.decode(encoded))
            result[rid] = shapes
        except Exception:
            result[rid] = []
    return result


def extract_facility_ids_from_alert(alert):
    """Extract facility IDs from an alert's informed_entity list."""
    facility_ids = []
    informed_entities = alert.get("attributes", {}).get("informed_entity", [])
    for entity in informed_entities:
        if "facility" in entity:
            facility_ids.append(entity["facility"])
    return facility_ids


def _build_service_alerts_by_stop(alerts_data, station_routes):
    """Map prefetched service-level alerts to the stops they affect.

    Returns {stop_id: [alert_dicts]} built from a single bulk alerts response.
    For STATION_ISSUE alerts: only mapped to stops explicitly in informed_entity.
    For all other kept effects: mapped to every stop that serves an affected route.
    """
    keep_effects = {
        "SHUTTLE", "SUSPENSION", "DETOUR", "SERVICE_CHANGE",
        "STOP_CLOSURE", "STOP_MOVE", "STATION_ISSUE",
    }
    route_to_stops = {}
    for stop_id, routes in station_routes.items():
        for r in routes:
            rid = r["id"]
            if rid not in route_to_stops:
                route_to_stops[rid] = set()
            route_to_stops[rid].add(stop_id)

    result = {}
    for alert in alerts_data:
        attr = alert["attributes"]
        effect = attr.get("effect", "")
        if effect not in keep_effects:
            continue
        entities = attr.get("informed_entity", [])
        alert_dict = {
            "header": attr.get("header", ""),
            "effect": effect,
            "description": (attr.get("description") or "").strip(),
        }
        if effect == "STATION_ISSUE":
            affected_stops = {e["stop"] for e in entities if "stop" in e}
        else:
            affected_routes = {e["route"] for e in entities if "route" in e}
            affected_stops = set()
            for rid in affected_routes:
                affected_stops.update(route_to_stops.get(rid, set()))
        for sid in affected_stops:
            result.setdefault(sid, []).append(alert_dict)
    return result
