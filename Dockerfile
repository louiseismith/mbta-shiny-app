# MBTA Accessibility Tracker — Shiny App
# ----------------------------------------
# Build:
#   docker build -t mbta-tracker .
#
# Run:
#   docker run -p 3838:3838 -e MBTA_API_KEY=your_key_here mbta-tracker
#   Then open http://localhost:3838/
#
# AI Report (Ollama):
#   The AI report feature calls http://localhost:11434 (hardcoded in the Python script).
#   Inside a container "localhost" refers to the container itself, not your host machine.
#
#   Linux — add --network=host to share the host's network:
#     docker run --network=host -e MBTA_API_KEY=your_key_here mbta-tracker
#
#   Mac / Windows — Ollama is not reachable from inside the container without
#   code changes; the AI report panel will show "unavailable" but the rest of
#   the app works fine.

FROM rocker/shiny:latest

# System dependencies needed by reticulate and Python venv
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# R packages (shiny is already in rocker/shiny)
# install2.r comes with rocker images and exits non-zero on failure,
# so a bad install breaks the build immediately rather than at runtime.
RUN install2.r --error --ncpus -1 leaflet dplyr reticulate

# Python virtualenv
# app.R resolves the venv as: file.path(getwd(), "..", "..", ".venv")
# Shiny Server sets getwd() to /srv/shiny-server/app, so ../../.venv == /srv/.venv
RUN python3 -m venv /srv/.venv \
    && /srv/.venv/bin/pip install --no-cache-dir requests python-dotenv

# Copy app source
COPY . /srv/shiny-server/app/

EXPOSE 3838

# Run the app directly (bypasses Shiny Server's multi-app routing so the app
# is served at / rather than /app/)
CMD ["R", "-e", "shiny::runApp('/srv/shiny-server/app', host='0.0.0.0', port=3838)"]
