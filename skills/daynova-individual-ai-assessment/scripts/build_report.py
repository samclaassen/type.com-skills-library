#!/usr/bin/env python3
"""DayNova Individual AI Assessment: score answers and render the branded report.

Usage:
  python3 build_report.py answers.json [--out report.html] [--pdf]

answers.json:
{
  "name": "Jordan Lee",              # optional
  "role": "Marketing Manager",       # optional
  "company": "Acme",                 # optional
  "scarf": {                          # each list ranked MOST -> LEAST important, all 5 keys
    "1": ["autonomy","certainty","status","relatedness","fairness"],
    "2": [...], "3": [...], "4": [...], "5": [...]
  },
  "habits": {"n1":2,"n2":1,"o1":3,"o2":2,"v1":1,"v2":2,"a1":2,"a2":1},   # option index 0-3
  "blocker": "trust"                  # one key from questions.json habits.blocker.options
}

The answers file is checked strictly before anything is written: one JSON object, no duplicate keys,
every SCARF list ranks all five keys exactly once, habit answers are whole numbers 0-3 (true/false,
decimals and text are rejected), identity fields must be text.

Prints a JSON summary of the scores to stdout and writes the HTML report.
"""
import argparse, datetime, html, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
Q = json.load(open(os.path.join(ROOT, "references", "questions.json"), encoding="utf-8"))
LOGO = open(os.path.join(ROOT, "assets", "logo.svg"), encoding="utf-8").read()

SCARF_KEYS = ["status", "certainty", "autonomy", "relatedness", "fairness"]
PILLARS = [("narrow", "N", "Narrow"), ("orchestrate", "O", "Orchestrate"),
           ("validate", "V", "Validate"), ("amplify", "A", "Amplify")]
CTA_URL = "https://daynova.ai/ai-services"

# ---------------------------------------------------------------- content
DRIVERS = {
    "status": {
        "title": "Status",
        "headline": "You're energized by recognition and growing expertise.",
        "body": "Change lands best for you when the expertise you already bring is visible and valued, and when there's a clear story for how your value grows with AI. When people see you leading the way, you pick up momentum fast.",
        "needs": ["Visible credit for early wins with AI", "A clear picture of how your role grows, not shrinks", "Room to learn without being put on the spot in front of peers"],
        "ask": "Ask your manager to name one AI skill that would raise your profile on the team, then make that your first project.",
    },
    "certainty": {
        "title": "Certainty",
        "headline": "You do your best work with a clear map.",
        "body": "Change lands best for you when expectations, timelines, and decision rules are explicit. With a clear plan in hand, your focus goes to doing great work instead of guessing what's next.",
        "needs": ["Clear expectations for how AI fits your role", "A visible timeline and what changes when", "Simple rules for which tools and data are approved"],
        "ask": "Ask for a one-page \"what's changing and when\" for your team. If it doesn't exist, offer to help draft it.",
    },
    "autonomy": {
        "title": "Autonomy",
        "headline": "You thrive when you shape how the work gets done.",
        "body": "Change lands best for you when you have real choice in how, when, and where AI shows up in your role. Give you the room to design your own approach and you'll often find the best use cases first.",
        "needs": ["A say in which tasks AI takes on in your work", "Freedom to experiment within clear guardrails", "Input on decisions about your own workflow"],
        "ask": "Propose one workflow you'd like to redesign with AI, on your terms, and ask for the space to pilot it.",
    },
    "relatedness": {
        "title": "Relatedness",
        "headline": "You're at your best when the team moves together.",
        "body": "Change lands best for you when relationships and team culture stay strong through it. Learning side by side, sharing what works, and having people you trust to ask make the whole shift feel lighter.",
        "needs": ["Learning alongside your team, not alone", "Trusted people to ask when you're stuck", "Shared wins that strengthen how the team works together"],
        "ask": "Start a 20-minute weekly \"show and tell\" where teammates share one AI win. You'll build the connection and the skills at once.",
    },
    "fairness": {
        "title": "Fairness",
        "headline": "You're motivated when the rules apply evenly to everyone.",
        "body": "Change lands best for you when access, training, and opportunity are transparent and shared. When you can see the process is fair, you put full energy behind it and help others trust it too.",
        "needs": ["Transparent access to tools and training", "Visible, consistent rules for everyone", "Equal opportunity to take on new AI-enabled work"],
        "ask": "Ask how tools and training are being rolled out across teams, and volunteer to help make the path visible for others.",
    },
}

LEVELS = [
    (6,  "Explorer",     "You're at the start of the curve, which is where the fastest gains are. A few focused habits will change your week."),
    (12, "Practitioner", "AI is part of your toolkit. The next gains come from going deeper on a few tasks instead of dabbling across many."),
    (18, "Integrator",   "AI is woven into how you work. Your next step is turning personal wins into repeatable workflows others can use."),
    (24, "Amplifier",    "You're ahead of most. You're turning AI capacity into higher-value work, and you're well placed to bring others with you."),
]

PILLAR_COPY = {
    "narrow": {
        "what": "Knowing where AI helps in your role, and giving it the context to do it well.",
        "build": "List the five tasks that eat most of your week. Pick the one that's most repeatable and use AI on it every time for two weeks.",
        "stretch": "Turn your best prompts into reusable instructions (a project, a custom assistant, or a saved template) so every session starts with the right context.",
    },
    "orchestrate": {
        "what": "How consistently AI is built into the way your work actually flows.",
        "build": "Pick one daily moment (first inbox pass, meeting notes, or a first draft) and make AI the default step there for the next 10 workdays.",
        "stretch": "Map one multi-step workflow end to end and connect the AI steps, so the output of one step feeds the next without copy and paste.",
    },
    "validate": {
        "what": "Your judgment layer: checking AI output before it reaches anyone else.",
        "build": "Write a three-line \"what good looks like\" checklist for your most common deliverable and run every AI draft through it.",
        "stretch": "Keep a running list of where AI slips in your field. Share it with your team so everyone reviews faster and with more confidence.",
    },
    "amplify": {
        "what": "Turning time saved into bigger, more strategic work.",
        "build": "For one week, note every time AI saves you 15 minutes or more. Seeing the total makes the next investment obvious.",
        "stretch": "Block the time AI saves you each week for one strategic project you've been putting off, and protect it like a meeting.",
    },
}

BLOCKER_MOVES = {
    "start": "Ask AI itself: describe your role and your week, and ask for the ten tasks it could help with most. Then test the top three.",
    "permission": "Ask your manager or IT for the approved-tools list and what data is safe to share. One clear answer opens up everything else.",
    "trust": "Start where you can check the answer easily: summaries of documents you know well, or drafts you'd edit anyway. Confidence builds from there.",
    "time": "Put two 30-minute \"AI lab\" blocks on your calendar each week. Small, protected time beats a big plan that never starts.",
    "fit": "Describe one real workflow to AI step by step and ask it to redesign the process, not just a single task. The fit often comes from the workflow, not the tool.",
    "next": "Pick one workflow you repeat weekly and build it into a reusable assistant or automation, then share it with a teammate.",
}

NEXT_STEP = {
    "early": ("Build confidence with hands-on AI training",
              "DayNova's AI training meets you where you are, using neuroscience-backed methods that make new habits stick. Bring this report and we'll start from your driver and your gaps.",
              "Explore AI training"),
    "late":  ("Bring your team along",
              "You're ready to help others move. Invite your leader to take the DayNova Company AI Assessment, then use both reports to plan what your team builds next.",
              "Talk to DayNova"),
}

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

TOP_FIELDS = ("name", "role", "company", "scarf", "habits", "blocker")

def validate(data):
    if not isinstance(data, dict):
        fail(f"the answers file must be one JSON object shaped like references/answers.example.json; got {_kind(data)}.")
    errors = []
    unknown = [k for k in data if k not in TOP_FIELDS]
    if unknown:
        errors.append(f"unknown field(s) {unknown}; allowed fields are {list(TOP_FIELDS)}")
    check_identity(data, ("name", "role", "company"), errors)

    scarf = data.get("scarf")
    nums = [str(q["num"]) for q in Q["scarf"]["questions"]]
    if not isinstance(scarf, dict):
        errors.append(f"scarf must be an object with one ranked list per question {nums}; got {_kind(scarf)}")
    else:
        extra = [k for k in scarf if k not in nums]
        if extra:
            errors.append(f"scarf has unknown question number(s) {extra}; expected exactly {nums}")
        for n in nums:
            order = scarf.get(n)
            ok = (isinstance(order, list) and len(order) == len(SCARF_KEYS)
                  and all(isinstance(k, str) for k in order) and sorted(order) == sorted(SCARF_KEYS))
            if not ok:
                errors.append(f"SCARF question {n} must rank all five of {SCARF_KEYS} exactly once, most to least "
                              f"important, as the person ranked them; got {_got(order)}")

    habits = data.get("habits")
    ids = [q["id"] for q in Q["habits"]["questions"]]
    if not isinstance(habits, dict):
        errors.append(f"habits must be an object of question ids to option indexes 0-3; got {_kind(habits)}")
    else:
        extra = [k for k in habits if k not in ids]
        if extra:
            errors.append(f"habits has unknown question id(s) {extra}; expected exactly {ids}")
        for qid in ids:
            if qid not in habits:
                errors.append(f"Habit {qid} is missing; it needs an option index 0-3")
                continue
            v = habits[qid]
            if not (_is_whole(v) and 0 <= v <= 3):
                errors.append(f"Habit {qid} needs a whole-number option index 0-3; got {_got(v)}")

    blocker = data.get("blocker")
    opts = list(Q["habits"]["blocker"]["options"])
    if not (isinstance(blocker, str) and blocker in opts):
        errors.append(f"blocker must be one of {opts}; got {_got(blocker)}")
    if errors:
        fail_all(errors)
    return data

def score_scarf(scarf):
    rank5 = {k: 0 for k in SCARF_KEYS}
    weighted = {k: 0 for k in SCARF_KEYS}
    for q in Q["scarf"]["questions"]:
        order = scarf.get(str(q["num"])) or scarf.get(q["num"])
        if not order or sorted(order) != sorted(SCARF_KEYS):
            fail(f"SCARF question {q['num']} must rank all five of {SCARF_KEYS}, got {order}")
        for idx, key in enumerate(order):
            rank = 5 - idx
            weighted[key] += rank
            if rank == 5:
                rank5[key] += 1
    top = max(rank5.values())
    tied = [k for k in SCARF_KEYS if rank5[k] == top]
    # Same tie-break as the DayNova Diagnostic: most #1 ranks, then highest weighted total.
    dominant = sorted(tied, key=lambda k: -weighted[k])[0]
    second = sorted([k for k in SCARF_KEYS if k != dominant], key=lambda k: (-rank5[k], -weighted[k]))[0]
    return {"dominant": dominant, "secondary": second, "rank5_counts": rank5, "weighted_totals": weighted}

def score_habits(habits):
    pillars = {p[0]: 0 for p in PILLARS}
    for q in Q["habits"]["questions"]:
        v = habits.get(q["id"])
        if not isinstance(v, int) or not 0 <= v <= 3:
            fail(f"Habit {q['id']} needs an option index 0-3, got {v!r}")
        pillars[q["pillar"]] += v
    total = sum(pillars.values())
    level = next(l for l in LEVELS if total <= l[0])
    return {"pillars": pillars, "total": total, "max": 24, "level": level[1], "level_blurb": level[2]}

def pick_moves(pillars, blocker):
    order = sorted(PILLARS, key=lambda p: (pillars[p[0]], [x[0] for x in PILLARS].index(p[0])))
    moves = []
    for key, _, title in order[:2]:
        kind = "build" if pillars[key] <= 3 else "stretch"
        moves.append({"pillar": title, "text": PILLAR_COPY[key][kind]})
    moves.append({"pillar": "What you asked for", "text": BLOCKER_MOVES[blocker]})
    return moves, order[0][0], order[-1][0]

# ---------------------------------------------------------------- render
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

def esc(s): return html.escape(str(s))

def bar(label, letter, val, mx, note):
    pct = round(100 * val / mx)
    return (f'<div class="bar"><div class="row"><span><span class="letter">{letter}</span>{esc(label)}</span>'
            f'<span>{val} / {mx}</span></div><div class="track"><div class="fill" style="width:{max(pct,3)}%"></div></div>'
            f'<p>{esc(note)}</p></div>')

def render(data, scarf, habits, moves, strongest, growth):
    d = DRIVERS[scarf["dominant"]]
    d2 = DRIVERS[scarf["secondary"]]
    name = data.get("name") or ""
    who = ", ".join(x for x in [name, data.get("role"), data.get("company")] if x)
    date = (lambda d: f"{d:%B} {d.day}, {d.year}")(datetime.date.today())
    early = habits["level"] in ("Explorer", "Practitioner")
    ns = NEXT_STEP["early" if early else "late"]
    strongest_t = dict((p[0], p[2]) for p in PILLARS)[strongest]
    growth_t = dict((p[0], p[2]) for p in PILLARS)[growth]
    bars = "".join(bar(t, l, habits["pillars"][k], 6, PILLAR_COPY[k]["what"]) for k, l, t in PILLARS)
    chips = "".join(f'<span class="chip{" on" if k == scarf["dominant"] else ""}">{esc(DRIVERS[k]["title"])}</span>' for k in SCARF_KEYS)
    needs = "".join(f"<li>{esc(n)}</li>" for n in d["needs"])
    mv = "".join(f'<div class="move"><div><div class="k">{esc(m["pillar"])}</div><div class="text2">{esc(m["text"])}</div></div></div>' for m in moves)
    title = f"Individual AI Assessment{' · ' + name if name else ''}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Figtree:wght@700;800;900&family=DM+Sans:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="page">
<div class="top">{LOGO}<div class="tag">Individual AI Assessment</div></div>
<div class="rule"></div>
<div class="eyebrow">Your personal AI profile</div>
<h1>Here's how you move with AI.</h1>
<p class="lead">{esc('Prepared for ' + who + '. ' if who else '')}Two lenses: what fuels you through change, and how AI shows up in your work today.</p>

<div class="hero">
  <div class="card"><div class="eyebrow">What fuels you through change</div>
    <h2>{esc(d['title'])}</h2><p class="text2" style="font-weight:500;color:var(--text)">{esc(d['headline'])}</p>
    <p class="text2" style="margin-top:8px">{esc(d['body'])}</p>
    <div class="chips">{chips}</div>
    <p class="muted" style="margin-top:10px">Close second: {esc(d2['title'])}.</p></div>
  <div class="card"><div class="eyebrow">AI fluency</div>
    <div class="big">{habits['total']}<small> / 24</small></div>
    <span class="pill">{esc(habits['level'])}</span>
    <p class="text2">{esc(habits['level_blurb'])}</p></div>
</div>

<section class="grid2">
  <div class="card"><h3>What helps you thrive</h3><ul class="clean">{needs}</ul></div>
  <div class="card"><h3>One ask to make this week</h3><p class="text2" style="margin-top:10px">{esc(d['ask'])}</p></div>
</section>

<section class="card"><div class="eyebrow">Your NOVA habits</div>
  <h2>Strongest in {esc(strongest_t)}. Biggest gains in {esc(growth_t)}.</h2>
  {bars}
</section>

<section class="card"><div class="eyebrow">Your next three moves</div><h2>Start here.</h2>
  <div class="moves" style="margin-top:14px">{mv}</div></section>

<section class="card cta"><div class="eyebrow">How DayNova helps</div><h2>{esc(ns[0])}</h2>
  <p class="text2">{esc(ns[1])}</p><a class="btn" href="{CTA_URL}">{esc(ns[2])} →</a></section>

<div class="foot"><span>DayNova · People innovate, AI elevates.</span><span>{date} · Built on DayNova's NOVA framework and the SCARF model of change.</span></div>
</div></body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("--out")
    ap.add_argument("--pdf", action="store_true", help="also write a PDF next to the HTML (Playwright or a local Chrome/Chromium)")
    a = ap.parse_args()
    data = validate(load_answers(a.answers))
    blocker = data["blocker"]
    scarf = score_scarf(data["scarf"])
    habits = score_habits(data["habits"])
    moves, growth, strongest = pick_moves(habits["pillars"], blocker)
    slug = re.sub(r"[^a-z0-9]+", "-", (data.get("name") or "report").lower()).strip("-")[:60].strip("-")
    out = a.out or f"{datetime.date.today():%Y-%m-%d}-individual-ai-assessment-{slug}.html"
    write_report(out, render(data, scarf, habits, moves, strongest, growth))
    summary = {"report": os.path.abspath(out), "driver": scarf["dominant"], "secondary_driver": scarf["secondary"],
               "rank5_counts": scarf["rank5_counts"], "weighted_totals": scarf["weighted_totals"],
               "fluency_total": habits["total"], "fluency_level": habits["level"], "pillars": habits["pillars"],
               "strongest_pillar": strongest, "growth_pillar": growth, "moves": moves}
    if a.pdf:
        summary["pdf"] = to_pdf(out)
    print(json.dumps(summary, indent=2))

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

if __name__ == "__main__":
    main()
