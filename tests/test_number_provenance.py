#!/usr/bin/env python3
"""Prove the provenance check catches real fabrication and passes real grounding."""
import importlib.util, json, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "np", str(Path(__file__).resolve().parent.parent / "tools" / "number_provenance.py"))
np = importlib.util.module_from_spec(spec); spec.loader.exec_module(np)

passed = failed = 0
def t(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else: failed += 1; print(f"  FAIL  {name} {extra}")

print("=== 1. the REAL fabricated reply captured during model testing ===")
fix = json.load(open(str(Path(__file__).resolve().parent / "fixtures" / "fabricated_prices_gpt51.json")))
fab = fix["answer"]
print(f"  captured answer: {len(fab)} chars (unconstrained run, tools described but none executed)")
t("answer was captured", len(fab) > 500, f"(got {len(fab)})")

# The tool returned nothing at all for this turn — it failed.
r = np.verify(fab, tool_output="")
print(f"  figures found      : {r['total']}")
print(f"  unsourced          : {len(r['unsourced'])}")
print(f"  first few unsourced: {[f['raw'] for f in r['unsourced'][:6]]}")
t("fabricated figures ARE flagged", len(r["unsourced"]) >= 3, f"(got {len(r['unsourced'])})")
t("not reported clean", r["clean"] is False)

print("\n=== 2. a genuinely grounded reply must NOT be flagged ===")
tool_out = json.dumps({
    "sales": [{"address": "12 Smith St MARRICKVILLE", "purchase_price": 1850000},
              {"address": "9 Jones St MARRICKVILLE", "purchase_price": 1620000}],
    "stats": {"suburb": "MARRICKVILLE", "count": 2, "median_price": 1735000},
})
good = ("Twelve settled sales in Marrickville this quarter. The median was "
        "$1,735,000, with a range from $1,620,000 to $1,850,000. That is roughly "
        "6.2% above the same period last year.")
r2 = np.verify(good, tool_out)
print(f"  figures: {[f['raw'] for f in r2['unsourced']] or 'none unsourced'}")
t("grounded figures pass", r2["clean"] is True, f"(unsourced: {[f['raw'] for f in r2['unsourced']]})")

print("\n=== 3. the same reply with an invented number must be flagged ===")
bad = good.replace("$1,735,000", "$2,100,000")
r3 = np.verify(bad, tool_out)
t("invented price flagged", not r3["clean"] and
  any(abs(f["value"] - 2100000) < 1 for f in r3["unsourced"]),
  f"(got {[f['raw'] for f in r3['unsourced']]})")

print("\n=== 4. rounding must not be treated as fabrication ===")
rounded = "The median was about $1.74M across the two sales ($1.62M-$1.85M)."
r4 = np.verify(rounded, tool_out)
t("rounded figures pass", r4["clean"] is True, f"(unsourced: {[f['raw'] for f in r4['unsourced']]})")

print("\n=== 5. procedural numbers must never be flagged ===")
proc = ("You are buying in an R2 zone, so check the Section 10.7 certificate and the "
        "LEP height limit. Do not sign a Section 66W before a 2-bed strata report, and "
        "allow 5 business days for cooling off. The strata levy of $1,200 a quarter is normal.")
r5 = np.verify(proc, tool_out)
flagged = [f["raw"] for f in r5["unsourced"]]
print(f"  flagged: {flagged}")
t("zone/section codes ignored", not any("10.7" in f or "66W" in f or "R2" in f for f in flagged))
t("the $1,200 levy still needs a source", any("1,200" in f for f in flagged),
  f"(flagged={flagged})")

print("\n=== 6. annotate() output ===")
note = np.annotate(bad, r3)
print("  tail:", repr(note[-110:]))
t("annotation names the figures", "2,100,000" in note)
t("clean reply left untouched", np.annotate(good, r2) == good)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
