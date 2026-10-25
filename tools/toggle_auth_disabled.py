#!/usr/bin/env python3
"""Remove AUTH_DISABLED from the luca-landing project and redeploy.

The framework's own diagnostic says: "AUTH_DISABLED is enabled. Unset it for a public
deployment." heyluca.com.au is a public deployment, so the correct fix is to unset it rather
than hide the warning.

The risk is real and worth stating: AUTH_DISABLED also means the app does not require a login.
With auth enabled, the marketing page could start demanding one. So this changes the setting
and then the caller MUST verify the root URL still renders publicly; if it does not, restore
the variable immediately.

Usage:  python3 toggle_auth_disabled.py remove|restore
"""
import json, sys, urllib.request, urllib.error

PROJ = "prj_cWQuRQbxZV8T6cSFF1Kr9FrUtAe6"   # luca-landing
KEY = "AUTH_DISABLED"


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
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}


def req(method, path, body=None):
    r = urllib.request.Request("https://api.vercel.com" + path,
                               data=json.dumps(body).encode() if body else None,
                               method=method, headers=H)
    try:
        raw = urllib.request.urlopen(r, timeout=90).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "body": e.read().decode()[:240]}


mode = sys.argv[1] if len(sys.argv) > 1 else "remove"
envs = req("GET", f"/v9/projects/{PROJ}/env")
if not isinstance(envs, dict) or "__err" in envs:
    print("  cannot list env:", envs); sys.exit(2)
found = [e for e in envs.get("envs", []) if e["key"] == KEY]
print(f"  {KEY} present: {[e['id'] for e in found] or 'no'}")

if mode == "remove":
    for e in found:
        r = req("DELETE", f"/v9/projects/{PROJ}/env/{e['id']}")
        print(f"  delete {e['id']} ->", "ok" if "__err" not in r else r)
elif mode == "restore":
    if found:
        print("  already present; nothing to do")
    else:
        r = req("POST", f"/v10/projects/{PROJ}/env?upsert=true",
                {"key": KEY, "value": "true", "type": "plain",
                 "target": ["production", "preview", "development"]})
        print("  restore ->", "ok" if "__err" not in r else r)

proj = req("GET", f"/v9/projects/{PROJ}")
link = proj.get("link") or {}
dep = req("POST", "/v13/deployments", {
    "name": proj.get("name"), "project": PROJ, "target": "production",
    "gitSource": {"type": "github", "repoId": link.get("repoId"),
                  "ref": link.get("productionBranch") or "main"}})
if "__err" in dep:
    print("  deploy failed:", dep); sys.exit(2)
print("  deploying:", dep.get("id"))
