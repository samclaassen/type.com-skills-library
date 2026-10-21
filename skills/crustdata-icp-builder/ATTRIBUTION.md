# Attribution

This skill is Crustdata's official `icp-builder` skill, imported from https://github.com/crustdata/skills (commit `5a11ac2eaac9ddd9f509fcf1d22ee98217031956`, path `skills/icp-builder`).

Changes made for type.com:

- Files flattened into one folder (upstream `assets/, evals/`); path mentions updated.
- Frontmatter renamed to `crustdata-icp-builder` with a one-line listing description; upstream display metadata (such as `display-name`) dropped.
- A source/license header added to the top of SKILL.md, with a Crustdata access rule: use type.com's built-in Crustdata tools first, otherwise ask the user to connect Crustdata's MCP server.
- `crustdata-tool-selection.md` added (Crustdata's `rules/crustdata-tool-selection.mdc`, without its Cursor frontmatter).
- README gains a type.com setup section; references to sibling skills use their type.com `crustdata-...` handles; `evals.json` `skill_name` matches the handle.
- Crustdata's two logo PNGs are bundled as base64 text (`crustdata-logo-light.png.b64.txt`, `crustdata-logo-dark.png.b64.txt`).

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
