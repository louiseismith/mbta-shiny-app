# app.R
# MBTA Accessibility Tracker — Shiny app
# Real-time elevator/escalator status by station; map-based station selection.
# Plan: mbta_accessibility_shiny_plan.md

# 0. SETUP ###################################

## 0.1 Load packages #################################

library(shiny)
library(leaflet)
library(dplyr)
library(reticulate)
library(plotly)
venv_path = file.path(getwd(), "..", "..", ".venv")
if (dir.exists(venv_path)) use_virtualenv(venv_path, required = TRUE)

## 0.2 Load API key ####################################

# Look for .env in app dir, then project root, so it works from either place
for (p in c(".", "..", "../..")) {
  env_file = file.path(p, ".env")
  if (file.exists(env_file)) {
    readRenviron(env_file)
    break
  }
}

## 0.3 Python data source #################################

# Script lives in the same folder as app.R
app_dir = getwd()
script_path = file.path(app_dir, "app_backend.py")
if (!file.exists(script_path)) stop("Python script not found: ", script_path)
reticulate::source_python(script_path)

# 1. HELPERS ###################################

# Fetch facilities + stations from Python (get_data_for_app)
# Convert to R lists so we can use dplyr and base R easily
get_app_data = function() {
  out = tryCatch(
    reticulate::py_to_r(get_data_for_app()),
    error = function(e) list(facilities = list(), stations = list())
  )
  if (!is.list(out) || is.null(out$facilities)) out = list(facilities = list(), stations = list())
  out
}

mbta_line_badge = function(line_id) {
  colors = list(
    "Red"      = c("DA291C", "FFFFFF"), "Mattapan" = c("DA291C", "FFFFFF"),
    "Orange"   = c("ED8B00", "FFFFFF"),
    "Blue"     = c("003DA5", "FFFFFF"),
    "Green-B"  = c("00843D", "FFFFFF"), "Green-C" = c("00843D", "FFFFFF"),
    "Green-D"  = c("00843D", "FFFFFF"), "Green-E" = c("00843D", "FFFFFF")
  )
  pair = if (grepl("^CR-", line_id)) c("80276C", "FFFFFF") else colors[[line_id]] %||% c("888888", "FFFFFF")
  label = if (grepl("^CR-", line_id)) sub("^CR-", "", line_id) else line_id
  tags$span(
    class = "trip-line-badge",
    style = paste0("background:#", pair[1], ";color:#", pair[2], ";font-size:0.72em;padding:1px 6px;"),
    label
  )
}

# Build a list of facility cards (as Shiny tag objects) for one station
station_facility_cards = function(facilities_list, station_id, flm = list()) {
  if (length(facilities_list) == 0) return(list())
  cards = list()
  for (i in seq_along(facilities_list)) {
    f = facilities_list[[i]]
    if (is.null(f$stop_id) || f$stop_id != station_id) next
    if (identical(f$type %||% "", "RAMP")) next
    alert = f$alert
    status = gsub("_", " ", as.character(f$status %||% ""))
    type = facility_type_label(as.character(f$type %||% ""))
    is_out = identical(f$status, "out_of_service")
    details = if (length(alert) && !is.null(alert$header)) as.character(alert$header) else as.character(f$short_name %||% "")

    # Status badge
    badge_class = if (is_out) "facility-badge-out" else "facility-badge-ok"
    badge = tags$span(class = badge_class, status)

    # Time info line (only for outages)
    time_line = NULL
    if (is_out) {
      outage_start = if (length(alert) && !is.null(alert$outage_start)) as.character(alert$outage_start) else NA_character_
      updated_at = if (length(alert) && !is.null(alert$updated_at)) as.character(alert$updated_at) else NA_character_
      parts = c()
      if (!is.na(outage_start)) {
        since_date = format(as.POSIXct(outage_start, format = "%Y-%m-%dT%H:%M:%S", tz = "America/New_York"), "%b %d, %Y")
        duration = format_duration(outage_start)
        parts = c(parts, paste0("Out since ", since_date, " (", duration, ")"))
      }
      if (!is.na(updated_at)) {
        parts = c(parts, paste0("Updated ", format_duration(updated_at), " ago"))
      }
      if (length(parts) > 0) {
        time_line = tags$div(class = "facility-time", paste(parts, collapse = " \u00b7 "))
      }
    }

    fi = flm[[as.character(f$id %||% "")]]
    lines_row = if (!is.null(fi) && !identical(fi$source %||% "", "unresolved") && length(fi$lines %||% list()) > 0) {
      line_badges = lapply(as.character(unlist(fi$lines)), mbta_line_badge)
      direction_text = if (!is.null(fi$direction) && nchar(fi$direction %||% "") > 0)
        tags$span(style = "color:#888;font-size:0.78em;", fi$direction)
      else NULL
      tags$div(style = "display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-top:4px;",
               tagList(c(line_badges, list(direction_text))))
    } else NULL

    card = tags$div(
      class = paste("facility-card", if (is_out) "facility-out" else "facility-ok"),
      tags$div(class = "facility-header", tags$strong(type), badge),
      tags$div(class = "facility-details", details),
      lines_row,
      time_line
    )
    cards = c(cards, list(card))
  }
  cards
}

`%||%` = function(x, y) if (is.null(x)) y else x

# Display names for facility types
facility_type_label = function(type) {
  labels = list(
    ELEVATOR = "Elevator",
    ESCALATOR = "Escalator",
    RAMP = "Ramp",
    PORTABLE_BOARDING_LIFT = "Portable Lift"
  )
  labels[[type]] %||% type
}

# Known MBTA underground concourse connections (station pairs linked by pedestrian walkway).
# Source: MBTA GTFS pathways.txt — only one cross-station concourse exists:
# Park Street <-> Downtown Crossing via the Winter Street Concourse.
CONCOURSE_PAIRS = list(
  "place-pktrm" = list(id = "place-dwnxg", name = "Downtown Crossing"),
  "place-dwnxg" = list(id = "place-pktrm", name = "Park Street")
)

# Shared palette
color_ok = "#5cb85c"
color_warn = "#f0ad4e"
color_out = "#d9534f"

# Build one legend row: circle marker icon + label
legend_item = function(color, symbol, label) {
  paste0(
    '<div style="display:flex;align-items:center;margin-top:4px;">',
    '<div style="width:18px;height:18px;border-radius:50%;background:', color,
    ';border:1.5px solid #bbb;',
    'display:inline-flex;align-items:center;justify-content:center;',
    'margin-right:6px;font-size:10px;font-weight:bold;color:white;flex-shrink:0;">',
    symbol, '</div>', label, '</div>'
  )
}

# Human-readable duration from an ISO timestamp to now
format_duration = function(iso_timestamp) {
  if (is.null(iso_timestamp) || is.na(iso_timestamp) || iso_timestamp == "") return(NA_character_)
  start = as.POSIXct(iso_timestamp, format = "%Y-%m-%dT%H:%M:%S", tz = "America/New_York")
  if (is.na(start)) return(NA_character_)
  diff_mins = as.numeric(difftime(Sys.time(), start, units = "mins"))
  if (diff_mins < 60) return(paste0(round(diff_mins), " min"))
  diff_hours = diff_mins / 60
  if (diff_hours < 24) return(paste0(round(diff_hours), " hr"))
  diff_days = diff_hours / 24
  if (diff_days < 30) return(paste0(round(diff_days), " days"))
  diff_months = diff_days / 30.44
  if (diff_months < 12) return(paste0(round(diff_months), " mo"))
  diff_years = diff_days / 365.25
  return(paste0(round(diff_years, 1), " yr"))
}

# Compute per-line accessibility verdict for one (station, route) pair.
# station_fac: list of facility dicts at this station matching user's needed types
# flm:         facility_line_mapping (facility_id -> {lines, source, ...})
# stairs:      TRUE if user can use stairs as fallback
# Returns list(route_id, verdict, n_serving, n_op, n_out, has_uncertain)
compute_line_verdict = function(route_id, station_fac, flm, stairs) {
  # Facilities whose mapping says they serve this route (excludes unresolved)
  serving = Filter(function(f) {
    fi = flm[[as.character(f$id %||% "")]]
    if (is.null(fi) || identical(fi$source %||% "", "unresolved")) return(FALSE)
    route_id %in% as.character(unlist(fi$lines %||% list()))
  }, station_fac)

  # Unresolved out-of-service facilities — line affiliation unknown
  unresolved_out = Filter(function(f) {
    fi = flm[[as.character(f$id %||% "")]]
    !is.null(fi) && identical(fi$source %||% "", "unresolved") &&
      identical(f$status, "out_of_service")
  }, station_fac)

  n_serving = length(serving)
  n_op  = sum(vapply(serving, function(f) identical(f$status, "operational"), logical(1)))
  n_out = n_serving - n_op

  verdict = if (n_serving == 0) {
    if (stairs) "clear" else "no_facilities"
  } else if (n_out == 0) {
    "clear"
  } else if (n_op > 0) {
    "traversable"
  } else {
    if (stairs) "traversable" else "blocked"
  }

  list(
    route_id      = route_id,
    verdict       = verdict,
    n_serving     = n_serving,
    n_op          = n_op,
    n_out         = n_out,
    has_uncertain = length(unresolved_out) > 0
  )
}

find_transfers = function(stop_a, stop_b, station_routes, fac, fac_needs, stairs, name_lookup) {
  # Build inverted index: route_id -> [stop_ids]
  route_to_stops = list()
  for (sid in names(station_routes)) {
    for (r in station_routes[[sid]]) {
      rid = r$id %||% ""
      if (nchar(rid) > 0)
        route_to_stops[[rid]] = c(route_to_stops[[rid]], sid)
    }
  }
  routes_a = station_routes[[stop_a]] %||% list()
  routes_b = station_routes[[stop_b]] %||% list()

  # Collapse Green-B/C/D/E -> "Green" for dedup — trunk stations appear under all branches
  family_id = function(rid) sub("^(Green)-[A-Z]$", "\\1", rid)
  transfers = list()
  seen = character(0)

  for (ra in routes_a) {
    for (rb in routes_b) {
      if (identical(ra$id, rb$id)) next
      via_stops = setdiff(
        intersect(route_to_stops[[ra$id]] %||% character(0),
                  route_to_stops[[rb$id]] %||% character(0)),
        c(stop_a, stop_b)
      )
      for (vsid in via_stops) {
        key = paste(vsid, family_id(ra$id), family_id(rb$id), sep = "|")
        if (key %in% seen) next
        seen = c(seen, key)
        xfer_fac = Filter(function(f) {
          identical(f$stop_id, vsid) && (f$type %||% "") %in% fac_needs
        }, fac)
        if (stairs || length(fac_needs) == 0) {
          accessible = TRUE; reason = NULL
        } else if (length(xfer_fac) == 0) {
          accessible = FALSE; reason = "no accessible facilities"
        } else {
          n_op = sum(vapply(xfer_fac, function(f) identical(f$status, "operational"), logical(1)))
          if (n_op > 0) {
            accessible = TRUE; reason = NULL
          } else {
            accessible = FALSE
            out_types = unique(vapply(xfer_fac, function(f) tolower(f$type %||% "facility"), character(1)))
            reason = paste0(paste(out_types, collapse = "/"), " out")
          }
        }
        transfers = c(transfers, list(list(
          station_id          = vsid,
          station_name        = name_lookup[[vsid]] %||% vsid,
          from_route          = ra,
          to_route            = rb,
          accessible          = accessible,
          inaccessible_reason = reason
        )))
      }
    }
  }
  if (length(transfers) > 1) {
    acc = vapply(transfers, function(t) t$accessible, logical(1))
    transfers = c(transfers[acc], transfers[!acc])
  }
  transfers
}

# 2. UI ###################################

ui = fluidPage(
  tags$head(
    tags$style(HTML(
      ":root {
        --color-ok: #5cb85c;
        --color-warn: #f0ad4e;
        --color-out: #d9534f;
      }
      .leaflet-container {
        border: 1px solid #ddd;
        border-radius: 4px;
      }
      .well {
        overflow-y: auto;
        max-height: calc(100vh - 80px);
        padding: 12px 14px;
      }
      .facility-cards { }
      .facility-card {
        border: 1px solid #ddd;
        border-radius: 4px;
        padding: 8px 10px;
        margin-bottom: 8px;
      }
      .facility-out {
        border-left: 4px solid var(--color-out);
      }
      .facility-ok {
        border-left: 4px solid var(--color-ok);
      }
      .facility-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 4px;
      }
      .facility-badge-out {
        background: var(--color-out);
        color: white;
        padding: 1px 6px;
        border-radius: 3px;
        font-size: 0.8em;
      }
      .facility-badge-ok {
        background: #2e7d32;
        color: white;
        padding: 1px 6px;
        border-radius: 3px;
        font-size: 0.8em;
      }
      .facility-details {
        font-size: 0.9em;
        color: #555;
        margin-bottom: 4px;
      }
      .facility-time {
        font-size: 0.8em;
        color: #767676;
      }
      .ai-report-box {
        background: #e8f4fd;
        border: 1px solid #b8daff;
        border-radius: 4px;
        padding: 10px 12px;
        margin-bottom: 12px;
        font-size: 0.9em;
        line-height: 1.5;
      }
      .ai-report-box .ai-report-label {
        font-weight: bold;
        margin-bottom: 6px;
        color: #004085;
      }
      .ai-report-error {
        font-style: italic;
        color: #888;
        font-size: 0.85em;
        margin-bottom: 12px;
      }
      .trip-verdict {
        padding: 8px 12px;
        border-radius: 4px;
        font-weight: bold;
        margin-top: 8px;
        margin-bottom: 12px;
        font-size: 0.95em;
      }
      .trip-verdict-ok       { background: #dff0d8; color: #3c763d; border: 1px solid #d6e9c6; }
      .trip-verdict-warn     { background: #fcf8e3; color: #8a6d3b; border: 1px solid #faebcc; }
      .trip-verdict-blocked  { background: #f2dede; color: #a94442; border: 1px solid #ebccd1; }
      .trip-station-card {
        border: 1px solid #ddd;
        border-radius: 4px;
        padding: 8px 10px;
        margin-bottom: 6px;
        font-size: 0.9em;
      }
      .trip-station-ok      { border-left: 4px solid var(--color-ok); }
      .trip-station-warn    { border-left: 4px solid var(--color-warn); }
      .trip-station-blocked { border-left: 4px solid var(--color-out); }
      .trip-station-nodata  { border-left: 4px solid #aaa; }
      .trip-station-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 4px;
      }
      .trip-station-name   { font-weight: bold; }
      .trip-status-text    { color: #767676; font-size: 0.82em; }
      .trip-out-detail     { margin-top: 4px; font-size: 0.85em; color: #555; }
      .trip-alt-text       { color: #004085; font-style: italic; margin-top: 2px; }
      .trip-perm-warning   { color: var(--color-out); font-size: 0.85em; margin-top: 4px; }
      .trip-line-verdicts {
        display: flex;
        flex-direction: column;
        gap: 5px;
        margin-top: 5px;
        margin-bottom: 2px;
      }
      .trip-line-verdict-item {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .trip-out-section {
        margin-top: 7px;
        padding-top: 6px;
        border-top: 1px solid #eee;
      }
      .trip-out-section-label {
        font-size: 0.75em;
        color: #999;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 4px;
      }

      .trip-connector {
        display: flex;
        flex-direction: column;
        align-items: center;
        margin: 2px 0;
      }
      .trip-connector-line {
        width: 2px;
        height: 10px;
        background: #ccc;
      }
      .trip-connector-badges {
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
        justify-content: center;
        padding: 3px 0;
      }
      .trip-line-badge {
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.78em;
        font-weight: bold;
        white-space: nowrap;
      }
      .trip-line-badge-unknown   { background: #888; color: white; }
      .trip-line-badge-transfer  { background: white; color: #555; border: 1px dashed #aaa; }
      .trip-line-badge-concourse { background: #f0f0f0; color: #555; border: 1px solid #ccc; }
      .trip-connector-transfers {
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 3px 8px;
        align-items: center;
      }
      .trip-transfer-option { font-size: 0.78em; }
      .trip-transfer-option.inaccessible { opacity: 0.45; }
      .trip-transfer-header { display: flex; align-items: baseline; justify-content: center; gap: 4px; }
      .trip-transfer-label { font-size: 0.85em; font-weight: bold; color: #999; letter-spacing: 0.03em; text-transform: uppercase; }
      .trip-transfer-station { color: #444; font-weight: 500; }
      .trip-transfer-badges { display: flex; align-items: center; justify-content: center; gap: 5px; margin-top: 2px; }
      .trip-transfer-arrow { color: #aaa; }
      .trip-transfer-note { color: #888; font-style: italic; }
      .trip-transfer-or { font-size: 0.75em; color: #aaa; padding: 1px 2px; }
      #trip_needs .shiny-options-group { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin-top: -4px; }
      #trip_needs .checkbox { margin-top: 2px; margin-bottom: 2px; }
      #trip_needs label { font-size: 0.88em; }
      .trip-results        { }
      /* Remove default white box background from Leaflet divIcon (cluster markers) */
      .mbta-cluster { background: none; border: none; }
      /* Tighten Bootstrap form-group spacing inside trip checker */
      #trip-tab-content .form-group { margin-bottom: 6px; }
      /* Reduce Bootstrap default 20px gap below tab headers */
      .well .nav-tabs { margin-bottom: 8px; }
      /* Drag-to-reorder station list */
      .trip-sortable-container { margin-top: 4px; margin-bottom: 4px; min-height: 28px; }
      .trip-sortable-item {
        display: flex; align-items: center; gap: 6px;
        padding: 4px 8px; margin-bottom: 3px;
        background: white; border: 1px solid #ddd; border-radius: 3px;
        font-size: 0.88em;
      }
      .trip-sortable-item .drag-handle { cursor: grab; color: #ccc; flex-shrink: 0; }
      .trip-sortable-item .drag-handle:hover { color: #999; }
      .trip-sortable-item .item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .trip-sortable-item .item-remove {
        cursor: pointer; color: #ccc; flex-shrink: 0;
        border: none; background: none; padding: 0; font-size: 1.1em; line-height: 1;
      }
      .trip-sortable-item .item-remove:hover { color: #d9534f; }
      .sortable-ghost { opacity: 0.35; }
      .outage-history-panel { margin-top: 14px; margin-bottom: 14px; border: 1px solid #ddd; border-radius: 4px; }
      .outage-history-panel > summary {
        cursor: pointer; font-weight: 600; font-size: 0.88em; color: #444;
        padding: 6px 10px; background: #f5f5f5; border-radius: 4px;
        user-select: none; list-style: none; display: flex; align-items: center; gap: 6px;
      }
      .outage-history-panel > summary::before { content: '▶'; font-size: 0.7em; color: #888; }
      .outage-history-panel > summary:hover { background: #ebebeb; color: #222; }
      details[open].outage-history-panel > summary {
        border-radius: 4px 4px 0 0; border-bottom: 1px solid #ddd;
      }
      details[open].outage-history-panel > summary::before { content: '▼'; }
      #outage_history_plot .hovertext { display: none !important; }
      #gantt-tooltip {
        display: none; position: fixed; z-index: 9999;
        background: rgba(40,40,40,0.88); color: white;
        border-radius: 4px; padding: 5px 9px;
        font-size: 11px; line-height: 1.5;
        pointer-events: none; white-space: nowrap;
        transform: translateX(-50%);
      }
      #gantt-tooltip::after {
        content: ''; position: absolute;
        top: 100%; left: 50%; transform: translateX(-50%);
        border: 5px solid transparent;
        border-top-color: rgba(40,40,40,0.88);
      }
    ")),
    tags$div(id = "gantt-tooltip"),
    tags$script(src = "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"),
    tags$script(HTML("
      $(document).on('shiny:value', function(e) {
        if (e.name !== 'trip_station_sortable') return;
        setTimeout(function() {
          var el = document.getElementById('trip-sortable-list');
          if (!el) return;
          Sortable.create(el, {
            animation: 150,
            handle: '.drag-handle',
            ghostClass: 'sortable-ghost',
            onEnd: function() {
              var ids = [].slice.call(el.querySelectorAll('[data-sid]'))
                .map(function(item) { return item.getAttribute('data-sid'); });
              Shiny.setInputValue('trip_order', ids, {priority: 'event'});
            }
          });
        }, 50);
      });
    ")),
    tags$script(HTML("
      document.addEventListener('toggle', function(e) {
        if (e.target.tagName !== 'DETAILS' || !e.target.open) return;
        var plt = e.target.querySelector('.plotly-graph-div');
        if (plt) Plotly.Plots.resize(plt);
      }, true);
    "))
  ),
  titlePanel("MBTA Accessibility Tracker"),
  sidebarLayout(
    sidebarPanel(
      p(strong("System summary:"), textOutput("summary", inline = TRUE)),
      tabsetPanel(id = "sidebar_tabs",
        # --- Tab 1: single-station view ---
        tabPanel("Station",
          br(),
          selectizeInput("station_search", "Find a station:",
            choices = NULL, options = list(placeholder = "Type a station name…", dropdownParent = "body")
          ),
          uiOutput("station_title"),
          uiOutput("ai_report"),
          uiOutput("outage_history_section"),
          div(class = "facility-cards", uiOutput("station_facilities"))
        ),
        # --- Tab 2: trip checker ---
        tabPanel("Trip Check",
          div(id = "trip-tab-content",
            checkboxGroupInput("trip_needs", "I can use:",
              choices = c(
                "Elevator"      = "ELEVATOR",
                "Escalator"     = "ESCALATOR",
                "Portable lift" = "PORTABLE_BOARDING_LIFT",
                "Stairs"        = "STAIRS"
              ),
              selected = c("ELEVATOR", "ESCALATOR", "STAIRS")
            ),
            selectizeInput("trip_add_station", NULL,
              choices = NULL, options = list(placeholder = "Type a station name…", dropdownParent = "body")
            ),
            uiOutput("trip_station_sortable"),
            div(style = "display:flex;gap:6px;margin-top:2px;margin-bottom:4px;",
              actionButton("trip_check_btn", "Check Trip",
                class = "btn-primary btn-sm", style = "flex:1;"
              ),
              actionButton("trip_clear_btn", "Clear",
                class = "btn-default btn-sm"
              )
            ),
            div(class = "trip-results", uiOutput("trip_results"))
          )
        )
      ),
      width = 4
    ),
    mainPanel(
      leafletOutput("map", height = "calc(100vh - 80px)"),
      width = 8
    )
  )
)

# 3. SERVER ###################################

server = function(input, output, session) {

  # One reactive that fetches data; invalidateLater triggers background refresh
  app_data = reactive({
    invalidateLater(300000)  # 5 minutes
    get_app_data()
  })

  # Selected station (from map click or search bar)
  selected_station = reactiveVal(NULL)

  # Populate station search dropdown once data is available
  observe({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) return()
    choices = vapply(stations, function(s) s$id, character(1))
    names(choices) = vapply(stations, function(s) s$name %||% s$id, character(1))
    choices = choices[order(names(choices))]
    updateSelectizeInput(session, "station_search", choices = c(Choose = "", choices), server = TRUE)
  })

  # When a station is picked from the search bar, zoom the map and select it
  observeEvent(input$station_search, {
    id = input$station_search
    if (is.null(id) || id == "") return()
    d = app_data()
    for (s in d$stations) {
      if (identical(s$id, id)) {
        leafletProxy("map") %>% setView(s$lon, s$lat, zoom = 15)
        break
      }
    }
    selected_station(id)
  }, ignoreInit = TRUE)

  # System summary text
  output$summary = renderText({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) return("Loading… or check API key and network.")
    n_stations = length(stations)
    n_with_outages = sum(vapply(stations, function(s) (s$n_out_of_service %||% 0L) > 0L, logical(1)))
    paste0(n_stations, " stations; ", n_with_outages, " with at least one outage.")
  })

  # Map: render basemap and legend once — no app_data() dependency so zoom/pan are never reset
  output$map = renderLeaflet({
    leaflet() %>%
      addProviderTiles("CartoDB.Positron") %>%
      setView(-71.06, 42.36, zoom = 11) %>%
      addControl(
        position = "bottomright",
        html = paste0(
          '<div class="info legend" style="background:white;padding:8px 10px;border-radius:4px;line-height:1.6;font-size:0.85em;">',
          '<strong>Stations</strong><br>',
          legend_item(color_ok,   "\u2713", "All operational"),
          legend_item(color_warn, "!",      "Some outages"),
          legend_item(color_out,  "\u2717", "All out"),
          '<div style="border-top:1px solid #ddd;margin-top:5px;padding-top:5px;">',
          '<strong>Clusters</strong><br>',
          legend_item(color_ok,   "8",   "N = all clear"),
          legend_item(color_warn, "2/8", "outages / total"),
          '</div></div>'
        )
      )
  })

  # Update markers on each data refresh — preserves zoom/pan position
  observe({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) return()

    st = dplyr::bind_rows(lapply(stations, as.data.frame))

    st$status_group = dplyr::case_when(
      (st$n_out_of_service %||% 0) == 0 ~ "all_ok",
      (st$n_operational %||% 0) == 0    ~ "all_out",
      TRUE                               ~ "some_out"
    )
    st$fill = dplyr::case_when(
      st$status_group == "all_ok"  ~ color_ok,
      st$status_group == "all_out" ~ color_out,
      TRUE                         ~ color_warn
    )
    st$symbol = dplyr::case_when(
      st$status_group == "all_ok"  ~ "\u2713",
      st$status_group == "all_out" ~ "\u2717",
      TRUE                         ~ "!"
    )

    # Build SVG data URI icon for each station.
    # URLencode the full SVG so that #, <, >, &, spaces etc. are all safely encoded.
    icon_uris = mapply(function(fill, sym) {
      svg = paste0(
        "<svg xmlns='http://www.w3.org/2000/svg' width='22' height='22'>",
        "<circle cx='11' cy='11' r='9' fill='", fill,
        "' stroke='#bbbbbb' stroke-width='1.5' fill-opacity='0.9'/>",
        "<text x='11' y='15.5' text-anchor='middle' fill='white' ",
        "font-size='11' font-weight='bold' font-family='sans-serif'>",
        sym, "</text></svg>"
      )
      paste0("data:image/svg+xml,", utils::URLencode(svg, reserved = TRUE))
    }, st$fill, st$symbol, SIMPLIFY = TRUE)

    # Cluster icon: green ✓ (no outages) or orange with outage count
    cluster_js = JS("
      function(cluster) {
        var markers = cluster.getAllChildMarkers();
        var total = markers.length;
        var outageCount = 0;
        markers.forEach(function(m) {
          var parts = (m.options.layerId || '').split('___');
          if (parseInt(parts[1] || '0') > 0) outageCount++;
        });
        var color = outageCount === 0 ? '#5cb85c' : '#f0ad4e';
        // Green: total count. Orange: outages/total fraction.
        var label = outageCount === 0 ? String(total) : (outageCount + '/' + total);
        var size = label.length > 3 ? 42 : 34;
        var fontSize = label.length > 3 ? '11' : '13';
        return L.divIcon({
          html: '<div style=\"width:' + size + 'px;height:' + size + 'px;' +
                'border-radius:50%;background:' + color + ';' +
                'border:2px solid rgba(255,255,255,0.5);' +
                'display:flex;align-items:center;justify-content:center;' +
                'font-size:' + fontSize + 'px;font-weight:bold;color:white;' +
                'box-shadow:0 1px 4px rgba(0,0,0,0.25);\">' + label + '</div>',
          className: 'mbta-cluster',
          iconSize: L.point(size, size, true)
        });
      }
    ")

    leafletProxy("map", data = st) %>%
      clearGroup("stations") %>%
      addMarkers(
        lng     = ~lon,
        lat     = ~lat,
        layerId = ~paste0(id, "___", n_out_of_service),
        label   = ~name,
        group   = "stations",
        icon = icons(
          iconUrl     = icon_uris,
          iconWidth   = 22, iconHeight   = 22,
          iconAnchorX = 11, iconAnchorY  = 11
        ),
        clusterOptions = markerClusterOptions(
          iconCreateFunction  = cluster_js,
          spiderfyOnMaxZoom   = FALSE,
          showCoverageOnHover = FALSE,
          zoomToBoundsOnClick = TRUE,
          maxClusterRadius    = 50,
          disableClusteringAtZoom = 13
        )
      )
  })

  # Draw route lines on the map when a trip is checked (trip-context only)
  observe({
    proxy = leafletProxy("map")

    if (!show_trip_results()) {
      proxy %>% clearGroup("route_lines")
      return()
    }

    results = trip_check_results()
    if (is.null(results)) {
      proxy %>% clearGroup("route_lines")
      return()
    }

    # Collect unique route IDs for bulk shape fetch (direct routes + transfer legs)
    route_info = list()
    for (seg in results$segment_routes) {
      for (rt in seg$routes) {
        rid = rt$id %||% ""
        if (nchar(rid) > 0 && is.null(route_info[[rid]])) route_info[[rid]] = rt
      }
      for (t in seg$transfers %||% list()) {
        for (rt in list(t$from_route, t$to_route)) {
          rid = rt$id %||% ""
          if (nchar(rid) > 0 && is.null(route_info[[rid]])) route_info[[rid]] = rt
        }
      }
    }

    proxy %>% clearGroup("route_lines")
    if (length(route_info) == 0) return()

    # Build station coord lookup and capture cached shapes before entering onFlushed
    d = isolate(app_data())
    coord_lookup = list()
    for (s in d$stations) {
      sid = s$id %||% ""
      if (nchar(sid) > 0)
        coord_lookup[[sid]] = c(as.numeric(s$lat %||% 0), as.numeric(s$lon %||% 0))
    }
    cached_shapes = d$route_shapes %||% list()
    segment_list = results$segment_routes

    # Fit map to the bounding box of the trip stations
    trip_lats = vapply(results$station_results, function(sr) {
      coord_lookup[[sr$id %||% ""]][[1]] %||% NA_real_
    }, numeric(1))
    trip_lngs = vapply(results$station_results, function(sr) {
      coord_lookup[[sr$id %||% ""]][[2]] %||% NA_real_
    }, numeric(1))
    trip_lats = trip_lats[!is.na(trip_lats)]
    trip_lngs = trip_lngs[!is.na(trip_lngs)]
    if (length(trip_lats) >= 2) {
      proxy %>% fitBounds(
        lng1 = min(trip_lngs), lat1 = min(trip_lats),
        lng2 = max(trip_lngs), lat2 = max(trip_lats),
        options = list(padding = c(60, 60))
      )
    }

    session$onFlushed(function() {
      shapes = if (length(cached_shapes) > 0) {
        cached_shapes
      } else {
        tryCatch(fetch_route_shapes(as.list(names(route_info))), error = function(e) list())
      }

      p = leafletProxy("map", deferUntilFlush = FALSE)
      for (seg in segment_list) {
        draw_polyline = function(rt, from_id, to_id, dashed = FALSE) {
          rid = rt$id %||% ""
          if (nchar(rid) == 0) return()
          color = if (!is.null(rt$color) && nchar(rt$color %||% "") == 6)
                    paste0("#", rt$color) else "#888"
          fc = coord_lookup[[from_id]]
          tc = coord_lookup[[to_id]]
          for (coords in shapes[[rid]] %||% list()) {
            if (length(coords) < 2) next
            lats = vapply(coords, function(pt) as.numeric(pt[[1]]), numeric(1))
            lngs = vapply(coords, function(pt) as.numeric(pt[[2]]), numeric(1))
            if (!is.null(fc) && !is.null(tc)) {
              i_from = which.min((lats - fc[1])^2 + (lngs - fc[2])^2)
              i_to   = which.min((lats - tc[1])^2 + (lngs - tc[2])^2)
              # Skip shapes that don't pass near both stations (wrong branch)
              dist_from = (lats[i_from] - fc[1])^2 + (lngs[i_from] - fc[2])^2
              dist_to   = (lats[i_to]   - tc[1])^2 + (lngs[i_to]   - tc[2])^2
              if (dist_from > 0.001 || dist_to > 0.001) next
              lats = lats[min(i_from, i_to):max(i_from, i_to)]
              lngs = lngs[min(i_from, i_to):max(i_from, i_to)]
            }
            if (length(lats) < 2) next
            p <<- p %>% addPolylines(
              lng = lngs, lat = lats,
              color = color, weight = 3,
              opacity = if (dashed) 0.5 else 0.7,
              dashArray = if (dashed) "6,5" else NULL,
              group = "route_lines"
            )
          }
        }

        for (rt in seg$routes) draw_polyline(rt, seg$from_id, seg$to_id)

        for (t in seg$transfers %||% list()) {
          draw_polyline(t$from_route, seg$from_id, t$station_id, dashed = TRUE)
          draw_polyline(t$to_route,   t$station_id, seg$to_id,   dashed = TRUE)
        }
      }
    }, once = TRUE)
  })

  observeEvent(input$map_marker_click, {
    # layerId is encoded as "station_id___outage_count" — strip the suffix
    id = strsplit(input$map_marker_click$id, "___")[[1]][1]
    if (identical(input$sidebar_tabs, "Trip Check")) {
      current = trip_stations()
      if (!(id %in% current)) trip_stations(c(current, id))
    } else {
      selected_station(id)
      updateSelectizeInput(session, "station_search", selected = id)
    }
  })

  # --- Trip checker ---

  trip_stations = reactiveVal(character(0))

  # Populate the trip "add station" dropdown from the same data as the map
  observe({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) return()
    choices = vapply(stations, function(s) s$id, character(1))
    names(choices) = vapply(stations, function(s) s$name %||% s$id, character(1))
    choices = choices[order(names(choices))]
    updateSelectizeInput(session, "trip_add_station", choices = choices, selected = character(0), server = TRUE)
  })

  # Auto-add when a station is selected from the dropdown
  observeEvent(input$trip_add_station, {
    id = input$trip_add_station
    if (is.null(id) || id == "") return()
    current = trip_stations()
    if (!(id %in% current)) trip_stations(c(current, id))
    updateSelectizeInput(session, "trip_add_station", selected = character(0))
  }, ignoreInit = TRUE)

  # Render drag-to-reorder station list
  output$trip_station_sortable = renderUI({
    ids = trip_stations()
    if (length(ids) == 0) {
      return(tags$div(class = "trip-sortable-container",
        tags$span(style = "color:#bbb;font-size:0.85em;", "No stations added yet")
      ))
    }
    d = isolate(app_data())
    name_lookup = setNames(
      vapply(d$stations, function(s) s$name %||% s$id, character(1)),
      vapply(d$stations, function(s) s$id, character(1))
    )
    items = lapply(ids, function(sid) {
      tags$div(class = "trip-sortable-item", `data-sid` = sid,
        tags$span(class = "drag-handle", "\u2630"),
        tags$span(class = "item-name", name_lookup[[sid]] %||% sid),
        tags$button(class = "item-remove",
          onclick = paste0("Shiny.setInputValue('trip_remove_id','", sid, "',{priority:'event'})"),
          "\u00d7")
      )
    })
    tags$div(id = "trip-sortable-list", class = "trip-sortable-container", tagList(items))
  })

  # Sync reordered list back to trip_stations
  observeEvent(input$trip_order, {
    new_order = input$trip_order
    current   = trip_stations()
    if (length(new_order) == length(current) && setequal(new_order, current))
      trip_stations(new_order)
  }, ignoreInit = TRUE)

  # Remove station via × button
  observeEvent(input$trip_remove_id, {
    id = input$trip_remove_id
    if (is.null(id) || id == "") return()
    trip_stations(trip_stations()[trip_stations() != id])
  }, ignoreInit = TRUE)

  # Tracks whether trip results should be displayed
  show_trip_results = reactiveVal(FALSE)
  trip_check_trigger = reactiveVal(0)

  observeEvent(input$trip_check_btn, {
    show_trip_results(TRUE)
    trip_check_trigger(trip_check_trigger() + 1)
  })

  # Re-run check when "I can use" toggles change (only if results already showing)
  observeEvent(input$trip_needs, {
    if (show_trip_results()) trip_check_trigger(trip_check_trigger() + 1)
  }, ignoreInit = TRUE)

  # Clear all stations and hide results
  observeEvent(input$trip_clear_btn, {
    trip_stations(character(0))
    show_trip_results(FALSE)
    leafletProxy("map") %>% clearGroup("route_lines")
  })

  # Compute trip results when Check Trip is pressed or "I can use" changes
  trip_check_results = eventReactive(trip_check_trigger(), {
    ids   = isolate(trip_stations())
    needs = isolate(input$trip_needs)
    if (length(ids) == 0 || is.null(needs) || length(needs) == 0) return(NULL)

    d              = isolate(app_data())
    fac            = d$facilities
    stations       = d$stations
    station_routes = d$station_routes %||% list()
    flm            = d$facility_line_mapping %||% list()
    stairs         = "STAIRS" %in% needs
    fac_needs      = needs[needs != "STAIRS"]

    name_lookup = setNames(
      vapply(stations, function(s) s$name %||% s$id, character(1)),
      vapply(stations, function(s) s$id, character(1))
    )
    wb_lookup = setNames(
      vapply(stations, function(s) as.integer(s$wheelchair_boarding %||% 0L), integer(1)),
      vapply(stations, function(s) s$id, character(1))
    )

    # Compute segment routes FIRST — needed for per-line verdict calculation
    segment_routes = if (length(ids) >= 2) {
      lapply(seq_len(length(ids) - 1), function(i) {
        routes_a = station_routes[[ids[i]]]   %||% list()
        routes_b = station_routes[[ids[i+1]]] %||% list()
        ids_a = vapply(routes_a, function(r) r$id %||% "", character(1))
        ids_b = vapply(routes_b, function(r) r$id %||% "", character(1))
        shared = routes_a[ids_a %in% intersect(ids_a, ids_b)]

        # If no direct route, check for concourse connections
        concourse_via = NULL
        if (length(shared) == 0 && length(station_routes) > 0) {
          # Route A → concourse partner of B → walk to B
          partner_b = CONCOURSE_PAIRS[[ids[i + 1]]]
          if (!is.null(partner_b)) {
            routes_p = station_routes[[partner_b$id]] %||% list()
            ids_p    = vapply(routes_p, function(r) r$id %||% "", character(1))
            via      = routes_a[ids_a %in% intersect(ids_a, ids_p)]
            if (length(via) > 0) { shared = via; concourse_via = partner_b }
          }
          # Route A → walk from A's concourse partner → route to B
          if (is.null(concourse_via)) {
            partner_a = CONCOURSE_PAIRS[[ids[i]]]
            if (!is.null(partner_a)) {
              routes_p = station_routes[[partner_a$id]] %||% list()
              ids_p    = vapply(routes_p, function(r) r$id %||% "", character(1))
              via      = routes_p[ids_p %in% intersect(ids_p, ids_b)]
              if (length(via) > 0) { shared = via; concourse_via = partner_a }
            }
          }
        }

        if (length(shared) > 1) {
          ord = order(
            vapply(shared, function(r) as.numeric(r$route_type %||% 99), numeric(1)),
            vapply(shared, function(r) r$id %||% "", character(1))
          )
          shared = shared[ord]
        }
        no_service = c(
          if (length(ids_a) == 0) name_lookup[[ids[i]]]   %||% ids[i]   else NULL,
          if (length(ids_b) == 0) name_lookup[[ids[i+1]]] %||% ids[i+1] else NULL
        )
        transfers = if (length(shared) == 0 && length(no_service) == 0 && length(station_routes) > 0)
          find_transfers(ids[i], ids[i+1], station_routes, fac, fac_needs, stairs, name_lookup)
        else list()
        list(from_id = ids[i], to_id = ids[i+1], routes = shared, concourse_via = concourse_via, transfers = transfers, no_service = no_service)
      })
    } else list()

    flm_loaded = length(flm) > 0

    # Per-station accessibility results with per-line verdicts
    station_results = lapply(seq_along(ids), function(i) {
      sid = ids[[i]]
      station_fac = Filter(function(f) {
        identical(f$stop_id, sid) && (f$type %||% "") %in% fac_needs
      }, fac)

      # Connecting route IDs adjacent to this station
      seg_route_ids = function(seg) vapply(seg$routes %||% list(), function(r) r$id %||% "", character(1))
      relevant_route_ids = unique(c(
        if (i > 1)           seg_route_ids(segment_routes[[i - 1]]) else character(0),
        if (i < length(ids)) seg_route_ids(segment_routes[[i]])     else character(0)
      ))
      relevant_route_ids = relevant_route_ids[nchar(relevant_route_ids) > 0]

      # Per-line verdicts (only when flm and connecting routes are available)
      line_verdicts = if (flm_loaded && length(relevant_route_ids) > 0) {
        lapply(relevant_route_ids, function(rid) compute_line_verdict(rid, station_fac, flm, stairs))
      } else list()

      n_total = length(station_fac)
      n_op    = sum(vapply(station_fac, function(f) identical(f$status, "operational"), logical(1)))
      n_out   = n_total - n_op

      if (length(line_verdicts) > 0) {
        # Derive status from per-line analysis
        verdicts      = vapply(line_verdicts, function(lv) lv$verdict, character(1))
        any_uncertain = any(vapply(line_verdicts, function(lv) lv$has_uncertain, logical(1)))
        worst = if (any(verdicts %in% c("blocked", "no_facilities"))) "blocked"
                else if ("traversable" %in% verdicts || any_uncertain)  "traversable"
                else                                                     "clear"
        status  = if (worst == "blocked") "blocked" else if (worst == "traversable") "warn" else "ok"
        is_warn = identical(status, "warn")
      } else {
        # Fallback: simple operational count (stairs prevents "blocked")
        status = if (n_total == 0) {
          if (stairs) "ok" else "no_data"
        } else if (n_op > 0) {
          "ok"
        } else {
          if (stairs) "warn" else "blocked"
        }
        is_warn = (status %in% c("ok", "warn") && n_out > 0)
      }

      out_fac = Filter(function(f) identical(f$status, "out_of_service"), station_fac)
      wb = if (sid %in% names(wb_lookup)) as.integer(wb_lookup[[sid]]) else 0L

      list(
        id = sid, name = name_lookup[[sid]] %||% sid,
        status = status, is_warn = is_warn,
        n_total = n_total, n_operational = n_op, n_out = n_out,
        out_facilities = out_fac,
        wheelchair_boarding = wb,
        line_verdicts = line_verdicts,
        relevant_route_ids = relevant_route_ids
      )
    })

    list(
      station_results       = station_results,
      segment_routes        = segment_routes,
      station_routes_loaded = length(station_routes) > 0,
      flm_loaded            = flm_loaded,
      stairs                = stairs
    )
  })

  output$trip_results = renderUI({
    if (!show_trip_results()) {
      return(p(em("Add stations above, then press \u201cCheck Trip\u201d.")))
    }

    results = trip_check_results()
    if (is.null(results)) {
      return(p(em("Add stations above, then press \u201cCheck Trip\u201d.")))
    }

    station_results       = results$station_results
    segment_routes        = results$segment_routes
    station_routes_loaded = results$station_routes_loaded %||% FALSE
    flm_loaded            = results$flm_loaded %||% FALSE
    n = length(station_results)
    if (n == 0) return(p(em("Add stations above, then press \u201cCheck Trip\u201d.")))

    d   = app_data()
    flm = d$facility_line_mapping %||% list()

    # Flat route_id → route info lookup (for badge colors)
    route_info_lookup = list()
    for (routes_at_stop in (d$station_routes %||% list())) {
      for (rt in routes_at_stop) {
        if (!is.null(rt$id) && is.null(route_info_lookup[[rt$id]]))
          route_info_lookup[[rt$id]] = rt
      }
    }

    # Overall verdict
    statuses  = vapply(station_results, function(r) r$status,  character(1))
    warns     = vapply(station_results, function(r) r$is_warn, logical(1))
    n_blocked = sum(statuses == "blocked")
    n_warn    = sum(warns)

    verdict_class = if (n_blocked > 0) "trip-verdict-blocked"
                    else if (n_warn > 0) "trip-verdict-warn"
                    else "trip-verdict-ok"
    verdict_text = if (n_blocked > 0)
      paste0("\u2717 ", n_blocked, " station(s) blocked for your needs")
    else if (n_warn > 0)
      paste0("\u26a0 ", n_warn, " station(s) have partial outages")
    else
      "\u2713 Trip looks clear"

    # Build interleaved station nodes + segment connectors
    items = list()
    for (i in seq_len(n)) {
      r = station_results[[i]]
      relevant_route_ids = r$relevant_route_ids %||% character(0)

      card_class = if (r$status == "blocked")  "trip-station-blocked"
                   else if (r$is_warn)          "trip-station-warn"
                   else if (r$status == "ok")   "trip-station-ok"
                   else                          "trip-station-nodata"
      icon = if (r$status == "blocked") "\u2717"
             else if (r$is_warn)         "\u26a0"
             else if (r$status == "ok")  "\u2713"
             else                         "?"

      status_text = if (length(r$line_verdicts) > 0) {
        if (r$status == "blocked")  "Route blocked"
        else if (r$is_warn)         paste0(r$n_operational, "/", r$n_total, " facilities operational")
        else                        "Clear"
      } else {
        if (r$status == "blocked")      "No operational facilities"
        else if (r$status == "no_data") "No facility data"
        else if (r$is_warn)             paste0(r$n_operational, "/", r$n_total, " facilities operational")
        else                            paste0("All ", r$n_total, " facilities operational")
      }

      perm_warning = if (identical(r$wheelchair_boarding, 2L))
        tags$div(class = "trip-perm-warning", "\u26a0 Not wheelchair accessible")
      else NULL

      # Per-line verdict row
      line_verdict_row = if (length(r$line_verdicts) > 0) {
        lv_items = lapply(r$line_verdicts, function(lv) {
          rt    = route_info_lookup[[lv$route_id]]
          bg    = if (!is.null(rt) && nchar(rt$color %||% "") == 6) paste0("#", rt$color) else "#888"
          fg    = if (!is.null(rt) && nchar(rt$text_color %||% "") == 6) paste0("#", rt$text_color) else "#fff"
          rname = if (!is.null(rt)) rt$name %||% lv$route_id else lv$route_id
          # Promote to "uncertain" when has_uncertain overrides an otherwise-clear verdict
          v = if (lv$has_uncertain && lv$verdict == "clear") "uncertain" else lv$verdict
          v_icon  = switch(v, clear="\u2713", traversable="\u26a0", blocked="\u2717",
                              no_facilities="?", uncertain="\u26a0", "?")
          v_color = switch(v, clear="#2e7d32", traversable="#8a6d3b", blocked=color_out,
                              no_facilities="#888", uncertain="#8a6d3b", "#888")
          v_label = switch(v,
            clear         = "Clear",
            traversable   = "Outage (traversable)",
            blocked       = "Route blocked",
            no_facilities = "No accessible facilities",
            uncertain     = "Potentially affected",
            "Unknown")
          tags$div(class = "trip-line-verdict-item",
            tags$span(class = "trip-line-badge",
                      style = paste0("background:", bg, ";color:", fg, ";"),
                      rname),
            tags$span(style = paste0("color:", v_color, ";font-size:0.82em;"), paste(v_icon, v_label))
          )
        })
        tags$div(class = "trip-line-verdicts", tagList(lv_items))
      } else NULL

      out_details = if (length(r$out_facilities) > 0) {
        tagList(lapply(r$out_facilities, function(f) {
          type_lbl = facility_type_label(as.character(f$type %||% ""))
          fname    = as.character(f$name %||% f$short_name %||% "")
          alt      = if (!is.null(f$alert) && !is.null(f$alert$description))
                       as.character(f$alert$description) else NULL

          fac_info   = if (!is.null(f$id)) flm[[as.character(f$id)]] else NULL
          fac_lines  = as.character(unlist(fac_info$lines %||% list()))
          fac_source = fac_info$source %||% ""
          matched    = if (length(fac_lines) > 0 && fac_source != "unresolved")
                         intersect(fac_lines, relevant_route_ids) else character(0)

          line_badges = if (length(matched) > 0) {
            lapply(matched, function(rid) {
              rt = route_info_lookup[[rid]]
              if (is.null(rt)) return(NULL)
              bg = if (!is.null(rt$color) && nchar(rt$color %||% "") == 6) paste0("#", rt$color) else "#888"
              fg = if (!is.null(rt$text_color) && nchar(rt$text_color %||% "") == 6) paste0("#", rt$text_color) else "#fff"
              tags$span(class = "trip-line-badge",
                        style = paste0("background:", bg, ";color:", fg, ";margin-right:4px;"),
                        rt$name %||% rid)
            })
          } else NULL

          # Note for outages on lines not in this trip segment
          off_route_note = if (
            flm_loaded && length(relevant_route_ids) > 0 &&
            length(fac_lines) > 0 && fac_source != "unresolved" && length(matched) == 0
          ) " \u2014 not on your route" else ""

          # Hedge for unresolved facilities (line affiliation unknown)
          uncertain_note = if (fac_source == "unresolved" && length(relevant_route_ids) > 0)
            tags$span(style = paste0("color:", color_warn, ";font-size:0.82em;margin-left:4px;"),
                      "\u26a0 Potentially on your route")
          else NULL

          tags$div(class = "trip-out-detail",
            if (!is.null(line_badges)) tagList(line_badges) else NULL,
            tags$span(paste0(type_lbl, if (nchar(fname) > 0) paste0(' "', fname, '"') else "",
                             " \u2014 out of service", off_route_note)),
            uncertain_note,
            if (!is.null(alt)) tags$div(class = "trip-alt-text", alt) else NULL
          )
        }))
      } else NULL

      # Wrap outage details in a labeled section when line verdicts are shown above
      out_section = if (!is.null(out_details)) {
        if (!is.null(line_verdict_row)) {
          tags$div(class = "trip-out-section",
            tags$div(class = "trip-out-section-label", "Facility outages"),
            out_details
          )
        } else {
          out_details
        }
      } else NULL

      items = c(items, list(
        tags$div(class = paste("trip-station-card", card_class),
          tags$div(class = "trip-station-header",
            tags$span(class = "trip-station-name", paste0(icon, " ", r$name)),
            tags$span(class = "trip-status-text", status_text)
          ),
          perm_warning,
          line_verdict_row,
          out_section
        )
      ))

      # Segment connector between this station and the next
      if (i < n) {
        seg = segment_routes[[i]]
        line_badge = function(rt) {
          bg = if (!is.null(rt$color) && nchar(rt$color %||% "") == 6)
                 paste0("#", rt$color) else "#888"
          fg = if (!is.null(rt$text_color) && nchar(rt$text_color %||% "") == 6)
                 paste0("#", rt$text_color) else "#fff"
          tags$span(class = "trip-line-badge",
                    style = paste0("background:", bg, ";color:", fg, ";"),
                    rt$name %||% rt$id)
        }
        connector_middle = if (length(seg$routes) > 0) {
          rt_badges = lapply(seg$routes, line_badge)
          concourse_badge = if (!is.null(seg$concourse_via))
            list(tags$span(class = "trip-line-badge trip-line-badge-concourse",
                           paste0("via ", seg$concourse_via$name, " concourse")))
          else NULL
          tags$div(class = "trip-connector-badges", tagList(c(rt_badges, concourse_badge)))
        } else if (!station_routes_loaded) {
          tags$div(class = "trip-connector-badges",
            tags$span(class = "trip-line-badge trip-line-badge-unknown", "Route data unavailable"))
        } else if (length(seg$transfers) > 0) {
          rows = unlist(lapply(seq_along(seg$transfers), function(j) {
            t = seg$transfers[[j]]
            opt_class = paste("trip-transfer-option", if (!t$accessible) "inaccessible" else "")
            note = if (!t$accessible && !is.null(t$inaccessible_reason))
                     tags$span(class = "trip-transfer-note", paste0("(", t$inaccessible_reason, ")"))
                   else NULL
            option = tags$div(class = opt_class,
              tags$div(class = "trip-transfer-header",
                tags$span(class = "trip-transfer-label", "Transfer:"),
                tags$span(class = "trip-transfer-station", t$station_name),
                note
              ),
              tags$div(class = "trip-transfer-badges",
                line_badge(t$from_route),
                tags$span(class = "trip-transfer-arrow", "\u2192"),
                line_badge(t$to_route)
              )
            )
            if (j > 1) list(tags$div(class = "trip-transfer-or", "or"), option)
            else list(option)
          }), recursive = FALSE)
          tags$div(class = "trip-connector-transfers", tagList(rows))
        } else if (length(seg$no_service) > 0) {
          msg = paste0("No rapid transit: ", paste(seg$no_service, collapse = ", "))
          tags$div(class = "trip-connector-badges",
            tags$span(class = "trip-line-badge trip-line-badge-transfer", msg))
        } else {
          tags$div(class = "trip-connector-badges",
            tags$span(class = "trip-line-badge trip-line-badge-transfer", "No connection found"))
        }
        items = c(items, list(
          tags$div(class = "trip-connector",
            tags$div(class = "trip-connector-line"),
            connector_middle,
            tags$div(class = "trip-connector-line")
          )
        ))
      }
    }

    tagList(
      tags$div(class = paste("trip-verdict", verdict_class), verdict_text),
      tagList(items)
    )
  })

  output$station_title = renderUI({
    id = selected_station()
    if (is.null(id)) return(p(em("Click a station on the map.")))
    d = app_data()
    stations = d$stations
    name = id
    wb = 0L
    for (s in stations) {
      if (identical(s$id, id)) {
        name = s$name %||% id
        wb = as.integer(s$wheelchair_boarding %||% 0L)
        break
      }
    }
    title = h4(name)
    if (identical(wb, 2L)) {
      tagList(
        title,
        tags$div(
          style = "color: #d9534f; font-weight: bold; margin-bottom: 8px;",
          "\u26A0 Not wheelchair accessible"
        )
      )
    } else {
      title
    }
  })

  # AI report: use reactiveVal so we can defer the slow Ollama call
  # and let the facility cards flush to the browser first.
  ai_report_text = reactiveVal(NULL)

  observeEvent(selected_station(), {
    id = selected_station()
    if (is.null(id)) {
      ai_report_text(NULL)
      return()
    }
    # Set loading state immediately — this flushes with the facility cards
    ai_report_text("__loading__")
    d = isolate(app_data())
    # Schedule the slow AI call AFTER the current outputs reach the browser
    session$onFlushed(function() {
      report = tryCatch(
        generate_station_report(id, d$facilities, d$stations, d$service_alerts %||% list()),
        error = function(e) paste0("__error__: ", e$message)
      )
      ai_report_text(report)
    }, once = TRUE)
  })

  output$ai_report = renderUI({
    report = ai_report_text()
    if (is.null(report)) return(NULL)
    if (identical(report, "__loading__")) {
      return(tags$div(
        class = "ai-report-box",
        tags$div(class = "ai-report-label", "AI Accessibility Report"),
        tags$p(em("Generating report…"))
      ))
    }
    if (grepl("^__error__:", report)) {
      return(tags$div(class = "ai-report-error", "AI report unavailable. Is Ollama running?"))
    }
    # Parse markdown-style text into HTML tags.
    # Supports • / - / * bullet lines and **bold** within any line.
    render_md = function(text) {
      bold = function(s) HTML(gsub("\\*\\*(.+?)\\*\\*", "<strong>\\1</strong>", s))
      lines = trimws(strsplit(text, "\n")[[1]])
      lines = lines[lines != ""]
      elements = list(); i = 1L
      while (i <= length(lines)) {
        if (grepl("^[•\\-\\*]\\s+", lines[[i]])) {
          j = i
          while (j <= length(lines) && grepl("^[•\\-\\*]\\s+", lines[[j]])) j = j + 1L
          items = sub("^[•\\-\\*]\\s+", "", lines[i:(j - 1L)])
          elements[[length(elements) + 1L]] = tags$ul(lapply(items, function(it) tags$li(bold(it))))
          i = j
        } else {
          elements[[length(elements) + 1L]] = tags$p(bold(lines[[i]]))
          i = i + 1L
        }
      }
      elements
    }
    tags$div(
      class = "ai-report-box",
      tags$div(class = "ai-report-label", "AI Accessibility Report"),
      tagList(render_md(report))
    )
  })

  output$station_facilities = renderUI({
    id = selected_station()
    if (is.null(id)) return(NULL)
    d = app_data()
    fac = d$facilities
    if (length(fac) == 0) return(NULL)
    cards = station_facility_cards(fac, id, flm = d$facility_line_mapping %||% list())
    if (length(cards) == 0) return(p(em("No facility data for this station.")))
    tagList(cards)
  })

  output$outage_history_section = renderUI({
    if (is.null(selected_station())) return(NULL)
    tags$details(
      class = "outage-history-panel",
      tags$summary("Outage History (30 days)"),
      plotlyOutput("outage_history_plot", height = "auto")
    )
  })

  output$outage_history_plot = renderPlotly({
    id = selected_station()
    req(!is.null(id))

    rows = fetch_outage_history(id, 30L)

    empty_plot = function(msg) {
      plot_ly(type = "scatter", mode = "lines") %>%
        layout(
          height = 60,
          annotations = list(list(
            text = msg, showarrow = FALSE,
            xref = "paper", yref = "paper", x = 0.5, y = 0.5,
            font = list(color = "#999", size = 12)
          )),
          xaxis = list(visible = FALSE), yaxis = list(visible = FALSE),
          margin = list(l = 0, r = 0, t = 0, b = 0)
        ) %>% config(displayModeBar = FALSE)
    }

    if (length(rows) == 0) return(empty_plot("No history data for this station."))

    # Look up station name so we can strip it from the (redundant) label prefix
    d = app_data()
    station_nm = id
    for (s in d$stations) {
      if (identical(s$id, id)) { station_nm = s$name %||% id; break }
    }
    strip_station = function(x) {
      prefix = paste0(station_nm, " ")
      ifelse(startsWith(x, prefix), substr(x, nchar(prefix) + 1L, nchar(x)), x)
    }

    fac_name     = sapply(rows, `[[`, "facility_name")
    fac_type     = sapply(rows, `[[`, "facility_type")
    status       = sapply(rows, `[[`, "status")
    logged_at    = as.POSIXct(sapply(rows, `[[`, "logged_at"),
                              format = "%Y-%m-%dT%H:%M:%S", tz = "UTC")
    alert_header = sapply(rows, function(r) r$alert_header %||% "")
    cause        = sapply(rows, function(r) r$cause %||% "")

    df = data.frame(
      facility_name = fac_name,
      facility_type = fac_type,
      status        = status,
      logged_at     = logged_at,
      alert_header  = alert_header,
      cause         = cause,
      stringsAsFactors = FALSE
    )
    df = df[order(df$facility_name, df$logged_at), ]

    window_start = Sys.time() - 30 * 24 * 3600
    now_t        = Sys.time()

    parts = lapply(split(df, df$facility_name), function(fac) {
      fac = fac[order(fac$logged_at), ]
      n   = nrow(fac)
      starts        = fac$logged_at
      starts[1]     = window_start  # first segment always covers full window
      data.frame(
        facility_name = fac$facility_name,
        seg_start     = starts,
        seg_end       = c(if (n > 1) fac$logged_at[-1] else NULL, now_t),
        status        = fac$status,
        alert_header  = fac$alert_header,
        cause         = fac$cause,
        stringsAsFactors = FALSE
      )
    })
    intervals = do.call(rbind, parts)
    intervals$seg_start = .POSIXct(as.numeric(intervals$seg_start), tz = "UTC")
    intervals$seg_end   = .POSIXct(as.numeric(intervals$seg_end),   tz = "UTC")

    intervals$status_label = ifelse(
      intervals$status == "operational", "Operational", "Out of service"
    )
    # Split "Type 123 (description)" → "Type 123<br>(description)" for clean 2-line labels
    wrap_label = function(x, max_desc = 24) {
      m = regexpr("\\s*\\(", x)
      if (m > 0) {
        line1 = trimws(substr(x, 1L, m - 1L))
        line2 = substr(x, m + attr(m, "match.length") - 1L, nchar(x))
        if (nchar(line2) > max_desc) line2 = paste0(substr(line2, 1L, max_desc - 1L), "…")
        return(paste(line1, line2, sep = "<br>"))
      }
      if (nchar(x) > max_desc) x = paste0(substr(x, 1L, max_desc - 1L), "…")
      x
    }
    intervals$label = sapply(strip_station(intervals$facility_name), wrap_label)

    fmt = function(t) format(t, "%b %d", tz = "UTC")
    intervals$hover = paste0(
      fmt(intervals$seg_start), " – ", fmt(intervals$seg_end),
      ifelse(intervals$cause != "" & intervals$cause != "UNKNOWN_CAUSE",
             paste0(" · ", tolower(gsub("_", " ", intervals$cause))), "")
    )

    n_fac    = length(unique(intervals$label))
    chart_h  = max(130, n_fac * 62 + 85)

    epsilon = 1800  # 30 min — covers sub-pixel gaps between adjacent traces
    intervals$seg_end_r = .POSIXct(as.numeric(intervals$seg_end) + epsilon, tz = "UTC")

    op  = intervals[intervals$status == "operational",    ]
    out = intervals[intervals$status == "out_of_service", ]

    # out_of_service drawn first (below), operational drawn second (on top)
    # so operational correctly covers any epsilon overlap at boundaries
    p = plot_ly()
    if (nrow(out) > 0) p = p %>% add_segments(
      data = out,
      x = ~seg_start, xend = ~seg_end_r, y = ~label, yend = ~label,
      line = list(color = "#d9534f", width = 14),
      name = "Out of service",
      text = ~hover, hoverinfo = "text"
    )
    if (nrow(op) > 0) p = p %>% add_segments(
      data = op,
      x = ~seg_start, xend = ~seg_end_r, y = ~label, yend = ~label,
      line = list(color = "#5cb85c", width = 14),
      name = "Operational",
      text = ~hover, hoverinfo = "text"
    )
    p %>%
      layout(
        height     = chart_h,
        xaxis      = list(title = "", range = list(window_start, now_t),
                          tickformat = "%b %d", tickangle = -35, nticks = 5),
        yaxis      = list(title = "", automargin = TRUE, tickfont = list(size = 11)),
        legend     = list(orientation = "h", x = 0, y = -0.22, font = list(size = 11)),
        hoverlabel = list(font = list(size = 11), namelength = 0),
        margin     = list(r = 10, t = 8, b = 55),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)"
      ) %>%
      config(displayModeBar = FALSE) %>%
      htmlwidgets::onRender("
        function(el) {
          var tip = document.getElementById('gantt-tooltip');
          el.on('plotly_hover', function(data) {
            var pt = data.points[0];
            if (!pt || !pt.text) return;
            tip.innerHTML = pt.text;
            tip.style.display = 'block';
            var e = data.event;
            tip.style.left = e.clientX + 'px';
            tip.style.top  = (e.clientY - tip.offsetHeight - 10) + 'px';
          });
          el.on('plotly_unhover', function() {
            tip.style.display = 'none';
          });
        }
      ")
  })
}

# 4. RUN ###################################

shinyApp(ui = ui, server = server)
