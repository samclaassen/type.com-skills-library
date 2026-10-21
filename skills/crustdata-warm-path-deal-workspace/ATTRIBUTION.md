# Attribution

This skill is Crustdata's official `warm-path-workspace` skill, imported from https://github.com/crustdata/skills (commit `5a11ac2eaac9ddd9f509fcf1d22ee98217031956`, path `skills/warm-path-workspace`).

Changes made for type.com:

- Files flattened into one folder (upstream `references/, scripts/, assets/, evals/`); path mentions updated.
- Frontmatter renamed to `crustdata-warm-path-deal-workspace` with a one-line listing description; upstream display metadata (such as `display-name`) dropped.
- A source/license header added to the top of SKILL.md, with a Crustdata access rule: use type.com's built-in Crustdata tools first, otherwise ask the user to connect Crustdata's MCP server.
- `crustdata-tool-selection.md` added (Crustdata's `rules/crustdata-tool-selection.mdc`, without its Cursor frontmatter).
- README gains a type.com setup section; `evals.json` `skill_name` matches the handle.
- The logo PNGs and the Geist web font are not bundled: `workspace-template.html` uses `__CD_LOGO_LIGHT__`/`__CD_LOGO_DARK__` placeholders that `build_workspace.py` fills (from `crustdata-logo-*.png.b64.txt` if present, otherwise a text wordmark), and pages use the system font stack. The branding rule says the same.
- The publish step attaches/uploads the HTML file instead of calling Claude's `Artifact` tool, and a Claude Code-specific ToolSearch note was removed from `stakeholder-agent.md`.
- `stakeholder-agent.md` requests business email only by default (personal email or phone only with consent), and the eval prompts refer to attached exports instead of local `~/Downloads` paths.

Everything else is Crustdata's original text.

## License

```
MIT License

Copyright (c) 2026 Crustdata

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
