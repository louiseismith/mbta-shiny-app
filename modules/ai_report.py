"""ai_report.py

Ollama-based AI station report generation for the MBTA accessibility tracker.
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

from mbta_api import fetch_route_alerts

load_dotenv()


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

    wheelchair_line = ""
    if wheelchair_boarding == 2:
        wheelchair_line = (
            "STATION WHEELCHAIR STATUS: NOT ACCESSIBLE — this station is "
            "permanently inaccessible to wheelchair users per GTFS data.\n\n"
        )

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
        station_facilities = []
        station_name = station_id
        for f in facilities.values():
            if f.get("stop_id") == station_id:
                station_facilities.append(f)
                station_name = f.get("station_name", station_id)

        wheelchair_boarding = 0
        for s in stations:
            if s.get("id") == station_id:
                wheelchair_boarding = s.get("wheelchair_boarding", 0)
                break

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
