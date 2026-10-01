#!/usr/bin/env python3
"""How much agency-site inventory is genuinely NOT on Domain or REA?

The honest version of the question Nick's friend raised. Earlier attempt compared agency
individual listings against portal SUBURB INDEX pages, which proved nothing. This one keeps
individual listings on both sides and compares by address.

Two independent angles, because either alone is weak:
  A. SEARCH   - ask for individual listings ON each portal domain and ON agency domains,
                then diff the address sets
  B. PORTAL-ONLY CHECK - take the agency addresses and search the portals directly for each.
                This is the check the product would have to run before tagging anything as
                "not on Domain or REA", so its cost and reliability matter as much as its
                answer.

Reported as a fraction, with the sample size, so it can be argued about honestly.
"""
import json, re, sys, time, urllib.request

def secret(n):
    for p in ("/proc/1/environ", "/data/.hermes/.builder-secrets"):
        try:
            raw = open(p, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        m = re.search(rf"{n}=([^\s\x00]+)", raw)
        if m:
            return m.group(1)
    return None

T = secret("TAVILY_API_KEY")
AGENCY = ["bresicwhitney.com.au", "ljhooker.com.au", "raywhite.com", "mcgrathestateagents.com.au",
          "richardmatthews.com.au", "adrianwilliam.com.au", "theagency.com.au", "belleproperty.com",
          "pillinger.com.au", "cobdenandhaven.com.au"]
PORTAL = ["domain.com.au", "realestate.com.au"]

SUBURBS = ["Marrickville NSW 2204", "Newtown NSW 2042"]

# Portal individual-listing URL shapes, and the index shapes to reject.
PORTAL_LISTING = re.compile(r"(domain\.com\.au/[a-z0-9-]+-\d{6,}"          # /2-336-livingstone-road-...-2016999885
                            r"|realestate\.com\.au/property/)"               # /property/unit-...
                            , re.I)
INDEXISH = re.compile(r"/(sale|buy|rent)/(in-|between-|with-|property-)|/sale/[a-z-]+-nsw-\d{4}/?$", re.I)

STREET = r"(Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Place|Pl|Crescent|Cres|Parade|Pde|Drive|Dr|Way|Terrace|Tce|Circuit|Cct|Highway|Hwy|Boulevard|Blvd|Square|Sq|Mews|Grove|Close|Court|Ct)"
OTHERNUM = r"(?:[Ss]t(?:reet)?|[Rr]d|[Aa]ve(?:nue)?|[Pp]l(?:ace)?|[Ll]ane|[Pp]de|[Pp]arade|[Tt]ce|[Dd]r(?:ive)?|[Ww]ay|[Cc]res(?:cent)?|[Bb]lvd|[Bb]oulevard|[Mm]ews|[Gg]rove|[Cc]ourt|[Cc]t|[Ss]q(?:uare)?|[Hh]wy|[Hh]ighway|[Cc]ircuit|[Cc]ct)\b"

def extract_address(text):
    """Unit-style (1/12-16 Schwebel Street) or plain (353 Marrickville Road) or (38-40 Renwick St)."""
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(rf"\b(\d+[A-Za-z]?)\s*/\s*(\d[\d\-]*)\s+([A-Za-z][A-Za-z'\- ]{{2,26}}?)\s+{STREET}\b", t, re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)} {m.group(3).strip()} {m.group(4)}".lower()
    m = re.search(rf"\b(\d[\d\-]*)\s+([A-Za-z][A-Za-z'\- ]{{2,26}}?)\s+{STREET}\b", t, re.I)
    if m:
        return f"{m.group(1)} {m.group(2).strip()} {m.group(3)}".lower()
    return None

def addr_from_url(u):
    """Strip a listing URL down to a comparable address string."""
    u = re.sub(r"^https?://(www\.)?", "", u or "")
    m = re.search(r"domain\.com\.au/(.+?)-\d{6,}$", u)
    if m:
        return m.group(1).replace("-", " ")
    m = re.search(r"realestate\.com\.au/property/(?:[a-z]+-)?(.+?)$", u)
    if m:
        return m.group(1).replace("-", " ")
    m = re.search(r"bresicwhitney\.com\.au/buy/(.+?)-\d+$", u)
    if m:
        return m.group(1).replace("-", " ")
    m = re.search(r"property\.ljhooker\.com\.au/[a-z]+-(.+?)-[a-z0-9]{6,}$", u)
    if m:
        return m.group(1).replace("-", " ").replace("nsw ", "nsw ")
    m = re.search(r"raywhite\.com/(?:[a-z]+/)+(\d+)/?$", u)
    if m:
        return None  # id-only URL, no address
    return None

def key(addr):
    """Normalise for comparison: drop unit prefix, road-type synonyms, punctuation."""
    if not addr:
        return None
    a = addr.lower()
    a = re.sub(r"^\d+[a-z]?/\d[\d\-]*\s+", "", a)          # unit prefix
    a = re.sub(r"\b(road|rd)\b", "rd", a)
    a = re.sub(r"\b(street|st)\b", "st", a)
    a = re.sub(r"\b(avenue|ave)\b", "ave", a)
    a = re.sub(r"\b(parade|pde)\b", "pde", a)
    a = re.sub(r"\b(crescent|cres)\b", "cres", a)
    a = re.sub(r"\b(terrace|tce)\b", "tce", a)
    a = re.sub(r"[^a-z0-9 ]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    m = re.match(r"^(\d+[a-z]?)\s+(.+)", a)
    return f"{m.group(1)} {m.group(2)}" if m else a

def tav(q, domains, n=20):
    body = {"api_key": T, "query": q, "max_results": n, "search_depth": "advanced"}
    if domains:
        body["include_domains"] = domains
    req = urllib.request.Request("https://api.tavily.com/search", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read()).get("results", [])

if not T:
    print("HARNESS FAILURE: no Tavily key"); sys.exit(2)

grand = {"agency": 0, "portal": 0, "agency_only": 0}
for suburb in SUBURBS:
    print(f"\n===== {suburb} =====")
    # A. individual listings on each side
    agency = tav(f"property for sale {suburb} site listing", AGENCY, 20)
    portal = tav(f"property for sale {suburb}", PORTAL, 20)

    ag = {}
    for r in agency:
        u = r.get("url", "")
        if "ljhooker" in u and "rental-" in u:   # rentals are a different market
            continue
        a = addr_from_url(u) or extract_address(r.get("title", ""))
        k = key(a)
        if k:
            ag[k] = {"url": u, "title": r.get("title", "")[:70]}
    pt = {}
    for r in portal:
        u = r.get("url", "")
        if INDEXISH.search(u) and not PORTAL_LISTING.search(u):
            continue
        a = addr_from_url(u) or extract_address(r.get("title", ""))
        k = key(a)
        if k:
            pt[k] = {"url": u}

    only = {k: v for k, v in ag.items() if k not in pt}
    print(f"  agency individual listings : {len(ag)}")
    print(f"  portal individual listings : {len(pt)}")
    print(f"  agency addresses NOT seen on a portal: {len(only)}")
    for k, v in list(only.items())[:6]:
        print(f"    + {k:<40} {v['url'][:74]}")

    # B. the check the product itself would have to run before tagging
    print(f"  portal-only check on each agency address (this is the tagging step's cost):")
    confirmed = 0
    for k, v in list(only.items())[:6]:
        try:
            hits = tav(f'"{k}" {suburb} for sale', PORTAL, 6)
        except Exception as e:
            print(f"    ? {k}: {type(e).__name__}"); continue
        found = any(key(addr_from_url(h.get("url", "")) or extract_address(h.get("title", ""))) == k
                    for h in hits)
        confirmed += (not found)
        print(f"    {'NOT ON PORTALS' if not found else 'found on a portal'}  {k}")
    if only:
        print(f"  -> of {min(6,len(only))} checked, {confirmed} stayed absent from both portals")

    grand["agency"] += len(ag); grand["portal"] += len(pt); grand["agency_only"] += len(only)

print("\n===== overall =====")
print(f"  agency individual listings        : {grand['agency']}")
print(f"  portal individual listings        : {grand['portal']}")
print(f"  agency-only (before verification) : {grand['agency_only']}")
if grand["agency"]:
    print(f"  agency-only share                 : {100*grand['agency_only']/grand['agency']:.0f}%")
print("\n  Sample, not a census: search surfaces top-N per query, so this is the")
print("  retrievable slice, not the whole market. Treat it as an order of magnitude.")
