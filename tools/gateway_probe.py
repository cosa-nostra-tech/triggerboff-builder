#!/usr/bin/env python3
"""Reproduce what /api/chat sends to the harness gateway and report what comes back.

The route posts { model: 'hermes-agent', messages: [...], stream: true } with an
X-Hermes-Session-Key header and a 180s abort. If the gateway rejects any of that, the client
only ever sees "Something went wrong. Please try again." because the route's own error text is
thrown away by the browser. This calls the gateway the same way and prints the truth.
"""
import json, urllib.request, urllib.error, time

def secret(name, path):
    try:
        for l in open(path, encoding="utf-8"):
            if l.startswith(name + "="):
                return l.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""

KEY = secret("API_SERVER_KEY", "/data/.hermes/harness-secrets")
URL = "https://sydney-property-harness-production-0135.up.railway.app/v1/chat/completions"
MSG = "find in budget only in the entrance area"

CASES = [
    ("model=hermes-agent  stream", "hermes-agent", True),
    ("model=hermes-agent  nostream", "hermes-agent", False),
    ("model=z-ai/glm-5.3  stream", "z-ai/glm-5.3", True),
]

for label, model, stream in CASES:
    body = {"model": model, "stream": stream,
            "messages": [{"role": "system", "content": "You are a Sydney property assistant."},
                         {"role": "user", "content": MSG}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream" if stream else "application/json")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("X-API-Key", KEY)
    req.add_header("X-Hermes-Session-Key", "triggerboff-user-audit-probe")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            first = r.read(1200).decode("utf-8", "replace")
            print(f"  {label:<28} HTTP {r.status}  {time.time()-t0:.1f}s")
            print("     ct:", r.headers.get("content-type"))
            print("     head:", first[:400].replace("\n", " ⏎ "))
    except urllib.error.HTTPError as e:
        print(f"  {label:<28} HTTP {e.code}  {time.time()-t0:.1f}s   <-- REJECTED")
        print("     body:", e.read().decode("utf-8", "replace")[:500].replace("\n", " "))
    except Exception as e:
        print(f"  {label:<28} {type(e).__name__} after {time.time()-t0:.1f}s: {str(e)[:160]}")
    print()
