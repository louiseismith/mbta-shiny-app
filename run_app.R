# run_app.R
# Run the MBTA Accessibility Tracker Shiny app.
# Run from the app directory (folder with app.R and accessibility_tracker_prototype.py),
# or from any parent directory; the script will find the app folder.

# Check if this path is the app directory (has app.R + Python script)
is_app_dir = function(path) {
  file.exists(file.path(path, "app.R")) &&
    file.exists(file.path(path, "accessibility_tracker_prototype.py"))
}

# Search for the app directory: check children first, then walk up
find_app_dir = function() {
  w = getwd()
  # Check immediate subdirectories
  subdirs = list.dirs(w, recursive = FALSE)
  for (d in subdirs) {
    if (is_app_dir(d)) return(d)
  }
  # Walk up the directory tree
  for (i in 1:10) {
    if (is_app_dir(w)) return(w)
    w_old = w
    w = dirname(w)
    if (w == w_old) break
  }
  NULL
}

if (is_app_dir(getwd())) {
  app_path = "."
} else {
  app_dir = find_app_dir()
  if (is.null(app_dir)) {
    stop(
      "App directory not found. Run this script from the folder that contains ",
      "app.R and accessibility_tracker_prototype.py, or from a parent of that folder."
    )
  }
  setwd(app_dir)
  app_path = "."
}

# Install R dependencies if missing
required = c("shiny", "leaflet", "dplyr", "reticulate")
missing = required[!sapply(required, requireNamespace, quietly = TRUE)]
if (length(missing) > 0) {
  message("Installing R packages: ", paste(missing, collapse = ", "))
  install.packages(missing, repos = "https://cloud.r-project.org")
}

# Point reticulate at the project venv (same path app.R uses)
library(reticulate)
venv_path = file.path(getwd(), "..", "..", ".venv")
if (!dir.exists(venv_path)) {
  stop(
    "Python virtualenv not found at ", normalizePath(venv_path, mustWork = FALSE),
    "\nCreate it with: uv venv && uv pip install requests python-dotenv"
  )
}
use_virtualenv(venv_path, required = TRUE)

# Verify Python packages are available
py_ok = tryCatch({
  stopifnot(py_module_available("requests"), py_module_available("dotenv"))
  TRUE
}, error = function(e) FALSE)

if (!py_ok) {
  stop("Missing Python packages. Install with: uv pip install requests python-dotenv")
}

shiny::runApp(app_path)
