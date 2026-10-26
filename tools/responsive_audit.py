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
    # Landscape phones and short laptops: the list above only samples one landscape
    # size (1024x768) and no phone in landscape at all, so the whole 375-430px-tall
    # band where the composer eats the screen was unmeasured. The chat screen is
    # clean here (measured 2026-10-23), the point is to keep it that way.
    ("phone-land",    844,  390),
    ("phone-land-sm", 667,  375),
    ("tablet-tall",   600,  900),
    ("fold",          280,  653),
    ("laptop-short", 1024,  600),
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

  // 2. anything painted outside the viewport, EXCLUDING content a scrollable
  //    on-page ancestor clips/reaches. A 6-column markdown table inside an
  //    overflow-x:auto wrapper paints past the viewport edge but the ancestor
  //    scrolls and the PAGE does not overflow (check #1 confirms) — flagging it
  //    contradicts check #1. Only a *scrollable* ancestor (auto|scroll) excuses
  //    the overflow, because the content is reachable; an ancestor that CLIPS
  //    with overflow hidden loses content and is still a defect.
  const all = [...document.querySelectorAll('body *')];
  const reachedByScroll = (el) => {
    let a = el.parentElement;
    while (a && a !== document.documentElement) {
      const acs = getComputedStyle(a);
      if (acs.overflowX === 'auto' || acs.overflowX === 'scroll') {
        const ar = a.getBoundingClientRect();
        if (ar.left >= -2 && ar.right <= vw + 2) return true;
      }
      a = a.parentElement;
    }
    return false;
  };
  const outside = [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    if (r.right > vw + 2 || r.left < -2) {
      if (reachedByScroll(el)) continue;
      outside.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,30),
                    left: Math.round(r.left), right: Math.round(r.right)});
    }
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
  const orb = uniq.find(b => b.top <= 30 && b.h > 120) || uniq[0];
  // The nav card is BOTTOM-anchored (bottom:22, height:211), so on a short desktop
  // viewport its top sits well under 200 (at 430px tall it is 197). A hardcoded
  // `top > 200` then fails to find a correctly-rendered sidebar and reports
  // panel-missing. Locate it as the rail box below the orb instead. A genuinely
  // absent nav leaves only the orb's own boxes (same top), so panel-missing still
  // fires; an overlap is still caught because the gap is measured on the same boxes.
  const last = uniq[uniq.length - 1];
  const nav = (orb && last && last.top > orb.top + 8) ? last : undefined;
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

  // 5. tap targets on touch widths. The touch band runs to the 900px layout
  // breakpoint (tablets at 481-900 are touch too); probing only <=480 left a
  // planted 14x8 link at 834px invisible to this audit.
  if (vw <= 900) {
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
    // Scrollable overflow is REACHABLE, not clipped. A textarea capped at 140px
    // with overflow-y:auto is the normal chat composer; its scrollHeight exceeds
    // clientHeight by design and the user can scroll to the end. Only hidden/clip
    // (or auto with no actual scrollbar axis) loses content.
    const sx = cs.overflowX === 'auto' || cs.overflowX === 'scroll';
    const sy = cs.overflowY === 'auto' || cs.overflowY === 'scroll';
    if ((el.scrollHeight > el.clientHeight + 2 && !sy) ||
        (el.scrollWidth > el.clientWidth + 2 && !sx))
      clipped.push({tag: el.tagName, text: el.textContent.trim().slice(0,28)});
  }
  if (clipped.length) add('text-clipped', clipped.length + ' element(s)', clipped.slice(0,4));

  // 7. The send control must READ as a control, not merely measure as one.
  //    Size checks passed for weeks while the button was unusable: a 75x75 hit box whose
  //    41px disc was blurred, bleeding through the pill's border, with a muted blue-grey
  //    arrow on black. Every geometry check was green; the thing was not a button. These
  //    four measure what a person sees.
  const sendBtn = document.querySelector('button[aria-label="Send"]');
  if (!sendBtn) {
    add('send-button-missing', 'no button[aria-label="Send"] on the chat screen');
  } else {
    const pill = sendBtn.parentElement;
    const pr = pill.getBoundingClientRect();
    const disc = [...sendBtn.querySelectorAll('span')].find(sp => {
      const cs = getComputedStyle(sp);
      return cs.borderRadius.startsWith('50%') && cs.backgroundColor !== 'rgba(0, 0, 0, 0)';
    });
    const svg = sendBtn.querySelector('svg');
    if (!disc) {
      add('send-button-missing-disc', 'no painted circle inside the send button');
    } else {
      const dr = disc.getBoundingClientRect(), dcs = getComputedStyle(disc);
      const gapR = (pr.x + pr.width) - (dr.x + dr.width);
      const gapB = (pr.y + pr.height) - (dr.y + dr.height);
      if (dcs.filter && dcs.filter !== 'none')
        add('send-button-blurred', 'disc filter=' + dcs.filter + ' — a soft edge reads as a smudge');
      if (dr.width < 40 || dr.height < 40)
        add('send-button-disc-small', 'painted disc ' + Math.round(dr.width) + 'x' + Math.round(dr.height) + ' (<40)');
      if (gapR < 3 || gapB < 3)
        add('send-button-flush', 'disc sits ' + gapR.toFixed(1) + '/' + gapB.toFixed(1) +
            'px from the pill edge — merges with the border');
      if (svg) {
        const pth = svg.querySelector('path');
        const stroke = pth ? (pth.getAttribute('stroke') || getComputedStyle(pth).stroke) : null;
        const parse = (c) => {
          const m = /rgba?\(([^)]+)\)/.exec(c || '');
          if (m) return m[1].split(',').map(Number).slice(0, 3);
          const h = /^#([0-9a-f]{6})$/i.exec(c || '');
          if (h) return [0, 2, 4].map(i => parseInt(h[1].substr(i, 2), 16));
          return null;
        };
        const lum = (rgb) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
          return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]); };
        const a = parse(stroke), b = parse(dcs.backgroundColor);
        if (a && b) {
          const L1 = lum(a), L2 = lum(b);
          const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
          if (ratio < 4.5)
            add('send-button-low-contrast', 'icon ' + stroke + ' on ' + dcs.backgroundColor +
                ' = ' + ratio.toFixed(2) + ':1 (needs 4.5)');
        }
      }
    }
  }

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
