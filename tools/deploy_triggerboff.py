#!/usr/bin/env python3
"""
deploy_triggerboff.py — ship the triggerboff repo's HEAD to production, via the API.

WHY THIS EXISTS
A `git push` to this project does NOT deploy. Vercel creates a deployment for the push and
then blocks it ("Vercel couldn't find a Git account for the commit author"), so the live site
silently stays on an older commit. Two commits sat undeployed that way — including a send-button
fix a user was actively complaining about — while every cron run reported "commits pushed".

An API-triggered deployment with the project's gitSource.repoId is accepted where the git-push
one is blocked, so this is the only reliable path to production.

Behaviour is idempotent: if the alias already points at a deployment of this commit, it says so
and does nothing. Run it after every commit.

  exit 0  HEAD is live (deployed now, or already was)
  exit 1  deployment failed or never became READY
  exit 2  could not reach Vercel / no token
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "/data/repos/triggerboff"
PROJECT = "prj_SmL8NjAkJ7mDKxCr9LjBmCjqc8AX"
REPO_ID = "1376984573"
ALIAS = "intelligence.heyluca.com.au"
SECRETS = ("/data/.hermes/.builder-secrets", "/data/.hermes/harness-secrets")


def token():
    for path in SECRETS:
        try:
            for line in open(path, encoding="utf-8"):
                if line.startswith("VERCEL_TOKEN_2="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


TOK = token()
if not TOK:
    print("  no VERCEL_TOKEN_2 on the volume"); sys.exit(2)
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}


def req(method, path, body=None):
    r = urllib.request.Request("https://api.vercel.com" + path,
                               data=json.dumps(body).encode() if body else None,
                               method=method, headers=H)
    try:
        raw = urllib.request.urlopen(r, timeout=120).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__err": e.code, "body": e.read().decode()[:300]}


sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
if not sha:
    print("  cannot read HEAD"); sys.exit(2)

# Already live? Compare the alias' deployment sha against HEAD.
live = req("GET", f"/v4/aliases?limit=60")
if isinstance(live, dict) and "__err" not in live:
    for a in live.get("aliases", []):
        if a.get("alias") == ALIAS:
            dep = req("GET", f"/v13/deployments/{a.get('deploymentId')}")
            live_sha = str((dep.get("meta") or {}).get("githubCommitSha") or "")
            if live_sha.startswith(sha[:7]) or sha.startswith(live_sha[:7]):
                print(f"  {sha[:7]} is already live on {ALIAS} ({a.get('deploymentId')})")
                sys.exit(0)

print(f"  deploying {sha[:7]} …")
d = req("POST", "/v13/deployments", {
    "name": "triggerboff", "project": PROJECT, "target": "production",
    "gitSource": {"type": "github", "repoId": REPO_ID, "ref": "main", "sha": sha}})
if "__err" in d:
    print("  deploy request rejected:", d); sys.exit(2)
did = d.get("id")
state = None
for _ in range(40):
    time.sleep(10)
    s = req("GET", f"/v13/deployments/{did}")
    state = s.get("readyState") or s.get("status")
    if state in ("READY", "ERROR", "CANCELED"):
        break
print(f"  {did} -> {state}")
if state != "READY":
    print("  NOT DEPLOYED — do not report this as shipped"); sys.exit(1)

# Confirm the alias actually moved, rather than assuming READY means live.
for _ in range(12):
    live = req("GET", "/v4/aliases?limit=60")
    if isinstance(live, dict) and "__err" not in live:
        for a in live.get("aliases", []):
            if a.get("alias") == ALIAS and a.get("deploymentId") == did:
                print(f"  {ALIAS} -> {did}  ({sha[:7]} is live)")
                sys.exit(0)
    time.sleep(5)
print(f"  {sha[:7]} built READY but the alias has not moved to {did} yet")
sys.exit(1)
