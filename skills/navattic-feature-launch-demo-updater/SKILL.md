---
name: navattic-feature-launch-demo-updater
description: "Maps a shipped or changed feature to the exact demo steps that reference the outdated version, then drafts the fix."
---

# Feature launch demo updater

> Navattic MCP Skill · Build and edit demos

Maps a shipped or changed feature to the exact demo steps that reference the outdated version, then drafts the fix.

## Workflow

1. Take the feature description or ticket as input and extract what changed.
2. Pull demo descriptions and flows via list_projects and get_project.
3. Scan each candidate demo's steps and screenshots via get_flow_document for mentions of the old version.
4. Flag exact step indices affected, not the whole demo.
5. Apply edits via update_step_content on confirmation and re-verify.
