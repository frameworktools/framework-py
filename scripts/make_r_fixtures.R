# Build the cross-language fixtures in tests/fixtures/r_project.
#
# The R package creates a project, the fixture adds settings and catalog
# entries that exercise the edge cases, and R records what it reads back:
# settings_read() in two environments, data_info() for every catalog name,
# file hashes, and the framework.db rows. The Python tests read the same
# project and must get the same answers.
#
#   Rscript scripts/make_r_fixtures.R [path to the R repository] [commit]
#
# To pin the fixtures to a commit, unpack it and pass both:
#   git -C ../framework archive <commit> | tar -x -C <dir>
#   Rscript scripts/make_r_fixtures.R <dir> <commit>
#
# Run it from the framework-py root. It loads the R package from source with
# pkgload, so the fixtures match that checkout, not an installed release.

args <- commandArgs(trailingOnly = TRUE)
r_repo <- normalizePath(if (length(args) > 0) args[[1]] else "../framework", mustWork = TRUE)
r_commit <- if (length(args) > 1) args[[2]] else "working tree"
out <- normalizePath("tests/fixtures/r_project", mustWork = FALSE)

suppressMessages(pkgload::load_all(r_repo, quiet = TRUE))

work <- tempfile("fw-fixture-")
dir.create(work)
Sys.setenv(FW_CONFIG_HOME = file.path(work, "config"))

layers <- file.path(work, "layers.json")
writeLines('{"spec_version": 1, "name": "Fixture Study",
  "layers": [{"layer": {"language": "r", "git": {"use_git": false}}}]}', layers)
project <- file.path(work, "project")
suppressMessages(project_create_from_layers(layers, path = project, interactive = FALSE))

# Edge cases ------------------------------------------------------------------

settings_path <- file.path(project, "settings.yml")
cat(
  "  options:\n",
  "    fixture_dir: from_options\n",
  "  fixture:\n",
  "    env_value: env(\"FW_FIXTURE_VAR\")\n",
  "    env_default: env(\"FW_FIXTURE_UNSET\", \"fallback\")\n",
  "    expr_value: !expr Sys.getenv(\"FW_FIXTURE_VAR\")\n",
  "    expr_default: !expr Sys.getenv(\"FW_FIXTURE_UNSET\", \"expr-fallback\")\n",
  "    dotenv_value: env(\"FW_FIXTURE_DOTENV\")\n",
  "    flags: {yes_word: yes, no_word: no, on_word: on, true_word: true, y_word: y}\n",
  "    numbers: {int: 42, float: 1.5, sci: 1e3, octal_like: 012, quoted: \"7\"}\n",
  "    empty_map: {}\n",
  "    empty_list: []\n",
  "    nothing: ~\n",
  "    tags: [alpha, beta]\n",
  "    records: [{id: 1}, {id: 2}]\n",
  "    mixed: [1, x]\n",
  "    nested: {keep: base, replace: base, drop: base}\n",
  "production:\n",
  "  directories:\n",
  "    notebooks: prod_notebooks\n",
  "  fixture:\n",
  "    tags: [gamma]\n",
  "    records: [{id: 3}]\n",
  "    mixed: [2.5, y, z]\n",
  "    nested: {replace: production, drop: ~, added: production}\n",
  sep = "", file = settings_path, append = TRUE
)

cat("FW_FIXTURE_DOTENV=\"from dotenv\"\n", file = file.path(project, ".env"), append = TRUE)

yaml::write_yaml(list(data = list(
  inputs = list(
    raw = list(
      survey = list(path = "inputs/raw/survey.csv", type = "csv", locked = TRUE,
                    description = "Survey responses", source_url = "https://example.org/survey"),
      visits = "inputs/raw/visits.tsv",
      wide = list(path = "inputs/raw/wide.parquet")
    ),
    private = list(
      ids = list(path = "inputs/private/ids.csv", delimiter = "semicolon")
    )
  ),
  reference = list(
    codes = list(path = "inputs/reference/codes.dta"),
    legacy = list(path = "inputs/reference/legacy.rds", locked = FALSE)
  )
)), file.path(project, "settings", "data.yml"))

dir.create(file.path(project, "inputs", "private"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(project, "inputs", "reference"), recursive = TRUE, showWarnings = FALSE)
writeLines(c("id,score", "1,3.5", "2,4.0"), file.path(project, "inputs", "raw", "survey.csv"))
writeLines(c("id\tvisit", "1\t2026-01-02"), file.path(project, "inputs", "raw", "visits.tsv"))
writeLines(c("id;code", "1;A"), file.path(project, "inputs", "private", "ids.csv"))

# What R reads ------------------------------------------------------------------

old <- setwd(project)
Sys.setenv(FW_FIXTURE_VAR = "from environment")
Sys.unsetenv(c("FW_FIXTURE_UNSET", "FW_FIXTURE_DOTENV", "R_CONFIG_ACTIVE", "FW_CONFIG_ACTIVE"))

relative <- function(path) {
  root <- normalizePath(project, winslash = "/")
  sub(paste0("^", root, "/"), "", normalizePath(path, winslash = "/", mustWork = FALSE))
}

names_in <- function(x, prefix = "") {
  out <- character()
  for (key in names(x)) {
    full <- if (nzchar(prefix)) paste(prefix, key, sep = ".") else key
    item <- x[[key]]
    if (is.character(item) || (is.list(item) && !is.null(item$path))) {
      out <- c(out, full)
    } else if (is.list(item)) {
      out <- c(out, names_in(item, full))
    }
  }
  out
}

default_settings <- settings_read("settings.yml", environment = "default")
production_settings <- suppressWarnings(settings_read("settings.yml", environment = "production"))

info <- list()
for (name in names_in(default_settings$data)) {
  spec <- data_info(name)
  spec$path <- relative(spec$path)
  info[[name]] <- spec
}

hashes <- list()
for (file in c("inputs/raw/survey.csv", "inputs/raw/visits.tsv", "inputs/private/ids.csv")) {
  hashes[[file]] <- as.character(unclass(.calculate_file_hash(file)))
}

invisible(.ensure_framework_db(project))
invisible(.set_data("inputs.raw.survey", path = "inputs/raw/survey.csv", type = "csv",
          delimiter = "comma", locked = TRUE, hash = hashes[["inputs/raw/survey.csv"]]))
invisible(.set_metadata("fixture", "written by R", project))

# Data, a cache entry, and an output, saved the way R saves them
r_frame <- data.frame(id = 1:3, score = c(1.5, NA, 3), group = c("a", "b", NA))
suppressMessages(data_save(r_frame, "inputs/intermediate/r_clean.csv", force = TRUE))
suppressMessages(cache("r_value", list(a = 1)))
suppressMessages(save_table(r_frame, "r_summary"))
results <- result_list()

# Files in other formats, and how R reads them (labels stripped, as by default)
for (file in c("test.dta", "test.sav", "test.xpt")) {
  invisible(file.copy(file.path(r_repo, "tests", "testthat", "fixtures", file), "inputs/reference"))
}
formats <- list()
for (file in c("test.dta", "test.sav", "test.xpt")) {
  formats[[file]] <- suppressMessages(data_read(file.path("inputs/reference", file)))
}

con <- DBI::dbConnect(RSQLite::SQLite(), "framework.db")
tables <- DBI::dbGetQuery(con, "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
data_row <- DBI::dbGetQuery(con, "SELECT name, path, type, delimiter, locked, hash,
  typeof(created_at) AS created_at_type, typeof(locked) AS locked_type FROM data
  WHERE name = 'inputs.raw.survey'")
saved_row <- DBI::dbGetQuery(con, "SELECT name, path, type, delimiter, locked, hash FROM data
  WHERE name = 'inputs/intermediate/r_clean.csv'")
cache_row <- DBI::dbGetQuery(con, "SELECT name, file_path, hash, typeof(created_at) AS created_at_type
  FROM cache")
result_row <- DBI::dbGetQuery(con, "SELECT name, type, public, blind, comment, hash,
  typeof(created_at) AS created_at_type FROM results")
DBI::dbDisconnect(con)

setwd(old)

expected <- list(
  r_version = as.character(utils::packageVersion("framework")),
  r_commit = r_commit,
  environment = list(FW_FIXTURE_VAR = "from environment"),
  settings_default = default_settings,
  settings_production = production_settings,
  data_info = info,
  hashes = hashes,
  tables = stats::setNames(as.list(tables$sql), tables$name),
  data_row = as.list(data_row[1, ]),
  saved_row = as.list(saved_row[1, ]),
  cache_row = as.list(cache_row[1, ]),
  result_row = as.list(result_row[1, ]),
  result_list = results,
  formats = formats
)

# Write the fixture -------------------------------------------------------------

unlink(out, recursive = TRUE)
dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
invisible(file.copy(project, dirname(out), recursive = TRUE))
invisible(file.rename(file.path(dirname(out), "project"), out))
writeLines(
  jsonlite::toJSON(expected, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE, digits = NA),
  file.path(dirname(out), "r_project.expected.json")
)
message("Wrote ", out, " and r_project.expected.json")
