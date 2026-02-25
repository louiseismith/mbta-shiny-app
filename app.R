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
script_path = file.path(app_dir, "accessibility_tracker_prototype.py")
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

# Build a list of facility cards (as Shiny tag objects) for one station
station_facility_cards = function(facilities_list, station_id) {
  if (length(facilities_list) == 0) return(list())
  cards = list()
  for (i in seq_along(facilities_list)) {
    f = facilities_list[[i]]
    if (is.null(f$stop_id) || f$stop_id != station_id) next
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

    card = tags$div(
      class = paste("facility-card", if (is_out) "facility-out" else "facility-ok"),
      tags$div(class = "facility-header", tags$strong(type), badge),
      tags$div(class = "facility-details", details),
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
      .trip-list-controls  { display: flex; gap: 4px; margin-top: 4px; margin-bottom: 8px; }
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
      .trip-line-badge-unknown  { background: #888; color: white; }
      .trip-line-badge-transfer { background: white; color: #555; border: 1px dashed #aaa; }
      #trip_needs .shiny-options-group { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin-top: -4px; }
      #trip_needs .checkbox { margin-top: 2px; margin-bottom: 2px; }
      #trip_needs label { font-size: 0.88em; }
      .trip-results        { }
      /* Tighten Bootstrap form-group spacing inside trip checker */
      #trip-tab-content .form-group { margin-bottom: 6px; }
      /* Align Add button baseline with the input field */
      #trip-add-row { display: flex; gap: 6px; align-items: flex-end; }
      #trip-add-row .form-group { flex: 1; margin-bottom: 0; }
      #trip-add-row .btn { margin-bottom: 1px; }
      #trip_station_list { margin-top: 6px; height: 72px; }
      /* Reduce Bootstrap default 20px gap below tab headers */
      .well .nav-tabs { margin-bottom: 8px; }
      /* Consistent 4px gap around controls row */
      .trip-list-controls { margin-top: 2px; margin-bottom: 4px; }
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
            choices = NULL, options = list(placeholder = "Type a station name…")
          ),
          uiOutput("station_title"),
          uiOutput("ai_report"),
          div(class = "facility-cards", uiOutput("station_facilities"))
        ),
        # --- Tab 2: trip checker ---
        tabPanel("Trip Check",
          div(id = "trip-tab-content",
            checkboxGroupInput("trip_needs", "I can use:",
              choices = c(
                "Elevator"      = "ELEVATOR",
                "Escalator"     = "ESCALATOR",
                "Ramp"          = "RAMP",
                "Portable lift" = "PORTABLE_BOARDING_LIFT"
              ),
              selected = c("ELEVATOR", "ESCALATOR", "RAMP", "PORTABLE_BOARDING_LIFT")
            ),
            div(id = "trip-add-row",
              selectizeInput("trip_add_station", NULL,
                choices = NULL, options = list(placeholder = "Type a station name…")
              ),
              actionButton("trip_add_btn", "Add", class = "btn-primary btn-sm")
            ),
            selectInput("trip_station_list", NULL,
              choices = character(0), size = 3, selectize = FALSE, width = "100%"
            ),
            div(class = "trip-list-controls",
              actionButton("trip_up_btn",     "\u2191 Up",     class = "btn-default btn-xs"),
              actionButton("trip_down_btn",   "\u2193 Down",   class = "btn-default btn-xs"),
              actionButton("trip_remove_btn", "\u00d7 Remove", class = "btn-danger btn-xs")
            ),
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
          '<strong>Status</strong><br>',
          legend_item(color_ok,   "\u2713", "All operational"),
          legend_item(color_warn, "!",      "Some outages"),
          legend_item(color_out,  "\u2717", "All out"),
          '</div>'
        )
      )
  })

  # Update markers on each data refresh — preserves zoom/pan position
  observe({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) return()

    st = dplyr::bind_rows(lapply(stations, as.data.frame))

    pal = leaflet::colorFactor(
      palette = c(color_ok, color_warn, color_out),
      domain = c("all_ok", "some_out", "all_out"),
      levels = c("all_ok", "some_out", "all_out")
    )
    st$status_group = dplyr::case_when(
      (st$n_out_of_service %||% 0) == 0 ~ "all_ok",
      (st$n_operational %||% 0) == 0 ~ "all_out",
      TRUE ~ "some_out"
    )
    st$symbol = dplyr::case_when(
      st$status_group == "all_ok"  ~ "\u2713",
      st$status_group == "all_out" ~ "\u2717",
      TRUE ~ "!"
    )

    leafletProxy("map", data = st) %>%
      clearMarkers() %>%
      addCircleMarkers(
        lng = ~lon,
        lat = ~lat,
        radius = 10,
        color = "#bbb",
        weight = 1.5,
        fillColor = ~pal(status_group),
        fillOpacity = 0.85,
        label = ~symbol,
        labelOptions = labelOptions(
          permanent = TRUE,
          direction = "center",
          textOnly = TRUE,
          style = list(
            "color" = "white",
            "font-weight" = "bold",
            "font-size" = "11px"
          )
        )
      ) %>%
      addCircleMarkers(
        lng = ~lon,
        lat = ~lat,
        layerId = ~id,
        radius = 10,
        color = "transparent",
        weight = 0,
        fillColor = "transparent",
        fillOpacity = 0,
        label = ~name
      )
  })

  observeEvent(input$map_marker_click, {
    id = input$map_marker_click$id
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
    updateSelectizeInput(session, "trip_add_station", choices = c(Choose = "", choices), server = TRUE)
  })

  # Add button — append station to list (no duplicates)
  observeEvent(input$trip_add_btn, {
    id = input$trip_add_station
    if (is.null(id) || id == "") return()
    current = trip_stations()
    if (id %in% current) return()
    trip_stations(c(current, id))
  })

  # Remove button — drop selected station
  observeEvent(input$trip_remove_btn, {
    sel = input$trip_station_list
    if (is.null(sel) || sel == "") return()
    current = trip_stations()
    trip_stations(current[current != sel])
  })

  # Move up
  observeEvent(input$trip_up_btn, {
    sel = input$trip_station_list
    if (is.null(sel) || sel == "") return()
    current = trip_stations()
    idx = which(current == sel)
    if (length(idx) == 0 || idx == 1) return()
    current[c(idx - 1, idx)] = current[c(idx, idx - 1)]
    trip_stations(current)
  })

  # Move down
  observeEvent(input$trip_down_btn, {
    sel = input$trip_station_list
    if (is.null(sel) || sel == "") return()
    current = trip_stations()
    idx = which(current == sel)
    if (length(idx) == 0 || idx == length(current)) return()
    current[c(idx, idx + 1)] = current[c(idx + 1, idx)]
    trip_stations(current)
  })

  # Keep the selectInput display in sync with trip_stations()
  observe({
    ids = trip_stations()
    if (length(ids) == 0) {
      updateSelectInput(session, "trip_station_list", choices = character(0))
      return()
    }
    d = app_data()
    name_lookup = setNames(
      vapply(d$stations, function(s) s$name %||% s$id, character(1)),
      vapply(d$stations, function(s) s$id, character(1))
    )
    choices = setNames(ids, vapply(ids, function(i) name_lookup[[i]] %||% i, character(1)))
    sel = isolate(input$trip_station_list)
    keep_sel = if (!is.null(sel) && sel %in% ids) sel else ids[1]
    updateSelectInput(session, "trip_station_list", choices = choices, selected = keep_sel)
  })

  # Tracks whether trip results should be displayed
  show_trip_results = reactiveVal(FALSE)

  observeEvent(input$trip_check_btn, { show_trip_results(TRUE) })

  # Clear all stations and hide results
  observeEvent(input$trip_clear_btn, {
    trip_stations(character(0))
    show_trip_results(FALSE)
  })

  # Compute trip results only when "Check Trip" is pressed
  trip_check_results = eventReactive(input$trip_check_btn, {
    ids = isolate(trip_stations())
    needs = isolate(input$trip_needs)
    if (length(ids) == 0 || is.null(needs) || length(needs) == 0) return(NULL)

    d = isolate(app_data())
    fac = d$facilities
    stations = d$stations
    station_routes = d$station_routes %||% list()

    name_lookup = setNames(
      vapply(stations, function(s) s$name %||% s$id, character(1)),
      vapply(stations, function(s) s$id, character(1))
    )
    wb_lookup = setNames(
      vapply(stations, function(s) as.integer(s$wheelchair_boarding %||% 0L), integer(1)),
      vapply(stations, function(s) s$id, character(1))
    )

    # Per-station accessibility results
    station_results = lapply(ids, function(sid) {
      station_fac = Filter(function(f) {
        identical(f$stop_id, sid) && (f$type %||% "") %in% needs
      }, fac)

      n_total = length(station_fac)
      n_op    = sum(vapply(station_fac, function(f) identical(f$status, "operational"), logical(1)))
      n_out   = n_total - n_op

      status = if (n_total == 0)   "no_data"
               else if (n_op > 0)  "ok"
               else                "blocked"

      is_warn = (status == "ok" && n_out > 0)
      out_fac = Filter(function(f) identical(f$status, "out_of_service"), station_fac)
      wb = if (sid %in% names(wb_lookup)) as.integer(wb_lookup[[sid]]) else 0L

      list(
        id = sid, name = name_lookup[[sid]] %||% sid,
        status = status, is_warn = is_warn,
        n_total = n_total, n_operational = n_op, n_out = n_out,
        out_facilities = out_fac,
        wheelchair_boarding = wb
      )
    })

    # Connecting routes per segment — local set intersection, no API calls
    segment_routes = if (length(ids) >= 2) {
      lapply(seq_len(length(ids) - 1), function(i) {
        routes_a = station_routes[[ids[i]]]   %||% list()
        routes_b = station_routes[[ids[i+1]]] %||% list()
        ids_a = vapply(routes_a, function(r) r$id %||% "", character(1))
        ids_b = vapply(routes_b, function(r) r$id %||% "", character(1))
        shared = routes_a[ids_a %in% intersect(ids_a, ids_b)]
        if (length(shared) > 1) {
          ord = order(
            vapply(shared, function(r) as.numeric(r$route_type %||% 99), numeric(1)),
            vapply(shared, function(r) r$id %||% "", character(1))
          )
          shared = shared[ord]
        }
        list(from_id = ids[i], to_id = ids[i+1], routes = shared)
      })
    } else list()

    list(
      station_results = station_results,
      segment_routes = segment_routes,
      station_routes_loaded = length(station_routes) > 0
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
    n = length(station_results)
    if (n == 0) return(p(em("Add stations above, then press \u201cCheck Trip\u201d.")))

    # Overall verdict
    statuses  = vapply(station_results, function(r) r$status,  character(1))
    warns     = vapply(station_results, function(r) r$is_warn, logical(1))
    n_blocked = sum(statuses == "blocked")
    n_warn    = sum(warns)

    verdict_class = if (n_blocked > 0) "trip-verdict-blocked"
                    else if (n_warn > 0) "trip-verdict-warn"
                    else "trip-verdict-ok"
    verdict_text = if (n_blocked > 0)
      paste0("\u2717 ", n_blocked, " station(s) have no operational facilities for your needs")
    else if (n_warn > 0)
      paste0("\u26a0 ", n_warn, " station(s) have partial outages")
    else
      "\u2713 Trip looks clear"

    # Build interleaved station nodes + segment connectors
    items = list()
    for (i in seq_len(n)) {
      r = station_results[[i]]

      card_class = if (r$status == "blocked")  "trip-station-blocked"
                   else if (r$is_warn)          "trip-station-warn"
                   else if (r$status == "ok")   "trip-station-ok"
                   else                          "trip-station-nodata"
      icon = if (r$status == "blocked") "\u2717"
             else if (r$is_warn)         "\u26a0"
             else if (r$status == "ok")  "\u2713"
             else                         "?"
      status_text = if (r$status == "blocked")  "No operational facilities for your needs"
                    else if (r$status == "no_data") "No facility data"
                    else if (r$is_warn) paste0(r$n_operational, "/", r$n_total, " needed facilities operational")
                    else paste0("All ", r$n_total, " needed facilities operational")

      perm_warning = if (identical(r$wheelchair_boarding, 2L))
        tags$div(class = "trip-perm-warning", "\u26a0 Permanently inaccessible to wheelchair users")
      else NULL

      out_details = if (length(r$out_facilities) > 0) {
        tagList(lapply(r$out_facilities, function(f) {
          type_lbl = facility_type_label(as.character(f$type %||% ""))
          fname    = as.character(f$name %||% f$short_name %||% "")
          alt      = if (!is.null(f$alert) && !is.null(f$alert$description))
                       as.character(f$alert$description) else NULL
          tags$div(class = "trip-out-detail",
            tags$span(paste0(type_lbl, if (nchar(fname) > 0) paste0(' "', fname, '"') else "", " \u2014 out of service")),
            if (!is.null(alt)) tags$div(class = "trip-alt-text", alt) else NULL
          )
        }))
      } else NULL

      items = c(items, list(
        tags$div(class = paste("trip-station-card", card_class),
          tags$div(class = "trip-station-header",
            tags$span(class = "trip-station-name", paste0(icon, " ", r$name)),
            tags$span(class = "trip-status-text", status_text)
          ),
          perm_warning,
          out_details
        )
      ))

      # Segment connector between this station and the next
      if (i < n) {
        seg = segment_routes[[i]]
        badges = if (length(seg$routes) > 0) {
          lapply(seg$routes, function(rt) {
            bg = if (!is.null(rt$color) && nchar(rt$color %||% "") == 6)
                   paste0("#", rt$color) else "#888"
            fg = if (!is.null(rt$text_color) && nchar(rt$text_color %||% "") == 6)
                   paste0("#", rt$text_color) else "#fff"
            tags$span(
              class = "trip-line-badge",
              style = paste0("background:", bg, ";color:", fg, ";"),
              rt$name %||% rt$id
            )
          })
        } else if (!station_routes_loaded) {
          list(tags$span(class = "trip-line-badge trip-line-badge-unknown", "Route data unavailable"))
        } else {
          list(tags$span(class = "trip-line-badge trip-line-badge-transfer", "No direct route"))
        }
        items = c(items, list(
          tags$div(class = "trip-connector",
            tags$div(class = "trip-connector-line"),
            tags$div(class = "trip-connector-badges", tagList(badges)),
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
          "\u26A0 Station permanently inaccessible to wheelchair users"
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
        generate_station_report(id, d$facilities, d$stations),
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
    paragraphs = strsplit(report, "\n\\s*\n")[[1]]
    paragraphs = trimws(paragraphs)
    paragraphs = paragraphs[paragraphs != ""]
    tags$div(
      class = "ai-report-box",
      tags$div(class = "ai-report-label", "AI Accessibility Report"),
      tagList(lapply(paragraphs, tags$p))
    )
  })

  output$station_facilities = renderUI({
    id = selected_station()
    if (is.null(id)) return(NULL)
    d = app_data()
    fac = d$facilities
    if (length(fac) == 0) return(NULL)
    cards = station_facility_cards(fac, id)
    if (length(cards) == 0) return(p(em("No facility data for this station.")))
    tagList(cards)
  })
}

# 4. RUN ###################################

shinyApp(ui = ui, server = server)
