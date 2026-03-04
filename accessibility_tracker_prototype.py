import json
import os
import polyline
import psycopg2
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BASE_URL = "https://api-v3.mbta.com"


def _get_supabase_conn():
    """Open a Postgres connection from SUPABASE_* env vars. Returns None if not configured."""
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


def _log_facility_statuses(facilities):
    """Write a row for any facility whose status has changed since the last snapshot."""
    conn = _get_supabase_conn()
    if conn is None:
        return  # Supabase not configured — skip silently
    try:
        with conn:
            with conn.cursor() as cur:
                # Last recorded status per facility
                cur.execute("""
                    SELECT facility_id, status FROM outage_log
                    WHERE id IN (
                        SELECT MAX(id) FROM outage_log GROUP BY facility_id
                    )
                """)
                last_status = {row[0]: row[1] for row in cur.fetchall()}

                now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                to_insert = []
                for fid, f in facilities.items():
                    current = f.get("status", "operational")
                    if last_status.get(fid) != current:
                        alert = f.get("alert") or {}
                        to_insert.append((
                            now, fid,
                            f.get("type"),
                            f.get("name") or f.get("short_name"),
                            f.get("stop_id"),
                            f.get("station_name"),
                            current,
                            alert.get("id"),
                            alert.get("outage_start"),
                            alert.get("cause"),
                            alert.get("outage_end"),
                            alert.get("header"),
                            json.dumps(alert) if alert else None,
                        ))

                if to_insert:
                    cur.executemany("""
                        INSERT INTO outage_log
                            (logged_at, facility_id, facility_type, facility_name,
                             stop_id, station_name, status, alert_id, outage_start,
                             cause, outage_end_scheduled, alert_header, alert_raw)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """, to_insert)
    finally:
        conn.close()
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
    """Fetch and decode shape geometry for given route IDs in one API call.

    Returns dict: {route_id: [[(lat, lon), ...], ...]}
    Each route maps to a list of polylines (one per shape, e.g. branches/directions).
    Shapes with priority <= 0 (non-revenue) are excluded.
    Returns empty dict on error or if route_ids is empty.
    """
    if not route_ids:
        return {}
    try:
        resp = requests.get(
            f"{BASE_URL}/shapes",
            headers=headers,
            params={"filter[route]": ",".join(route_ids)},
        )
        resp.raise_for_status()
    except Exception:
        return {}

    # relationships is None by default — assign shapes to route via per-route request
    # (bulk request already filtered to this route_id via the outer loop)
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
                # Skip non-revenue shapes (yard moves, maintenance tracks) —
                # canonical revenue shapes have IDs starting with "canonical-"
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


def _fetch_station_routes_from_api():
    """Fetch station→routes mapping from MBTA API.

    Queries all rapid-transit and commuter-rail routes, then fetches stops per route.
    Returns dict: {stop_id: [{id, name, color, text_color, route_type}]}
    Makes approximately one API call per route (~25-30 total).
    """
    routes_resp = requests.get(
        f"{BASE_URL}/routes",
        headers=headers,
        params={"filter[type]": "0,1,2"},
    )
    routes_resp.raise_for_status()
    routes = routes_resp.json().get("data", [])

    station_routes = {}
    for route in routes:
        route_id = route["id"]
        attrs = route["attributes"]
        route_info = {
            "id": route_id,
            "name": attrs.get("long_name") or attrs.get("short_name"),
            "color": attrs.get("color"),
            "text_color": attrs.get("text_color"),
            "route_type": attrs.get("type"),
        }
        stops_resp = requests.get(
            f"{BASE_URL}/stops",
            headers=headers,
            params={"filter[route]": route_id},
        )
        stops_resp.raise_for_status()
        for stop in stops_resp.json().get("data", []):
            stop_id = stop["id"]
            if stop_id not in station_routes:
                station_routes[stop_id] = []
            station_routes[stop_id].append(route_info)

    return station_routes


def sync_station_routes():
    """Fetch station→route mapping from MBTA API and store in Supabase.

    Intended to run on a schedule from the scraper (daily is sufficient).
    Truncates and replaces the full station_routes table each run.
    """
    data = _fetch_station_routes_from_api()
    conn = _get_supabase_conn()
    if conn is None:
        return
    try:
        synced_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE station_routes")
                rows = [
                    (stop_id, r["id"], r["name"], r["color"], r["text_color"], r["route_type"], synced_at)
                    for stop_id, routes in data.items()
                    for r in routes
                ]
                if rows:
                    cur.executemany("""
                        INSERT INTO station_routes
                            (stop_id, route_id, route_name, route_color,
                             route_text_color, route_type, synced_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, rows)
        print(f"sync_station_routes: {len(rows)} rows written at {synced_at}.")
    finally:
        conn.close()


def _read_station_routes_from_db():
    """Read station→routes mapping from Supabase.

    Returns dict: {stop_id: [{id, name, color, text_color, route_type}]}
    Returns empty dict if Supabase not configured or table is empty.
    """
    conn = _get_supabase_conn()
    if conn is None:
        return {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT stop_id, route_id, route_name, route_color,
                           route_text_color, route_type
                    FROM station_routes
                """)
                rows = cur.fetchall()
        result = {}
        for stop_id, route_id, name, color, text_color, route_type in rows:
            if stop_id not in result:
                result[stop_id] = []
            result[stop_id].append({
                "id": route_id,
                "name": name,
                "color": color,
                "text_color": text_color,
                "route_type": route_type,
            })
        return result
    except Exception:
        return {}
    finally:
        conn.close()


def sync_route_shapes():
    """Fetch canonical route shapes from MBTA API and store in Supabase.

    Intended to run on a schedule from the scraper (weekly is sufficient —
    shapes only change with major construction).
    Truncates and replaces the full route_shapes table each run.
    """
    try:
        routes_resp = requests.get(
            f"{BASE_URL}/routes",
            headers=headers,
            params={"filter[type]": "0,1,2"},
        )
        routes_resp.raise_for_status()
        routes = routes_resp.json().get("data", [])
    except Exception:
        print("sync_route_shapes: failed to fetch routes from MBTA API.")
        return

    conn = _get_supabase_conn()
    if conn is None:
        return
    try:
        synced_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        rows = []
        for route in routes:
            route_id = route["id"]
            try:
                resp = requests.get(
                    f"{BASE_URL}/shapes",
                    headers=headers,
                    params={"filter[route]": route_id},
                )
                resp.raise_for_status()
                encoded_shapes = [
                    s["attributes"]["polyline"]
                    for s in resp.json().get("data", [])
                    if s.get("id", "").startswith("canonical-")
                    and s["attributes"].get("polyline")
                ]
            except Exception:
                encoded_shapes = []
            if encoded_shapes:
                rows.append((route_id, json.dumps(encoded_shapes), synced_at))
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE route_shapes")
                if rows:
                    cur.executemany("""
                        INSERT INTO route_shapes (route_id, shapes, synced_at)
                        VALUES (%s, %s::jsonb, %s)
                    """, rows)
        print(f"sync_route_shapes: {len(rows)} routes written at {synced_at}.")
    finally:
        conn.close()


def _read_route_shapes_from_db():
    """Read canonical route shapes from Supabase and decode polylines.

    Returns dict: {route_id: [[(lat, lon), ...], ...]}
    Returns empty dict if Supabase not configured or table is empty.
    """
    conn = _get_supabase_conn()
    if conn is None:
        return {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT route_id, shapes FROM route_shapes")
                rows = cur.fetchall()
        result = {}
        for route_id, shapes_val in rows:
            try:
                encoded_list = shapes_val if isinstance(shapes_val, list) else json.loads(shapes_val)
                result[route_id] = [polyline.decode(enc) for enc in encoded_list]
            except Exception:
                pass
        return result
    except Exception:
        return {}
    finally:
        conn.close()


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
    # Build route → stop_ids reverse index from the cached station_routes
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
    }


# =============================================================================
# Facility pattern classifier
# =============================================================================

_LINE_KEYWORDS  = {"Orange Line", "Red Line", "Blue Line", "Green Line", "Commuter Rail"}
_TERMINUS_NAMES = {
    "Alewife", "Ashmont", "Braintree", "Oak Grove", "Forest Hills",
    "Bowdoin", "Wonderland", "Charles/MGH", "Mattapan", "Riverside",
    "Heath Street", "Cleveland Circle", "Boston College",
}
_GREEN_JARGON = {"Park Street & North", "Copley & West", "Kenmore & West"}


def classify_pattern(facility_name: str) -> int:
    """Classify an MBTA facility name into a naming pattern.

    Returns:
        1 — explicit line keyword ("Red Line", "Orange Line", etc.)
        2 — terminus reference ("Alewife platform", "Ashmont platform", etc.)
        3 — Green Line directional jargon ("Copley & West", "Park Street & North", etc.)
        4 — cross-platform connector ("X platform to Y platform")
        5 — generic / shared infrastructure (ramp, lobby, busway, etc.)
        0 — unknown / hard to classify
    """
    name = facility_name

    if any(k in name for k in _LINE_KEYWORDS):
        return 1

    if "platform" in name and "platform" in name.split(" to ", 1)[-1]:
        return 4

    if "platform" not in name:
        return 5

    if any(t in name for t in _GREEN_JARGON):
        return 3

    if any(t in name for t in _TERMINUS_NAMES):
        return 2

    return 0


def _query_ollama(prompt, model="gemma3:12b"):
    """Send a prompt to Ollama Cloud and return the response text."""
    OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
    resp = requests.post(
        "https://ollama.com/api/chat",
        headers={
            "Authorization": f"Bearer {OLLAMA_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": 300},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


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


def generate_station_report(station_id, facilities, stations, service_alerts_by_stop=None):
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

        # Use prefetched service alerts if available, otherwise fall back to API
        if service_alerts_by_stop is not None:
            service_alerts = list(service_alerts_by_stop.get(station_id, []))
        else:
            service_alerts = fetch_route_alerts(station_id)

        prompt = _build_station_prompt(
            station_name, station_facilities, service_alerts=service_alerts,
            wheelchair_boarding=wheelchair_boarding,
        )
        return _query_ollama(prompt)
    except Exception as e:
        return f"__error__: {e}"


