import asyncio, json, os, subprocess, uuid
from playwright.async_api import async_playwright

REPO = "/data/repos/triggerboff"
PORT = os.environ.get("AUDIT_PORT", "3198")
BASE = f"http://127.0.0.1:{PORT}"
COOKIE = "authjs.session-token"

env = {}
for line in open(f"{REPO}/.env.local"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1); env[k] = v.strip().strip('"').strip("'")

MINT = """
const { hkdf } = require('@panva/hkdf'); const { EncryptJWT } = require('jose'); const crypto = require('crypto');
(async () => { const salt = process.env.C;
  const key = await hkdf('sha256', process.env.S, salt, `Auth.js Generated Encryption Key (${salt})`, 64);
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'t@e.com', name:'Nick Benson' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
mp = f"{REPO}/.mint-card.cjs"; open(mp, "w").write(MINT)
r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                   env={**os.environ, "S": env["NEXTAUTH_SECRET"], "C": COOKIE, "U": str(uuid.uuid4())})
os.remove(mp); token = r.stdout.strip()

CARD_JSON = (
    '[{"address":"51/44-50 Ewart Street","suburb":"Marrickville","state":"NSW","postcode":"2204",'
    '"price":"$794,000","priceNote":"sold Aug 2026","beds":2,"baths":1,"cars":0,'
    '"type":"Unit","area":"69sqm","strata":"$1,240/q",'
    '"verdict":"Fair, not a bargain",'
    '"why":"Two beds and no parking, so the discount is doing the work a garage should be doing.",'
    '"risks":["No parking","Special levy pending"],'
    '"vsMedian":"21% below the Marrickville unit median",'
    '"url":"https://www.domain.com.au/51-44-50-ewart-street-marrickville-nsw-2204-2016999885",'
    '"source":"Domain"},'
    '{"address":"12/9 Schwebel Street","suburb":"Marrickville","beds":2,"baths":1,"cars":1,'
    '"type":"Unit","price":"$860,000","priceNote":"guide","verdict":"The one to see first",'
    '"why":"Parking, a lift, and a levy half the other block. If the strata report is clean, this is the pick.",'
    '"bonus":true,'
    '"url":"https://bresicwhitney.com.au/buy/12-9-schwebel-street-marrickville-4748317",'
    '"source":"BresicWhitney"}]'
)

EVENTS = [
    {"type": "stage", "stage": "calculating", "tool": "nsw_property_sales", "line": "MARRICKVILLE"},
    {"type": "stage", "stage": "evaluating", "tool": "nsw_property_sales", "line": "MARRICKVILLE"},
    {"type": "delta", "text": "Here's the shortlist — three that fit, one I'd avoid.\n\n"},
    {"type": "delta", "text": "```propertyspec\n"},
    {"type": "delta", "text": CARD_JSON + "\n"},
    {"type": "delta", "text": "```\n"},
    {"type": "done", "memoriesSaved": [], "saved": True},
]

INIT = """
const EVENTS = %s;
const orig = window.fetch;
window.fetch = async function (input, init) {
  const url = (typeof input === 'string') ? input : ((input && input.url) || '');
  const j = (o) => new Response(JSON.stringify(o), { status:200, headers:{'Content-Type':'application/json'} });
  if (url.includes('/api/memories')) return j({ memories: [] });
  if (url.includes('/api/messages')) return j({ messages: [] });
  if (url.includes('/api/chat')) {
    const enc = new TextEncoder();
    const stream = new ReadableStream({ async start(c) {
      for (const e of EVENTS) { c.enqueue(enc.encode(JSON.stringify(e) + '\\n')); await new Promise(r=>setTimeout(r,60)); }
      c.close(); } });
    return new Response(stream, { status:200, headers:{'Content-Type':'application/x-ndjson'} });
  }
  return orig(input, init);
};
""" % json.dumps(EVENTS)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'])
        ctx = await b.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
        await ctx.add_cookies([{"name": COOKIE, "value": token, "domain": "127.0.0.1",
                                "path": "/", "httpOnly": True, "secure": False}])
        page = await ctx.new_page()
        await page.add_init_script(INIT)
        await page.goto(f"{BASE}/dashboard/chat", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        await page.evaluate("""() => { const b=[...document.querySelectorAll('button')]
            .find(x=>x.innerText && x.innerText.trim().length>18 && !x.disabled); if(b) b.click(); }""")
        await page.wait_for_timeout(5000)

        m = await page.evaluate("""() => {
            const t = document.body.innerText;
            return {
              cardsRendered: document.body.innerText.split('OUR CALL').length - 1,
              addresses: ['51/44-50 Ewart Street','12/9 Schwebel Street'].filter(a=>t.includes(a)),
              verdicts: ['Fair, not a bargain','The one to see first'].filter(v=>t.includes(v)),
              bonusTag: t.includes('Not on Domain or REA'),
              rawLeaked: t.includes('propertyspec') || t.includes('"address"'),
              links: [...document.querySelectorAll('a')].filter(a=>/domain\\.com\\.au|bresicwhitney/.test(a.href)).length,
            };
        }""")
        print("  cards rendered      :", m["cardsRendered"])
        print("  addresses shown     :", m["addresses"])
        print("  verdicts shown      :", m["verdicts"])
        print("  bonus tag rendered  :", m["bonusTag"])
        print("  listing links       :", m["links"])
        print("  raw json leaked     :", m["rawLeaked"], "(want False)")
        ok = (m["cardsRendered"] == 2 and len(m["addresses"]) == 2 and len(m["verdicts"]) == 2
              and m["bonusTag"] and not m["rawLeaked"] and m["links"] >= 2)
        print(f"\n  {'PASS' if ok else 'FAIL'}")
        await page.screenshot(path="/tmp/property_card.png", full_page=True)
        await b.close()

asyncio.run(main())
