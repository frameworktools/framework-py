---
name: git-workflow
description: Use when reviewing changes, committing analysis work, resolving merge conflicts, or coordinating branches in a research repository.
metadata:
  title: Using Git in an analysis project
  summary: Reviewable Git changes for analysis code and project files.
---

# Using Git in an analysis project

## Changes

- Check the branch, status, and diff before editing or committing.
- Keep unrelated work as it is.
- Stage explicit paths, so generated output, data, credentials, and
  machine-specific files stay out of commits.
- Keep each commit to one coherent change, and describe what changed.

## Conflicts

- Resolve notebook conflicts with notebook-aware tools when available.
- Regenerate generated files from their source.
- Rerun affected analyses after resolving a conflict.

## Shared history

- Leave shared history as it is. Check divergence before merging or rebasing.
- Commit, push, and publish only when the user asks.
