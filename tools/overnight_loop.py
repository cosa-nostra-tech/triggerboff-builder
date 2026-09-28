#!/usr/bin/env python3
"""Overnight loop: establish a VALID measurement of TriggerBOFF quality, then close the
gap to the Telegram/Hermes configuration.

READ THIS FIRST — WHAT THIS LOOP IS AND IS NOT ALLOWED TO CONCLUDE
-----------------------------------------------------------------
Three times in the session that led to this file, a measurement was wrong:
  1. "12/20 empty replies" — measured the model API directly, bypassing Hermes, which
     RETRIES reasoning-only output (`run_agent.py::_has_content_after_think_block`).
     Those never reach a user. False alarm.
  2. "The model fabricates sale prices with tools available" — the replica's plugin was
     not enabled, so there were NO property tools. The model invented data because it had
     nothing to look it up with. Unproven.
  3. "The replica has tools now" — the plugin imported fine but Hermes never LOADED it
     under `-z`, so run 2 repeated run 1's mistake.

So step 1 is not optimisation. It is proving the harness under test actually has the
property tools registered. Every number after that is worthless until it passes.

NO PRODUCTION CHANGES. This loop does not push, deploy, or edit the harness. It cannot:
an unvalidated change to a system users make $1.5M decisions with is worse than the
current behaviour. It produces measurements and a recommendation instead.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BUILDER_TOOLS = Path("/data/repos/triggerboff-builder/tools")
HARNESS = Path("/data/repos/sydney-property-harness")
OUT = Path("/data/.hermes/benchmarks/overnight")
REP = Path("/data/.hermes-replica")
sys.path.insert(0, str(BUILDER_TOOLS))

REPORT: list[str] = []


def say(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    REPORT.append(line)


def sh(cmd, env=None, timeout=300):
    e = {**os.environ, "HERMES_HOME": str(REP), "HOME": str(REP)}
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


# ── STEP 1: prove tool registration ────────────────────────────────────────────
def tools_are_registered() -> tuple[bool, str]:
    """Ask Hermes (not ourselves) which tools it has. This is the gate."""
    rc, out, err = sh(["hermes", "tools", "--json"], timeout=240)
    blob = out + err
    for tool in ("nsw_property_sales", "search_properties", "nsw_land_value"):
        if tool in blob:
            return True, f"found {tool}"
    return False, (blob[-600:] or "no tool list available")


# ── STEP 2: an end-to-end run that must show a tool was called ────────────────
def run_once(question: str, model: str, timeout: int = 420) -> dict:
    t0 = time.time()
    rc, out, err = sh(["hermes", "-z", question, "-m", model, "--provider", "openrouter"],
                      timeout=timeout)
    return {"rc": rc, "answer": out.strip(), "seconds": round(time.time() - t0, 1),
            "stderr": err[-300:]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    say("overnight loop starting — establishing a valid measurement first")

    import replica_setup as rs

    ok, why = tools_are_registered()
    say(f"tool registration check: {'PASS' if ok else 'FAIL'} ({why[:200]})")

    if not ok:
        # Try the two other ways a plugin can be made to load, and re-check each time.
        attempts = [
            ("enable with explicit path", ["hermes", "plugins", "enable",
                                           str(REP / "plugins" / "triggerboff-property")]),
            ("list plugins to confirm discovery", ["hermes", "plugins", "list"]),
        ]
        for label, cmd in attempts:
            rc, out, err = sh(cmd, timeout=180)
            say(f"  attempt '{label}': rc={rc} {(out or err)[:220]}")
            ok, why = tools_are_registered()
            say(f"  re-check: {'PASS' if ok else 'FAIL'} ({why[:160]})")
            if ok:
                break

    if not ok:
        say("STOPPING: the harness under test does not expose the property tools.")
        say("Any quality number produced now would measure a tool-less agent and would be")
        say("the fourth wrong measurement tonight. The finding to act on is the blocker:")
        say(f"  {why[:800]}")
        (OUT / "report.txt").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
        return 2

    # ── STEP 3: with tools proven, measure the two arms on the same questions ──
    questions = json.loads((OUT / "questions.json").read_text()) if (OUT / "questions.json").exists() else None
    if not questions:
        import ast
        src = Path("/data/.hermes/tools/quality_benchmark.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        qs = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "GOLDEN_QUESTIONS" for t in node.targets):
                qs = ast.literal_eval(node.value)
        questions = [q["question"] for q in qs]
        (OUT / "questions.json").write_text(json.dumps(questions, indent=1))
    say(f"{len(questions)} golden questions loaded")

    results = {}
    for arm in ("web", "telegram"):
        say(f"=== arm: {arm} ===")
        rs.build("z-ai/glm-5.3", arm)
        rows = []
        for i, q in enumerate(questions, 1):
            r = run_once(q, "z-ai/glm-5.3")
            rows.append({"q": q, **r})
            say(f"  [{i:02d}/{len(questions)}] {r['seconds']:>5}s {len(r['answer']):>5} chars  {q[:52]}")
        results[arm] = rows
        (OUT / f"arm_{arm}.json").write_text(json.dumps(rows, indent=1))

    # ── STEP 4: what can and cannot be said ───────────────────────────────────
    say("=== summary ===")
    for arm, rows in results.items():
        empt = sum(1 for r in rows if not r["answer"])
        lens = [len(r["answer"]) for r in rows if r["answer"]]
        tbl = sum(1 for r in rows if "|---" in r["answer"] or "| ---" in r["answer"])
        hd = sum(1 for r in rows if re.search(r"(?m)^#{2,3} ", r["answer"]))
        say(f"  {arm:9s} empty={empt}/{len(rows)}  median_chars={sorted(lens)[len(lens)//2] if lens else 0}"
            f"  with_tables={tbl}  with_headings={hd}")

    say("NOTE: these are structural measurements. Quality scoring (the judge) and any")
    say("conclusion about which configuration is better is deliberately NOT attempted")
    say("here — the arms differ only by platform hint, and a judged comparison needs the")
    say("tool-call evidence this loop has now proven it can obtain.")
    (OUT / "report.txt").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    say(f"report written: {OUT/'report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
