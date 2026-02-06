# run_app.R
# Run the MBTA Accessibility Tracker Shiny app.
# Usage (either way works):
#   From shiny_app directory:  setwd("02_ai_productivity/shiny_app"); source("run_app.R")
#   From project root:         source("02_ai_productivity/shiny_app/run_app.R")

# Check if we're already in the app directory (has app.R + Python script)
is_app_dir = function(path) {
  file.exists(file.path(path, "app.R")) &&
    file.exists(file.path(path, "accessibility_tracker_prototype.py"))
}

# Find project root (folder that contains 02_ai_productivity/shiny_app)
find_root = function() {
  w = getwd()
  for (i in 1:10) {
    if (is_app_dir(file.path(w, "02_ai_productivity", "shiny_app")))
      return(file.path(w, "02_ai_productivity", "shiny_app"))
    w_old = w
    w = dirname(w)
    if (w == w_old) break
  }
  NULL
}

if (is_app_dir(getwd())) {
  app_path = "."
} else {
  app_path = find_root()
  if (is.null(app_path)) {
    stop(
      "App not found. Run this script from 02_ai_productivity/shiny_app, ",
      "or from the project root (folder containing 02_ai_productivity)."
    )
  }
  # app_path is full path to shiny_app; set wd to its parent and run by name
  setwd(dirname(app_path))
  app_path = basename(app_path)
}

# Install R dependencies if missing
required = c("shiny", "leaflet", "dplyr", "reticulate")
missing = required[!sapply(required, requireNamespace, quietly = TRUE)]
if (length(missing) > 0) {
  message("Installing R packages: ", paste(missing, collapse = ", "))
  install.packages(missing, repos = "https://cloud.r-project.org")
}

# Check and install Python dependencies using uv
library(reticulate)
python_packages = c("requests", "python-dotenv")
message("Checking Python dependencies...")
py_installed = tryCatch({
  missing_packages = character(0)
  for (pkg in python_packages) {
    # python-dotenv is imported as 'dotenv'
    module_name = if (pkg == "python-dotenv") "dotenv" else pkg
    if (!py_module_available(module_name)) {
      missing_packages = c(missing_packages, pkg)
    }
  }
  
  if (length(missing_packages) > 0) {
    message("Installing Python packages with uv: ", paste(missing_packages, collapse = ", "))
    # Use uv pip install (uv as pip replacement)
    cmd = paste("uv pip install", paste(missing_packages, collapse = " "))
    result = system(cmd, ignore.stdout = TRUE, ignore.stderr = TRUE)
    if (result != 0) {
      stop("uv pip install failed")
    }
    # Verify installation
    for (pkg in missing_packages) {
      module_name = if (pkg == "python-dotenv") "dotenv" else pkg
      if (!py_module_available(module_name)) {
        stop("Package ", pkg, " still not available after installation")
      }
    }
  }
  TRUE
}, error = function(e) {
  warning(
    "Could not verify/install Python packages with uv. ",
    "Please install manually: uv pip install requests python-dotenv\n",
    "Error: ", conditionMessage(e)
  )
  FALSE
})

if (!py_installed) {
  stop("Python dependencies not available. Please install: uv pip install requests python-dotenv")
}

shiny::runApp(app_path)
