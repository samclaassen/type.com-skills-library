---
name: navattic-product-change-mapper
description: "Cross-references a shipped or upcoming product change against the demo library to flag what's now inaccurate."
---

# Product change mapper

> Navattic MCP Skill · Refresh demos

Cross-references a shipped or upcoming product change against the demo library to flag what's now inaccurate.

## Workflow

1. Take the feature or UI change as input, ideally with a description of the “before” state.
2. List demos via list_projects / get_project, prioritizing the affected product area.
3. Scan candidates via get_flow_document for references to the old version.
4. Output demo, step, what's outdated, and confidence level.
