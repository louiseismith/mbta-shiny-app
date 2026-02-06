# run_app.R
# Run the MBTA Accessibility Tracker Shiny app.
# Usage: In RStudio open this file and Source, or from terminal:
#   Rscript 02_ai_productivity/shiny_app/run_app.R
# (From project root.) Or from R: source("02_ai_productivity/shiny_app/run_app.R")

# Find project root (folder that contains 01_api_queries and 02_ai_productivity)
find_root = function() {
  w = getwd()
  for (i in 1:10) {
    if (dir.exists(file.path(w, "02_ai_productivity", "shiny_app")) &&
        dir.exists(file.path(w, "01_api_queries")))
      return(w)
    w_old = w
    w = dirname(w)
    if (w == w_old) break
  }
  NULL
}

root = find_root()
if (is.null(root)) {
  stop("Project root not found. Run this script from somewhere inside the project, or from the project root.")
}
setwd(root)
app_path = "02_ai_productivity/shiny_app"

# Install dependencies if missing
required = c("shiny", "leaflet", "dplyr", "reticulate")
missing = required[!sapply(required, requireNamespace, quietly = TRUE)]
if (length(missing) > 0) {
  message("Installing R packages: ", paste(missing, collapse = ", "))
  install.packages(missing, repos = "https://cloud.r-project.org")
}

shiny::runApp(app_path)
