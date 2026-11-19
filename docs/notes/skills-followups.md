# Skills: follow-ups

Bugs that already exist on main, left out of the skills_copy() work. One line each, with where the behavior lives (line numbers as of the skills-copy branch).

- A tracked Cloud skill's variant can switch from python to r: pull compares the skill ID and version, never the variant (src/framework/skill_pull.py:310, `_decide`).
- An offline pull leaves out personal skills: the package fallback lists only the package skills, so your own Cloud skills aren't reported (src/framework/skill_pull.py:569, `pull_skills`).
- upgrade() can refill a diverged skill: it plans each folder alone, without the pull's diverged check (src/framework/upgrade.py:1067).
- An inline skill has no baseline hash: pull writes it without a lock entry, so a later edit can't be told from the setup's copy (src/framework/skill_pull.py:473, `_apply`).
- A skill removed from the setup whose copies have diverged is listed as removed with no skills_copy() hint (src/framework/skill_pull.py:798, `pull_skills`).
- pull_all() skips a metadata-only skill update: it applies skills only when something is added, restored, updated, or replaced, so a Cloud identity or version change with the same bytes waits for pull_skills() (src/framework/sync_all.py:444, `_skills_pull_plan`).
- The "kept, not tracked" row's adopt hint uses skills_copy(from_=..., to=...) folder names and has no `path` argument, so it can't name a copy outside the assistant folders (src/framework/skill_pull.py:678, `pull_skills`).
- push_all(dry_run=True) leaves the top-level `failed` empty: diverged, unreadable, or missing skills show only in the skills plan, not in `failed` (src/framework/sync_all.py:800, `push_all`).
