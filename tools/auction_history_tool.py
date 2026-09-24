#!/usr/bin/env python3
"""
auction_history_tool.py — Auction history and vendor motivation signals for Domain listings.

Fetches prior campaigns, pass-in events, price changes, and days on market via the
Domain API. Surfaces actionable signals like repeated pass-ins that may indicate
a motivated vendor.

Usage: python3 auction_history_tool.py <listing_id_or_url>
  e.g. python3 auction_history_tool.py 2019683750
  e.g. python3 auction_history_tool.py https://www.domain.com.au/14-addison-road-marrickville-nsw-2204-2019683750
"""

import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DOMAIN_API_BASE = "https://api.domain.com.au/v1"


# ── Auth ───────────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("DOMAIN_API_KEY", "")
    if not key:
        hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        env_path = os.path.join(hermes_home, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DOMAIN_API_KEY="):
                        key = line.partition("=")[2].strip()
                        break
    return key


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _domain_request(endpoint: str):
    api_key = _get_api_key()
    if not api_key:
        return {"error": "DOMAIN_API_KEY not set. Add it to Railway Variables or ~/.hermes/.env"}

    url = f"{DOMAIN_API_BASE}{endpoint}"
    req = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        return {"error": f"HTTP {e.code}", "detail": body, "status_code": e.code}
    except Exception as e:
        return {"error": str(e)}


# ── ID extraction ──────────────────────────────────────────────────────────────

def _extract_listing_id(input_str: str) -> str:
    """Accept a Domain URL or plain numeric ID and return just the numeric ID."""
    input_str = input_str.strip()
    # Domain URLs end with the numeric listing ID, e.g. ...nsw-2204-2019683750
    m = re.search(r"(\d{7,12})(?:/|$|\?)", input_str)
    if m:
        return m.group(1)
    # Fallback: if the whole thing is already a number
    if re.fullmatch(r"\d+", input_str):
        return input_str
    return input_str


# ── Date helpers ───────────────────────────────────────────────────────────────

def _parse_date(s) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], fmt[:len(fmt)])
        except ValueError:
            continue
    # Try ISO format with timezone offset e.g. 2024-03-15T00:00:00+11:00
    try:
        return datetime.fromisoformat(str(s)[:25])
    except Exception:
        return None


def _days_between(d1, d2) -> Optional[int]:
    if d1 and d2:
        try:
            delta = abs((d2.replace(tzinfo=None) - d1.replace(tzinfo=None)).days)
            return delta
        except Exception:
            pass
    return None


# ── Core logic ─────────────────────────────────────────────────────────────────

def _classify_outcome(event: dict) -> str:
    """Classify an event as sold, passed_in, withdrawn, relisted, price_change, or unknown."""
    etype = (event.get("eventType") or event.get("type") or "").lower()
    desc = (event.get("description") or event.get("comment") or "").lower()
    price = event.get("price") or event.get("salePrice")

    if any(t in etype for t in ("sold", "sale")):
        return "sold"
    if any(t in etype for t in ("passed", "passin", "pass in", "noresult")):
        return "passed_in"
    if "withdraw" in etype or "cancel" in etype:
        return "withdrawn"
    if "relist" in etype:
        return "relisted"
    if any(t in etype for t in ("price", "pricechange", "price_change")):
        return "price_change"

    # Fall back to description text
    if any(t in desc for t in ("passed in", "no sale", "vendor bid", "not sold")):
        return "passed_in"
    if "sold" in desc:
        return "sold"
    if "withdrawn" in desc or "cancelled" in desc:
        return "withdrawn"
    if "price" in desc and ("change" in desc or "reduc" in desc or "drop" in desc):
        return "price_change"

    return "unknown"


def _parse_history_events(history_data) -> list[dict]:
    """
    The Domain history endpoint may return different shapes depending on API version.
    We handle both a list of events directly or a dict with an 'events' key.
    """
    if isinstance(history_data, list):
        events = history_data
    elif isinstance(history_data, dict):
        events = (
            history_data.get("events")
            or history_data.get("history")
            or history_data.get("priceHistory")
            or []
        )
    else:
        events = []
    return events


def _group_into_campaigns(events: list[dict]) -> list[dict]:
    """
    Group raw events into discrete campaigns (a campaign = a continuous listing run).
    A new campaign starts after a passed_in/sold/withdrawn terminal event,
    or after a gap of >60 days between events.
    """
    if not events:
        return []

    # Sort by date ascending
    def _sort_key(e):
        d = _parse_date(e.get("date") or e.get("eventDate") or e.get("listedDate"))
        return d or datetime.min

    sorted_events = sorted(events, key=_sort_key)

    # Terminal outcomes that end a campaign
    TERMINAL = {"sold", "passed_in", "withdrawn"}

    campaigns = []
    current = []

    for ev in sorted_events:
        outcome = _classify_outcome(ev)
        if not current:
            current.append(ev)
        else:
            prev_date = _parse_date(
                current[-1].get("date") or current[-1].get("eventDate") or current[-1].get("listedDate")
            )
            curr_date = _parse_date(
                ev.get("date") or ev.get("eventDate") or ev.get("listedDate")
            )
            gap = _days_between(prev_date, curr_date) if prev_date and curr_date else 0

            # Start new campaign if previous ended (terminal outcome) or long gap
            prev_outcome = _classify_outcome(current[-1])
            if prev_outcome in TERMINAL or (gap and gap > 90):
                campaigns.append(current)
                current = [ev]
            else:
                current.append(ev)

        if outcome in TERMINAL:
            campaigns.append(current)
            current = []

    if current:
        campaigns.append(current)

    # Summarise each campaign
    result = []
    for camp_events in campaigns:
        if not camp_events:
            continue

        dates = [
            _parse_date(e.get("date") or e.get("eventDate") or e.get("listedDate"))
            for e in camp_events
        ]
        dates = [d for d in dates if d]

        start = min(dates) if dates else None
        end = max(dates) if dates else None

        outcomes = [_classify_outcome(e) for e in camp_events]
        final_outcome = outcomes[-1] if outcomes else "unknown"

        prices = [
            e.get("price") or e.get("salePrice") or e.get("priceGuide")
            for e in camp_events
        ]
        prices = [p for p in prices if p and isinstance(p, (int, float)) and p > 0]

        price_changes = [
            {"date": str(e.get("date") or e.get("eventDate") or "")[:10],
             "price": e.get("price") or e.get("salePrice") or e.get("priceGuide")}
            for e in camp_events
            if _classify_outcome(e) == "price_change"
        ]

        campaign_summary = {
            "start_date": start.strftime("%Y-%m-%d") if start else None,
            "end_date": end.strftime("%Y-%m-%d") if end else None,
            "days_on_market": _days_between(start, end) if start and end else None,
            "outcome": final_outcome,
            "passed_in": final_outcome == "passed_in",
            "price_at_start": prices[0] if prices else None,
            "price_at_end": prices[-1] if prices else None,
            "price_changes": price_changes,
            "events": [
                {
                    "date": str(e.get("date") or e.get("eventDate") or "")[:10],
                    "type": _classify_outcome(e),
                    "raw_type": e.get("eventType") or e.get("type") or "",
                    "price": e.get("price") or e.get("salePrice") or e.get("priceGuide"),
                    "description": e.get("description") or e.get("comment") or "",
                }
                for e in camp_events
            ],
        }
        result.append(campaign_summary)

    return result


def _build_signal(campaigns: list[dict], passed_in_count: int, days_on_market: Optional[int]) -> str:
    """Generate a plain-English motivation signal for the buyer's agent."""
    parts = []

    if passed_in_count == 0 and not campaigns:
        return "No prior auction history found — fresh to market or data unavailable."

    if passed_in_count >= 3:
        parts.append(f"Passed in {passed_in_count} times — vendor likely highly motivated, consider well below guide")
    elif passed_in_count == 2:
        parts.append(f"Passed in twice — vendor may be motivated, negotiate firmly")
    elif passed_in_count == 1:
        parts.append(f"Passed in once — worth noting, test the vendor's flexibility")

    if len(campaigns) >= 3:
        parts.append(f"listed across {len(campaigns)} separate campaigns — extended selling effort")
    elif len(campaigns) == 2:
        parts.append(f"relisted after initial campaign — property has history")

    if days_on_market and days_on_market > 120:
        parts.append(f"{days_on_market} days on market current campaign — extended time, leverage this")
    elif days_on_market and days_on_market > 60:
        parts.append(f"{days_on_market} days on market — getting stale, good negotiation position")

    # Price reductions across all campaigns
    all_price_changes = sum(len(c.get("price_changes", [])) for c in campaigns)
    if all_price_changes >= 2:
        parts.append(f"{all_price_changes} price reductions recorded")
    elif all_price_changes == 1:
        parts.append("1 price reduction recorded")

    if not parts:
        return "No notable signals — appears to be current standard campaign."

    return "; ".join(parts) + "."


# ── Public entry point ─────────────────────────────────────────────────────────

def run(listing_input: str) -> dict:
    """
    Fetch auction history and vendor signals for a Domain listing.

    Args:
        listing_input: Domain listing ID (numeric) or full Domain URL.

    Returns:
        dict with keys: listing_id, address, current_price_guide, history,
                        passed_in_count, days_on_market_current, signal, data_source
    """
    listing_id = _extract_listing_id(listing_input)

    result = {
        "listing_id": listing_id,
        "address": None,
        "current_price_guide": None,
        "history": [],
        "passed_in_count": 0,
        "days_on_market_current": None,
        "signal": None,
        "data_source": "Domain API v1",
    }

    if not _get_api_key():
        result["error"] = "DOMAIN_API_KEY not set. Add it to Railway Variables or ~/.hermes/.env"
        return result

    # ── 1. Current listing details ─────────────────────────────────────────────
    listing = _domain_request(f"/listings/{listing_id}")
    if isinstance(listing, dict) and "error" in listing:
        sc = listing.get("status_code")
        if sc == 404:
            result["error"] = f"Listing {listing_id} not found (404). It may have been removed."
        else:
            result["error"] = listing["error"]
            if "detail" in listing:
                result["error_detail"] = listing["detail"]
        return result

    # Extract address and price guide from listing
    prop = listing.get("propertyDetails") or listing.get("property") or {}
    price_details = listing.get("priceDetails") or listing.get("price") or {}

    result["address"] = (
        prop.get("displayableAddress")
        or prop.get("address")
        or listing.get("addressParts", {}).get("displayAddress")
        or None
    )
    result["current_price_guide"] = (
        price_details.get("displayPrice")
        or price_details.get("priceFrom")
        or listing.get("displayPrice")
        or None
    )
    result["days_on_market_current"] = listing.get("daysListed") or listing.get("daysOnMarket")

    # ── 2. History endpoint ────────────────────────────────────────────────────
    history_raw = _domain_request(f"/listings/{listing_id}/history")

    if isinstance(history_raw, dict) and "error" in history_raw:
        # History endpoint may not exist for all listings — not fatal
        logger.warning("History endpoint error: %s", history_raw["error"])
        result["history_warning"] = f"History endpoint unavailable: {history_raw['error']}"
        history_raw = []

    events = _parse_history_events(history_raw)
    campaigns = _group_into_campaigns(events)

    passed_in_count = sum(1 for c in campaigns if c.get("passed_in"))

    result["history"] = campaigns
    result["passed_in_count"] = passed_in_count
    result["signal"] = _build_signal(
        campaigns, passed_in_count, result["days_on_market_current"]
    )

    # Also pull priceHistory from the listing object itself if available
    listing_price_history = (
        listing.get("priceHistory")
        or listing.get("pricingHistory")
        or []
    )
    if listing_price_history and not campaigns:
        # Supplement with any price history embedded in the listing
        result["embedded_price_history"] = listing_price_history[:10]

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python3 auction_history_tool.py <listing_id_or_url>", file=sys.stderr)
        sys.exit(1)
    listing_input = sys.argv[1]
    output = run(listing_input)
    print(json.dumps(output, indent=2, default=str))


# ── Tool Registry ──────────────────────────────────────────────────────────────

try:
    from tools.registry import registry

    registry.register(
        name="auction_history",
        toolset="property",
        schema={
            "name": "auction_history",
            "description": (
                "Get auction history, prior campaigns, pass-in events and price changes "
                "for a Domain listing. Surfaces vendor motivation signals like repeated "
                "pass-ins and extended days on market that indicate negotiating leverage. "
                "Use this before making an offer or bidding at auction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "listing_input": {
                        "type": "string",
                        "description": (
                            "Domain listing ID (e.g. '2019683750') or full Domain URL "
                            "(e.g. 'https://www.domain.com.au/...-2019683750')"
                        ),
                    },
                },
                "required": ["listing_input"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            run(args.get("listing_input", "")), indent=2, default=str
        ),
        check_fn=lambda: bool(_get_api_key()),
        requires_env=["DOMAIN_API_KEY"],
    )

except ImportError:
    # Running outside Hermes context (e.g. direct testing)
    pass
