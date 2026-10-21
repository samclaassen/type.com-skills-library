#!/usr/bin/env python3
"""DayNova Company AI Assessment: score a leader's answers and render the branded report.

Usage:
  python3 build_report.py answers.json [--out report.html] [--pdf]

answers.json:
{
  "name": "Sam Rivera",            # optional, the leader taking it
  "role": "VP Marketing",          # optional
  "company": "Acme",               # optional
  "scope": "Marketing team",       # optional, what the leader is rating (team, function, whole company)
  "stage": "in_practice",          # "foundation" or "in_practice"
  "answers": {"n1":4,"n2":3,"n3":2,"o1":3,"o2":2,"o3":4,"v1":3,"v2":4,"v3":2,"a1":3,"a2":3,"a3":2}
}                                   # raw Likert 1-5 exactly as answered; the script handles reverse items

The answers file is checked strictly before anything is written: one JSON object, no duplicate keys,
ratings must be whole numbers 1-5 (true/false, decimals and text are rejected), identity fields must be text.

Prints a JSON summary to stdout and writes the HTML report.
"""
import argparse, datetime, html, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
Q = json.load(open(os.path.join(ROOT, "references", "questions.json"), encoding="utf-8"))
LOGO = open(os.path.join(ROOT, "assets", "logo.svg"), encoding="utf-8").read()

# ---------------------------------------------------------------- content
PILLAR_COPY = {
    "narrow": {
        "what": "Focus: knowing where AI should go first, and why.",
        "strong": "Your team knows where AI belongs and what it's for. That focus keeps tools serving strategy.",
        "growth": "Sharper priorities will make every AI investment count for more.",
        "moves": {
            "foundation": ["Run a 90-minute leadership session to agree on the first two or three outcomes AI should deliver, and why those first.",
                           "Write a one-page AI intent statement: priorities, what's out of scope, and how you'll measure success."],
            "in_practice": ["Audit every AI tool in use: who owns it, what it's for, and what success looks like. Retire or consolidate what has no owner.",
                            "Agree, as a team, which decisions stay human and which are good candidates for AI support."],
        },
    },
    "orchestrate": {
        "what": "Coordination: clear workflows, roles, handoffs and owners.",
        "strong": "Work flows with clear roles and owners, so AI has a solid structure to plug into.",
        "growth": "Clearer workflows and ownership give AI a stable place to plug in and scale.",
        "moves": {
            "foundation": ["Map one core workflow from idea to delivery: every step, owner and handoff. It becomes the blueprint for where AI fits.",
                           "Name a single accountable owner for AI decisions in your team, with a clear remit."],
            "in_practice": ["Pick the task type where people use the most different tools, and agree on one shared approach and standard.",
                            "Document where each AI tool plugs into your process and who's responsible for it."],
        },
    },
    "validate": {
        "what": "Judgment: shared standards and review before work goes out.",
        "strong": "Your team owns the judgment layer, so speed doesn't come at the cost of quality.",
        "growth": "Shared quality standards will let the team move faster with more confidence.",
        "moves": {
            "foundation": ["Write down what good looks like for your three most important deliverables, before AI starts producing them.",
                           "Hold a 45-minute risk conversation covering brand, compliance and accuracy for your specific work, and capture the rules."],
            "in_practice": ["Create a shared review checklist for AI-generated content and make it part of the definition of done.",
                            "Run a short calibration session where the team reviews AI outputs together, so everyone spots the same misses."],
        },
    },
    "amplify": {
        "what": "Ambition: turning AI capacity into strategic work and real outcomes.",
        "strong": "You're turning AI capacity into real strategic value, not just doing more of the same.",
        "growth": "Pointing freed-up capacity at strategic work turns AI efficiency into growth.",
        "moves": {
            "foundation": ["Ask the team: if execution capacity doubled, what strategic work would we take on? Capture the top three answers.",
                           "Add one outcome-based measure (not volume) to how the team's AI work will be judged."],
            "in_practice": ["Pick one measurable business outcome to attribute to AI this quarter, and track it from a baseline.",
                            "Reserve a set share of the capacity AI frees up for one named strategic initiative."],
        },
    },
}

SECONDARY_CTA = ("Bring your people into the picture",
                 "Readiness is half the story. Have your team take the DayNova Individual AI Assessment to see what fuels each person through change and where their AI habits are strongest.")

# ---------------------------------------------------------------- styles
CSS = r"""
:root{--bg:#081224;--bg2:#101A2F;--card:rgba(245,247,250,.045);--line:#263247;--text:#F5F7FA;--text2:#A8B3C7;--muted:rgba(245,247,250,.58);
--amber:#F2A93B;--orange:#E97A1F;--cta:#D96A1B;--cyan:#1FA9C9;--blue:#4B8FD8;
--sunrise:linear-gradient(90deg,#F4C414 0%,#F2A93B 18%,#E97A1F 38%,#D94FA6 62%,#8A63C7 82%,#4B8FD8 100%);}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);font-family:'DM Sans',system-ui,sans-serif;font-weight:300;line-height:1.6;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{max-width:860px;margin:0 auto;padding:40px 32px 56px}
.top{display:flex;justify-content:space-between;align-items:center;gap:16px}
.logo{height:30px;width:auto}
.tag{font-family:Figtree,sans-serif;font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--amber)}
.rule{height:3px;background:var(--sunrise);border-radius:2px;margin:18px 0 36px}
.eyebrow{font-family:Figtree,sans-serif;font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--amber);margin-bottom:10px}
h1{font-family:Figtree,sans-serif;font-weight:900;font-size:clamp(30px,5vw,44px);line-height:1.05;letter-spacing:-.02em;margin-bottom:12px}
h2{font-family:Figtree,sans-serif;font-weight:900;font-size:26px;line-height:1.15;letter-spacing:-.01em;margin-bottom:10px}
h3{font-family:Figtree,sans-serif;font-weight:800;font-size:17px;margin-bottom:6px}
.lead{color:var(--text2);font-size:17px;max-width:680px;overflow-wrap:anywhere}
section{margin-top:40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:26px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.hero{display:grid;grid-template-columns:1.25fr 1fr;gap:16px;margin-top:32px}
.big{font-family:Figtree,sans-serif;font-weight:900;font-size:54px;line-height:1;background:var(--sunrise);-webkit-background-clip:text;background-clip:text;color:transparent}
.big small{font-size:20px;color:var(--muted);-webkit-text-fill-color:var(--muted)}
.pill{display:inline-block;font-family:Figtree,sans-serif;font-weight:700;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#081224;background:var(--amber);border-radius:999px;padding:4px 12px;margin:10px 0 8px}
.muted{color:var(--muted);font-size:14px}
.text2{color:var(--text2)}
ul.clean{list-style:none;margin-top:10px}
ul.clean li{padding:8px 0 8px 22px;position:relative;border-top:1px solid var(--line);color:var(--text2)}
ul.clean li:first-child{border-top:none}
ul.clean li:before{content:"";position:absolute;left:2px;top:17px;width:8px;height:8px;border-radius:50%;background:var(--orange)}
.bar{margin:14px 0}
.bar .row{display:flex;justify-content:space-between;font-family:Figtree,sans-serif;font-weight:700;font-size:14px;margin-bottom:6px}
.bar .row span:last-child{color:var(--muted);font-weight:600}
.track{height:8px;border-radius:99px;background:rgba(245,247,250,.09);overflow:hidden}
.fill{height:100%;border-radius:99px;background:var(--sunrise)}
.bar p{color:var(--muted);font-size:13px;margin-top:6px}
.letter{display:inline-flex;width:26px;height:26px;border-radius:7px;align-items:center;justify-content:center;background:rgba(242,169,59,.14);color:var(--amber);font-family:Figtree,sans-serif;font-weight:900;font-size:13px;margin-right:8px}
.moves{counter-reset:m;display:grid;gap:12px}
.move{display:grid;grid-template-columns:40px 1fr;gap:14px;align-items:start}
.move:before{counter-increment:m;content:counter(m);font-family:Figtree,sans-serif;font-weight:900;font-size:18px;width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:var(--sunrise);color:#081224}
.move .k{font-family:Figtree,sans-serif;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--amber)}
.cta{background:linear-gradient(125deg,rgba(232,96,26,.20),rgba(139,74,140,.18) 55%,rgba(59,111,212,.20));border:1px solid rgba(242,169,59,.35)}
.btn{display:inline-block;margin-top:14px;background:var(--cta);color:#FFF7F0;text-decoration:none;font-family:Figtree,sans-serif;font-weight:700;font-size:15px;padding:12px 20px;border-radius:10px}
.foot{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.chip{border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--text2)}
.chip.on{border-color:var(--amber);color:var(--amber)}
@media (max-width:700px){.hero,.grid2{grid-template-columns:1fr}.page{padding:28px 16px 44px}.big{font-size:44px}}
@page{size:letter;margin:0}
@media print{.page{padding:28px 36px}section{break-inside:avoid}.btn{border:1px solid #FFF7F0}}
"""
# ---------------------------------------------------------------- scoring
def fail(msg):
    sys.stderr.write("ERROR: " + msg + "\n"); sys.exit(2)

# ---------------------------------------------------------------- input checks
# Everything below runs before scoring and before any report file is opened, so a bad
# answers file stops with a clear ERROR and leaves no partial report behind.
class _DuplicateKey(ValueError):
    pass

def _unique_keys(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise _DuplicateKey(k)
        obj[k] = v
    return obj

def _show(v):
    """A value as it appears in JSON, shortened for error messages."""
    try:
        s = json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(v)
    return s if len(s) <= 60 else s[:57] + "..."

def _kind(v):
    if v is None: return "nothing (null)"
    if isinstance(v, bool): return "a true/false value"
    if isinstance(v, int): return "a whole number"
    if isinstance(v, float): return "a decimal number"
    if isinstance(v, str): return "text"
    if isinstance(v, list): return "a list"
    if isinstance(v, dict): return "an object"
    return type(v).__name__

def _got(v):
    """Describe a received value for an error message, e.g. 'true (a true/false value)'."""
    if v is None:
        return "nothing (null)"
    if isinstance(v, (list, dict)):
        return f"{_kind(v)} {_show(v)}"
    return f"{_show(v)} ({_kind(v)})"

def _is_whole(v):
    # bool is a subclass of int in Python; true/false are never valid ratings.
    return type(v) is int

def fail_all(errors):
    if len(errors) == 1:
        fail(errors[0])
    fail("the answers file has problems:\n  - " + "\n  - ".join(errors))

def _path(trail):
    """Field path text such as scarf.3[4] for a trail of (step, parent) links, where a step is a key (str) or index (int)."""
    steps = []
    while trail is not None:
        step, trail = trail
        steps.append(step)
    where = ""
    for step in reversed(steps):
        if isinstance(step, int):
            where = f"{where}[{step}]"
        else:
            where = f"{where}.{step}" if where else step
    return where

def _unencodable(data):
    """Where a string or key holds text that can't be written as UTF-8, or None.
    JSON allows an unpaired surrogate escape such as "\\ud800"; it decodes to no real character.
    Iterative on purpose: Python 3.12+ parses JSON nested deeper than the recursion limit.
    Checks each key, then its value, in file order, and names the first problem found."""
    stack = [(False, data, None)]  # (is_key, value, trail of the value's own position)
    while stack:
        is_key, v, trail = stack.pop()
        if isinstance(v, str):
            try:
                v.encode("utf-8")
            except UnicodeEncodeError:
                where = _path(trail)
                if is_key:
                    return f"a field name in {where}" if where else "a top-level field name"
                return where or "the answers file"
        elif isinstance(v, dict):
            for k, x in reversed(list(v.items())):
                stack.append((False, x, (k, trail)))
                stack.append((True, k, trail))
        elif isinstance(v, list):
            for i in range(len(v) - 1, -1, -1):
                stack.append((False, v[i], (i, trail)))
    return None

def load_answers(path):
    """Read the answers file strictly: valid UTF-8 JSON, one object, no duplicate keys at any depth,
    and only text that can be written back out as UTF-8."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f, object_pairs_hook=_unique_keys)
    except _DuplicateKey as e:
        fail(f"{path} repeats the key {e.args[0]!r}. Each key may appear only once; "
             "keep the one answer the person actually gave.")
    except json.JSONDecodeError as e:
        fail(f"{path} is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}.")
    except UnicodeDecodeError:
        fail(f"{path} is not UTF-8 text.")
    except RecursionError:
        fail(f"{path} is nested too deeply to be an answers file.")
    except OSError as e:
        fail(f"can't read {path}: {e.strerror or e}.")
    except ValueError as e:
        # Last, because the handlers above are ValueError subclasses. Catches JSON that parses
        # but can't become a value, such as an integer longer than Python's 4300-digit limit.
        fail(f"{path} has a value that can't be read: {str(e).split(';')[0]}.")
    bad = _unencodable(data)
    if bad:
        fail(f"{bad} contains a broken character: an unpaired surrogate escape such as \\ud800, "
             "which isn't a real character. Retype that text.")
    return data

def check_identity(data, fields, errors):
    """Optional identity fields must be text. Blank text and null count as left out."""
    for f in fields:
        if f not in data:
            continue
        v = data[f]
        if v is None:
            del data[f]
        elif not isinstance(v, str):
            errors.append(f"{f} must be text in quotes, or left out; got {_got(v)}")
        elif not v.strip():
            del data[f]
        else:
            data[f] = v.strip()

def write_report(out, page):
    """Write the finished page in one step, so a failure never leaves an empty or partial report."""
    out_abs = os.path.abspath(out)
    tmp = f"{out_abs}.tmp-{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(page)
        os.replace(tmp, out_abs)
    except OSError as e:
        fail(f"can't write the report to {out}: {e.strerror or e}.")
    finally:
        # Whatever stopped the write, never leave the temporary file behind.
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

TOP_FIELDS = ("name", "role", "company", "scope", "stage", "answers")

def validate(data):
    if not isinstance(data, dict):
        fail(f"the answers file must be one JSON object shaped like references/answers.example.json; got {_kind(data)}.")
    errors = []
    unknown = [k for k in data if k not in TOP_FIELDS]
    if unknown:
        errors.append(f"unknown field(s) {unknown}; allowed fields are {list(TOP_FIELDS)}")
    check_identity(data, ("name", "role", "company", "scope"), errors)
    stage = data.get("stage")
    if not (isinstance(stage, str) and stage in Q["stages"]):
        errors.append(f"stage must be one of {list(Q['stages'])}; got {_got(stage)}")
    answers = data.get("answers")
    if not isinstance(answers, dict):
        errors.append(f"answers must be an object of question ids to 1-5 ratings, like references/answers.example.json; got {_kind(answers)}")
    else:
        st = Q["stages"][stage] if isinstance(stage, str) and stage in Q["stages"] else next(iter(Q["stages"].values()))
        ids = [q["id"] for p in st["pillars"] for q in p["questions"]]
        extra = [k for k in answers if k not in ids]
        if extra:
            errors.append(f"answers has unknown question id(s) {extra}; expected exactly {ids}")
        for qid in ids:
            if qid not in answers:
                errors.append(f"Question {qid} is missing; it needs a whole-number 1-5 rating")
                continue
            raw = answers[qid]
            if not (_is_whole(raw) and 1 <= raw <= 5):
                errors.append(f"Question {qid} needs a whole-number 1-5 rating; got {_got(raw)}")
    if errors:
        fail_all(errors)
    return data

def score(stage, answers):
    if stage not in Q["stages"]:
        fail(f"stage must be one of {list(Q['stages'])}")
    st = Q["stages"][stage]
    pillars = {}
    for p in st["pillars"]:
        pillars[p["key"]] = 0
        for q in p["questions"]:
            raw = answers.get(q["id"])
            if not isinstance(raw, int) or not 1 <= raw <= 5:
                fail(f"Question {q['id']} needs a 1-5 answer, got {raw!r}")
            pillars[p["key"]] += (6 - raw) if q["reverse"] else raw
    total = sum(pillars.values())
    band = next((b for b in st["bands"] if total <= b["max"]), st["bands"][-1])
    keys = [p["key"] for p in st["pillars"]]
    order = sorted(keys, key=lambda k: (pillars[k], keys.index(k)))
    return {"stage": stage, "stage_label": st["label"], "pillars": pillars, "total": total, "max": 60,
            "band": band["name"], "band_obj": band, "growth": order[0], "second_growth": order[1],
            "strongest": sorted(keys, key=lambda k: (-pillars[k], keys.index(k)))[0]}

def pick_moves(r):
    s = r["stage"]
    g, g2 = r["growth"], r["second_growth"]
    t = {p["key"]: p["title"] for p in Q["stages"][s]["pillars"]}
    return [{"pillar": t[g], "text": PILLAR_COPY[g]["moves"][s][0]},
            {"pillar": t[g], "text": PILLAR_COPY[g]["moves"][s][1]},
            {"pillar": t[g2], "text": PILLAR_COPY[g2]["moves"][s][0]}]

# ---------------------------------------------------------------- render
def esc(s): return html.escape(str(s))

def bar(label, letter, val, mx, note):
    pct = round(100 * val / mx)
    return (f'<div class="bar"><div class="row"><span><span class="letter">{letter}</span>{esc(label)}</span>'
            f'<span>{val} / {mx}</span></div><div class="track"><div class="fill" style="width:{max(pct,3)}%"></div></div>'
            f'<p>{esc(note)}</p></div>')

def render(data, r, moves):
    st = Q["stages"][r["stage"]]
    b = r["band_obj"]
    t = {p["key"]: p["title"] for p in st["pillars"]}
    who = ", ".join(x for x in [data.get("name"), data.get("role"), data.get("company")] if x)
    scope = data.get("scope")
    date = (lambda d: f"{d:%B} {d.day}, {d.year}")(datetime.date.today())
    bars = "".join(bar(p["title"], p["letter"], r["pillars"][p["key"]], 15, PILLAR_COPY[p["key"]]["what"]) for p in st["pillars"])
    # band ladder
    lo = 12
    ladder = ""
    for x in st["bands"]:
        on = " on" if x["name"] == b["name"] else ""
        ladder += f'<span class="chip{on}">{esc(x["name"])} · {lo}–{x["max"]}</span>'
        lo = x["max"] + 1
    mv = "".join(f'<div class="move"><div><div class="k">{esc(m["pillar"])}</div><div class="text2">{esc(m["text"])}</div></div></div>' for m in moves)
    gq = next(p["quote"] for p in st["pillars"] if p["key"] == r["growth"])
    lead = (f"Prepared for {who}. " if who else "") + (f"A readiness read on {scope}, " if scope else "A readiness read on your team, ") + f"on the {st['label']} path."
    title = "Company AI Assessment" + (f" · {data['company']}" if data.get("company") else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Figtree:wght@700;800;900&family=DM+Sans:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="page">
<div class="top">{LOGO}<div class="tag">Company AI Assessment</div></div>
<div class="rule"></div>
<div class="eyebrow">Team AI readiness · {esc(st['label'])}</div>
<h1>Here's where your team stands.</h1>
<p class="lead">{esc(lead)}</p>

<div class="hero">
  <div class="card"><div class="eyebrow">Overall readiness</div>
    <div class="big">{r['total']}<small> / 60</small></div>
    <span class="pill">{esc(b['name'])}</span>
    <p class="text2">{esc(b['blurb'])}</p>
    <div class="chips">{ladder}</div></div>
  <div class="card"><div class="eyebrow">At a glance</div>
    <h3>Strongest: {esc(t[r['strongest']])}</h3><p class="text2">{esc(PILLAR_COPY[r['strongest']]['strong'])}</p>
    <h3 style="margin-top:16px">Biggest gains: {esc(t[r['growth']])}</h3><p class="text2">{esc(PILLAR_COPY[r['growth']]['growth'])}</p>
    <p class="muted" style="margin-top:12px">"{esc(gq)}"</p></div>
</div>

<section class="card"><div class="eyebrow">The NOVA pillars</div>
  <h2>Pillar by pillar.</h2>
  {bars}
  <p class="muted">Each pillar is scored out of 15 from three statements. Statements that describe a gap are reverse-scored.</p>
</section>

<section class="card"><div class="eyebrow">Your next 30 days</div><h2>Three moves to start with.</h2>
  <div class="moves" style="margin-top:14px">{mv}</div></section>

<section class="card cta"><div class="eyebrow">How DayNova helps</div><h2>{esc(b['cta']['label'])}</h2>
  <p class="text2">DayNova maps AI strategy to how your team actually works, builds the workflows with the biggest gains, and embeds the capability with neuroscience-backed coaching, so it sticks.</p>
  <a class="btn" href="{esc(b['cta']['href'])}">{esc(b['cta']['label'])} →</a></section>

<section class="grid2"><div class="card" style="grid-column:1/-1"><h3>{esc(SECONDARY_CTA[0])}</h3><p class="text2">{esc(SECONDARY_CTA[1])}</p></div></section>

<div class="foot"><span>DayNova · People innovate, AI elevates.</span><span>{date} · Built on DayNova's NOVA framework.</span></div>
</div></body></html>"""

def _find_chrome():
    """Locate a Chrome/Chromium/Edge binary for headless PDF printing."""
    import shutil
    env = os.environ.get("CHROME_PATH")
    if env and os.path.exists(env):
        return env
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "chrome", "microsoft-edge", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(path):
            return path
    return None

def to_pdf(html_path):
    """Write a Letter-size PDF next to the HTML. Tries Playwright, then a local Chrome/Chromium.
    Returns the PDF path, or None when no headless browser is available (the HTML is still the report)."""
    pdf = os.path.splitext(html_path)[0] + ".pdf"
    url = "file://" + os.path.abspath(html_path)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page()
            pg.goto(url, wait_until="networkidle")
            pg.pdf(path=pdf, format="Letter", print_background=True)
            br.close()
        return os.path.abspath(pdf)
    except Exception as e:  # missing package or missing browser build: fall back
        sys.stderr.write(f"Playwright PDF unavailable ({type(e).__name__}); trying a local Chrome/Chromium.\n")
    chrome = _find_chrome()
    if not chrome:
        sys.stderr.write("PDF skipped: no headless Chrome/Chromium found. Deliver the HTML report.\n")
        return None
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as profile:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
               f"--user-data-dir={profile}", "--no-pdf-header-footer", "--virtual-time-budget=8000",
               f"--print-to-pdf={os.path.abspath(pdf)}", url]
        try:
            subprocess.run(cmd, check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            sys.stderr.write(f"PDF skipped: {e}\n")
            return None
    return os.path.abspath(pdf) if os.path.exists(pdf) and os.path.getsize(pdf) > 0 else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("--out")
    ap.add_argument("--pdf", action="store_true", help="also write a PDF next to the HTML (Playwright or a local Chrome/Chromium)")
    a = ap.parse_args()
    data = validate(load_answers(a.answers))
    r = score(data["stage"], data["answers"])
    moves = pick_moves(r)
    slug = re.sub(r"[^a-z0-9]+", "-", (data.get("company") or data.get("name") or "report").lower()).strip("-")[:60].strip("-")
    out = a.out or f"{datetime.date.today():%Y-%m-%d}-company-ai-assessment-{slug}.html"
    write_report(out, render(data, r, moves))
    summary = {k: v for k, v in r.items() if k != "band_obj"}
    summary.update({"report": os.path.abspath(out), "band_blurb": r["band_obj"]["blurb"],
                    "recommended_next_step": r["band_obj"]["cta"]["label"], "moves": moves})
    if a.pdf:
        summary["pdf"] = to_pdf(out)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
