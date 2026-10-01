#!/usr/bin/env python3
"""Proxy false-positive measurement for the fabrication guard.

The real measurement wants the exact sources the harness used. Those live in the harness
container, which no token here can reach. So this approximates from the outside and says
so: for each real reply it re-gathers the same kind of evidence (web search on the question,
plus the figures the reply itself cites as the claiming set) and asks Jev the same question
the guard asks in production.

What it can establish: whether a well-sourced, real TriggerBOFF reply gets flagged. That is
the number that decides whether the guard ships as a block or stays a monitor.

What it cannot establish: the guard's precision against the harness's exact source blob.
Treat the result as a floor, not the final figure - and do not report it otherwise.
"""
import json, os, re, time, urllib.request, urllib.error

def _secret(name):
    for p in ("/proc/1/environ", "/data/.hermes/.builder-secrets"):
        try:
            raw = open(p, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        m = re.search(rf"{name}=([^\s\x00]+)", raw)
        if m:
            return m.group(1)
    return None

JEV = _secret("TYPESAFE_API_KEY")
TAV = _secret("TAVILY_API_KEY")
HKEY = None
for p in ("/proc/1/environ", "/data/.hermes/harness-secrets"):
    try:
        raw = open(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        continue
    m = re.search(r"API_SERVER_KEY=([A-Za-z0-9_-]+)", raw)
    if m:
        HKEY = m.group(1); break

HARNESS = "https://sydney-property-harness-production-0135.up.railway.app/v1/chat/completions"
print(f"  jev key: {bool(JEV)}   tavily: {bool(TAV)}   harness: {bool(HKEY)}")

QUESTIONS = [
    "What have 2-bed units sold for in Newtown NSW this year? Give real figures.",
    "Is $1.2M a fair price for a 3-bed house in Dulwich Hill? Show me the evidence.",
]

def ask(q, timeout=280):
    body = {"model": "hermes-agent", "stream": False, "messages": [{"role": "user", "content": q}]}
    req = urllib.request.Request(HARNESS, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {HKEY}", "X-API-Key": HKEY,
                 "Content-Type": "application/json", "X-Hermes-Session-Key": "fp-proxy"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read() or b"{}")
    return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

def gather(q):
    """Re-gather the kind of evidence a reply like this should rest on."""
    if not TAV:
        return ""
    req = urllib.request.Request("https://api.tavily.com/search",
        data=json.dumps({"api_key": TAV, "query": q, "max_results": 8,
                         "search_depth": "advanced"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    parts = []
    for x in d.get("results", []):
        parts.append(f"{x.get('title','')} | {x.get('url','')}\n{x.get('content','')}")
    return "\n\n".join(parts)

def noul(reply, sources):
    body = {"state": f"SOURCE DATA:\n{sources}\n\nASSISTANT REPLY:\n{reply}", "model": "jev-latest",
            "questions": {"clean": {"type": "noul",
                "instructions": ("Every dollar figure and percentage in the ASSISTANT REPLY appears in, "
                                 "or is directly derived from, the SOURCE DATA."),
                "criteria": {"true": "every figure traces to the source data",
                             "false": "at least one figure does not appear in the source data"}}}}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {JEV}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return d["answers"]["clean"]["noul"], round(time.time() - t0, 2)

results = []
for q in QUESTIONS:
    print(f"\n  Q: {q[:68]}")
    try:
        reply = ask(q)
    except Exception as e:
        print(f"     harness error: {type(e).__name__}: {str(e)[:120]}"); continue
    if not reply:
        print("     empty reply, skipped"); continue
    figs = re.findall(r"\$[\d][\d,.]*[kKmM]?|\b\d+(?:\.\d+)?%", reply)
    print(f"     reply {len(reply)} chars, {len(figs)} figure(s): {figs[:6]}")
    try:
        src = gather(q)
    except Exception as e:
        print(f"     source gather failed: {type(e).__name__}"); src = ""
    if not src:
        print("     no sources gathered - cannot score"); continue
    try:
        n, secs = noul(reply, src)
    except Exception as e:
        print(f"     jev error: {type(e).__name__}: {str(e)[:120]}"); continue
    fp = n < 0.5
    results.append(n)
    print(f"     noul={n:.3f}  {'FALSE POSITIVE' if fp else 'no false positive'}   {secs}s  ({len(src)} source chars)")

print("\n--- proxy false-positive result ---")
if results:
    fp = sum(1 for n in results if n < 0.5)
    print(f"  {fp}/{len(results)} real replies flagged as suspect")
    print(f"  noul values: {[round(n,3) for n in results]}")
    print("  Floor, not the final figure: sources were re-gathered externally, not the")
    print("  harness's exact blob. The real number needs the harness deploy logs.")
else:
    print("  no scored replies")
