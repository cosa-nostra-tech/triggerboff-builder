#!/usr/bin/env python3
"""Measure PRODUCTION end-to-end: the railway harness, real tools, real loop, judged.

This is the measurement every earlier one was a proxy for. It posts the golden questions
to the live harness exactly as the web app does (no client system message — the harness
applies SOUL.md and the platform hint itself), then scores each answer with an
independent judge against the suite's own rubric.

Includes a structural check that needs no judge: did the reply come back at all, did it
use Markdown headings, and did it emit a table. Those three are the visible difference
between the two platform hints, so they test the fix that was deployed.
"""
from __future__ import annotations

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HARNESS = "https://sydney-property-harness-production-0135.up.railway.app/v1/chat/completions"
OUT = Path("/data/.hermes/benchmarks")
JUDGE = "anthropic/claude-opus-4.6"
OR = "https://openrouter.ai/api/v1/chat/completions"


def _secrets() -> dict:
    s = {}
    for path in ("/data/.hermes/harness-secrets", "/data/.hermes/.builder-secrets"):
        if os.path.exists(path):
            for line in open(path):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    s.setdefault(k, v)
    return s


S = _secrets()
HARNESS_KEY = S.get("API_SERVER_KEY", "")
OR_KEY = S.get("OPENROUTER_API_KEY", "") or (
    # fall back to the container env
    next((p.split(b"=", 1)[1].decode() for p in
          open("/proc/1/environ", "rb").read().split(b"\0")
          if p.startswith(b"OPENROUTER_API_KEY=")), ""))


def ask_harness(question: str, timeout: int = 300, attempts: int = 4) -> dict:
    """Ask the live harness. Retries empty answers.

    An empty reply is almost always a deploy in flight, not a bad answer. Scoring one as
    0 would make a good variant look terrible and send an unattended loop downhill — so
    empties are retried, and if they persist the caller can see `transient=True` instead
    of a score.
    """
    last = None
    for i in range(attempts):
        last = _ask_once(question, timeout)
        if last.get("answer"):
            last["transient"] = False
            return last
        if i < attempts - 1:
            time.sleep(20)
    if last is not None:
        last["transient"] = True
    return last or {"ok": False, "answer": "", "seconds": 0, "error": "no attempt made",
                    "transient": True}


def _ask_once(question: str, timeout: int = 300) -> dict:
    body = {"model": "hermes-agent", "stream": False,
            "messages": [{"role": "user", "content": question}]}
    req = urllib.request.Request(
        HARNESS, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {HARNESS_KEY}", "X-API-Key": HARNESS_KEY,
                 "Content-Type": "application/json",
                 "X-Hermes-Session-Key": "prod-bench", })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read() or b"{}")
        txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        # Some paths return the reply under `response`/`message`.
        if not txt:
            txt = d.get("response") or d.get("message") or ""
        return {"ok": True, "answer": txt, "seconds": round(time.time() - t0, 1)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "answer": "", "seconds": round(time.time() - t0, 1),
                "error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "answer": "", "seconds": round(time.time() - t0, 1),
                "error": f"{type(e).__name__}: {e}"}


def judge(question: str, floor: str, response: str, rubric: str) -> dict:
    prompt = f"""{rubric}

QUESTION ASKED:
{question}

THE USER WOULD ALSO EXPECT (the floor):
{floor}

RESPONSE TO SCORE:
---
{response}
---

Reply with ONLY a JSON object, exactly these keys:
{{"live_data":0-3,"accuracy":0-3,"proactivity":0-3,"transparency":0-3,"calibration":0-3,"actionability":0-3,"no_fabrication":0-3,"succinct":0-3,"clarity":0-3,"confidence":0-3,"why":"one short sentence"}}

"succinct": 3 = every sentence earns its place, no padding, no restating, no filler offers
of further help; 0 = long, repetitive or padded.
"clarity": 3 = a skimming reader gets the point immediately and structure aids scanning;
0 = dense or disorganised.
"confidence": 3 = facts stated as facts, a clear recommendation rather than a menu of
options, hedging resolved; 0 = vague, over-hedged or refuses to commit."""
    body = {"model": JUDGE, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2500, "temperature": 0}
    req = urllib.request.Request(OR, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {OR_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            d = json.loads(r.read() or b"{}")
        txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {"error": "unparseable"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    src = Path("/data/.hermes/tools/quality_benchmark.py").read_text(encoding="utf-8")
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
    print(f"production endpoint : {HARNESS}")
    print(f"key present         : {bool(HARNESS_KEY)} ({len(HARNESS_KEY)} chars)")
    print(f"questions           : {len(qs)}")

    # Preflight: one cheap call must work before spending on 20.
    pre = ask_harness("Reply with exactly: PROD_OK")
    print(f"preflight           : ok={pre['ok']} {pre.get('error','')[:120]}")
    if not pre["ok"]:
        print("STOP: cannot reach production — not scoring anything.")
        return 1

    DIMS = ["live_data", "accuracy", "proactivity", "transparency", "calibration",
            "actionability", "no_fabrication"]

    def one(q):
        r = ask_harness(q["question"])
        r["q"] = q["question"]
        r["floor"] = q.get("minimum_floor", "")
        return r

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, qs))

    with ThreadPoolExecutor(max_workers=4) as ex:
        scored = list(ex.map(lambda r: {**r, "scores": judge(r["q"], r["floor"], r["answer"], rubric)}, rows))

    means = {}
    for d in DIMS:
        v = [r["scores"].get(d) for r in scored if isinstance(r["scores"].get(d), (int, float))]
        means[d] = round(sum(v) / len(v), 2) if v else None
    overall = [v for v in means.values() if v is not None]
    mean_all = round(sum(overall) / len(overall), 2) if overall else None

    empt = sum(1 for r in rows if not r["answer"])
    errs = [r.get("error") for r in rows if r.get("error")]
    heads = sum(1 for r in rows if re.search(r"(?m)^#{2,3} ", r["answer"]))
    tbl = sum(1 for r in rows if re.search(r"(?m)^\|.*\|", r["answer"]))
    lens = sorted(len(r["answer"]) for r in rows if r["answer"])

    print(f"\n=== PRODUCTION RESULT ({len(rows)} questions) ===")
    print(f"  mean           : {mean_all}/3")
    print("  " + "  ".join(f"{d[:4]}={means[d]}" for d in DIMS))
    print(f"  empty replies  : {empt}/{len(rows)}")
    print(f"  errors         : {len(errs)} {errs[:2]}")
    print(f"  median length  : {lens[len(lens)//2] if lens else 0} chars")
    print(f"  with headings  : {heads}/{len(rows)}")
    print(f"  with tables    : {tbl}/{len(rows)}")
    print("\n  per question:")
    for r in sorted(scored, key=lambda x: -(x["scores"].get("accuracy") or 0)):
        s = r["scores"]
        print(f"    acc={s.get('accuracy')} pro={s.get('proactivity')} nof={s.get('no_fabrication')}"
              f"  {len(r['answer']):>5}c  {r['q'][:52]}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "production_result.json").write_text(json.dumps(
        {"mean": mean_all, "means": means, "empty": empt, "heads": heads, "tables": tbl,
         "rows": scored}, indent=1))
    print(f"\nsaved -> {OUT/'production_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
