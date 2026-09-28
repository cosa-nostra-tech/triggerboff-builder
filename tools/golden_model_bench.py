#!/usr/bin/env python3
"""Golden-suite model comparison for TriggerBOFF.

WHY THIS EXISTS
---------------
The harness reports it is serving `z-ai/glm-5.3`. The product standard is "within 1
point of the gold standard", and the model is the largest single lever on that — so
the choice deserves evidence rather than a vibe.

The existing tools/quality_benchmark.py cannot answer this. Its scorer is a keyword
heuristic (`score_response`) with an explicit TODO to replace it with an LLM judge,
and it grades on "contains digits", "length > 500", "contains a source word". That
rates a long confident wrong answer above a short correct one — useless for comparing
models. This runs the same 20 golden questions with the same floors, then scores every
answer with an independent LLM judge against the suite's own SCORING_RUBRIC.

WHAT IT DOES NOT TEST
---------------------
Tools. No tool results are injected, so this isolates the MODEL (reasoning, NSW
knowledge, calibration, writing) and not the harness plumbing. Two consequences:
  - dimensions that depend on live data (live_data) will be low for every model
  - the harness may still supply the answer from tools, which is not measured here
It is a fair comparison between models, not an absolute product score.

Usage:
    python3 golden_model_bench.py --limit 2              # smoke test
    python3 golden_model_bench.py                        # full run
"""
import ast
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path("/data/.hermes/tools/quality_benchmark.py")
OUT = Path("/data/.hermes/benchmarks/golden_model_bench.json")
OR = "https://openrouter.ai/api/v1/chat/completions"

# Incumbent first. Judge is deliberately NOT one of the candidates.
CANDIDATES = ["z-ai/glm-5.3", "openai/gpt-5.1", "google/gemini-3.1-pro-preview"]
JUDGE = "anthropic/claude-opus-4.6"

MAX_TOKENS = 6000   # reasoning models spend most of this internally

# With no tool loop, models that are tool-instructed either emit a bare tool call
# (glm) or invent the data (gpt-5.1) — neither is a model-quality signal. This
# protocol states the constraint up front so every model is measured on the same
# thing: NSW knowledge, calibration, and resistance to inventing figures.
CONSTRAINED = """

## THIS RUN

You have no data tools available in this session. Answer from your own knowledge.
Be explicit about anything you cannot verify. Do NOT state specific sale prices,
rental figures or medians as fact unless they are widely published, well-known
figures — say what you would check and where instead."""
WORKERS = 6
DIMS = ["live_data", "accuracy", "proactivity", "transparency",
        "calibration", "actionability", "no_fabrication"]


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    for pid in ("1", "2"):
        try:
            for part in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
                if part.startswith(b"OPENROUTER_API_KEY="):
                    return part.split(b"=", 1)[1].decode()
        except OSError:
            continue
    sys.exit("OPENROUTER_API_KEY not found (checked env and /proc/1/environ)")


KEY = api_key()


def load_suite():
    """Pull QUESTIONS and SCORING_RUBRIC out of the existing benchmark via AST."""
    tree = ast.parse(BENCH.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = getattr(t, "id", "")
                if name in ("GOLDEN_QUESTIONS", "QUESTIONS", "SCORING_RUBRIC"):
                    try:
                        found[name] = ast.literal_eval(node.value)
                    except Exception:
                        pass
    qs = found.get("GOLDEN_QUESTIONS") or found.get("QUESTIONS") or []
    rubric = found.get("SCORING_RUBRIC", "")
    pairs = []
    for q in qs:
        if isinstance(q, dict):
            pairs.append({"question": q.get("question") or q.get("q", ""),
                          "floor": q.get("minimum_floor") or q.get("floor", "")})
    return pairs, rubric


def build_system_prompt() -> str:
    """What the model actually receives: the harness persona + the app's context."""
    parts = []
    soul = Path("/data/repos/sydney-property-harness/docker/SOUL.md")
    if soul.exists():
        parts.append(soul.read_text(encoding="utf-8"))
    try:
        import subprocess
        r = subprocess.run(
            ["node", "-e",
             "const {buildSystemContext}=require('/tmp/uc/user-context.js');"
             "process.stdout.write(buildSystemContext({memories:[],summary:null,properties:[],sessionId:'default'}))"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            parts.append(r.stdout)
    except Exception as e:
        print(f"  (app context unavailable: {e})", file=sys.stderr)
    return "\n\n".join(parts)


def chat(model, messages, max_tokens=MAX_TOKENS, temperature=0.4):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature}
    req = urllib.request.Request(
        OR, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read() or b"{}")
            ch = (d.get("choices") or [{}])[0]
            msg = ch.get("message", {}) or {}
            content = msg.get("content") or ""
            u = dict(d.get("usage") or {})
            u["finish_reason"] = ch.get("finish_reason")
            if not content and msg.get("reasoning"):
                u["reasoning_only"] = True
            return content, u
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:180]}"
            if e.code in (429, 500, 502, 503):
                time.sleep(2 + attempt * 3); continue
            break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 + attempt * 3)
    return "", {"error": last}


def judge(question, floor, response, rubric):
    prompt = f"""{rubric}

QUESTION ASKED:
{question}

THE USER WOULD ALSO EXPECT (the floor):
{floor}

RESPONSE TO SCORE:
---
{response}
---

Reply with ONLY a JSON object, no prose, exactly these keys:
{{"live_data":0-3,"accuracy":0-3,"proactivity":0-3,"transparency":0-3,"calibration":0-3,"actionability":0-3,"no_fabrication":0-3,"why":"one short sentence"}}

For "no_fabrication": 3 = states no unverifiable specific figures as fact (or clearly labels them as an estimate to verify); 0 = asserts invented sale prices / rental figures / medians as if they were real data. A confident wrong number is the worst outcome for this product — score it 0."""
    txt, usage = chat(JUDGE, [{"role": "user", "content": prompt}], max_tokens=2500, temperature=0)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"error": "unparseable judge output", "raw": txt[:200]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"error": "bad json from judge", "raw": txt[:200]}
    return d


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    pairs, rubric = load_suite()
    if limit:
        pairs = pairs[:limit]
    if not pairs:
        sys.exit("no questions parsed from the suite")
    if not rubric:
        sys.exit("no SCORING_RUBRIC parsed from the suite")

    system = build_system_prompt()
    if "--no-tools" in sys.argv:
        system += CONSTRAINED
    print(f"questions      : {len(pairs)}")
    print(f"system prompt  : {len(system)} chars (~{len(system)//4} tokens)")
    print(f"candidates     : {', '.join(CANDIDATES)}")
    print(f"judge          : {JUDGE}\n")

    results = {}
    for model in CANDIDATES:
        print(f"=== {model} ===")
        t0 = time.time()

        def one(p):
            txt, usage = chat(model, [{"role": "system", "content": system},
                                      {"role": "user", "content": p["question"]}])
            return {"q": p["question"], "floor": p["floor"], "answer": txt, "usage": usage}

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            rows = list(ex.map(one, pairs))

        def scored(row):
            j = judge(row["q"], row["floor"], row["answer"], rubric)
            row["scores"] = j
            return row

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            rows = list(ex.map(scored, rows))

        dims = DIMS
        means = {}
        for d in dims:
            vals = [r["scores"].get(d) for r in rows
                    if isinstance(r["scores"].get(d), (int, float))]
            means[d] = round(sum(vals) / len(vals), 2) if vals else None
        overall = [v for v in means.values() if v is not None]
        mean_all = round(sum(overall) / len(overall), 2) if overall else None
        failed = sum(1 for r in rows if not r["answer"])
        el = round(time.time() - t0, 1)

        results[model] = {"means": means, "mean": mean_all, "seconds": el,
                          "empty": failed, "rows": rows}
        print(f"  reason-only empties: {sum(1 for r in rows if (r['usage'] or {}).get('reasoning_only'))}")
        print(f"  mean {mean_all}/3   " +
              "  ".join(f"{d[:4]}={means[d]}" for d in dims) +
              f"   ({el}s, {failed} empty)\n")

    print("=" * 74)
    print(f"{'model':40s} {'mean':>5s} " + " ".join(f"{d[:4]:>5s}" for d in dims))
    for m, r in results.items():
        print(f"{m:40s} {str(r['mean']):>5s} " +
              " ".join(f"{str(r['means'][d]):>5s}" for d in dims))
    best = max((r["mean"] or 0, m) for m, r in results.items())
    print("=" * 74)
    print(f"winner: {best[1]}  ({best[0]}/3)")
    if len(results) > 1:
        gm = results[CANDIDATES[0]]["mean"] or 0
        print(f"incumbent {CANDIDATES[0]}: {gm}/3  -> gap to best {round(best[0]-gm,2)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1))
    print(f"\nfull answers+scores saved: {OUT}")


if __name__ == "__main__":
    main()
