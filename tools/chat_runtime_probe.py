#!/usr/bin/env python3
"""Look at what /api/chat is actually doing in production: plan limits and runtime errors."""
import json, urllib.request, urllib.error

def secret(n):
    for p in ("/data/.hermes/.builder-secrets", "/data/.hermes/harness-secrets"):
        try:
            for l in open(p, encoding="utf-8"):
                if l.startswith(n + "="):
                    return l.split("=", 1)[1].strip()
        except OSError:
            pass
    return ""

TOK = secret("VERCEL_TOKEN_2")
H = {"Authorization": f"Bearer {TOK}"}
PROJ = "prj_SmL8NjAkJ7mDKxCr9LjBmCjqc8AX"


def get(path):
    try:
        return json.loads(urllib.request.urlopen(
            urllib.request.Request("https://api.vercel.com" + path, headers=H), timeout=60).read())
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "body": e.read().decode()[:200]}
    except Exception as e:
        return {"__err": type(e).__name__, "body": str(e)[:160]}


a = get("/v2/user")
u = a.get("user") if isinstance(a.get("user"), dict) else {}
print("  account:", u.get("username") or a.get("__err"), "| plan:", u.get("version") or "n/a")

p = get(f"/v9/projects/{PROJ}")
print("  project:", p.get("name"))
print("  nodeVersion:", p.get("nodeVersion"), "| framework:", p.get("framework"))

deps = get(f"/v6/deployments?projectId={PROJ}&limit=3&target=production")
if not isinstance(deps, dict): deps = {}
for d in (deps.get("deployments") or [])[:3]:
    print(f"    {d.get('uid')}  {d.get('state')}  {str(d.get('created'))[:13]}  {d.get('url')}")

latest = (deps.get("deployments") or [{}])[0].get("uid")
if latest:
    print("\n  runtime errors on", latest)
    for path in (f"/v3/deployments/{latest}/events?limit=40&direction=backward",
                 f"/v1/deployments/{latest}/runtime-logs?limit=40"):
        r = get(path)
        if "__err" in r:
            print(f"    {path.split('?')[0]} -> {r['__err']} {str(r.get('body'))[:100]}")
            continue
        evs = r if isinstance(r, list) else (r.get("events") or r.get("logs") or r)
        if isinstance(evs, list):
            hits = [e for e in evs if str(e.get("type", "")).lower() in ("error", "stderr", "warning")
                    or "error" in str(e.get("text", "")).lower()]
            print(f"    {path.split('?')[0]} -> {len(evs)} events, {len(hits)} error-ish")
            for e in (hits or evs)[:12]:
                txt = str(e.get("text") or e.get("payload", {}).get("text") or e)[:220]
                print("      ", txt.replace("\n", " ")[:200])
        else:
            print("    shape:", str(evs)[:200])
