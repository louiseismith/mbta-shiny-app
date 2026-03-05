"""Quick test script for iterating on AI station report prompts.

Usage:
    python test_prompt.py                  # defaults to Chinatown
    python test_prompt.py "Sullivan Square"
    python test_prompt.py --list           # show all stations with outages

Tips:
 - Compare stations with and without description data — Chinatown, JFK/UMass, Sullivan Square    
  have MBTA instructions; Oak Grove and Ruggles don't. That contrast will show you where the
  prompt works well vs. where it's still thin.                                                    
  - Try an all-operational station too (any not on the --list) to see how the model handles the
  "nothing is wrong" case.                                                                        
  - When you're happy with the prompt, the Shiny app will pick up the changes automatically on
  restart since it calls the same _build_station_prompt function.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_backend import get_data_for_app
from modules.mbta_api import fetch_route_alerts
from modules.ai_report import _build_station_prompt, _format_duration, _query_ollama

data = get_data_for_app()
facilities = data["facilities"]
stations = data["stations"]

# --list: show stations that have outages (most interesting for testing)
if len(sys.argv) > 1 and sys.argv[1] == "--list":
    print("Stations with outages:")
    for s in sorted(stations, key=lambda s: s["name"]):
        if (s.get("n_out_of_service") or 0) > 0:
            print(f"  {s['name']}  (id={s['id']}, {s['n_out_of_service']} out)")
    sys.exit(0)

# Pick station by name (partial match, case-insensitive)
target = sys.argv[1] if len(sys.argv) > 1 else "Chinatown"
station = None
for s in stations:
    if target.lower() in s["name"].lower():
        station = s
        break
if not station:
    print(f"No station matching '{target}'. Use --list to see options.")
    sys.exit(1)

station_id = station["id"]
station_name = station["name"]

# Collect facilities for this station
station_facilities = [f for f in facilities.values() if f.get("stop_id") == station_id]

# Look up wheelchair_boarding from stations list
wheelchair_boarding = 0
for s in stations:
    if s.get("id") == station_id:
        wheelchair_boarding = s.get("wheelchair_boarding", 0)
        break

# Fetch service alerts for routes through this station
service_alerts = fetch_route_alerts(station_id)

# Build and display prompt
prompt = _build_station_prompt(station_name, station_facilities,
                               service_alerts=service_alerts,
                               wheelchair_boarding=wheelchair_boarding)
print("=" * 60)
print("STATION:", station_name)
print("=" * 60)
print()
print("--- PROMPT ---")
print(prompt)
print()

# Call Ollama
print("--- RESPONSE ---")
try:
    response = _query_ollama(prompt)
    print(response)
except Exception as e:
    print(f"Ollama error: {e}")
print()
