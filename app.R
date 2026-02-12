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
use_virtualenv(file.path(getwd(), "..", "..", ".venv"), required = TRUE)

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
      .facility-cards {
        max-height: 50vh;
        overflow-y: auto;
      }
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
        background: var(--color-ok);
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
        color: #888;
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
    "))
  ),
  titlePanel("MBTA Accessibility Tracker"),
  sidebarLayout(
    sidebarPanel(
      selectizeInput("station_search", "Find a station:",
        choices = NULL, options = list(placeholder = "Type a station name…")
      ),
      p(strong("System summary:"), textOutput("summary", inline = TRUE)),
      hr(),
      uiOutput("station_title"),
      uiOutput("ai_report"),
      div(
        class = "facility-cards",
        uiOutput("station_facilities")
      ),
      width = 4
    ),
    mainPanel(
      leafletOutput("map", height = "600px"),
      width = 8
    )
  )
)

# 3. SERVER ###################################

server = function(input, output, session) {

  # One reactive that fetches data (so we can refresh later if needed)
  app_data = reactive({
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

  # Map: stations as markers; color by outage (CartoDB Positron = light basemap)
  output$map = renderLeaflet({
    d = app_data()
    stations = d$stations
    if (length(stations) == 0) {
      return(
        leaflet() %>%
          addProviderTiles("CartoDB.Positron") %>%
          setView(-71.06, 42.36, zoom = 11)
      )
    }

    # Convert to a data frame for easier use
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

    m = leaflet(st) %>%
      addProviderTiles("CartoDB.Positron") %>%
      setView(mean(st$lon, na.rm = TRUE), mean(st$lat, na.rm = TRUE), zoom = 11) %>%
      addCircleMarkers(
        lng = ~lon,
        lat = ~lat,
        layerId = ~id,
        radius = 10,
        color = "white",
        weight = 2,
        fillColor = ~pal(status_group),
        fillOpacity = 0.85,
        label = ~name
      ) %>%
      addLegend(
        "bottomright",
        colors = c(color_ok, color_warn, color_out),
        labels = c("All operational", "Some outages", "All out"),
        title = "Status"
      )
    m
  })

  observeEvent(input$map_marker_click, {
    id = input$map_marker_click$id
    selected_station(id)
    updateSelectizeInput(session, "station_search", selected = id)
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
