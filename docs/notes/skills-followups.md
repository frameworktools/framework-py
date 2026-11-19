# Skills: follow-ups

Skills lock v2 (R's `docs/architecture/skills-lock-v2.md`) resolved every item this file listed: the variant switch, the offline report, `upgrade()` into a diverged skill, the inline baseline, the removed-and-diverged hint, `pull_all()` metadata updates, the adopt hint's paths, and `push_all(dry_run=True)`'s `failed`. The shared scenarios in `tests/shared/conformance/skills` cover each one.

Open:

- The asset pin (`assets.lock.json`) names R commit 920c684 on the unreleased `skills-lock-v2` branch, for the scenarios. CI checks R out at that commit, so it fails until the branch is pushed. Move the pin to the R release tag that ships skills lock v2.
- R doesn't export `skills_move()` at that commit, so `parity.toml` lists it under `python_only`. Map it under `[functions.skills_move]` once the pin reaches the R release that exports it.
- Before v2 ships, a patch release of the v1 reader should stop on a lock with `schema` above 1 with an upgrade message (spec, "Old clients"). framework-py's last v1 release says only "Unsupported skill lock file".
- Cloud does not send `sync.known_hashes` yet (spec, "Cloud API"). Until it does, a project-token pull recognizes only the setup's current hash for a Cloud skill copy with no record.
