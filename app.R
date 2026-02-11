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

# Facilities list (from py_to_r) -> data frame for one station
station_facilities_df = function(facilities_list, station_id) {
  if (length(facilities_list) == 0) return(data.frame())
  out = vector("list", length(facilities_list))
  for (i in seq_along(facilities_list)) {
    f = facilities_list[[i]]
    if (is.null(f$stop_id) || f$stop_id != station_id) next
    alert = f$alert
    outage_start = if (length(alert) && !is.null(alert$outage_start)) as.character(alert$outage_start) else NA_character_
    updated_at = if (length(alert) && !is.null(alert$updated_at)) as.character(alert$updated_at) else NA_character_
    out[[i]] = data.frame(
      Type = as.character(f$type %||% ""),
      Status = gsub("_", " ", as.character(f$status %||% "")),
      Details = if (length(alert) && !is.null(alert$header)) as.character(alert$header) else as.character(f$short_name %||% ""),
      `Out Since` = if (!is.na(outage_start)) format(as.POSIXct(outage_start, format = "%Y-%m-%dT%H:%M:%S", tz = "America/New_York"), "%b %d, %Y") else NA_character_,
      Duration = format_duration(outage_start),
      Updated = format_duration(updated_at),
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  }
  valid = !vapply(out, is.null, logical(1))
  if (!any(valid)) return(data.frame())
  d = dplyr::bind_rows(out[valid])
  d
}

`%||%` = function(x, y) if (is.null(x)) y else x

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
      ".sidebar-table-container {
        max-width: 100%;
        overflow-x: auto;
        overflow-y: auto;
        max-height: 50vh;
      }
      .sidebar-table-container table {
        font-size: 0.9em;
      }
    "))
  ),
  titlePanel("MBTA Accessibility Tracker"),
  sidebarLayout(
    sidebarPanel(
      p("Click a station on the map to see elevator/escalator status."),
      p(strong("System summary:"), textOutput("summary", inline = TRUE)),
      hr(),
      uiOutput("station_title"),
      div(
        class = "sidebar-table-container",
        tableOutput("station_facilities")
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
      palette = c("green", "orange", "red"),
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
        pal = pal,
        values = ~status_group,
        title = "Status",
        labels = c("All operational", "Some outages", "All out")
      )
    m
  })

  # Selected station (from map click)
  selected_station = reactiveVal(NULL)

  observeEvent(input$map_marker_click, {
    selected_station(input$map_marker_click$id)
  })

  output$station_title = renderUI({
    id = selected_station()
    if (is.null(id)) return(p(em("Click a station on the map.")))
    d = app_data()
    stations = d$stations
    name = id
    for (s in stations) {
      if (identical(s$id, id)) {
        name = s$name %||% id
        break
      }
    }
    h4(name)
  })

  output$station_facilities = renderTable({
    id = selected_station()
    if (is.null(id)) return(NULL)
    d = app_data()
    fac = d$facilities
    if (length(fac) == 0) return(NULL)
    # fac is a reticulate dict/list; get facilities for this station
    tbl = station_facilities_df(fac, id)
    if (nrow(tbl) == 0) return(data.frame(Message = "No facility data for this station."))
    tbl
  }, striped = TRUE)
}

# 4. RUN ###################################

shinyApp(ui = ui, server = server)
