#!/usr/bin/env python3
"""Prove the platform_hints override actually replaces the api_server hint."""
import json, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, "/opt/hermes-agent")

SRC = Path("/data/.hermes/config.yaml")
COPY = Path("/tmp/cfg_test.yaml")
from pathlib import Path as _P
SCRIPT = str(_P(__file__).resolve().parent.parent.parent / "sydney-property-harness" / "scripts" / "apply_platform_hints.py")

passed = failed = 0
def t(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else: failed += 1; print(f"  FAIL  {name} {extra}")

print("=== 1. the built-in default we are overriding ===")
from agent.prompt_builder import PLATFORM_HINTS
default = PLATFORM_HINTS.get("api_server", "")
print("  ", default[:150].replace("\n", " "), "...")
t("default forbids markdown", "no markdown" in default.lower() or "No markdown" in default)
t("default demands brevity", "brief" in default.lower())

print("\n=== 2. apply the override to a COPY of the real config ===")
shutil.copy(SRC, COPY)
r = subprocess.run([sys.executable, SCRIPT, str(COPY)], capture_output=True, text=True)
print("  ", r.stdout.strip() or r.stderr.strip())
t("script applied cleanly", r.returncode == 0, f"(rc={r.returncode})")

print("\n=== 3. does Hermes read it back? ===")
import yaml
cfg = yaml.safe_load(COPY.read_text(encoding="utf-8"))
overrides = cfg.get("platform_hints") or {}
t("top-level platform_hints exists", bool(overrides))
spec = overrides.get("api_server") or {}
t("api_server entry has 'replace'", isinstance(spec.get("replace"), str))
print(f"  replaced hint is {len(spec.get('replace',''))} chars")

print("\n=== 4. run Hermes's OWN resolver with those overrides ===")
# Mirror agent/agent_init.py: agent._platform_hint_overrides = _cfg_dict(cfg, "platform_hints")
# then agent/system_prompt.py::_resolve_platform_hint(agent, platform_key, default_hint)
from agent.system_prompt import _resolve_platform_hint

class FakeAgent:
    def __init__(self, ov): self._platform_hint_overrides = ov

agent = FakeAgent(overrides)
resolved = _resolve_platform_hint(agent, "api_server", default)
print("  resolved:", resolved[:170].replace("\n", " "), "...")
t("the default is gone", "assume plain text" not in resolved.lower())
t("no 'no markdown' rule remains", "no markdown" not in resolved.lower())
t("override text is what the model sees", "renders full Markdown" in resolved)
t("charts are advertised", "chart" in resolved.lower())
t("no-disclosure rule carried over", "never narrate tooling" in resolved.lower())

print("\n=== 5. an unknown platform is unaffected (no leak) ===")
other = _resolve_platform_hint(agent, "telegram", PLATFORM_HINTS.get("telegram", ""))
t("telegram hint untouched", "You are on Telegram" in other)
t("telegram still forbids... nothing broken", "Prefer bullets" in other)

print("\n=== 6. idempotent on a second boot ===")
r2 = subprocess.run([sys.executable, SCRIPT, str(COPY)], capture_output=True, text=True)
print("  ", (r2.stdout + r2.stderr).strip())
t("second run exits 0 (must not abort a boot under set -e)", r2.returncode == 0, f"(rc={r2.returncode})")
r3 = subprocess.run([sys.executable, SCRIPT, str(COPY)], capture_output=True, text=True)
t("third run also exits 0", r3.returncode == 0, f"(rc={r3.returncode})")
cfg2 = yaml.safe_load(COPY.read_text(encoding="utf-8"))
t("still exactly one platform_hints block",
  COPY.read_text(encoding="utf-8").count("\nplatform_hints:") == 1)
t("config still parses", isinstance(cfg2, dict))
t("hint unchanged after re-run",
  (cfg2.get("platform_hints") or {}).get("api_server", {}).get("replace") == spec.get("replace"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
