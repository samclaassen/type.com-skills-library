import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "convex/react";
import { api } from "../convex/_generated/api";
import { useTypeAppSession } from "./typeAuth";
import { loadPageAssets, type PageAssets } from "./page/assets";
import { pageMeta } from "./page/meta";

const PAGE = pageMeta.slug;
const PAGE_LABEL = pageMeta.label;
const CAPTURED = pageMeta.captured;

type Viewport = "desktop" | "tablet" | "mobile";
const VIEWPORTS: Record<Viewport, { label: string; width: number }> = {
  desktop: { label: "Desktop", width: 1440 },
  tablet: { label: "Tablet", width: 768 },
  mobile: { label: "Mobile", width: 390 },
};

type Edit = { key: string; text: string; updatedAt: number; updatedBy: string | null };
type Block = { key: string; original: string; section: string };

// Editor affordances injected into the replica page. Tokens mirror the app's
// brand and warning colors so the overlay reads as part of the editor.
const EDITOR_CSS = `
:host{all:initial;display:block;}
.tp-html{height:100%;overflow-y:auto;}
.tp-body{height:100%;}
.tp-html.tp-doc-scroll .tp-body{height:auto;min-height:100%;}
.tp-html{--copy-editor-accent:hsl(254.7 57.3% 65.1%);--copy-editor-changed:hsl(43.9 100% 62% / .45);}
.tp-html.copy-editing [data-copy]{cursor:text;}
.tp-html.copy-editing [data-copy]:hover{outline:1.5px dashed var(--copy-editor-accent);outline-offset:2px;border-radius:2px;}
[data-copy]:focus{outline:2px solid var(--copy-editor-accent)!important;outline-offset:2px;border-radius:2px;}
.tp-html.copy-show-changes [data-copy-edited]{background:var(--copy-editor-changed);box-shadow:0 0 0 2px var(--copy-editor-changed);border-radius:2px;}
[data-copy-flash]{animation:copy-flash 1.4s ease-out 1;}
@keyframes copy-flash{0%,40%{box-shadow:0 0 0 4px var(--copy-editor-accent);}100%{box-shadow:0 0 0 0 transparent;}}
`;

function escapeAttr(value: string) {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function buildShadowMarkup({ bodyHtml, pageCss }: PageAssets) {
  const h: Record<string, string> = pageMeta.htmlAttrs;
  const extra = Object.entries(h)
    .filter(([k]) => k !== "class")
    .map(([k, v]) => `${k}="${escapeAttr(v)}"`)
    .join(" ");
  const rootClass = `tp-html${pageMeta.documentScroll ? " tp-doc-scroll" : ""} ${escapeAttr(h.class ?? "")}`;
  return `<style>${EDITOR_CSS}</style>${pageCss.map((c) => `<style>${c}</style>`).join("")}<div class="${rootClass}" ${extra}><div class="tp-body">${bodyHtml}</div></div>`;
}

function ensureGlobalPageCss(globalCss: string) {
  if (document.getElementById("copy-editor-page-globals")) return;
  const style = document.createElement("style");
  style.id = "copy-editor-page-globals";
  style.textContent = globalCss;
  document.head.appendChild(style);
}

function clean(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

export function App() {
  const session = useTypeAppSession();
  const canWrite = session.capabilities.write;
  const edits = useQuery(api.app.listEdits, { page: PAGE });
  const setEdit = useMutation(api.app.setEdit);
  const revertEdit = useMutation(api.app.revertEdit);

  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [showChanges, setShowChanges] = useState(true);
  const [panelOpen, setPanelOpen] = useState(true);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [stage, setStage] = useState({ w: 0, h: 0 });
  const [loadedTick, setLoadedTick] = useState(0);
  const [frameState, setFrameState] = useState<"loading" | "ready" | "blocked">("loading");

  const hostRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<ShadowRoot | null>(null);
  const originals = useRef<Map<string, string>>(new Map());
  const overridesRef = useRef<Map<string, string>>(new Map());
  const handlersRef = useRef({ save: (_k: string, _t: string) => {} });


  const overrides = useMemo(() => {
    const map = new Map<string, string>();
    for (const e of (edits ?? []) as Edit[]) map.set(e.key, e.text);
    return map;
  }, [edits]);
  overridesRef.current = overrides;

  const save = useCallback(
    (key: string, raw: string) => {
      const text = clean(raw);
      const original = originals.current.get(key) ?? "";
      const current = overridesRef.current.get(key);
      const run =
        text === original || text === ""
          ? current !== undefined
            ? revertEdit({ page: PAGE, key })
            : null
          : text !== (current ?? original)
            ? setEdit({ page: PAGE, key, text, now: Date.now() })
            : null;
      if (!run) return;
      setError(null);
      run.catch(() => setError("Couldn't save that change. Try again."));
    },
    [revertEdit, setEdit],
  );
  handlersRef.current.save = save;

  // Measure the available stage so wide viewports scale down to fit.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) =>
      setStage({ w: entry.contentRect.width, h: entry.contentRect.height }),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const attach = useCallback((doc: ShadowRoot) => {
    docRef.current = doc;

    const found: Block[] = [];
    let section = "Top of page";
    doc.querySelectorAll<HTMLElement>("h1, h2, h3, [data-copy]").forEach((el) => {
      if (!el.hasAttribute("data-copy")) {
        const label = clean(el.getAttribute("aria-label") || el.textContent || "");
        if (label) section = label;
        return;
      }
      const key = el.getAttribute("data-copy")!;
      if (!originals.current.has(key)) originals.current.set(key, el.textContent ?? "");
      found.push({ key, original: originals.current.get(key)!, section });
    });
    setBlocks(found);

    const copyTarget = (e: Event) => {
      const t = e.target as Node | null;
      const el = t && (t.nodeType === 1 ? (t as Element) : t.parentElement);
      return el?.closest<HTMLElement>("[data-copy]") ?? null;
    };

    doc.addEventListener(
      "click",
      (e) => {
        const t = e.target as Element | null;
        if (t?.closest?.("a, button, [role=button], label, summary")) e.preventDefault();
        const el = copyTarget(e);
        if (el && el.isContentEditable && doc.activeElement !== el) el.focus();
      },
      true,
    );
    doc.addEventListener("submit", (e) => e.preventDefault(), true);
    doc.addEventListener("keydown", (ev) => {
      const e = ev as KeyboardEvent;
      const el = copyTarget(e);
      if (!el) return;
      if (e.key === "Enter") {
        e.preventDefault();
        el.blur();
      } else if (e.key === "Escape") {
        const key = el.getAttribute("data-copy")!;
        el.textContent = overridesRef.current.get(key) ?? originals.current.get(key) ?? "";
        el.blur();
      }
    });
    doc.addEventListener("paste", (ev) => {
      const e = ev as ClipboardEvent;
      const el = copyTarget(e);
      if (!el) return;
      e.preventDefault();
      const text = e.clipboardData?.getData("text/plain") ?? "";
      document.execCommand("insertText", false, text.replace(/\s+/g, " "));
    });
    doc.addEventListener("focusout", (e) => {
      const el = copyTarget(e);
      if (!el) return;
      handlersRef.current.save(el.getAttribute("data-copy")!, el.textContent ?? "");
    });

    // Recreate the sticky header's scrolled state the live site toggles in JS.
    // The page's own scroll container (an inner app div on SPA sites, else the page root).
    const scrollers = Array.from(doc.querySelectorAll<HTMLElement>(".tp-html, .tp-html *")).filter((el) => {
      const oy = getComputedStyle(el).overflowY;
      return (oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight + 50;
    });
    const scroller = scrollers.sort((a, b) => b.scrollHeight - a.scrollHeight)[0] ?? null;
    const header = doc.querySelector("header[data-web-header-state]");
    if (scroller && header) {
      scroller.addEventListener("scroll", () =>
        header.setAttribute("data-web-header-state", scroller.scrollTop > 4 ? "scrolled" : "static"),
      );
    }
    setFrameState("ready");
    setLoadedTick((n) => n + 1);
  }, []);

  // Load the captured page files, then render them into an isolated shadow root.
  useEffect(() => {
    let cancelled = false;
    loadPageAssets()
      .then((assets) => {
        const host = hostRef.current;
        if (cancelled || !host) return;
        ensureGlobalPageCss(assets.globalCss);
        const root = host.shadowRoot ?? host.attachShadow({ mode: "open" });
        if (!root.querySelector("[data-copy]")) root.innerHTML = buildShadowMarkup(assets);
        attach(root);
      })
      .catch(() => {
        if (!cancelled) setFrameState("blocked");
      });
    return () => {
      cancelled = true;
    };
  }, [attach]);

  // Apply saved copy, editability and highlight state to the replica.
  useEffect(() => {
    const doc = docRef.current;
    if (!doc) return;
    const pageRoot = doc.querySelector(".tp-html");
    pageRoot?.classList.toggle("copy-editing", canWrite);
    pageRoot?.classList.toggle("copy-show-changes", showChanges);
    doc.querySelectorAll<HTMLElement>("[data-copy]").forEach((el) => {
      const key = el.getAttribute("data-copy")!;
      const original = originals.current.get(key) ?? "";
      const override = overrides.get(key);
      const desired = override ?? original;
      if (doc.activeElement !== el && el.textContent !== desired) el.textContent = desired;
      if (override !== undefined && override !== original) el.setAttribute("data-copy-edited", "");
      else el.removeAttribute("data-copy-edited");
      if (!canWrite) {
        if (el.getAttribute("contenteditable") !== "false") el.setAttribute("contenteditable", "false");
      } else if (!el.isContentEditable) {
        el.setAttribute("contenteditable", "plaintext-only");
        if (!el.isContentEditable) el.setAttribute("contenteditable", "true");
        el.setAttribute("spellcheck", "true");
      }
    });
  }, [overrides, canWrite, showChanges, loadedTick]);

  const changes = useMemo(
    () =>
      blocks
        .filter((b) => overrides.has(b.key) && overrides.get(b.key) !== b.original)
        .map((b) => ({
          ...b,
          text: overrides.get(b.key)!,
          edit: ((edits ?? []) as Edit[]).find((e) => e.key === b.key) ?? null,
        })),
    [blocks, overrides, edits],
  );

  const jumpTo = (key: string) => {
    const el = docRef.current?.querySelector<HTMLElement>(`[data-copy="${key}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.removeAttribute("data-copy-flash");
    void el.offsetWidth;
    el.setAttribute("data-copy-flash", "");
  };

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice(label);
    } catch {
      setNotice("Clipboard is blocked in this view");
    }
    window.setTimeout(() => setNotice(null), 2200);
  };

  const changesMarkdown = () => {
    const lines = [`# ${PAGE_LABEL} copy changes`, ""];
    let last = "";
    for (const c of changes) {
      if (c.section !== last) {
        lines.push(`## ${c.section}`, "");
        last = c.section;
      }
      lines.push(`- **Before:** ${clean(c.original)}`, `  **After:** ${c.text}`, "");
    }
    return lines.join("\n");
  };

  const fullCopyMarkdown = () => {
    const lines = [`# ${PAGE_LABEL} (with edits)`, ""];
    let last = "";
    for (const b of blocks) {
      if (b.section !== last) {
        lines.push("", `## ${b.section}`);
        last = b.section;
      }
      const text = overrides.get(b.key) ?? clean(b.original);
      if (clean(text)) lines.push(`- ${text}`);
    }
    return lines.join("\n");
  };

  const vw = VIEWPORTS[viewport].width;
  const scale = stage.w > 0 ? Math.min(1, (stage.w - 32) / vw) : 1;
  const frameHeight = stage.h > 0 ? (stage.h - 32) / scale : 900;

  return (
    <div className="copy-editor">
      <header className="copy-toolbar">
        <div className="copy-toolbar-title">
          <strong>{PAGE_LABEL}</strong>
          <span className="text-secondary text-small">Captured {CAPTURED}</span>
          {!canWrite ? (
            <span className="badge">View only</span>
          ) : frameState === "ready" ? (
            <span className="badge badge-success">Editing on · {blocks.length} text blocks</span>
          ) : frameState === "blocked" ? (
            <span className="badge badge-error">Couldn't load the page</span>
          ) : (
            <span className="badge">Loading page…</span>
          )}
        </div>
        <div className="segmented" role="tablist" aria-label="Viewport">
          {(Object.keys(VIEWPORTS) as Viewport[]).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={viewport === v}
              className={viewport === v ? "segmented-item is-active" : "segmented-item"}
              onClick={() => setViewport(v)}
            >
              {VIEWPORTS[v].label}
              <span className="text-muted"> {VIEWPORTS[v].width}</span>
            </button>
          ))}
        </div>
        <div className="copy-toolbar-actions">
          {edits === undefined && <span className="badge">Loading copy…</span>}
          {error && <span className="badge badge-error">{error}</span>}
          {notice && <span className="badge badge-success">{notice}</span>}
          <label className="toggle">
            <input type="checkbox" checked={showChanges} onChange={(e) => setShowChanges(e.target.checked)} />
            Highlight changes
          </label>
          <button className="button" onClick={() => setPanelOpen((o) => !o)}>
            Changes <span className="count">{changes.length}</span>
          </button>
        </div>
      </header>

      <div className="copy-body">
        <div className="copy-stage" ref={stageRef}>
          <div className="copy-frame-wrap" style={{ width: vw * scale, height: frameHeight * scale }}>
            <div
              ref={hostRef}
              className="copy-frame"
              aria-label={`${PAGE_LABEL} replica`}
              style={{ width: vw, height: frameHeight, transform: `scale(${scale})` }}
            />
          </div>
        </div>

        {panelOpen && (
          <aside className="copy-panel">
            <div className="copy-panel-head">
              <div>
                <div className="copy-panel-title">Copy changes</div>
                <div className="text-secondary text-small">
                  {canWrite ? "Click any text on the page to edit it. Enter saves, Esc cancels." : "You can view changes but not edit."}
                </div>
              </div>
            </div>
            <div className="row copy-panel-actions">
              <button className="button" disabled={!changes.length} onClick={() => copyToClipboard(changesMarkdown(), "Changes copied")}>
                Copy changes
              </button>
              <button className="button" disabled={!blocks.length} onClick={() => copyToClipboard(fullCopyMarkdown(), "Full page copy copied")}>
                Copy full page
              </button>
            </div>
            <div className="copy-panel-list">
              {edits === undefined ? (
                <p className="text-secondary text-small">Loading changes…</p>
              ) : changes.length === 0 ? (
                <div className="empty">
                  <p>No changes yet</p>
                  <p className="text-secondary text-small">Edits you make on the page show up here with the original text beside them.</p>
                </div>
              ) : (
                changes.map((c) => (
                  <div key={c.key} className="change card">
                    <div className="text-label change-section">{c.section}</div>
                    <div className="change-before">{clean(c.original)}</div>
                    <div className="change-after">{c.text}</div>
                    <div className="row change-foot">
                      <span className="text-muted text-small">{c.edit?.updatedBy ?? ""}</span>
                      <span className="spacer" />
                      <button className="button button-small" onClick={() => jumpTo(c.key)}>Show</button>
                      {canWrite && (
                        <button
                          className="button button-small"
                          onClick={() =>
                            revertEdit({ page: PAGE, key: c.key }).catch(() => setError("Couldn't revert. Try again."))
                          }
                        >
                          Revert
                        </button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

export default App;
