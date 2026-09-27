"""TriggerBOFF Golden Question Benchmark Suite.

Fires 20 property questions at the live TriggerBOFF agent and scores each
response across 6 dimensions: live_data, accuracy, proactivity, transparency,
calibration, actionability.

Usage:
    python3 quality_benchmark.py --check      # preflight only: reach + auth
    python3 quality_benchmark.py              # run the suite, print the report
    python3 quality_benchmark.py --baseline   # same, saved as the baseline

Environment:
    API_SERVER_KEY         the harness gateway's API server key (required)
    TRIGGERBOFF_API_URL    override the endpoint host (optional)

Exit codes: 0 = measured, 1 = preflight failed, 2 = ran but nothing measured.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Endpoint ───────────────────────────────────────────────────────────────────
# The harness is an admin/reverse-proxy server, NOT an agent API. The previous
# default posted to `{bare_host}/chat` — a route it does not have (the only
# "/chat" in server.py is a comment). Every run hit the proxied dashboard,
# 401'd, and was then SCORED AS IF the product had answered badly, producing a
# fake 0/18.
#
# The real contract is the one the frontend uses (app/api/chat/route.ts):
#   POST {host}/v1/chat/completions        Authorization: Bearer <API_SERVER_KEY>
#   {"model": "hermes-agent", "messages": [...]}
# The agent endpoint lives on the port-suffixed hostname, not the bare one.
RAILWAY_URL = os.environ.get(
    "TRIGGERBOFF_API_URL",
    os.environ.get("RAILWAY_URL",
                   "https://sydney-property-harness-production-0135.up.railway.app"),
).rstrip("/")

CHAT_PATH = "/v1/chat/completions"

# No hardcoded fallback key: a key committed to a repo is a leaked key (the old
# default was exactly that). Resolve from the environment and fail loudly.
API_KEY = (
    os.environ.get("API_SERVER_KEY")
    or os.environ.get("TRIGGERBOFF_API_KEY")
    or os.environ.get("RAILWAY_API_KEY")
    or os.environ.get("HERMES_API_KEY")
    or ""
)

BENCHMARK_DIR = Path(os.environ.get("HERMES_HOME", "/data/.hermes")) / "benchmarks"

# The six scored dimensions, in one place.
SCORE_DIMENSIONS = ["live_data", "accuracy", "proactivity", "transparency",
                    "calibration", "actionability"]

GOLDEN_QUESTIONS = [
    # Live data questions — forces tool use
    {
        "id": "q01",
        "category": "live_data",
        "question": "What have homes actually sold for in Marrickville in the last 6 months? Give me real numbers.",
        "minimum_floor": "Must cite actual sale prices or median, not just general knowledge"
    },
    {
        "id": "q02",
        "category": "live_data",
        "question": "What is the current rental yield in Newtown for a 2-bedroom apartment?",
        "minimum_floor": "Must use live rental data, not historical estimates"
    },
    {
        "id": "q03",
        "category": "live_data",
        "question": "How long would it take me to commute from Dulwich Hill to the CBD by public transport?",
        "minimum_floor": "Must return specific minutes and transport modes"
    },
    # Calculation questions — forces accuracy
    {
        "id": "q04",
        "category": "calculation",
        "question": "I'm a first home buyer with $120K saved earning $140K. What's the full cost stack on a $950K property in NSW?",
        "minimum_floor": "Must include stamp duty (with FHB exemption), LMI, legal, inspection fees"
    },
    {
        "id": "q05",
        "category": "calculation",
        "question": "I have $200K deposit and earn $180K. What's the maximum I can realistically borrow and what suburbs does that open up?",
        "minimum_floor": "Must calculate borrowing capacity with specific figure"
    },
    # Scheme eligibility
    {
        "id": "q06",
        "category": "schemes",
        "question": "Do I qualify for the First Home Guarantee? I earn $95K and want to buy a $750K property.",
        "minimum_floor": "Must check current income and price cap thresholds, give yes/no"
    },
    {
        "id": "q07",
        "category": "schemes",
        "question": "What's the difference between the First Home Guarantee and Help to Buy?",
        "minimum_floor": "Must accurately describe both with current thresholds"
    },
    # Judgement + calibrated uncertainty
    {
        "id": "q08",
        "category": "judgement",
        "question": "Is now a good time to buy in Sydney or should I wait 12 months?",
        "minimum_floor": "Must show calibrated uncertainty, not just one directional answer"
    },
    {
        "id": "q09",
        "category": "judgement",
        "question": "The price guide says $1.1M-$1.2M. What do you think it will actually sell for?",
        "minimum_floor": "Must explain underquoting, give likely range above guide"
    },
    # Domain-specific trap questions
    {
        "id": "q10",
        "category": "traps",
        "question": "The agent wants me to sign a Section 66W certificate. Should I?",
        "minimum_floor": "Must explain it waives cooling off — critical NSW-specific knowledge"
    },
    {
        "id": "q11",
        "category": "traps",
        "question": "The strata levies are $1,200 a quarter. Is that normal?",
        "minimum_floor": "Must contextualise vs property type and Sydney norms"
    },
    {
        "id": "q12",
        "category": "traps",
        "question": "What should I look for in a Section 10.7 certificate?",
        "minimum_floor": "Must explain planning restrictions, LEP, zoning, heritage, flood"
    },
    # Multi-step reasoning
    {
        "id": "q13",
        "category": "reasoning",
        "question": "Compare Marrickville, Dulwich Hill, and Petersham for a family with a $1.3M budget and primary school age kids.",
        "minimum_floor": "Must cover price, schools, character, and make a recommendation"
    },
    {
        "id": "q14",
        "category": "reasoning",
        "question": "I found a terrace in Erskineville for $1.4M. Walk me through everything I need to do before exchange.",
        "minimum_floor": "Must cover B&P, strata, S10.7, solicitor, cooling-off timeline"
    },
    # Planning and development
    {
        "id": "q15",
        "category": "planning",
        "question": "Can the block next door be developed? It's zoned R2 in Marrickville.",
        "minimum_floor": "Must explain R2 height/FSR limits and what can be built"
    },
    # Proactivity tests
    {
        "id": "q16",
        "category": "proactivity",
        "question": "I want to buy in Annandale. Budget is $1.5M.",
        "minimum_floor": "Must proactively give cost stack, suburb stats, and key traps without being asked"
    },
    {
        "id": "q17",
        "category": "proactivity",
        "question": "Just got pre-approval for $900K.",
        "minimum_floor": "Must proactively calculate full budget after costs and suggest next steps"
    },
    # Confidence + source transparency
    {
        "id": "q18",
        "category": "transparency",
        "question": "What's the median house price in Balmain right now?",
        "minimum_floor": "Must cite data source and recency, not just give a number"
    },
    # Memory/continuity
    {
        "id": "q19",
        "category": "continuity",
        "question": "Based on everything you know about my situation, what should I be focusing on right now?",
        "minimum_floor": "Must reference prior conversation context if available, or explain what it would need"
    },
    # Confidence signal — the one that matters most
    {
        "id": "q20",
        "category": "confidence",
        "question": "I'm about to make the biggest financial decision of my life. Can I trust the information you're giving me?",
        "minimum_floor": "Must be honest about limitations while building legitimate confidence"
    },
]

SCORING_RUBRIC = """
Score this response 0-3 on each dimension. Be rigorous — 3 means genuinely excellent.

Dimensions:
- live_data: Did it use real live data (from tools/APIs), not just LLM knowledge? (0=no data cited, 1=old/vague data, 2=data cited, 3=specific recent data with source)
- accuracy: Are the figures, thresholds, and facts correct? (0=wrong, 1=partially right, 2=mostly right, 3=fully accurate)
- proactivity: Did it volunteer something the user didn't explicitly ask for but needed? (0=none, 1=minor, 2=useful addition, 3=exactly what a domain expert would add)
- transparency: Did it show its working, cite sources, and acknowledge uncertainty? (0=no, 1=some, 2=mostly, 3=fully transparent)
- calibration: Was its confidence level appropriate — not over- or under-confident? (0=badly miscalibrated, 1=slightly off, 2=mostly right, 3=perfectly calibrated)
- actionability: Does the user know what to do next? (0=unclear, 1=vague, 2=clear, 3=specific next step given)

Return JSON only:
{"live_data": N, "accuracy": N, "proactivity": N, "transparency": N, "calibration": N, "actionability": N, "root_cause": "one sentence on the biggest gap"}
"""


def query_railway(question: str, session_id: str = "benchmark", timeout: int = 120) -> dict:
    """Send a question to the TriggerBOFF agent endpoint.

    Returns {"response", "latency_s", "status", "error"}. A non-None `error`
    means the answer could not be MEASURED (bad key, wrong host, timeout) —
    callers must not score that as a quality failure.
    """
    if not API_KEY:
        return {"response": "", "latency_s": 0.0, "status": "no_key",
                "error": "No API key. Set API_SERVER_KEY to the harness gateway's "
                         "API server key (Railway → sydney-property-harness → Variables)."}

    url = f"{RAILWAY_URL}{CHAT_PATH}"
    payload = json.dumps({
        "model": "hermes-agent",
        "messages": [{"role": "user", "content": question}],
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "X-API-Key": API_KEY,
    }

    start = time.time()
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        reply = ""
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                reply = (choices[0].get("message") or {}).get("content") or ""
            reply = reply or data.get("response") or data.get("message") or ""
        latency = round(time.time() - start, 2)
        if not reply:
            return {"response": "", "latency_s": latency, "status": "empty",
                    "error": "Endpoint returned 200 with no message content."}
        return {"response": reply, "latency_s": latency, "status": "ok", "error": None}

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()[:300]
        except Exception:
            body = ""
        if e.code in (401, 403):
            status = "auth"
            hint = (" — the gateway rejected the key. Set API_SERVER_KEY in this "
                    "container's environment to the harness gateway key.")
        elif e.code == 404:
            status = "wrong_route"
            hint = (f" — {CHAT_PATH} not found on this host. The agent endpoint lives "
                    "on the port-suffixed hostname, not the bare production host.")
        else:
            status, hint = "http", ""
        return {"response": "", "latency_s": round(time.time() - start, 2),
                "status": status, "error": f"HTTP {e.code}: {body}{hint}"}

    except Exception as e:  # noqa: BLE001 — never raise into the run loop
        return {"response": "", "latency_s": round(time.time() - start, 2),
                "status": "network", "error": f"{type(e).__name__}: {e}"}


def check_connectivity() -> dict:
    """Preflight: can we actually reach and authenticate to the agent?

    Run this BEFORE any benchmark so an auth/config failure is reported as
    'not measured' instead of being scored as a bad answer.
    """
    print(f"Endpoint : {RAILWAY_URL}{CHAT_PATH}")
    print(f"API key  : {'set (len %d)' % len(API_KEY) if API_KEY else 'MISSING'}")
    if not API_KEY:
        return {"ok": False, "status": "no_key",
                "detail": "API_SERVER_KEY not set — nothing to test."}
    r = query_railway("Reply with exactly: PROBE_OK", session_id="preflight", timeout=60)
    if r["error"]:
        print(f"Result   : FAIL ({r['status']})\n  {r['error'][:200]}")
        return {"ok": False, "status": r["status"], "detail": r["error"]}
    print(f"Result   : OK in {r['latency_s']}s -> {r['response'][:60]!r}")
    return {"ok": True, "status": "ok", "latency_s": r["latency_s"],
            "sample": r["response"][:120]}


def score_response(question: str, response: str, minimum_floor: str) -> dict:
    """Score a response using a simple heuristic (LLM scoring would be better)."""
    # Heuristic scoring — replace with LLM judge when OpenRouter key available
    scores = {}

    resp_lower = response.lower()

    # live_data: did it mention specific numbers, dates, sources?
    has_numbers = any(c.isdigit() for c in response)
    has_source = any(w in resp_lower for w in ["domain", "abs", "valuer general", "nsw", "data", "source", "recorded"])
    scores["live_data"] = min(3, (1 if has_numbers else 0) + (1 if has_source else 0) + (1 if len(response) > 500 else 0))

    # accuracy: hard to check heuristically — give 2 if response is substantive
    scores["accuracy"] = 2 if len(response) > 200 and has_numbers else 1

    # proactivity: does it go beyond the literal question?
    beyond_question = len(response) > 600
    scores["proactivity"] = 2 if beyond_question else 1

    # transparency: mentions uncertainty or sources
    has_uncertainty = any(w in resp_lower for w in ["may", "could", "approximately", "around", "estimate", "suggest", "recommend"])
    scores["transparency"] = 2 if (has_source or has_uncertainty) else 1

    # calibration
    scores["calibration"] = 2

    # actionability: ends with clear next step
    has_cta = any(w in resp_lower for w in ["next step", "recommend", "should", "consider", "check", "contact", "speak"])
    scores["actionability"] = 2 if has_cta else 1

    scores["root_cause"] = "Heuristic scoring only — add OPENROUTER_API_KEY for LLM judging"
    return scores


def run_benchmark(save_as_baseline: bool = False) -> dict:
    """Run the full golden question benchmark suite."""
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    # Preflight. Without this an auth failure silently became a fake 0/18 score,
    # indistinguishable from the product genuinely answering badly.
    print("\nPreflight:")
    pre = check_connectivity()
    if not pre["ok"]:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": f"{RAILWAY_URL}{CHAT_PATH}",
            "measured": False,
            "reason": pre["status"],
            "detail": pre["detail"],
            "note": ("Benchmark NOT MEASURED. This is a connectivity/credentials "
                     "failure, not a quality result. Do not report a score."),
        }
        print(f"\n❌ Benchmark aborted — could not reach the agent ({pre['status']}).")
        print(f"   {pre['detail'][:200]}")
        return report

    results = []
    print(f"\nRunning {len(GOLDEN_QUESTIONS)} golden questions against {RAILWAY_URL}{CHAT_PATH}...")
    print("=" * 60)

    for i, q in enumerate(GOLDEN_QUESTIONS):
        print(f"[{i+1:02d}/{len(GOLDEN_QUESTIONS)}] {q['id']} ({q['category']}) — {q['question'][:60]}...")
        result = query_railway(q["question"], session_id=f"benchmark_{q['id']}")

        if result["error"]:
            # Not measured — excluded from the averages below.
            print(f"  ⚠️  Not measured ({result['status']}): {result['error'][:100]}")
            scores: dict = {d: None for d in SCORE_DIMENSIONS}
            scores["root_cause"] = f"Not measured: {result['status']}"
        else:
            scores = score_response(q["question"], result["response"], q["minimum_floor"])
            total = sum(v for k, v in scores.items() if k != "root_cause" and v is not None)
            print(f"  ✓ {result['latency_s']}s — score {total}/18")

        results.append({
            "question_id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "response": result["response"],
            "latency_s": result["latency_s"],
            "status": result["status"],
            "error": result["error"],
            "scores": scores,
        })

        time.sleep(1)  # Rate limit

    # Aggregate over MEASURED results only.
    measured = [r for r in results if r["error"] is None]
    if not measured:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": f"{RAILWAY_URL}{CHAT_PATH}",
            "measured": False,
            "reason": "all_calls_failed",
            "questions_run": len(results),
            "failed": len(results),
            "note": "Every question failed to return an answer. No quality score exists.",
            "results": results,
        }
        print("\n❌ No questions returned an answer — nothing to score.")
        return report

    total_scores = {d: 0 for d in SCORE_DIMENSIONS}
    for r in measured:
        for d in total_scores:
            total_scores[d] += r["scores"].get(d) or 0

    avg_scores = {d: round(v / len(measured), 2) for d, v in total_scores.items()}
    overall = round(sum(avg_scores.values()), 2)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": f"{RAILWAY_URL}{CHAT_PATH}",
        "measured": True,
        "questions_run": len(results),
        "questions_measured": len(measured),
        "questions_failed": len(results) - len(measured),
        "avg_scores": avg_scores,
        "overall_out_of_18": overall,
        "pass": overall >= 14,  # 78% threshold
        "results": results,
    }

    # Save
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"baseline_{ts}.json" if save_as_baseline else f"benchmark_{ts}.json"
    out_path = BENCHMARK_DIR / filename
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print(f"BENCHMARK COMPLETE — measured {len(measured)}/{len(results)} questions")
    print(f"Overall: {overall}/18 ({'PASS' if report['pass'] else 'FAIL'})")
    for d, v in avg_scores.items():
        print(f"  {d:15s}: {v}/3")
    print(f"Saved to: {out_path}")

    return report


# Hermes registry
try:
    from tools.registry import registry

    registry.register(
        name="run_quality_benchmark",
        toolset="quality",
        schema={
            "name": "run_quality_benchmark",
            "description": (
                "Run the TriggerBOFF golden question benchmark suite against the Railway API. "
                "Scores responses across 6 dimensions: live_data, accuracy, proactivity, "
                "transparency, calibration, actionability. Returns aggregate scores and "
                "identifies the biggest quality gaps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "save_as_baseline": {
                        "type": "boolean",
                        "description": "Save as the gold standard baseline (default false)"
                    }
                },
                "required": [],
            },
        },
        handler=lambda args, **kw: json.dumps(
            run_benchmark(save_as_baseline=args.get("save_as_baseline", False)),
            indent=2
        )[:4000],  # truncate for context window
        check_fn=lambda: True,
        requires_env=[],
    )
except ImportError:
    pass


if __name__ == "__main__":
    if "--check" in sys.argv:
        result = check_connectivity()
        sys.exit(0 if result.get("ok") else 1)
    report = run_benchmark(save_as_baseline="--baseline" in sys.argv)
    # Non-zero when nothing was measured, so a scheduler cannot mistake an
    # aborted run for a passing score.
    sys.exit(0 if report.get("measured") else 2)
