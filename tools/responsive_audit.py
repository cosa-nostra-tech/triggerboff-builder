#!/usr/bin/env python3
"""Responsive audit for the TriggerBOFF chat screen.

WHY IT EXISTS
The panel-gap bug (orb card bottom-anchored, nav card top-anchored, so they collided on
any viewport above 783px) survived because I measured exactly ONE viewport and picked the
panel boxes out of a list by eye. Both mistakes are designed out here:

  * many viewports, including short and very tall
  * a hard identity assertion on every load, so a redirect to a login page can never be
    scored as a pass (a test earlier tonight ran green against the Hermes setup login)
  * panels identified by structure, not by reading a list and guessing

Defects are found programmatically, so the loop has something objective to hill-climb on.
Each finding carries the viewport, the measurement and the offending element.

Usage:  python3 responsive_audit.py [-o out.json] [--port 3200] [--screenshots DIR]
Exit:   0 = no defects, 1 = defects found, 2 = harness failure (report, do NOT score).
"""
import argparse, json, os, subprocess, sys, uuid

REPO = "/data/repos/triggerboff"
VIEWPORTS = [
    ("mobile-small",  360,  640),
    ("mobile",        390,  844),
    ("mobile-wide",   430,  932),
    ("tablet-port",   768, 1024),
    ("tablet-land",  1024,  768),
    ("laptop",       1280,  800),
    ("desktop",      1440,  771),   # the Figma frame
    ("desktop-tall", 1440,  900),
    ("desktop-tall", 1440, 1200),
    ("wide",         1920, 1080),
    ("short",        1440,  600),
    ("tiny",          320,  568),
]

# Every check returns a list of defect dicts. Pure measurement - no thresholds by vibe.
PROBE = r"""
() => {
  const out = [];
  const vw = window.innerWidth, vh = window.innerHeight;
  const add = (kind, detail, el) => out.push({kind, detail, el});

  // identity: the chat screen actually rendered
  const txt = document.body.innerText || '';
  // Identity by the composer, not the desktop panel: at mobile widths the sidebar is
  // replaced by a top bar, so "Hi, I'm Luca" is legitimately absent and asserting on it
  // fails a correctly responsive page.
  add('probe:identity', 'textlen=' + txt.length +
      ' composer=' + !!document.querySelector('textarea') +
      ' nav=' + /Chat with Luca|Property Pipeline/.test(txt));

  // 1. horizontal overflow - the classic responsive break
  if (document.documentElement.scrollWidth > vw + 1)
    add('overflow-x', 'scrollWidth ' + document.documentElement.scrollWidth + ' > vw ' + vw);

  // 2. anything painted outside the viewport
  const all = [...document.querySelectorAll('body *')];
  const outside = [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    if (r.right > vw + 2 || r.left < -2)
      outside.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,30),
                    left: Math.round(r.left), right: Math.round(r.right)});
  }
  if (outside.length) add('outside-viewport', outside.length + ' element(s)', outside.slice(0,4));

  // 3. panel overlap: the sidebar cards must never intersect
  const rail = all.filter(d => { const r = d.getBoundingClientRect();
      return Math.abs(r.left - 20) < 8 && Math.abs(r.width - 190) < 8 && r.height > 60; });
  const boxes = rail.map(d => d.getBoundingClientRect())
    .map(r => ({top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height)}))
    .sort((a,b) => a.top - b.top || a.h - b.h);
  const uniq = []; const seen = new Set();
  for (const b of boxes) { const k = b.top+':'+b.bottom+':'+b.h; if (!seen.has(k)) { seen.add(k); uniq.push(b); } }
  const orb = uniq.find(b => b.top <= 30 && b.h > 120);
  const nav = uniq.find(b => b.top > 200);
  if (orb && nav) {
    const gap = nav.top - orb.bottom;
    if (gap < 6) add('panel-overlap', 'gap ' + gap + 'px between orb card and nav card',
                     {orb, nav});
  } else if (vw >= 900) {
    add('panel-missing', 'orb or nav card not found at desktop width', uniq.slice(0,4));
  }

  // 4. vertical clipping of the conversation (composer must not cover the last line)
  const scroller = all.find(el => { const cs = getComputedStyle(el);
    return cs.overflowY === 'auto' && el.getBoundingClientRect().height > 120; });
  const composer = document.querySelector('textarea')?.closest('div');
  if (scroller && composer) {
    const sr = scroller.getBoundingClientRect(), cr = composer.getBoundingClientRect();
    if (sr.bottom > cr.top + 2)
      add('composer-covers-scroll', 'scroll region bottom ' + Math.round(sr.bottom) +
          ' overlaps composer top ' + Math.round(cr.top));
  }

  // 5. tap targets on touch widths
  if (vw <= 480) {
    const small = [];
    for (const el of all) {
      const tag = el.tagName;
      if (!['BUTTON','A','INPUT','TEXTAREA'].includes(tag)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.height < 34 || r.width < 34)
        small.push({tag, w: Math.round(r.width), h: Math.round(r.height)});
    }
    if (small.length) add('small-tap-target', small.length + ' under 34px', small.slice(0,4));
  }

  // 6. text clipped by its own box
  const clipped = [];
  for (const el of all) {
    if (el.children.length) continue;
    if (!(el.textContent || '').trim()) continue;
    const cs = getComputedStyle(el);
    if (cs.overflow === 'visible') continue;
    if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)
      clipped.push({tag: el.tagName, text: el.textContent.trim().slice(0,28)});
  }
  if (clipped.length) add('text-clipped', clipped.length + ' element(s)', clipped.slice(0,4));

  return out;
}
"""

def mint_cookie():
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
    mp = f"{REPO}/.mint-audit.cjs"; open(mp, "w").write(MINT)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": env["NEXTAUTH_SECRET"], "C": "authjs.session-token",
                            "U": str(uuid.uuid4())})
    os.remove(mp)
    if r.returncode != 0 or not r.stdout.strip():
        print("HARNESS FAILURE: could not mint a session cookie:", r.stderr[:200]); sys.exit(2)
    return r.stdout.strip()

def ensure_server(port):
    import socket
    def up():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1): return True
        except OSError: return False
    if up():
        return None  # caller must own it; we only start if free
    p = subprocess.Popen(["npx", "next", "start", "-p", str(port)], cwd=REPO,
                         stdout=open(f"/tmp/audit_{port}.log", "w"), stderr=subprocess.STDOUT,
                         env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
    import time
    for _ in range(40):
        if up(): return p
        time.sleep(1)
    print(f"HARNESS FAILURE: server never came up on {port}"); sys.exit(2)

async def run(port, shots, out):
    from playwright.async_api import async_playwright
    token = mint_cookie()
    defects, results = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--enable-unsafe-swiftshader','--use-gl=angle','--use-angle=swiftshader'])
        for name, w, h in VIEWPORTS:
            ctx = await b.new_context(viewport={'width': w, 'height': h}, device_scale_factor=2)
            await ctx.add_cookies([{"name":"authjs.session-token","value":token,"domain":"127.0.0.1",
                                    "path":"/","httpOnly":True,"secure":False}])
            page = await ctx.new_page()
            try:
                await page.goto(f"http://127.0.0.1:{port}/dashboard/chat", wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2500)
                # HARD GATE: never score a page that is not the chat screen
                url = page.url
                if "/dashboard/chat" not in url or "login" in url:
                    print(f"HARNESS FAILURE: {name} landed on {url}"); sys.exit(2)
                probe = await page.evaluate(PROBE)
            except SystemExit:
                raise
            except Exception as e:
                print(f"HARNESS FAILURE at {name}: {type(e).__name__}: {str(e)[:120]}"); sys.exit(2)

            ident = next((d for d in probe if d['kind'] == 'probe:identity'), None)
            if not ident or 'composer=true' not in ident['detail']:
                print(f"HARNESS FAILURE: {name} did not render the chat screen ({ident})"); sys.exit(2)

            found = [d for d in probe if not d['kind'].startswith('probe:')]
            results.append({"viewport": name, "w": w, "h": h, "defects": found})
            for d in found:
                defects.append({"viewport": f"{name} {w}x{h}", **d})
            if shots:
                os.makedirs(shots, exist_ok=True)
                await page.screenshot(path=f"{shots}/{name}_{w}x{h}.png")
            flag = "DEFECT" if found else "clean "
            print(f"  {flag}  {name:<14} {w:>4}x{h:<4}  {', '.join(sorted({d['kind'] for d in found})) or ''}")
            await ctx.close()
        await b.close()

    payload = {"viewports": len(VIEWPORTS), "defects": defects,
               "defect_count": len(defects),
               "by_kind": {k: sum(1 for d in defects if d['kind'] == k) for k in sorted({d['kind'] for d in defects})},
               "results": results}
    json.dump(payload, open(out, "w"), indent=2)
    print(f"\n  {len(defects)} defect(s) across {len(VIEWPORTS)} viewports -> {out}")
    if payload["by_kind"]:
        for k, n in payload["by_kind"].items(): print(f"    {k}: {n}")
    return payload

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/data/.hermes/benchmarks/responsive/audit.json")
    ap.add_argument("--port", type=int, default=3200)
    ap.add_argument("--screenshots", default="/data/.hermes/benchmarks/responsive/shots")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    proc = ensure_server(a.port)
    try:
        import asyncio
        payload = asyncio.run(run(a.port, a.screenshots, a.out))
    finally:
        if proc:
            try: os.killpg(os.getpgid(proc.pid), 9)
            except Exception: pass
    sys.exit(1 if payload["defect_count"] else 0)

if __name__ == "__main__":
    main()
