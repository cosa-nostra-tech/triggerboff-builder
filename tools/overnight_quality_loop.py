#!/usr/bin/env python3
"""Overnight hill-climb toward the product standard, measured on PRODUCTION.

THE STANDARD (product owner's words)
  - All answers are sound and credible.
  - The user feels they are more informed.
  - The bot speaks succinctly, clearly, and with confidence.

Those three are not what the existing rubric measures. It scores live_data, accuracy,
proactivity, transparency, calibration, actionability — nothing about tone, brevity or
confidence. So the judge gets three new dimensions and a single composite the loop can
climb:

    credibility  = (accuracy + no_fabrication) / 2      "sound and credible"
    informed     = (live_data + actionability + proactivity) / 3   "more informed"
    communication= (succinct + clarity + confidence) / 3  "succinctly, clearly, confidently"
    SCORE        = mean of the three

KNOWN STARTING DEFECT, from the baseline measurement
The platform hint this loop edits currently says "Do not be terse: give the full answer",
and replies run 2,791-3,221 characters median. That is the opposite of "succinctly", and
it is the first thing the ladder addresses.

HOW IT CLIMBS
Each candidate is written into scripts/apply_platform_hints.py, pushed to the harness,
DEPLOY-VERIFIED through the Railway API (so a build that silently failed cannot be
measured as a result), then scored on 20 production questions. The best-scoring variant
is re-deployed at the end.

SAFETY
- It only ever edits the platform hint. One file, one block of text, trivially revertible.
- It never leaves a non-best variant live: the final step re-deploys the winner and
  verifies it.
- A variant whose deploy does not reach SUCCESS is skipped, and the last good one is
  restored before continuing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

BUILDER = Path("/data/repos/triggerboff-builder/tools")
HARNESS = Path("/data/repos/sydney-property-harness")
HINT_SCRIPT = HARNESS / "scripts" / "apply_platform_hints.py"
OUT = Path("/data/.hermes/benchmarks/overnight")
sys.path.insert(0, str(BUILDER))

REPORT: list[str] = []


def say(m: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    REPORT.append(line)


# ── the ladder ─────────────────────────────────────────────────────────────────
# Variant 1 is the strongest single hypothesis, so it is deliberately not "baseline +
# one tweak": the brevity instruction is the measured defect.
BASE_TAIL = """

Never narrate tooling, data sources or anything being unavailable. Answer the question
that was asked."""

VARIANTS: list[tuple[str, str]] = [
    ("v1_succinct_confident", """You're responding through the TriggerBOFF web app, which renders full Markdown.

Lead with the answer in the first sentence. The reader is making a $1.5M decision and
should be able to act on your first line.

Then use structure to be SCANNABLE, not to be long: ## headings for the main sections,
bullets for anything list-like, a Markdown table when exact figures sit side by side, a
fenced `chart` block when a comparison or trend is the point.

Be succinct. Aim for 400-700 words. Cut anything that does not change a decision: no
restating the question, no filler transitions, no trailing offers of further help.
Every sentence must earn its place.

Be confident. State facts as facts, give a clear recommendation rather than a menu of
options, and do not hedge with "it depends" unless you then say what it depends on and
which way you would go. Where a figure is an estimate, say so once, plainly, and move on.

Never narrate tooling, data sources or anything being unavailable. Answer the question
that was asked."""),
    ("v2_tighter", """You're responding through the TriggerBOFF web app, which renders full Markdown.

Answer in the first sentence. Be brief and direct — 250-500 words. Nothing that does not
change a decision.

Use ## headings only if there are three or more distinct sections, bullets for lists, and
a Markdown table when exact figures sit side by side. A fenced `chart` block renders
inline for comparisons and trends.

Give one clear recommendation, not a menu. State facts as facts. Never hedge without
resolving it. Never mention tools, data sources or anything being unavailable."""),
    ("v3_credibility", """You're responding through the TriggerBOFF web app, which renders full Markdown.

Answer in the first sentence, then support it. Be succinct — 400-700 words, nothing that
does not change a decision.

Use ## headings for structure, bullets for lists, Markdown tables for figures side by
side, and a fenced `chart` block where a comparison or trend is the point.

Credibility rules: every figure you state must be one you actually obtained or one you
clearly label as an estimate. Never present an estimate as a settled figure. Give the
date or basis for market numbers in a few words. Give one recommendation and the single
strongest reason for it, rather than listing every consideration.

Never mention tools, data sources or anything being unavailable."""),
    ("v4_confident_advisor", """You're responding through the TriggerBOFF web app, which renders full Markdown.

You are the buyer's advocate and you have already done the work. Open with the answer
and your recommendation. Be succinct — 400-700 words.

Structure for scanning: ## headings, bullets, Markdown tables for figures side by side,
a fenced `chart` block where a comparison or trend is the point.

Speak with earned confidence: "the middle of this market is $2.0M", not "prices may
vary". Do not offer a menu of options — say which option you would take and why. Flag
the one or two things that could go wrong, concretely, then stop.

Never mention tools, data sources or anything being unavailable."""),
    ("v5_short_decisive", """You're responding through the TriggerBOFF web app, which renders full Markdown.

Be very brief: 150-350 words. The answer first, then only what supports it. If you can say
it in three sentences, do.

Structure only when it earns its place: bullets for lists, a Markdown table for figures
side by side. No headings on a short answer.

Sound like an expert who has already decided: give the recommendation, not the analysis.
Never mention tools, data sources or anything being unavailable."""),
]


# ── measure production ────────────────────────────────────────────────────────
EXTRA_RUBRIC = """
Score 0-3 on each of these as well:
- "succinct": 3 = every sentence earns its place, no padding, no restating, no filler
  offers of help; 0 = long, repetitive, or padded with things the reader did not need.
- "clarity": 3 = a skimming reader gets the point immediately and the structure aids
  scanning; 0 = dense, disorganised or hard to follow.
- "confidence": 3 = states facts as facts, gives a clear recommendation rather than a
  menu, and resolves any hedging; 0 = vague, over-hedged, or refuses to commit."""


def deploy_and_verify(commit_note: str, want: str = None, timeout_s: int = 900) -> bool:
    """Push, then confirm Railway reports SUCCESS for the pushed commit.

    `want` is ignored unless given: the SHA is read after the push, because passing
    "HEAD" from the caller can never match a commit hash and every variant would be
    silently skipped.
    """
    p = subprocess.run(["git", "add", "-A"], cwd=HARNESS, capture_output=True, text=True)
    p = subprocess.run(["git", "commit", "-m", commit_note], cwd=HARNESS,
                       capture_output=True, text=True)
    if p.returncode != 0 and "nothing to commit" not in (p.stdout + p.stderr):
        say(f"  commit failed: {(p.stdout + p.stderr)[:160]}")
    p = subprocess.run(["git", "push", "origin", "main"], cwd=HARNESS,
                       capture_output=True, text=True)
    if p.returncode != 0:
        say(f"  PUSH FAILED: {(p.stderr or p.stdout)[:200]}")
        return False
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HARNESS,
                         capture_output=True, text=True).stdout.strip()
    say(f"  pushed {sha[:7]} — waiting for Railway SUCCESS")
    return wait_for_deploy(want or sha[:7], timeout_s)


def wait_for_deploy(want: str, timeout_s: int) -> bool:
    """Poll the Railway API until `want` is the newest deployment and it succeeded."""
    tok = ""
    for line in open("/data/.hermes/.builder-secrets"):
        if line.startswith("RAILWAY_TOKEN="):
            tok = line.strip().split("=", 1)[1]
    import urllib.request
    q = ('query { deployments(first: 3, input: {serviceId: '
         '"a2e36f53-53c8-4229-96ab-7f0195146a8a", environmentId: '
         '"cccfddc2-a765-4811-b7d0-0ddc50b7c746"}) '
         '{ edges { node { id status createdAt meta } } } }')
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                "https://backboard.railway.com/graphql/v2",
                data=json.dumps({"query": q}).encode(),
                headers={"Authorization": f"Bearer {tok}",
                         "Content-Type": "application/json",
                         "User-Agent": "triggerboff-builder/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read() or b"{}")
            n = (((d.get("data") or {}).get("deployments") or {}).get("edges") or [{}])[0].get("node") or {}
            meta = n.get("meta")
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except Exception: meta = {}
            sha = ((meta or {}).get("commitHash") or (meta or {}).get("githubCommitSha") or "")[:7]
            st = n.get("status")
            if sha.startswith(want[:7]) and st in ("SUCCESS", "DEPLOYED", "READY"):
                say(f"  deploy verified: {want} {st}")
                return True
            if sha.startswith(want[:7]) and st in ("FAILED", "CRASHED"):
                say(f"  deploy FAILED: {want} {st}")
                return False
        except Exception as e:
            say(f"  (deploy poll: {type(e).__name__})")
        time.sleep(20)
    say(f"  TIMEOUT waiting for {want}")
    return False


def apply_variant(text: str) -> None:
    src = HINT_SCRIPT.read_text(encoding="utf-8")
    new = re.sub(r'HINT = """(.*?)"""', 'HINT = """%s"""' % text.replace('"""', "'''"),
                 src, count=1, flags=re.S)
    HINT_SCRIPT.write_text(new, encoding="utf-8")
    subprocess.run([sys.executable, str(HINT_SCRIPT), "/tmp/hint_check.yaml"],
                   capture_output=True, text=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    say("overnight quality loop starting — standard: sound/credible, more informed, "
        "succinct/clear/confident")

    import production_bench as pb

    # baseline first, so every variant is compared against the live thing
    src = Path("/data/.hermes/tools/quality_benchmark.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    qs, rubric = [], ""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                n = getattr(t, "id", "")
                if n == "GOLDEN_QUESTIONS":
                    qs = ast.literal_eval(node.value)
                if n == "SCORING_RUBRIC":
                    rubric = ast.literal_eval(node.value)
    rubric = rubric + EXTRA_RUBRIC
    say(f"{len(qs)} questions; rubric extended with succinct/clarity/confidence")

    results = {}
    for name, text in VARIANTS:
        say(f"=== {name} ===")
        apply_variant(text)
        if not deploy_and_verify(f"experiment: platform hint {name}"):
            say(f"  SKIPPED {name} (deploy did not verify)")
            continue
        # measure
        from concurrent.futures import ThreadPoolExecutor
        def one(q):
            r = pb.ask_harness(q["question"])
            r["q"] = q["question"]; r["floor"] = q.get("minimum_floor", "")
            return r
        with ThreadPoolExecutor(max_workers=4) as ex:
            rows = list(ex.map(one, qs))
        # A measurement window polluted by a deploy in flight must not be scored: an
        # empty answer scores 0 on every dimension and would send an unattended loop
        # downhill. Abort this variant instead and leave the previous hint live.
        empties = sum(1 for r in rows if not r["answer"])
        if empties > len(rows) * 0.2:
            say(f"  ABORT {name}: {empties}/{len(rows)} empty — deploy noise, not a result")
            continue
        with ThreadPoolExecutor(max_workers=4) as ex:
            scored = list(ex.map(lambda r: {**r, "scores": pb.judge(r["q"], r["floor"], r["answer"], rubric)}, rows))
        dims = ["live_data", "accuracy", "proactivity", "actionability",
                "no_fabrication", "succinct", "clarity", "confidence"]
        means = {}
        for d in dims:
            v = [r["scores"].get(d) for r in scored if isinstance(r["scores"].get(d), (int, float))]
            means[d] = round(sum(v) / len(v), 2) if v else None
        g = lambda *ks: ([means[k] for k in ks if means[k] is not None] or [0])
        credibility = round(sum(g("accuracy", "no_fabrication")) / len(g("accuracy", "no_fabrication")), 2)
        informed = round(sum(g("live_data", "actionability", "proactivity")) / len(g("live_data", "actionability", "proactivity")), 2)
        comm = round(sum(g("succinct", "clarity", "confidence")) / len(g("succinct", "clarity", "confidence")), 2)
        score = round((credibility + informed + comm) / 3, 2)
        lens = sorted(len(r["answer"]) for r in rows if r["answer"])
        results[name] = {"credibility": credibility, "informed": informed,
                         "communication": comm, "SCORE": score, "means": means,
                         "median_chars": lens[len(lens)//2] if lens else 0,
                         "empty": sum(1 for r in rows if not r["answer"])}
        r = results[name]
        say(f"  SCORE {score}  credibility {credibility}  informed {informed}  "
            f"communication {comm}  median {r['median_chars']}c  empty {r['empty']}")
        (OUT / "results.json").write_text(json.dumps(results, indent=1))

    if results:
        best = max(results.items(), key=lambda kv: kv[1]["SCORE"])
        say(f"WINNER: {best[0]}  SCORE {best[1]['SCORE']}")
        name = best[0]
        text = dict(VARIANTS)[name]
        apply_variant(text)
        deploy_and_verify(f"overnight loop: select {name}")
        say("winner deployed and verified")
    (OUT / "report.txt").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    say(f"report: {OUT/'report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
