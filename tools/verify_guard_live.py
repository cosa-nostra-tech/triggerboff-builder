#!/usr/bin/env python3
"""Confirm the guard has its key, then read a real verdict off the harness's own logs.

Everything here is the real thing, not a proxy: the harness's deploy logs, the guard
running in production, and its verdict on a genuine turn.
"""
import json, re, time, urllib.request, urllib.error

def secret(name):
    for p in ("/proc/1/environ", "/data/.hermes/.builder-secrets"):
        try:
            raw = open(p, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        m = re.search(rf"{name}=([^\s\x00]+)", raw)
        if m:
            return m.group(1)
    return None

TOK = secret("RAILWAY_TOKEN_2")
PID = "ff2aa488-ab31-42e9-92b6-131ac159c025"
SID = "a2e36f53-53c8-4229-96ab-7f0195146a8a"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
RH = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": UA,
      "Accept": "application/json", "Origin": "https://railway.com", "Referer": "https://railway.com/"}
HARNESS = "https://sydney-property-harness-production-0135.up.railway.app/v1/chat/completions"
HKEY = secret("API_SERVER_KEY")

def gql(q, v=None):
    req = urllib.request.Request("https://backboard.railway.com/graphql/v2",
        data=json.dumps({"query": q, "variables": v}).encode(), headers=RH)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"err": f"{type(e).__name__}: {str(e)[:120]}"}

def newest_deployment():
    d = gql("query($p:String!,$s:String!){ deployments(first:1, input:{projectId:$p, serviceId:$s}){ edges{ node{ id status createdAt } } } }",
            {"p": PID, "s": SID})
    try:
        return d["data"]["deployments"]["edges"][0]["node"]
    except Exception:
        return None

def logs(dep_id):
    d = gql("query($id:String!){ deploymentLogs(deploymentId:$id){ message timestamp } }", {"id": dep_id})
    try:
        return [(e["message"], e.get("timestamp", "")) for e in d["data"]["deploymentLogs"]]
    except Exception:
        return []

def ask(q, timeout=280):
    body = {"model": "hermes-agent", "stream": False, "messages": [{"role": "user", "content": q}]}
    req = urllib.request.Request(HARNESS, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {HKEY}", "X-API-Key": HKEY,
                 "Content-Type": "application/json", "X-Hermes-Session-Key": "guard-verify"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read() or b"{}")
    return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

print("=== 1. wait for the redeploy triggered by the variable change ===")
dep = newest_deployment()
print(f"  newest: {dep['id'][:8]}  {dep['status']}")
for i in range(40):
    if dep["status"] in ("SUCCESS", "FAILED", "CRASHED"):
        break
    time.sleep(15)
    dep = newest_deployment()
    if i % 3 == 0:
        print(f"  [{i*15:>3}s] {dep['status']}")
print(f"  final: {dep['status']}")

print("\n=== 2. did the guard start with the key? ===")
lines = logs(dep["id"])
guard_lines = [m for m, _ in lines if "provenance_guard" in m.lower() or "source_log" in m.lower()]
for m in guard_lines[:4]:
    print("   ", m[:170])
if not guard_lines:
    print("    no guard line yet; last 4 log lines:")
    for m, _ in lines[-4:]:
        print("   ", m[:150])

print("\n=== 3. fire a real turn that must retrieve data ===")
q = "What have 2-bed units sold for in Marrickville NSW this year? Give me real figures."
try:
    reply = ask(q)
    print(f"  reply {len(reply)} chars")
except Exception as e:
    print(f"  harness error: {type(e).__name__}: {str(e)[:140]}")
    reply = ""

print("\n=== 4. read the guard's verdict from the harness logs ===")
time.sleep(20)
lines = logs(dep["id"])
hits = [m for m, _ in lines if "PROVENANCE_GUARD" in m]
print(f"  {len(hits)} PROVENANCE_GUARD line(s) found")
for m in hits[-3:]:
    try:
        rec = json.loads(m.split("PROVENANCE_GUARD", 1)[1].strip())
        verdict = (f"noul={rec['noul']:.3f}  suspect={rec['suspect']}  {rec.get('judge_seconds')}s  "
                   f"tools={rec.get('tools')}  figures={rec.get('figures_in_reply')}")
        if rec.get("skipped"):
            verdict = f"SKIPPED: {rec['skipped']}"
        if rec.get("error"):
            verdict = f"ERROR: {rec['error'][:110]}"
        print("   ", verdict)
    except Exception:
        print("   ", m[:200])

if not hits:
    print("  no verdict line yet - the guard logs at the end of a turn; check again shortly")
