# MBTA Accessibility Tracker — Shiny App

Real-time accessibility status for the Boston MBTA. Check individual stations or plan a multi-stop trip to see elevator, escalator, ramp, and portable lift status alongside an AI-generated travel briefing.

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [How to run](#how-to-run)
- [What it does](#what-it-does)
- [Files](#files)

## Requirements

- **R** packages: `shiny`, `leaflet`, `dplyr`, `reticulate` (installed automatically by `run_app.R` if missing).
- **Python** (used via `reticulate`): `requests`, `python-dotenv`, `psycopg2-binary`, `polyline`. Install with **uv**:
  `uv pip install requests python-dotenv psycopg2-binary polyline`
- **API keys** in a `.env` file (see [Setup](#setup)):
  - `MBTA_API_KEY` — required. Get one at the [MBTA Developer Portal](https://api-v3.mbta.com/).
  - `OLLAMA_API_KEY` — optional, for AI station reports. The app degrades gracefully if missing.
  - `SUPABASE_HOST`, `SUPABASE_DB`, `SUPABASE_USER`, `SUPABASE_PASSWORD`, `SUPABASE_PORT` — for outage history logging and route/shape caching. App works without these but outage history and trip route lines will be unavailable.

## Setup

- **R:** From R or RStudio, source `run_app.R`; it will install any missing R packages.
- **Python:** Use **uv** (not pip). From a terminal:
  ```bash
  uv pip install requests python-dotenv psycopg2-binary polyline
  ```
- **API keys:** Create a `.env` file in the app directory (the folder with `app.R`) with:
  ```
  MBTA_API_KEY=your_key_here
  OLLAMA_API_KEY=your_key_here
  SUPABASE_HOST=your_pooler_host
  SUPABASE_DB=postgres
  SUPABASE_USER=postgres.your_project_ref
  SUPABASE_PASSWORD=your_password
  SUPABASE_PORT=6543
  ```

## How to run

**From the app directory** (the folder that contains `app.R`, `run_app.R`, and `accessibility_tracker_prototype.py`):

```r
source("run_app.R")
```

Or in RStudio: set the working directory to that folder, then open and **Source** `run_app.R`.

**From a parent folder:** You can also run `source("path/to/app/run_app.R")` from R, or `Rscript path/to/app/run_app.R` from a terminal.

## What it does

- **Data:** Fetches elevators, escalators, ramps, portable boarding lifts, and accessibility alerts from the MBTA API at startup and auto-refreshes every 5 minutes. Map zoom and pan are preserved across refreshes. Service alerts are prefetched at startup so station clicks require no additional API calls.

- **Map:** Stations on a CartoDB Positron basemap with status-aware clustering. Individual markers show ✓ (green), ! (orange), or ✗ (red). Clusters show total station count when all clear, or an M/N fraction when outages are present. Clustering disables at zoom 13 so individual stations are always visible up close.

- **Station tab:** Search or click a station to see per-facility status cards with timing information. An AI-generated briefing (via Ollama Cloud) summarizes current conditions, any MBTA-provided alternative routing, and active service disruptions on lines serving that station.

- **Trip Check tab:** Build an ordered list of stations to validate an entire planned trip. Add stations by searching or clicking the map. The results view shows an interleaved timeline — station nodes alternating with segment connectors that display the MBTA-branded line badges for routes connecting each pair of stations. Route polylines are drawn on the map for each trip segment. Select which facility types you can use (elevator, escalator, ramp, portable lift) to filter what counts as an outage. An overall verdict banner summarizes whether the trip is clear, has partial outages, or is blocked.

- **Outage history:** Accessibility status changes are logged to a Supabase (hosted Postgres) database by an hourly GitHub Actions scraper, building a historical record independent of user visits.

## Files

| File | Description |
|------|-------------|
| `app.R` | Shiny app (UI + server) |
| `run_app.R` | Launcher script; installs missing R packages |
| `accessibility_tracker_prototype.py` | MBTA API queries, Supabase logging, AI report generation |
| `requirements.txt` | Python package dependencies |
| `manifest.json` | Posit Connect deployment manifest |
| `.github/workflows/deploy.yml` | GitHub Actions workflow for Posit Connect auto-deployment |
| `benchmark_facility_interpreter.py` | Benchmark script for evaluating LLMs on facility name interpretation |
| `facility_interpreter_golden.py` | 24-case golden dataset for facility interpreter benchmarks |
| `run_benchmarks.sh` | Batch runner for benchmarking all candidate models |
| `benchmark_results/` | Benchmark output files per model |
