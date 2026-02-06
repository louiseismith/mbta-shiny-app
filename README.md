# MBTA Accessibility Tracker — Shiny App

Real-time elevator/escalator status for the Boston MBTA. Click a station on the map to see accessibility details.

## Requirements

- **R** packages: `shiny`, `leaflet`, `dplyr`, `reticulate`
- **Python** (used via `reticulate`): `requests`, `python-dotenv`
- **MBTA API key** in a `.env` file at the project root: `MBTA_API_KEY=your_key`  
  Get a key at the [MBTA Developer Portal](https://api-v3.mbta.com/).

## Install R dependencies

```r
install.packages(c("shiny", "leaflet", "dplyr", "reticulate"), repos = "https://cloud.r-project.org")
```

Ensure Python has `requests` and `python-dotenv` (e.g. `pip install requests python-dotenv`).

## How to run

From your **project root** (the folder that contains `01_api_queries` and `02_ai_productivity`):

```r
shiny::runApp("02_ai_productivity/shiny_app")
```

Or set the working directory to the project root, then run the same command.

## What it does

- Loads current elevator/escalator facilities and wheelchair-related alerts from the MBTA API (via the Python script in `01_api_queries/accessibility_tracker_prototype.py`).
- Shows an interactive map of stations; marker color = all operational (green), some outages (orange), or all out (red).
- Click a station to see its facilities and any outage details in the sidebar.

Plan: see `mbta_accessibility_shiny_plan.md` in the project root.
