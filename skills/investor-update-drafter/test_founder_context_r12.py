"""Round-12 end-to-end regressions for the positive founder grammar; no network calls."""
import tempfile
from test_founder_context_r10 import run_case

CLAIM = "We will open the freight ledger module to every shipper in October."
cases = []
for cue in ("We put the launch on hold.", "We start once the tranche lands.", "Legal holds the final word.",
            "The Bramwell deal fell through.", "Bramwell passed on the round.", "Bramwell gave us the wording.", "We are Quixotic."):
    for side in ("before", "after"):
        notes = "Ilse: " + (cue + " " + CLAIM if side == "before" else CLAIM + " " + cue)
        cases.append((side + "-" + str(len(cases)), False, {"n": notes, "c": CLAIM, "sp": "Ilse"}))
for cue in ("Nix the October date.", "That is out of the question.", "The date is a fiction.", "Everything hinges on cash."):
    cases.append(("later-" + str(len(cases)), False, {"n": "Ilse: " + CLAIM + "\nBramwell: Thanks.\nBramwell: Next topic.\nIlse: " + cue, "c": CLAIM, "sp": "Ilse"}))
for role in ("sarcastic", "tentative", "being ironic"):
    cases.append(("role-" + role, False, {"n": "Ilse (" + role + "): " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for sec in ("product", "highlight", "ask"):
    claim = CLAIM[:-1] + ", cash allowing."
    cases.append(("aside-" + sec, False, {"n": "Ilse: " + claim, "c": claim, "sp": "Ilse", "sec": sec}))
for claim in ("Our director of partnerships starts on Monday.", "We open the ledger module to every shipper."):
    cases.append(("unstated-" + str(len(cases)), False, {"n": "Ilse: " + claim, "c": claim, "sp": "Ilse", "st": False}))
for cue in ("Morning all, thanks for dialling in.", "Good to see everyone.", "Quick one on hiring.",
            "We hired four engineers this year.", "Gross margin improved.", "The security audit finished.",
            "The board approved it.", "Hiring is next."):
    cases.append(("clean-" + str(len(cases)), True, {"n": "Ilse (CEO): " + CLAIM + " " + cue, "c": CLAIM, "sp": "Ilse"}))
cases.append(("clean-twin", True, {"n": "Ilse (CEO): " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
cases.append(("later-reinforcer", True, {"n": "Ilse: " + CLAIM + "\nBramwell: Thanks.\nBramwell: Next topic.\nIlse: The date is firm.", "c": CLAIM, "sp": "Ilse"}))
# Noun slots are open to novel project names; predicate and qualification syntax stays bounded.
for noun in ("ceramic observatory", "orchard console", "turbine registry"):
    claim = "We will launch the " + noun + " in December."
    for cue in ("", " The calibration checklist is complete.", " The scope vote passed."):
        cases.append(("novel-noun-" + str(len(cases)), True,
                      {"n": "Ilse (Engineering): " + claim + cue, "c": claim, "sp": "Ilse"}))
    fact = "The " + noun + " is complete."
    cases.append(("novel-status-" + str(len(cases)), True, {"n": "Ilse: " + fact, "c": fact, "sp": "Ilse", "st": False}))
for request in ("Intros to veterinary buyers.", "Please introduce us to orchard operators.",
                "Help us find ceramic suppliers.", "Can you connect us with turbine designers?"):
    cases.append(("request-" + str(len(cases)), True, {"n": "Ilse: " + request, "c": request, "sp": "Ilse", "st": False, "sec": "ask"}))
for claim in ("We open the ceramic observatory to every buyer.", "Intros to buyers. We open the ceramic observatory to every buyer.",
              "Please introduce us to buyers; we launch the observatory.", "A ceramic observatory for every buyer."):
    cases.append(("ask-assertion-" + str(len(cases)), False, {"n": "Ilse: " + claim, "c": claim, "sp": "Ilse", "st": False, "sec": "ask"}))
# Prior speaker labels and intervening acknowledgements cannot erase a framing cue.
for cue in ("For practice I am voicing the founder's draft.", "The following is an imagined announcement.",
            "Treat the next line as theatre.", "I am performing the founder's speech."):
    for speaker in ("Ilse", "Bramwell"):
        for spacer in ("", "\nBramwell: Thanks."):
            cases.append(("prior-framing-" + str(len(cases)), False,
                          {"n": speaker + ": " + cue + spacer + "\nIlse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for cue in ("The calibration checklist is complete.", "We hired two engineers.", "The scope vote passed."):
    for speaker in ("Ilse", "Bramwell"):
        cases.append(("prior-work-" + str(len(cases)), True,
                      {"n": speaker + ": " + cue + "\nBramwell: Thanks.\nIlse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
cases.append(("prior-question", True, {"n": "Bramwell: How is the ledger module going?\nIlse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for cue in ("The calibration recording is archived.", "We archived the inspection recordings.", "The turbine mapping is complete."):
    cases.append(("prior-archive-" + str(len(cases)), True,
                  {"n": "Bramwell: " + cue + chr(10) + "Ilse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for cue, keep in (("This is a staged announcement.", False), ("Treat the next sentence as theatre.", False),
                  ("The calibration recording is archived.", True), ("Good morning everyone.", True)):
    cases.append(("unlabelled-preface-" + str(len(cases)), keep,
                  {"n": cue + chr(10) + "Ilse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for prefix in ("Ilse: We will hire a designer in December.",
               "Ilse: We will hire a designer in December. The security review passed."):
    cases.append(("separate-earlier-commitment-" + str(len(cases)), True,
                  {"n": prefix + chr(10) + "Ilse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
for speaker in ("Bramwell", "Me"):
    cases.append(("separate-speaker-commitment-" + speaker, True,
                  {"n": speaker + ": We will hire a designer in December." + chr(10) + "Ilse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
me_claim = "We will ship SSO in Q4."
cases.append(("me-after-distinct-commitment", True,
              {"n": "Maren (CEO): We will open the carrier portal to all customers in November. Both engineering hires started this week." + chr(10) + "Me: " + me_claim, "c": me_claim, "sp": "Me"}))
# Agenda noun slots are business topics, not arbitrary framing words.
for cue, keep in (("Let us start with roleplay.", False), ("Let us start with fiction.", False),
                  ("Let us begin with storytelling.", False), ("Quick update on imaginary forecasts.", False),
                  ("Let us start with hiring.", True), ("Let us begin with sales.", True),
                  ("Quick update on engineering.", True), ("Let us kick off.", True)):
    for position in ("same", "prior", "later"):
        if position == "same":
            notes = "Ilse: " + cue + " " + CLAIM
        elif position == "prior":
            notes = "Bramwell: " + cue + chr(10) + "Ilse: " + CLAIM
        else:
            notes = "Ilse: " + CLAIM + chr(10) + "Bramwell: Thanks." + chr(10) + "Ilse: " + cue
        cases.append(("agenda-" + position + "-" + str(len(cases)), keep,
                      {"n": notes, "c": CLAIM, "sp": "Ilse"}))
# Complete past-performance questions retain work context without accepting framing.
for cue, keep in (("How did the month go?", True), ("How did engineering perform?", True),
                  ("How did hiring progress?", True), ("How did the quarter go?", True),
                  ("How did the turbine registry perform?", False),
                  ("How did onboarding go?", True), ("How did the billing handoff progress?", True),
                  ("How did the rehearsal go?", False), ("How did the fictitious onboarding go?", False),
                  ("How did the roleplay go?", False), ("How did the imaginary launch perform?", False),
                  ("How did hiring progress if the funding clears?", False),
                  ("How did the month go assuming the audit clears?", False)):
    cases.append(("past-question-" + str(len(cases)), keep,
                  {"n": "Bramwell: " + cue + chr(10) + "Ilse: " + CLAIM, "c": CLAIM, "sp": "Ilse"}))
failures = []
with tempfile.TemporaryDirectory() as tmp:
    for name, keep, case in cases:
        result = run_case(tmp, name, case)
        good = ("error" not in result and (all(result["present"]) and not result["reasons"] if keep else not any(result["anywhere"]) and bool(result["reasons"])))
        if not good:
            failures.append((name, result))
for failure in failures:
    print("FAIL", failure)
if failures:
    raise SystemExit(1)
print("PASS: {} round-12 founder grammar cases, 4 outputs each".format(len(cases)))
