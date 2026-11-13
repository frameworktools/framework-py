---
name: framework-cache
description: Use when caching expensive computations in a project that uses the Framework package. Covers cache_remember(), cache(), cache_get(), and cache_forget().
metadata:
  title: "Framework: caching computations"
  summary: Cache computed values with explicit names and expiry.
---

# Framework: caching computations

```python
# Computes once, then reuses the result until it expires
model = fw.cache_remember("fitted_model", lambda: fit(training), expire_after="7 days")

fw.cache("processed", frame)       # write
frame = fw.cache_get("processed")  # read (None if missing)
fw.cache_forget("processed")       # remove
```

Cache entries are recorded in `framework.db` with a hash and an expiry. Use
names that say what the value is.
