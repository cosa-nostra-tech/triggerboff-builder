#!/usr/bin/env python3
"""
rental_yield_tool.py — Gross rental yield calculator for Sydney suburbs.

Fetches live Domain rental listings for a given suburb + bedroom count,
computes median weekly rent, and returns the gross yield against a purchase price.

Usage: python3 rental_yield_tool.py Marrickville 3 1800000
"""

import json, logging, os, re, sys
from typing import Optional

# domain_fallback sits beside this file in $HERMES_HOME/tools/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from domain_fallback import get_api_key as _fallback_get_api_key  # noqa: E402
from domain_fallback import is_failure  # noqa: E402
from domain_fallback import request as _domain_request_impl  # noqa: E402

logger = logging.getLogger(__name__)

DOMAIN_API_BASE = "https://api.domain.com.au/v1"


# ── API key helper (shared with domain_property_tool.py) ───────────────────────

def _get_api_key() -> str:
    return _fallback_get_api_key()


# ── Domain API helper ──────────────────────────────────────────────────────────

def _domain_request(endpoint: str, method: str = "GET", body: Optional[dict] = None,
                    tool: str = "rental_yield"):
    """Call the Domain API through domain_fallback.

    Returns parsed data on success, or a structured ``unavailable`` payload on
    failure — check with ``is_failure()`` before treating the value as data.
    """
    return _domain_request_impl(
        tool=tool, endpoint=endpoint, method=method, body=body,
        base=DOMAIN_API_BASE, timeout=15,
    )


# ── Price extraction ───────────────────────────────────────────────────────────

def _parse_weekly_rent(price_details: dict) -> Optional[float]:
    """Extract a numeric weekly rent from a Domain priceDetails block."""
    # Prefer the raw numeric field if present
    price = price_details.get("price")
    if price and isinstance(price, (int, float)) and price > 0:
        return float(price)

    # Fall back to parsing the display string e.g. "$650 per week", "$600 pw", "$580/week"
    display = price_details.get("displayPrice", "")
    if display:
        # Strip currency symbol and commas, grab first integer/float
        nums = re.findall(r"[\d,]+(?:\.\d+)?", display.replace(",", ""))
        if nums:
            candidate = float(nums[0])
            # Domain sometimes returns monthly or fortnightly — ignore implausible values
            if 50 < candidate < 10000:
                return candidate

    return None


# ── Main run function ──────────────────────────────────────────────────────────

def run(suburb: str, bedrooms: int, purchase_price: int) -> dict:
    """
    Calculate gross rental yield for a Sydney suburb.

    Args:
        suburb:         Suburb name e.g. "Marrickville"
        bedrooms:       Number of bedrooms (exact match)
        purchase_price: Purchase price in AUD e.g. 1800000

    Returns:
        dict with suburb, bedrooms, median_weekly_rent, sample_size,
        gross_yield_pct, purchase_price, and comparable_rentals list.
    """
    body = {
        "listingType": "Rent",
        "locations": [{"state": "NSW", "suburb": suburb}],
        "bedrooms": {"minimum": bedrooms, "maximum": bedrooms},
        "pageSize": 20,
    }

    result = _domain_request("/listings/residential/_search", method="POST", body=body)

    if is_failure(result):
        # domain_fallback returns a structured dict for every failure path
        return result if isinstance(result, dict) else {"status": "unavailable"}

    # Extract weekly rents and build comparable_rentals list
    rents = []
    comparable_rentals = []

    for item in result:
        listing = item.get("listing", {})
        price_details = listing.get("priceDetails", {})
        property_details = listing.get("propertyDetails", {})

        address = property_details.get("displayableAddress", "")
        beds = property_details.get("bedrooms")
        display_price = price_details.get("displayPrice", "")

        weekly_rent = _parse_weekly_rent(price_details)
        if weekly_rent is not None:
            rents.append(weekly_rent)

        comparable_rentals.append({
            "address": address,
            "price": display_price,
            "beds": beds,
        })

    if not rents:
        return {
            "suburb": suburb,
            "bedrooms": bedrooms,
            "purchase_price": purchase_price,
            "error": "No rental listings with parseable prices found for this suburb/bedroom combination.",
            "sample_size": 0,
            "comparable_rentals": comparable_rentals,
        }

    # Median weekly rent
    sorted_rents = sorted(rents)
    n = len(sorted_rents)
    if n % 2 == 1:
        median_weekly_rent = sorted_rents[n // 2]
    else:
        median_weekly_rent = (sorted_rents[n // 2 - 1] + sorted_rents[n // 2]) / 2.0

    gross_yield_pct = round((median_weekly_rent * 52) / purchase_price * 100, 2)

    return {
        "suburb": suburb,
        "bedrooms": bedrooms,
        "purchase_price": purchase_price,
        "median_weekly_rent": round(median_weekly_rent, 2),
        "sample_size": n,
        "gross_yield_pct": gross_yield_pct,
        "comparable_rentals": comparable_rentals,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 4:
        print("Usage: python3 rental_yield_tool.py <suburb> <bedrooms> <purchase_price>")
        sys.exit(1)
    suburb_arg = sys.argv[1]
    bedrooms_arg = int(sys.argv[2])
    price_arg = int(sys.argv[3])
    print(json.dumps(run(suburb_arg, bedrooms_arg, price_arg), indent=2))


# ── Tool Registry ──────────────────────────────────────────────────────────────

try:
    from tools.registry import registry
    registry.register(
        name="rental_yield",
        toolset="property",
        schema={
            "name": "rental_yield",
            "description": (
                "Calculate gross rental yield for a Sydney suburb by fetching live Domain "
                "rental listings and computing median weekly rent against a purchase price. "
                "Returns median_weekly_rent, sample_size, gross_yield_pct, and a list of "
                "comparable_rentals so the buyer can assess investment viability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suburb": {
                        "type": "string",
                        "description": "NSW suburb name e.g. 'Marrickville'",
                    },
                    "bedrooms": {
                        "type": "integer",
                        "description": "Number of bedrooms to match exactly e.g. 3",
                    },
                    "purchase_price": {
                        "type": "integer",
                        "description": "Purchase price in AUD e.g. 1800000",
                    },
                },
                "required": ["suburb", "bedrooms", "purchase_price"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            run(
                suburb=args.get("suburb", ""),
                bedrooms=int(args.get("bedrooms", 2)),
                purchase_price=int(args.get("purchase_price", 0)),
            ),
            indent=2,
        ),
        check_fn=lambda: bool(_get_api_key()),
        requires_env=["DOMAIN_API_KEY"],
    )
except ImportError:
    pass
