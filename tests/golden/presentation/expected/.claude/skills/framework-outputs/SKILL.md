---
name: framework-outputs
description: Use when saving tables, figures, models, or rendered notebooks in a project that uses the Framework package. Covers save_table(), save_figure(), save_model(), save_notebook(), and result_list().
metadata:
  title: "Framework: saving outputs"
  summary: Save and list analysis tables, figures, models, and notebooks.
---

# Framework: saving outputs

Prefer the `save_*()` helpers. Each writes to the project's outputs folder for
that kind of result and records the file, so `result_list()` finds it.

```python
fw.save_table(summary, "quarterly_summary")            # outputs/tables/ (csv)
fw.save_table(report, "annual_report", format="xlsx")
fw.save_figure(fig, "enrollment_trend")                # outputs/figures/ (png)
fw.save_model(model, "regression_model")               # outputs/models/ (pickle)
fw.save_notebook("notebooks/analysis.qmd")             # render and save HTML

fw.result_list()              # everything saved so far
fw.result_list(type="model")  # models only
```

In a privacy-sensitive project, outputs save to the private folders by
default. Pass `public=True` only when the user asks.
