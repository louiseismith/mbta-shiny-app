# MBTA Accessibility Tracker — Shiny App

Real-time elevator/escalator status for the Boston MBTA. Click a station on the map to see accessibility details.

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [How to run](#how-to-run)
- [What it does](#what-it-does)

## Requirements

- **R** packages: `shiny`, `leaflet`, `dplyr`, `reticulate` (installed automatically by `run_app.R` if missing).
- **Python** (used via `reticulate`): `requests`, `python-dotenv`. Install with **uv**:  
  `uv pip install requests python-dotenv`
- **MBTA API key** in a `.env` file: `MBTA_API_KEY=your_key`.  
  The app looks for `.env` in the `shiny_app` directory, then in parent directories.  
  Get a key at the [MBTA Developer Portal](https://api-v3.mbta.com/).

## Setup

- **R:** From R or RStudio, source `run_app.R`; it will install any missing R packages.
- **Python:** Use **uv** (not pip). From a terminal:
  ```bash
  uv pip install requests python-dotenv
  ```
  Or let `run_app.R` try to install them with `uv` when you run the app.
- **API key:** Create a `.env` file in `02_ai_productivity/shiny_app` (or project root) with:
  ```
  MBTA_API_KEY=your_key_here
  ```

## How to run

**From the `shiny_app` directory** (recommended):

```r
setwd("02_ai_productivity/shiny_app")  # or your path to shiny_app
source("run_app.R")
```

Or in RStudio: set the working directory to `shiny_app`, then open and **Source** `run_app.R`.

**From the project root** (folder that contains `02_ai_productivity`):

```r
source("02_ai_productivity/shiny_app/run_app.R")
```

Or from a terminal (from project root):

```bash
Rscript 02_ai_productivity/shiny_app/run_app.R
```

`run_app.R` detects whether you are in `shiny_app` or in the project root and runs the app accordingly. It does not require an `01_api_queries` folder.

## What it does

- **Data:** Fetches facilities and alerts from the MBTA API via the Python script **in this folder**: `accessibility_tracker_prototype.py` (no dependency on `01_api_queries`).
- **Map:** Stations on a light CartoDB Positron basemap; marker color = all operational (green), some outages (orange), or all out (red). Markers have a white border for visibility.
- **Sidebar:** Click a station to see its elevator/escalator table; the table scrolls inside the sidebar and won’t overflow. Alert fields (e.g. severity) are normalized so mixed types from the API don’t cause errors.
