"""
TriggerBOFF Golden Question Benchmark Suite.

Fires 20 property questions at the Railway TriggerBOFF API and scores each
response across 6 dimensions vs the Telegram Hermes gold standard.

Usage:
    python quality_benchmark.py [--baseline] [--report]

    --baseline: run against Telegram Hermes to establish gold standard scores
    --report:   run against Railway and compare to stored baseline
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

RAILWAY_URL = os.environ.get(
    "RAILWAY_URL",
    "https://sydney-property-harness-production.up.railway.app"
)
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "a8d3040b2c31731d")
BENCHMARK_DIR = Path(os.environ.get("HERMES_HOME", "/data/.hermes")) / "benchmarks"

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


def query_railway(question: str, session_id: str = "benchmark") -> dict:
    """Send a question to the Railway TriggerBOFF API."""
    url = f"{RAILWAY_URL}/chat"
    data = json.dumps({
        "message": question,
        "session_id": session_id,
        "platform": "benchmark"
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": HERMES_API_KEY
    }

    start = time.time()
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            latency = round(time.time() - start, 2)
            return {
                "response": result.get("response", str(result)),
                "latency_s": latency,
                "tools_called": result.get("tools_called", []),
                "error": None
            }
    except Exception as e:
        return {
            "response": "",
            "latency_s": round(time.time() - start, 2),
            "tools_called": [],
            "error": str(e)
        }


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

    results = []
    print(f"\nRunning {len(GOLDEN_QUESTIONS)} golden questions against {RAILWAY_URL}...")
    print("=" * 60)

    for i, q in enumerate(GOLDEN_QUESTIONS):
        print(f"[{i+1:02d}/{len(GOLDEN_QUESTIONS)}] {q['id']} ({q['category']}) — {q['question'][:60]}...")
        result = query_railway(q["question"], session_id=f"benchmark_{q['id']}")

        if result["error"]:
            print(f"  ❌ Error: {result['error']}")
            scores = {d: 0 for d in ["live_data", "accuracy", "proactivity", "transparency", "calibration", "actionability"]}
            scores["root_cause"] = f"API error: {result['error']}"
        else:
            scores = score_response(q["question"], result["response"], q["minimum_floor"])
            total = sum(v for k, v in scores.items() if k != "root_cause")
            print(f"  ✓ {result['latency_s']}s — score {total}/18 — {scores['root_cause']}")

        results.append({
            "question_id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "response": result["response"],
            "latency_s": result["latency_s"],
            "error": result["error"],
            "scores": scores
        })

        time.sleep(1)  # Rate limit

    # Aggregate
    total_scores = {d: 0 for d in ["live_data", "accuracy", "proactivity", "transparency", "calibration", "actionability"]}
    for r in results:
        for d in total_scores:
            total_scores[d] += r["scores"].get(d, 0)

    avg_scores = {d: round(v / len(results), 2) for d, v in total_scores.items()}
    overall = round(sum(avg_scores.values()), 2)

    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "endpoint": RAILWAY_URL,
        "questions_run": len(results),
        "avg_scores": avg_scores,
        "overall_out_of_18": overall,
        "pass": overall >= 14,  # 78% threshold
        "results": results
    }

    # Save
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"baseline_{ts}.json" if save_as_baseline else f"benchmark_{ts}.json"
    out_path = BENCHMARK_DIR / filename
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print(f"BENCHMARK COMPLETE")
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
    baseline = "--baseline" in sys.argv
    run_benchmark(save_as_baseline=baseline)
