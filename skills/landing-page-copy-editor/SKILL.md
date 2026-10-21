---
name: landing-page-copy-editor
description: |-
  Turn any public landing page URL into a pixel-perfect, editable replica inside a Type app so the team can rewrite and finalize copy in place (click-to-edit text, change log with before/after, copy exports, desktop/tablet/mobile preview). Use when asked to make an editable copy of a landing page, put type.com/<page> in an app to edit copy, or spin up another vertical landing page for copy work.
---

# Landing Page Copy Editor

Input: one landing page URL (plus optional slug/label). Output: a published Type app that shows the
page exactly as it renders live, where anyone with write access can click any text, rewrite it, and
see all changes (original vs new) in a side panel with Copy changes / Copy full page exports and a
Desktop 1440 / Tablet 768 / Mobile 390 preview switch.


## How it works (why each piece exists)

1. **Capture after JavaScript runs** (`capture.mjs`). Server-rendered HTML is NOT the same as the
   live page (hero demos, animations and client layouts are missing). Headless Chromium loads the
   URL at 1440px, scrolls the real scroll container top-to-bottom to trigger lazy content, waits,
   then serializes the body plus all stylesheet links and inline `<style>` tags.
2. **Build assets** (`build_page.py`): absolutize every URL, wrap each visible text node in
   `<span data-copy="c0001">` (these are the editable units), collapse type.com's animated rotating
   headline word, fetch every stylesheet, inline SVG/font assets that lack CORS (CSS masks and
   fonts fail cross-origin without it), then run `shadowcss.py`.
3. **Shadow-root rendering, not an iframe.** Type runs apps in a sandboxed frame; a nested iframe
   inherits the sandbox and gets an opaque origin, so the editor cannot touch its DOM ("Editor
   couldn't connect to the page"). Instead the page renders inside a shadow root. `shadowcss.py`
   makes that work: width `@media` queries become `@container tppage` queries (so the viewport
   switcher works), `vw/vh` become `cqw/cqh`, `:root/html/body` selectors become `.tp-html/.tp-body`,
   and `@font-face`/`@property` rules are hoisted to the document (they don't work inside shadow DOM).
4. **Size limits.** Type source files must be < 500KB each, bundles < 2MB, total ~8MB. The build
   splits body HTML, page CSS (including oversized `@layer`/`@media` blocks) and global CSS into
   ~320KB files and `assets.ts` lazy-imports each one as its own chunk.
5. **Backend**: one `copyEdits` table keyed by (page, key). `listEdits`, `setEdit`, `revertEdit`.
   No bulk reset (never ship a public wipe).

## Workflow

Do the steps in order. Keep implementation details out of the user-facing reply.

1. **Load the skill files into the sandbox.** Read every file of this skill (`capture.mjs`,
   `build_page.py`, `shadowcss.py`, `verify.mjs`, `apply_template.py`, `App.tsx`, `app.ts`,
   `schema.ts`, `editor-styles.css`) with `read_skill_file` (paginate until `truncated=false`) and
   write them all into ONE flat directory, e.g. `/tmp/lp-editor/`. Then:
   ```bash
   cd /tmp/lp-editor && bun add playwright-core@1.48.2 && (python3 -c "import bs4" || pip install beautifulsoup4)
   ```
   Chromium is at `/usr/bin/chromium` (override with `CHROMIUM=`).
2. **Pick a slug** (`[a-z0-9-]`, e.g. `ecommerce`) and label (e.g. `type.com/ecommerce`).
3. **Create the app**: `start_app_build` with title `<Name> Landing Page Copy Editor` and
   `viewerIdentity: "optional"` (so the change log shows member names). To refresh an existing
   editor app instead, `checkout_app` it (see the re-capture warning below).
4. **Install template + capture + build** (APP = returned directory):
   ```bash
   cd /tmp/lp-editor
   python3 apply_template.py "$APP"
   mkdir -p /tmp/lp-<slug> && node capture.mjs "<url>" /tmp/lp-<slug>/capture.json 7000
   python3 build_page.py /tmp/lp-<slug>/capture.json "$APP" <slug> "<label>"
   ```
   `build_page.py` prints copy block count, file sizes and warnings. Any `ERROR files over the 500KB`
   line must be fixed before preparing (lower `CHUNK` or `INLINE_MAX` at the top of the script).
5. **Verify against the live page before publishing** (both widths):
   ```bash
   node verify.mjs /tmp/lp-<slug>/harness.json /tmp/lp-<slug>/shots 1440
   node verify.mjs /tmp/lp-<slug>/harness.json /tmp/lp-<slug>/mshots 390
   ```
   Expect `heightDelta` ≈ 0 and `editable: true`. Stitch `live-NN.png` next to `replica-NN.png`
   (PIL), upload with `upload_file` (attachToCurrentMessage=false) and look at them: fonts, logos,
   hero, header, footer. Small deltas (< ~2%) usually come from animation frames captured mid-motion.
6. **Typecheck and prepare**: `cd "$APP" && bunx tsc --noEmit -p .` then `prepare_app_build`.
   New app → goes live; existing app → tell the user to click **Review draft** then **Publish**.
7. **Reply** with: what page was captured and when, the differences to expect (below), and how to
   use the editor (click text, Enter saves, Esc cancels, Changes panel, Copy changes / Copy full
   page, viewport switcher). The status chip "Editing on · N text blocks" means it is working.

## Known limitations (tell the user)

- It is a snapshot at capture time; animations are frozen (hero demos show one frame, rotating
  headline words show one word). Re-run the skill to refresh.
- Repeated text (duplicated marquees, mock UIs) is edited per occurrence.
- Links/buttons don't navigate. Images/videos still load from the live site.
- JS-measured layouts are captured at 1440px, so a few mobile details can differ slightly.
- Re-capturing renumbers copy keys: saved edits for that page will no longer line up. Query
  `app:listEdits` first; if edits exist, have the user export them ("Copy changes") or create a new
  app/slug instead of overwriting.

## Troubleshooting

- **Chip says "Couldn't load the page"**: a lazy asset chunk failed; check `get_app_logs` and file sizes.
- **Chip says "View only"**: the viewer lacks write capability (preview surfaces are read-only).
- **Clipped/animated words** on a new site: look for inline `style="width:…"` set by JS inside the
  element and strip it in `build_page.py` (see the rotating-role block for the pattern).
- **Missing icons/logos**: usually CSS `mask-image` SVGs without CORS; they are inlined automatically
  unless larger than `INLINE_MAX` (see warnings). Plain background images don't need CORS.
- **Cookie banners / chat widgets captured**: add their selector to the removal list in `capture.mjs`.