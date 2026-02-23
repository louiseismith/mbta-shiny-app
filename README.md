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
- **Python** (used via `reticulate`): `requests`, `python-dotenv`. Install with **uv**:
  `uv pip install requests python-dotenv`
- **MBTA API key** in a `.env` file: `MBTA_API_KEY=your_key`.
  The app looks for `.env` in the app directory, then in parent directories.
  Get a key at the [MBTA Developer Portal](https://api-v3.mbta.com/).
- **Ollama API key** (optional, for AI reports): `OLLAMA_API_KEY=your_key` in the same `.env` file.
  The app degrades gracefully if unavailable — everything works except the AI report shows a fallback message.

## Setup

- **R:** From R or RStudio, source `run_app.R`; it will install any missing R packages.
- **Python:** Use **uv** (not pip). From a terminal:
  ```bash
  uv pip install requests python-dotenv
  ```
  Or let `run_app.R` try to install them with `uv` when you run the app.
- **API keys:** Create a `.env` file in the app directory (the folder with `app.R`) with:
  ```
  MBTA_API_KEY=your_key_here
  OLLAMA_API_KEY=your_key_here
  ```

## How to run

**From the app directory** (the folder that contains `app.R`, `run_app.R`, and `accessibility_tracker_prototype.py`):

```r
source("run_app.R")
```

Or in RStudio: set the working directory to that folder, then open and **Source** `run_app.R`.

**From a parent folder:** You can also run `source("path/to/app/run_app.R")` from R, or `Rscript path/to/app/run_app.R` from a terminal. The script will find the app directory by walking up until it sees `app.R` and `accessibility_tracker_prototype.py`.

## What it does

- **Data:** Fetches elevators, escalators, ramps, portable boarding lifts, and accessibility alerts from the MBTA API via `accessibility_tracker_prototype.py`.
- **Map:** Stations on a light CartoDB Positron basemap. Marker color and symbol indicate status: ✓ all operational (green), ! some outages (orange), ✗ all out (red). Click a marker to select a station.
- **Station tab:** Search or click a station to see facility status cards. Stations flagged as permanently inaccessible to wheelchair users show a warning. An AI-generated accessibility briefing summarizes what's working, what's not, MBTA-provided alternative routing, and any service disruptions on lines through that station.
- **Trip Check tab:** Build an ordered list of stations to check an entire planned trip at once. Select which facility types you can use (elevator, escalator, ramp, portable lift), then add stations via the search bar or by clicking directly on the map. Each station shows a per-facility status and any available alternate routing. An overall verdict banner summarizes whether the trip is clear, has partial outages, or is blocked.

## Files

| File | Description |
|------|-------------|
| `app.R` | Shiny app (UI + server) |
| `run_app.R` | Launcher script; installs missing R packages |
| `accessibility_tracker_prototype.py` | MBTA API queries + AI report generation |
| `requirements.txt` | Python package dependencies |
| `manifest.json` | Posit Connect deployment manifest |
| `.github/workflows/deploy.yml` | GitHub Actions workflow for Posit Connect deployment |
