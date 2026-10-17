#!/usr/bin/env python3
"""Audit the chat screen with a POPULATED conversation.

WHY IT EXISTS
responsive_audit.py only scores the empty welcome state. Every markdown table,
URL, listing link and multi-turn message therefore ships unmeasured — the class
of thing that actually overflows a phone. This loads a realistic four-message
conversation (table, long URL, phone number, multi-paragraph replies) through
the app's own React + ChatMarkdown pipeline and runs responsive_audit.PROBE at
the same 12 viewports, plus a conversation-specific link tap-target check.

HOW IT INJECTS THE DATA
Not through Supabase: the builder box's .env.local carries a placeholder
NEXT_PUBLIC_SUPABASE_URL, so /api/messages returns []. Instead the one endpoint
is stubbed with Playwright routing and the component renders the payload
exactly as it would a fetched one. If that endpoint starts returning real rows,
this stays valid — it changes the source, not the render path.

Usage:  python3 conv_audit.py --port 3212
Exit:   0 = clean, 1 = defects, 2 = harness failure (report, do NOT score).

Before running: freeport.py <port>, then start_detached.py <port> (the server
must be started with NODE_ENV=development or the minted cookie is rejected).
"""
import argparse, json, os, subprocess, sys

REPO = "/data/repos/triggerboff"
sys.path.insert(0, "/data/repos/triggerboff-builder/tools")
import responsive_audit as RA

TEST_UID = "11111111-2222-4333-8444-555555555555"

MSGS = [
    ("user", "What's a fair price for 12 Bridge St, Erskineville? Give me comparables."),
    ("assistant", """**Verdict: $1.62m is the number.** The vendor wants $1.75m. Walk.

Three settled sales within 600m, last 90 days:

| Address | Sold | Date | Beds | Land m² | $/m² |
|---|---|---|---|---|---|
| 8 Bridge St | $1,590,000 | 12 Aug | 3 | 158 | $10,063 |
| 21 Malcolm St | $1,655,000 | 03 Sep | 3 | 171 | $9,678 |
| 5 Park Lane | $1,710,000 | 27 Jul | 4 | 204 | $8,382 |

The full record is here: https://www.valuergeneral.nsw.gov.au/land_value_summaries/lv.php?lga=SYDNEY&house_number=12&street_name=BRIDGE&locality=ERSKINEVILLE&postcode=2043

Watch the flood overlay — the rear third of the block sits in the 1-in-100 year line, which is why the last two campaigns passed in.
"""),
    ("user", "Is that flood line going to hurt me on insurance?"),
    ("assistant", """Yes, and budget for it. Flood-affected postcodes in the inner west run 20-35% above the Sydney median for building cover.

**Do this before you bid:** call the insurer with the address, not the suburb.

If they quote above $4,200 a year on a $1.6m house, that is the market telling you the risk is real. Sanity check: https://www.sira.nsw.gov.au/insurance-and-disputes/check-your-premium
"""),
    ("assistant", """One more thing — the strata record for the neighbouring block (SP 74821) shows 14 lots and a $680/quarter admin fund.

Call the managing agent before Saturday: 02 9550 1234."""),
]


def mint_fixed(uid):
    """Mint an Auth.js v5 session cookie with a chosen sub (same HKDF scheme as the audit)."""
    e = {}
    for line in open(f"{REPO}/.env.local"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            e[k] = v.strip().strip('"').strip("'")
    js = """
const { hkdf } = require('@panva/hkdf'); const { EncryptJWT } = require('jose'); const crypto = require('crypto');
(async () => { const salt = process.env.C;
  const key = await hkdf('sha256', process.env.S, salt, `Auth.js Generated Encryption Key (${salt})`, 64);
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'t@e.com', name:'Nick Benson' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-conv.cjs"
    open(mp, "w").write(js)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": e["NEXTAUTH_SECRET"],
                            "C": "authjs.session-token", "U": uid})
    os.remove(mp)
    if r.returncode != 0 or not r.stdout.strip():
        print("HARNESS FAILURE: could not mint cookie:", r.stderr[:200])
        sys.exit(2)
    return r.stdout.strip()


async def run(port, out):
    from playwright.async_api import async_playwright
    token = mint_fixed(TEST_UID)
    defects, results = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--enable-unsafe-swiftshader'])
        try:
            for name, w, h in RA.VIEWPORTS:
                ctx = await b.new_context(viewport={'width': w, 'height': h}, device_scale_factor=2)
                await ctx.add_cookies([{"name": "authjs.session-token", "value": token,
                                        "domain": "127.0.0.1", "path": "/",
                                        "httpOnly": True, "secure": False}])
                page = await ctx.new_page()
                await page.route("**/api/messages", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"messages": [{"role": r, "content": c} for r, c in MSGS]})))
                await page.goto(f"http://127.0.0.1:{port}/dashboard/chat",
                                wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                if "/dashboard/chat" not in page.url:
                    print(f"HARNESS FAILURE: {name} landed on {page.url}"); sys.exit(2)
                txt = await page.evaluate("() => document.body.innerText")
                if "Bridge St" not in txt:
                    print(f"HARNESS FAILURE: conversation did not render at {name}"); sys.exit(2)

                probe = await page.evaluate(RA.PROBE)
                found = [d for d in probe if not d['kind'].startswith('probe:')]

                # Links rendered from the conversation, at touch widths. The stock probe
                # covers BUTTON/A too, but only for its own page state; this is explicit
                # because links are the product's payload.
                if w <= 480:
                    links = await page.evaluate("""() => [...document.querySelectorAll('a')].map(a => {
                        const r = a.getBoundingClientRect();
                        return {w: Math.round(r.width), h: Math.round(r.height),
                                t: (a.textContent||'').slice(0,30)};
                    }).filter(x => x.w && x.h && (x.w < 34 || x.h < 34))""")
                    if links:
                        found.append({"kind": "conv-small-link",
                                      "detail": f"{len(links)} link(s) under 34px", "el": links[:4]})

                results.append({"viewport": name, "w": w, "h": h, "defects": found})
                for d in found:
                    defects.append({"viewport": f"{name} {w}x{h}", **d})
                flag = "DEFECT" if found else "clean "
                print(f"  {flag}  {name:<14} {w:>4}x{h:<4}  "
                      f"{', '.join(sorted({d['kind'] for d in found})) or ''}")
                await ctx.close()
        finally:
            await b.close()

    json.dump({"viewports": len(RA.VIEWPORTS), "defects": defects,
               "defect_count": len(defects), "results": results},
              open(out, "w"), indent=2)
    print(f"\n  {len(defects)} defect(s) with a populated conversation -> {out}")
    for k in sorted({d['kind'] for d in defects}):
        print(f"    {k}: {sum(1 for d in defects if d['kind'] == k)}")
    return defects


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3212)
    ap.add_argument("--out", default="/data/.hermes/benchmarks/responsive/conv_audit.json")
    a = ap.parse_args()
    import asyncio
    d = asyncio.run(run(a.port, a.out))
    sys.exit(1 if d else 0)
