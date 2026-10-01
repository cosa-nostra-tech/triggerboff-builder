import asyncio, os, subprocess, uuid
from playwright.async_api import async_playwright

REPO="/data/repos/triggerboff"; PORT=os.environ.get("AUDIT_PORT","3195"); BASE=f"http://127.0.0.1:{PORT}"
COOKIE="authjs.session-token"
env={}
for line in open(f"{REPO}/.env.local"):
    if "=" in line and not line.strip().startswith("#"):
        k,v=line.strip().split("=",1); env[k]=v.strip().strip('"').strip("'")
MINT = """
const { hkdf } = require('@panva/hkdf'); const { EncryptJWT } = require('jose'); const crypto = require('crypto');
(async () => { const salt = process.env.C;
  const key = await hkdf('sha256', process.env.S, salt, `Auth.js Generated Encryption Key (${salt})`, 64);
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'t@e.com', name:'Nick Benson' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
mp=f"{REPO}/.mint-measure.cjs"; open(mp,"w").write(MINT)
r=subprocess.run(["node",mp],cwd=REPO,capture_output=True,text=True,
                 env={**os.environ,"S":env["NEXTAUTH_SECRET"],"C":COOKIE,"U":str(uuid.uuid4())})
os.remove(mp); token=r.stdout.strip()

# Replay the exact turn shape from the screenshot: a settled reply with a "Copy" control,
# then the composer with text in it.
EVENTS = [
  {"type":"stage","stage":"calculating","tool":"nsw_property_sales","line":"WOLLONGONG"},
  {"type":"stage","stage":"evaluating","tool":"nsw_property_sales","line":"WOLLONGONG"},
  {"type":"delta","text":"Want me to run the flood and planning checks on Staff Street and Ocean Street? Wollongong CBD looks tight, so before any offer that's the next check.\n\nNoted for your profile: you're a first home buyer, now also considering Wollongong's northern suburbs."},
  {"type":"done","memoriesSaved":[],"saved":True},
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
      for (const e of EVENTS) { c.enqueue(enc.encode(JSON.stringify(e) + '\\n')); await new Promise(r=>setTimeout(r,80)); }
      c.close(); } });
    return new Response(stream, { status:200, headers:{'Content-Type':'application/x-ndjson'} });
  }
  return orig(input, init);
};
""" % __import__("json").dumps(EVENTS)

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=['--enable-unsafe-swiftshader','--use-gl=angle','--use-angle=swiftshader'])
        ctx=await b.new_context(viewport={'width':1440,'height':900}, device_scale_factor=2)
        await ctx.add_cookies([{"name":COOKIE,"value":token,"domain":"127.0.0.1","path":"/","httpOnly":True,"secure":False}])
        page=await ctx.new_page(); await page.add_init_script(INIT)
        await page.goto(f"{BASE}/dashboard/chat", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        # start a turn via the suggested-question path (the one that works)
        await page.evaluate("""() => { const b=[...document.querySelectorAll('button')]
            .find(x=>x.innerText && x.innerText.trim().length>18 && !x.disabled); if(b) b.click(); }""")
        await page.wait_for_timeout(4000)
        # put text in the composer for a like-for-like comparison
        await page.evaluate("""() => { const ta=document.querySelector('textarea');
            const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
            s.call(ta,'hey Luca! I just found a bunch of money');
            ta.dispatchEvent(new Event('input',{bubbles:true})); }""")
        await page.wait_for_timeout(600)

        m = await page.evaluate("""() => {
            const px = (el) => el ? getComputedStyle(el).fontSize : null;
            const w  = (el) => el ? getComputedStyle(el).fontWeight : null;
            const lh = (el) => el ? getComputedStyle(el).lineHeight : null;
            // the first paragraph inside the assistant bubble (the output text)
            const paras=[...document.querySelectorAll('p')].filter(p=>p.innerText.trim().length>40);
            const reply=paras[paras.length-1] || [...document.querySelectorAll('div')]
                .find(d=>d.children.length===0 && d.innerText.trim().length>60) || null;
            const copyBtn=[...document.querySelectorAll('button,div,span')]
                .find(e=>e.innerText && e.innerText.trim()==='Copy');
            const ta=document.querySelector('textarea');
            return {
              reply_p:  {size: px(reply), weight: w(reply), lh: lh(reply)},
              copy:     {size: px(copyBtn), weight: w(copyBtn)},
              composer: {size: px(ta), weight: w(ta), lh: lh(ta)},
              body:     {size: px(document.body)},
            };
        }""")
        for k,v in m.items():
            print(f"  {k:<10} {v}")
        await page.screenshot(path="/tmp/font_measure.png")
        await b.close()

asyncio.run(main())
