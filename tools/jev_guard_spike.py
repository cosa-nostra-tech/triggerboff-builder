#!/usr/bin/env python3
"""Spike: can Jev's Noul primitive serve as the fabrication guard?

The question the guard must answer, per reply: "is every figure in this reply present in,
or directly derivable from, the data this turn actually retrieved?"

Polarity: Noul returns the probability that the answer is YES. The question is phrased so
that YES = the reply is clean. So a LOW noul value is a suspected fabrication.

Two measurements, and the second is the one that decides whether this ships:
  1. CATCH RATE     on the fabricated-prices fixture - known-invented figures must score low
  2. FALSE POSITIVE on a real, well-sourced reply - must score high

A guard that catches everything but also flags good answers is worse than no guard: it
blunts the reply and costs the user confidence for no gain.
"""
import json, os, re, sys, time, urllib.request, urllib.error

KEY = [l.split("=", 1)[1].strip() for l in open("/data/.hermes/.builder-secrets")
       if l.startswith("TYPESAFE_API_KEY=")][0]
URL = "https://api.typesafe.ai/v1/systemone"
FIXTURE = "/data/repos/triggerboff-builder/tests/fixtures/fabricated_prices_gpt51.json"
PAIRS = "/data/.hermes/benchmarks/reply_sources.jsonl"

QUESTION = ("Every dollar figure and percentage in the ASSISTANT REPLY appears in, or is "
            "directly derived from, the SOURCE DATA.")


def noul(state: str, instructions: str = QUESTION) -> dict:
    body = {"state": state, "model": "jev-latest", "questions": {
        "clean": {"type": "noul", "instructions": instructions,
                  "criteria": {"true": "every figure traces to the source data",
                               "false": "at least one figure does not appear in the source data"}}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return {"noul": d["answers"]["clean"]["noul"],
            "usage": d.get("usage", {}), "seconds": round(time.time() - t0, 2)}


def score(reply: str, sources: str) -> dict:
    state = f"SOURCE DATA:\n{sources}\n\nASSISTANT REPLY:\n{reply}"
    return noul(state)


print("=== 1. CATCH RATE on the fabricated-prices fixture ===")
cases = []
try:
    raw = json.load(open(FIXTURE))
    items = raw if isinstance(raw, list) else raw.get("cases") or raw.get("turns") or [raw]
    for it in items:
        reply = it.get("reply") or it.get("answer") or it.get("output") or ""
        src = it.get("tool_output") or it.get("sources") or it.get("source") or ""
        if isinstance(src, (dict, list)):
            src = json.dumps(src)
        if reply:
            cases.append((reply, src, it.get("note") or it.get("description") or ""))
except Exception as e:
    print(f"  could not read fixture: {type(e).__name__}: {e}")
print(f"  {len(cases)} fixture case(s)")

flagged = 0
for i, (reply, src, note) in enumerate(cases, 1):
    try:
        s = score(reply, src)
    except Exception as e:
        print(f"  [{i}] ERROR {type(e).__name__}: {str(e)[:90]}"); continue
    hit = s["noul"] < 0.5
    flagged += hit
    print(f"  [{i}] noul={s['noul']:.3f}  {'CAUGHT' if hit else 'MISSED'}  "
          f"{s['seconds']}s  {(note or reply[:52])[:52]!r}")
if cases:
    print(f"  catch rate: {flagged}/{len(cases)}")

print("\n=== 2. FALSE POSITIVE on real replies with their real sources ===")
pairs = []
if os.path.exists(PAIRS):
    for line in open(PAIRS):
        line = line.strip()
        if line:
            try: pairs.append(json.loads(line))
            except Exception: pass
print(f"  {len(pairs)} real reply/source pair(s) captured so far")
for i, p in enumerate(pairs[-3:], 1):
    src = "\n".join(x.get("result") or "" for x in (p.get("sources") or []))
    if not src:
        print(f"  [{i}] no sources attached - cannot score (this is the gap the capture closes)"); continue
    try:
        s = score(p["answer"], src)
    except Exception as e:
        print(f"  [{i}] ERROR {type(e).__name__}: {str(e)[:90]}"); continue
    fp = s["noul"] < 0.5
    print(f"  [{i}] noul={s['noul']:.3f}  {'FALSE POSITIVE' if fp else 'ok (no false positive)'}  "
          f"{s['seconds']}s  tools={p.get('tools')}")
if not pairs:
    print("  none captured yet - the background verification is still producing the first one")

print("\n=== 3. sanity: a deliberately invented figure in context ===")
src = '{"suburb":"MARRICKVILLE","median_unit":970000,"sales":[{"address":"13/25a George St","price":935000}]}'
good = "Marrickville's median unit price is $970k, and 13/25a George St sold for $935,000."
bad  = "Marrickville's median unit price is $970k, and 13/25a George St sold for $1,480,000."
for label, r in (("faithful", good), ("invented", bad)):
    try:
        s = score(r, src)
        print(f"  {label:9} noul={s['noul']:.3f}  {'flagged' if s['noul']<0.5 else 'passed'}  ({s['seconds']}s, {s['usage']})")
    except Exception as e:
        print(f"  {label:9} ERROR {type(e).__name__}: {str(e)[:100]}")
