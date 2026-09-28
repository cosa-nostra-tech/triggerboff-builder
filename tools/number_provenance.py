#!/usr/bin/env python3
"""Mechanical check that every market figure in a reply came from a real source.

WHY THIS EXISTS
---------------
A language model has no access to truth, so "be accurate" cannot be enforced by
asking for it. What CAN be enforced mechanically is narrower and is the failure that
actually costs money: **a number in the reply that no source produced.**

Observed directly in testing: asked for real Marrickville sale prices with no tool
loop available, one model produced 3,816 characters opening

    "Here are **real, settled sale prices** for houses in Marrickville ... These are
     not agent guides or asking prices - they're actual"

Every figure was invented. Told the same thing with the constraint stated, the same
model refused to quote figures at all. So the behaviour is promptable but not
guaranteed - which is precisely when a mechanical check earns its place.

HOW IT PLUGS IN
---------------
Hermes runs shell hooks in dashboard chat as well as CLI/gateway. Two events do the
work, both of which receive JSON on stdin and read JSON on stdout:

  transform_tool_result  - accumulate each tool's result text for the turn
  transform_llm_output   - verify the reply's figures against that accumulated text,
                           and return {"action": "modify", "text": ...} if unsourced
                           figures are present

This module is the pure logic both hooks call. It is deliberately dependency-free so
it can run as a subprocess in the hook sandbox.

WHAT IT DOES NOT DO
-------------------
It does not judge whether a figure is CORRECT, only whether it was SOURCED. A wrong
number that came from a tool is a tool problem; a number from nowhere is a model
problem. Keeping those separate is the point - it makes each one measurable.
"""
from __future__ import annotations

import re

# Money: $1.4M, $1,450,000, $950k, 1.4 million
_MONEY = re.compile(
    r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(m|mn|mil|million|k|thousand)?\b", re.I)
# Bare large money-ish numbers that get used as prices: 1,450,000 / 1850000
_BARE_LARGE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{6,})\b")
# Percentages: 6.2%, 4.5 per cent
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent)", re.I)

# Procedural tokens that look numeric but are not market data. Never flagged.
_IGNORE = re.compile(
    r"^(\d{1,2}(?:\.\d+)?|s\d+|\d+w|\d{2,3}[a-z]{1,3}|r\d|\d{3}\d?)$", re.I)


def _to_number(digits: str, suffix: str | None) -> float | None:
    try:
        n = float(digits.replace(",", ""))
    except ValueError:
        return None
    s = (suffix or "").lower()
    if s in ("m", "mn", "mil", "million"):
        n *= 1_000_000
    elif s in ("k", "thousand"):
        n *= 1_000
    return n


def extract_figures(text: str) -> list[dict]:
    """Every money / percentage figure in the text, with the span it occupied."""
    out, seen = [], set()
    for m in _MONEY.finditer(text):
        n = _to_number(m.group(1), m.group(2))
        if n is None:
            continue
        out.append({"raw": m.group(0).strip().rstrip(",;:.\u2014-"), "value": n,
                    "kind": "money", "span": m.span()})
    for m in _BARE_LARGE.finditer(text):
        raw = m.group(1)
        if _IGNORE.match(raw):
            continue
        n = _to_number(raw, None)
        # Only treat as a market figure when it is large enough to be a price.
        if n is None or n < 10_000:
            continue
        if any(s <= m.start() and m.end() <= e for _, s, e in
               [(0, f["span"][0], f["span"][1]) for f in out]):
            continue
        out.append({"raw": raw, "value": n, "kind": "money", "span": m.span()})
    for m in _PCT.finditer(text):
        try:
            n = float(m.group(1))
        except ValueError:
            continue
        out.append({"raw": m.group(0).strip().rstrip(",;:.\u2014-"), "value": n,
                    "kind": "percent", "span": m.span()})
    # Deduplicate identical consecutive hits
    ded = []
    for f in sorted(out, key=lambda x: x["span"]):
        k = (f["kind"], f["value"])
        if k in seen:
            continue
        seen.add(k)
        ded.append(f)
    return ded


def _source_numbers(source_blob: str) -> set[float]:
    """Every number appearing in the concatenated tool output."""
    nums: set[float] = set()
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(m|mn|mil|million|k|thousand)?",
                         source_blob, re.I):
        n = _to_number(m.group(1), m.group(2))
        if n is not None:
            nums.add(n)
    return nums


def verify(reply: str, tool_output: str, tolerance: float = 0.01) -> dict:
    """Split the reply's figures into sourced and unsourced.

    A figure counts as sourced when the same value appears in the tool output, or is
    within `tolerance` of it (models legitimately round a median, and rounding is not
    fabrication). Percentages are only required to be sourced when tools returned
    any percentage at all, so a % figure derived from a sourced price/rent pair is
    not punished.
    """
    figs = extract_figures(reply)
    src = _source_numbers(tool_output or "")
    src_has_pct = bool(_PCT.search(tool_output or ""))
    no_source_data = not src

    sourced, unsourced = [], []
    for f in figs:
        hit = any(abs(f["value"] - s) <= max(1.0, abs(s) * tolerance) for s in src)
        if hit:
            sourced.append(f); continue
        # A percentage with real data present is normally a rate the model derived
        # from sourced figures (yield from price and rent); only require a source
        # for it when the tools themselves reported percentages.
        if f["kind"] == "percent" and not no_source_data and not src_has_pct:
            sourced.append(f); continue
        unsourced.append(f)
    return {
        "total": len(figs),
        "sourced": sourced,
        "unsourced": unsourced,
        "no_source_data": no_source_data,
        "clean": not unsourced,
    }


def annotate(reply: str, result: dict) -> str:
    """Append a visible provenance note when unsourced figures were found.

    Deliberately NOT a silent strip: removing a number leaves a sentence with a hole
    in it, which reads worse than the number did. The note tells the reader which
    figures to verify, which is the honest move and keeps the reply useful.
    """
    if result["clean"] or not result["unsourced"]:
        return reply
    items = ", ".join(f["raw"] for f in result["unsourced"][:6])
    more = "" if len(result["unsourced"]) <= 6 else f" (+{len(result['unsourced']) - 6} more)"
    return (reply.rstrip() +
            "\n\n---\n*Verify before relying on: "
            f"{items}{more} — not confirmed against settled sales data.*")
