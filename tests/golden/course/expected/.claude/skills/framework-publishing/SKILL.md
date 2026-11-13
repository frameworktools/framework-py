---
name: framework-publishing
description: Use when publishing saved files or rendered notebooks in a project that uses the Framework package. Covers publishing destinations and private-file checks.
metadata:
  title: "Framework: publishing artifacts"
  summary: Publish saved artifacts and notebooks through Framework.
---

# Framework: publishing artifacts

`fw.publish()` and `fw.publish_notebook()` upload to the project's storage
bucket when one is configured, or to Framework Cloud when the project was set
up from it.

```python
fw.publish("outputs/report.html")              # upload a file
fw.publish_notebook("notebooks/analysis.qmd")  # render and upload
```

The publish functions warn about any source in a private folder and ask the
user to confirm. In a script they stop. Leave `include_private` off, and let
the user answer the prompt.
