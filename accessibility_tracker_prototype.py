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


def fetch_route_alerts(stop_id):
    """Fetch all non-facility alerts for the routes serving a station.

    Returns a list of alert dicts (header, effect, description) for
    service-level alerts (shuttles, suspensions, etc.) that affect routes
    passing through the given stop.  Facility-specific alerts (elevator/
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
        service_alerts.append({
            "header": header,
            "effect": effect,
            "description": (attr.get("description") or "").strip(),
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


def get_data_for_app():
    """
    Return facilities and stations for the Shiny app (map + station details).
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

        alert_summary = {
            "id": alert["id"],
            "header": alert_attrs.get("header"),
            "description": alert_attrs.get("description"),
            "cause": alert_attrs.get("cause"),
            "effect": alert_attrs.get("effect"),
            "updated_at": alert_attrs.get("updated_at"),
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
            "wheelchair_boarding": info.get("wheelchair_boarding", 0),
        })

    return {"facilities": facilities, "stations": stations}


def _query_ollama(prompt, model="gemma3:12b"):
    """Send a prompt to a local Ollama instance and return the response text."""
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 300},
        },
        timeout=60,
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


def _build_station_prompt(station_name, station_facilities,
                          service_alerts=None, wheelchair_boarding=0):
    """Build a structured prompt for the AI accessibility report."""
    if service_alerts is None:
        service_alerts = []

    # --- Facility type counts ---
    type_labels = {
        "ELEVATOR": "elevator",
        "ESCALATOR": "escalator",
        "RAMP": "ramp",
        "PORTABLE_BOARDING_LIFT": "portable lift",
    }
    counts = {}
    out_counts = {}
    for f in station_facilities:
        ftype = f.get("type", "UNKNOWN")
        counts[ftype] = counts.get(ftype, 0) + 1
        if f.get("status") == "out_of_service":
            out_counts[ftype] = out_counts.get(ftype, 0) + 1

    count_parts = []
    for ftype in ("ELEVATOR", "ESCALATOR", "RAMP", "PORTABLE_BOARDING_LIFT"):
        total = counts.get(ftype, 0)
        if total == 0:
            continue
        out = out_counts.get(ftype, 0)
        label = type_labels.get(ftype, ftype.lower())
        count_parts.append(f"{label}s: {total} ({out} out)" if total != 1
                           else f"{label}s: 1 ({out} out)")

    summary_line = " | ".join(count_parts) if count_parts else "No facilities on record"

    # --- Wheelchair boarding status ---
    wheelchair_line = ""
    if wheelchair_boarding == 2:
        wheelchair_line = (
            "STATION WHEELCHAIR STATUS: NOT ACCESSIBLE — this station is "
            "permanently inaccessible to wheelchair users per GTFS data.\n\n"
        )

    # --- Facility details block ---
    operational = [f for f in station_facilities if f.get("status") == "operational"]
    out_of_service = [f for f in station_facilities if f.get("status") == "out_of_service"]

    facility_lines = []

    for f in out_of_service:
        name = f.get("name") or f.get("short_name") or ""
        line = f"- {f['type']} \"{name}\": OUT OF SERVICE"

        alert = f.get("alert")
        if alert:
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
        facility_lines.append(line)

    if len(operational) > 6:
        by_type = {}
        for f in operational:
            ftype = f.get("type", "UNKNOWN")
            by_type[ftype] = by_type.get(ftype, 0) + 1
        parts = [f"{count} {type_labels.get(t, t.lower())}(s)"
                 for t, count in sorted(by_type.items())]
        facility_lines.append(f"- {', '.join(parts)} operational")
    else:
        for f in operational:
            name = f.get("name") or f.get("short_name") or ""
            facility_lines.append(f"- {f['type']} \"{name}\": OPERATIONAL")

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
        f"I rely on escalators and elevators and I'm about to travel through "
        f"{station_name} station. Give me a quick travel briefing based on "
        f"this data.\n\n"
        f"{wheelchair_line}"
        f"{summary_line}\n\n"
        f"Facilities:\n{facilities_block}\n\n"
        f"Service alerts:\n{service_block}\n\n"
        f"In one short paragraph (1-3 sentences), tell me:\n"
        f"1. Whether I can get from street to platform accessibly right now.\n"
        f"2. If not, what I should do instead — use specific details from "
        f"the MBTA instructions above.\n"
        f"In another short paragraph (1-2 sentences), tell me:\n"
        f"1. Any service disruptions that affect my trip.\n\n"
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

        # Look up wheelchair_boarding from stations list
        wheelchair_boarding = 0
        for s in stations:
            if s.get("id") == station_id:
                wheelchair_boarding = s.get("wheelchair_boarding", 0)
                break

        # Fetch service alerts (shuttles, delays, etc.) for routes through this station
        service_alerts = fetch_route_alerts(station_id)

        prompt = _build_station_prompt(
            station_name, station_facilities, service_alerts=service_alerts,
            wheelchair_boarding=wheelchair_boarding,
        )
        return _query_ollama(prompt)
    except Exception as e:
        return f"__error__: {e}"


