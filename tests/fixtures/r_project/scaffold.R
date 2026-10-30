# scaffold.R
# This file is sourced by framework::scaffold() to set up your project environment

# Set random seed for reproducibility
# set.seed(20241109)  # Uncomment and set your seed

# Set ggplot2 theme
if (requireNamespace('ggplot2', quietly = TRUE)) {
  ggplot2::theme_set(ggplot2::theme_minimal())
  message("ggplot2 theme set to theme_minimal")
}
