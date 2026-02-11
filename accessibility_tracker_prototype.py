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


def fetch_route_alerts(stop_id):
    """Fetch all non-facility alerts for the routes serving a station.

    Returns a list of alert dicts (header, effect, description, active_period)
    for service-level alerts (shuttles, delays, suspensions, etc.) that affect
    routes passing through the given stop.  Facility-specific alerts (elevator/
    escalator closures) are excluded since they are already tracked separately.
    """
    # 1. Discover which routes serve this stop
    routes_resp = requests.get(
        f"{BASE_URL}/routes",
        headers=headers,
        params={"filter[stop]": stop_id},
    )
    routes_resp.raise_for_status()
    route_ids = [r["id"] for r in routes_resp.json().get("data", [])]
    if not route_ids:
        return []

    # 2. Fetch alerts for those routes
    alerts_resp = requests.get(
        f"{BASE_URL}/alerts",
        headers=headers,
        params={"filter[route]": ",".join(route_ids)},
    )
    alerts_resp.raise_for_status()

    # 3. Keep only high-impact service alerts that affect travel through this station.
    # Skip facility closures (handled separately) and per-train delays (noisy, transient).
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
        # Skip STATION_ISSUE alerts about other stations (header starts with station name)
        header = attr.get("header", "")
        if effect == "STATION_ISSUE":
            entities = attr.get("informed_entity", [])
            stop_ids = {e.get("stop") for e in entities if "stop" in e}
            if stop_id not in stop_ids:
                continue
        active_periods = attr.get("active_period", [])
        service_alerts.append({
            "header": header,
            "effect": effect,
            "description": (attr.get("description") or "").strip(),
            "active_period": active_periods,
        })
    return service_alerts


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

        active_periods = alert_attrs.get("active_period", [])
        outage_start = active_periods[0].get("start") if active_periods else None

        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "severity": alert_attrs.get("severity"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
            "active_period": active_periods,
            "outage_start": outage_start,
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
        active_periods = alert_attrs.get("active_period", [])
        outage_start = active_periods[0].get("start") if active_periods else None

        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "severity": alert_attrs.get("severity"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
            "active_period": active_periods,
            "outage_start": outage_start,
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


def _query_ollama(prompt, model="gemma3"):
    """Send a prompt to a local Ollama instance and return the response text."""
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 200},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def _format_duration(iso_timestamp):
    """Convert an ISO timestamp to a human-readable duration like '3 days' or '2 months'."""
    if not iso_timestamp:
        return None
    try:
        # Take first 19 chars (YYYY-MM-DDTHH:MM:SS) to strip any timezone offset
        start = datetime.fromisoformat(iso_timestamp[:19])
        diff = datetime.utcnow() - start
        minutes = diff.total_seconds() / 60
        if minutes < 0:
            return "starting soon"
        if minutes < 60:
            n = int(minutes)
            return f"{n} minute{'s' if n != 1 else ''}"
        hours = minutes / 60
        if hours < 24:
            n = int(hours)
            return f"{n} hour{'s' if n != 1 else ''}"
        days = hours / 24
        if days < 30:
            n = int(days)
            return f"{n} day{'s' if n != 1 else ''}"
        months = days / 30.44
        if months < 12:
            n = int(months)
            return f"{n} month{'s' if n != 1 else ''}"
        years = days / 365.25
        return f"{years:.1f} years"
    except Exception:
        return None


def _build_station_prompt(station_name, station_facilities, system_stats,
                          service_alerts=None):
    """Build a structured prompt for the AI accessibility report.

    Parameters
    ----------
    service_alerts : list[dict] | None
        Non-facility alerts for the routes serving this station (shuttles,
        delays, suspensions, etc.) as returned by fetch_route_alerts().
    """
    if service_alerts is None:
        service_alerts = []

    n_elevators = sum(1 for f in station_facilities if f.get("type") == "ELEVATOR")
    n_escalators = sum(1 for f in station_facilities if f.get("type") == "ESCALATOR")
    n_out = sum(1 for f in station_facilities if f.get("status") == "out_of_service")
    elevators_out = sum(
        1 for f in station_facilities
        if f.get("type") == "ELEVATOR" and f.get("status") == "out_of_service"
    )

    # Build a set of out-of-service facility identifiers for contradiction detection.
    # We need short searchable strings like "elevator 876" that will appear in
    # MBTA detour text, not full names like "Chinatown Elevator 876 (Oak Grove...)".
    out_of_service_ids = set()
    for f in station_facilities:
        if f.get("status") == "out_of_service":
            ftype = (f.get("type") or "").lower()     # "elevator"
            fid = f.get("id") or ""                    # "876"
            if ftype and fid:
                out_of_service_ids.add(f"{ftype} {fid}")  # "elevator 876"

    # --- Facility details block ---
    facility_lines = []
    for f in station_facilities:
        name = f.get("name") or f.get("short_name") or ""
        status_label = "OPERATIONAL" if f.get("status") == "operational" else "OUT OF SERVICE"
        line = f"- {f['type']} \"{name}\": {status_label}"

        if f.get("status") != "operational" and f.get("alert"):
            alert = f["alert"]
            duration = _format_duration(alert.get("outage_start"))
            if duration:
                line += f" (down {duration})"
            cause = alert.get("cause", "")
            if cause:
                line += f"\n  Cause: {cause}"
            if alert.get("header"):
                line += f"\n  Alert: {alert['header']}"
            desc = (alert.get("description") or "").strip()
            if desc:
                line += f"\n  MBTA instructions: {desc}"
                # Check if instructions reference another out-of-service facility
                desc_lower = desc.lower()
                this_id = f"{(f.get('type') or '').lower()} {f.get('id') or ''}"
                for oos_id in out_of_service_ids:
                    if oos_id in desc_lower and oos_id != this_id:
                        line += (
                            f"\n  WARNING: These instructions reference "
                            f"{oos_id} which is ALSO out of service."
                        )
                        break

        facility_lines.append(line)

    facilities_block = "\n".join(facility_lines) if facility_lines else "(none)"

    # --- Service alerts block ---
    if service_alerts:
        alert_lines = []
        for sa in service_alerts:
            line = f"- [{sa['effect']}] {sa['header']}"
            if sa.get("description"):
                line += f"\n  Details: {sa['description'][:300]}"
            alert_lines.append(line)
        service_block = "\n".join(alert_lines)
    else:
        service_block = "(none)"

    prompt = (
        f"I rely on escalators and elevators and I'm about to travel through {station_name} "
        f"station. Give me a quick travel briefing based on this data.\n\n"
        f"Elevators: {n_elevators} ({elevators_out} out) | "
        f"Escalators: {n_escalators} ({n_out - elevators_out} out)\n\n"
        f"Facilities:\n{facilities_block}\n\n"
        f"Service alerts:\n{service_block}\n\n"
        f"In one short paragraph (3-5 sentences), tell me:\n"
        f"1. Whether I can get from street to platform by elevator right now.\n"
        f"2. If not, what exactly I should do instead — use specific stop names, "
        f"bus routes, and distances from the MBTA instructions above. If any "
        f"instructions have a WARNING, skip them and use a working alternative.\n"
        f"3. Any service disruptions (shuttles, closures) that affect my trip.\n\n"
        f"Only use the data above. Write as if talking directly to me. "
        f"Start with the key information immediately — no greeting or preamble."
    )
    return prompt


def generate_station_report(station_id, facilities, stations):
    """Generate an AI report for a station. Called from R via reticulate."""
    try:
        # Collect facilities for this station
        station_facilities = []
        station_name = station_id
        for f in facilities.values():
            if f.get("stop_id") == station_id:
                station_facilities.append(f)
                station_name = f.get("station_name", station_id)

        # Compute system-wide stats
        total_stations = len(stations)
        stations_with_outages = sum(
            1 for s in stations if (s.get("n_out_of_service") or 0) > 0
        )
        total_facilities = len(facilities)
        total_out_of_service = sum(
            1 for f in facilities.values() if f.get("status") == "out_of_service"
        )
        system_stats = {
            "total_stations": total_stations,
            "stations_with_outages": stations_with_outages,
            "total_facilities": total_facilities,
            "total_out_of_service": total_out_of_service,
        }

        # Fetch service alerts (shuttles, delays, etc.) for routes through this station
        service_alerts = fetch_route_alerts(station_id)

        prompt = _build_station_prompt(
            station_name, station_facilities, system_stats, service_alerts
        )
        return _query_ollama(prompt)
    except Exception as e:
        return f"__error__: {e}"


if __name__ == "__main__":
    print("Fetching MBTA accessibility data...\n")
    facilities = build_accessibility_status()
    outages = summarize_status(facilities)

    # The facilities dict can be used for:
    # - Shiny dashboard display
    # - JSON API response
    # - Database storage for historical tracking
