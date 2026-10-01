#!/usr/bin/env python3
"""End-to-end proof that the source capture works, and the first real FP measurement.

Waits for the harness to redeploy with the source_log plugin, asks one question that must
call tools, then asserts:
  1. tool_sources.jsonl gained entries for this turn  (the missing half of the pair)
  2. reply_sources.jsonl gained one pair with sources attached
  3. the pair is usable: run the deterministic guard over it and report what it says

Result goes to /data (not /tmp) and is printed for the notification.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error

SRC = "/data/.hermes/tool_sources.jsonl"
PAIRS = "/data/.hermes/benchmarks/reply_sources.jsonl"
HARNESS = "https://sydney-property-harness-production-0135.up.railway.app/v1/chat/completions"

key = None
for p in ("/proc/1/environ", "/data/.hermes/harness-secrets"):
    try:
        raw = open(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        continue
    m = re.search(r"API_SERVER_KEY=([A-Za-z0-9_-]+)", raw)
    if m:
        key = m.group(1)
        break
if not key:
    print("HARNESS FAILURE: no harness API key"); sys.exit(2)

before_src = sum(1 for _ in open(SRC)) if os.path.exists(SRC) else 0
before_pairs = sum(1 for _ in open(PAIRS)) if os.path.exists(PAIRS) else 0
print(f"  before: {before_src} source entries, {before_pairs} pairs")

print("  waiting 300s for the harness to redeploy...")
time.sleep(300)

question = "What have 2-bed units sold for in Newtown NSW over the last year? Give me actual figures."
body = {"model": "hermes-agent", "stream": False, "messages": [{"role": "user", "content": question}]}
req = urllib.request.Request(HARNESS, data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {key}", "X-API-Key": key,
             "Content-Type": "application/json", "X-Hermes-Session-Key": "source-capture-check"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=280) as r:
        d = json.loads(r.read() or b"{}")
except Exception as e:
    print(f"HARNESS FAILURE: {type(e).__name__}: {str(e)[:160]}"); sys.exit(2)

answer = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
print(f"  reply: {len(answer)} chars")

time.sleep(3)
after_src = sum(1 for _ in open(SRC)) if os.path.exists(SRC) else 0
after_pairs = sum(1 for _ in open(PAIRS)) if os.path.exists(PAIRS) else 0
gained = after_src - before_src
print(f"  after : {after_src} source entries (+{gained}), {after_pairs} pairs (+{after_pairs-before_pairs})")

print("\n--- verdict ---")
print(f"  tool results captured this turn : {'PASS' if gained > 0 else 'FAIL'}  (+{gained})")
print(f"  reply paired with its sources   : {'PASS' if after_pairs > before_pairs else 'FAIL'}")

# what did it capture, and does the pair support the guard?
if gained > 0:
    entries = [json.loads(l) for l in open(SRC)][-gained:]
    print("  tools seen:", [e.get("tool") for e in entries])
    print("  chars of source:", sum(len(e.get("result") or "") for e in entries))

if after_pairs > before_pairs:
    pair = [json.loads(l) for l in open(PAIRS)][-1]
    print(f"  pair: {len(pair['sources'])} sources, {pair['source_chars']} chars, tools={pair['tools']}")
    # the measurement this was all for: does the guard fire on a legitimate reply?
    sys.path.insert(0, "/data/repos/triggerboff-builder/tools")
    try:
        from number_provenance import verify
        blob = "\n".join(s.get("result") or "" for s in pair["sources"])
        res = verify(pair["answer"], blob)
        figures = res.get("figures") or res.get("unsourced") or []
        print(f"  GUARD on this real reply: {json.dumps(res)[:400]}")
        print("\n  ^ if this fires on a legitimate, well-sourced reply it is a FALSE POSITIVE")
        print("    and that is the number that decides whether the guard ships.")
    except Exception as e:
        print(f"  guard import/run: {type(e).__name__}: {str(e)[:160]}")
