#!/usr/bin/env python3
"""Build the exhaustive Sydney real-estate agency list from the NSW licence register.

Source: NSW Government "Property API" (api.onegov.nsw.gov.au), the state's licensing
register. It is exhaustive by law - no one may sell property in NSW without being on it -
so this is the authoritative list including every boutique agency, not a directory sample.

The register has no bulk dump: /browse takes a required `searchText` word/phrase. So it is
enumerated by iterating suburbs and unioning the results, deduplicated by licenceNumber.

Output: /data/.hermes/nsw/sydney_agents.json  (+ a summary on stdout)
"""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request

SECRETS = "/data/.hermes/.builder-secrets"
OUT = "/data/.hermes/nsw/sydney_agents.json"
BASE = "https://api.onegov.nsw.gov.au"

S = {l.split("=", 1)[0]: l.split("=", 1)[1].strip()
     for l in open(SECRETS) if "=" in l and not l.startswith("#")}
K, SEC = S.get("NSW_API_KEY"), S.get("NSW_API_SECRET")
if not (K and SEC):
    print("HARNESS FAILURE: missing NSW_API_KEY / NSW_API_SECRET"); sys.exit(2)

SUBURBS = """
Alexandria Annandale Arncliffe Artarmon Ashbury Ashcroft Ashfield Auburn Balgowlah Balmain
Balmain East Banksia Bankstown Barden Ridge Bardwell Park Bardwell Valley Bass Hill Baulkham Hills
Beaconsfield Beacon Hill Belfield Bellevue Hill Belmore Berala Beverly Hills Bexley Bexley North
Birchgrove Blakehurst Botany Breakfast Point Brighton-Le-Sands Bronte Burwood Burwood Heights
Cabarita Cammeray Camperdown Campsie Canada Bay Canterbury Caringbah Carlingford Carlton Castlecrag
Castle Hill Casula Cecil Hills Chatswood Chifley Chippendale Chiswick Chullora Claremont Meadows
Clemton Park Clontarf Clovelly Coogee Concord Concord West Condell Park Connells Point Coogee
Cremorne Cremorne Point Cromer Cronulla Croydon Croydon Park Curl Curl Daceyville Darlinghurst
Darlington Dee Why Denistone Denistone East Dolls Point Dolans Bay Double Bay Dover Heights
Drummoyne Dulwich Hill Dundas Dundas Valley Dural Earlwood Eastgardens East Hills Eastlakes
East Ryde Eastwood Edensor Park Edgecliff Elanora Heights Elderslie Elizabeth Bay Enfield Engadine
Epping Ermington Erskineville Fairfield Fairlight Five Dock Forest Lodge Forestville Frenchs Forest
Freshwater Georges Hall Gladesville Glebe Glenfield Gordon Granville Greenacre Greenfield Park
Greenwich Gymea Haberfield Hammondville Harbord Haymarket Heathcote Hillsdale Hinchinbrook
Hmas Platypus Homebush Homebush West Hornsby Hunters Hill Huntleys Cove Hurstville Hurstville Grove
Illawong Jannali Kangaroo Point Kareela Kellyville Kentlyn Killara Killarney Heights Kingsford
Kingsgrove Kingsway Kirrawee Kirribilli Kogarah Kogarah Bay Kurnell Kyle Bay Lakemba Lane Cove
Lane Cove North Lane Cove West Lansvale Lavender Bay Leichhardt Lewisham Liberty Grove Lidcombe
Lilyfield Lindfield Linley Point Little Bay Liverpool Longueville Lugarno Lurnea Macquarie Fields
Macquarie Park Maianbar Malabar Manly Maroubra Marrickville Marsfield Matraville Mascot
McMahons Point Meadowbank Melrose Park Menai Merrylands Middle Cove Millers Point Milperra
Milsons Point Miranda Monterey Mortdale Mortlake Mosman Mount Colah Mount Druitt Mount Kuring-Gai
Naremburn Narrabeen Narraweena Narwee Neutral Bay Newington Newtown Normanhurst North Balgowlah
Northbridge North Curl Curl North Epping North Manly North Narrabeen North Parramatta North Rocks
North Ryde North Sydney Northmead Oatlands Oatley Old Guildford Oyster Bay Paddington Padstow
Pagewood Panania Parramatta Peakhurst Pennant Hills Penshurst Petersham Picnic Point Point Piper
Potts Point Prairiewood Punchbowl Putney Pymble Pyrmont Queens Park Ramsgate Randwick Redfern
Regents Park Revesby Rhodes Riverwood Rockdale Rodd Point Rose Bay Rosebery Rosehill Roselands
Roseville Roseville Chase Rozelle Rouse Hill Royal National Park Rushcutters Bay Russell Lea
Rydalmere Ryde Sans Souci Seaforth Sefton Seven Hills Smeaton Grange Sylvania Sylvania Waters
South Coogee South Hurstville South Turramurra St Ives St Leonards St Peters Stanmore Strathfield
Summer Hill Surry Hills Sutherland Sydenham Tamarama Telopea Tempe Tennyson Point Terrey Hills
The Rocks Thornleigh Toongabbie Turramurra Turella Ultimo Undercliffe Vaucluse Villawood
Wahroonga Waitara Wareemba Warriewood Waterloo Waverley Waverton Wentworthville West Pymble
West Ryde Westmead Wetherill Park Willoughby Wollstonecraft Wolli Creek Woollahra Woolloomooloo
Woolooware Woolwich Yagoona Yowie Bay Zetland
""".split()

def token():
    body = urllib.request.urlopen(urllib.request.Request(
        f"{BASE}/oauth/client_credential/accesstoken?grant_type=client_credentials",
        headers={"Authorization": "Basic " + base64.b64encode(f"{K}:{SEC}".encode()).decode(),
                 "User-Agent": "TriggerBOFFBot/1.0"}), timeout=60).read()
    return json.loads(body)["access_token"]

def browse(text, tok, timeout=60):
    u = f"{BASE}/propertyregister/v1/browse?searchText={urllib.parse.quote(text)}"
    req = urllib.request.Request(u, headers={"apikey": K, "Authorization": f"Bearer {tok}",
        "User-Agent": "TriggerBOFFBot/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"__error__": e.code}
    except Exception as e:
        return {"__error__": type(e).__name__}

tok = token()
print(f"  token ok ({len(tok)} chars); enumerating {len(SUBURBS)} Sydney suburbs")

by_licence = {}
errors = 0
for i, sub in enumerate(SUBURBS, 1):
    res = browse(sub, tok)
    if isinstance(res, dict) and "__error__" in res:
        errors += 1
    elif isinstance(res, list):
        for r in res:
            if not isinstance(r, dict):
                continue
            ln = r.get("licenceNumber") or r.get("licenceID")
            if ln:
                by_licence[str(ln)] = r
    if i % 40 == 0:
        print(f"    [{i}/{len(SUBURBS)}] {len(by_licence)} licences so far")
    time.sleep(0.25)

rows = list(by_licence.values())
current = [r for r in rows if (r.get("status") or "").lower() == "current"]
expired = [r for r in rows if (r.get("status") or "").lower() != "current"]

def trading_names(r):
    bn = r.get("businessNames") or []
    out = []
    for b in bn:
        if isinstance(b, dict) and b.get("businessName"):
            out.append(b["businessName"])
        elif isinstance(b, str):
            out.append(b)
    return out

agencies = [{
    "licence_no": r.get("licenceNumber"),
    "licence_type": r.get("licenceType"),
    "status": r.get("status"),
    "expiry": r.get("expiryDate"),
    "legal_name": r.get("licensee") or r.get("licenceName"),
    "trading_names": trading_names(r),
    "suburb": r.get("suburb"),
    "postcode": r.get("postcode"),
    "historical_licence_numbers": r.get("historicalLicenceNumbers") or [],
} for r in current]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({"source": "NSW Government Property API - propertyregister/v1/browse",
           "enumerated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "suburbs_searched": len(SUBURBS), "suburb_errors": errors,
           "licences_total": len(rows), "current": len(agencies), "not_current": len(expired),
           "agencies": sorted(agencies, key=lambda a: (a["suburb"] or "zzz", a["legal_name"] or ""))},
          open(OUT, "w"), indent=1)
open(OUT.replace(".json", "_all.json"), "w").write(json.dumps(rows, indent=1))

print(f"\n  licences found        : {len(rows)}")
print(f"  CURRENT               : {len(agencies)}")
print(f"  expired/other         : {len(expired)}")
print(f"  suburb query errors   : {errors}")
subs = sorted({a["suburb"] for a in agencies if a["suburb"]})
print(f"  suburbs represented   : {len(subs)}")
print(f"  saved -> {OUT}")
print("\n  sample of CURRENT Inner West agencies:")
for a in [x for x in agencies if (x["suburb"] or "") in ("MARRICKVILLE", "NEWTOWN", "DULWICH HILL")][:8]:
    tn = ", ".join(a["trading_names"][:2])
    print(f"    {a['suburb']:<14} {a['legal_name'][:44]:<44} {('| ' + tn[:38]) if tn else ''}")
