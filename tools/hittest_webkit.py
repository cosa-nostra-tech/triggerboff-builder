#!/usr/bin/env python3
"""
hittest_webkit.py — the same hit test in WebKit, and with the viewport shrunk as the iOS
keyboard does.

The Chromium emulation says the composer is fine, so Chromium is not the instrument. Two
differences from a real iPhone remain:

  1. ENGINE. iOS runs WebKit. Layout, fixed positioning and safe-area handling all differ.
  2. KEYBOARD. Focusing a field on iOS shrinks the VISUAL viewport. The page then re-lays out,
     and if the composer is positioned by something that does not follow, it moves out from
     under the thumb — which reads exactly as "the chat bar is hard to press".

This runs WebKit at phone size, samples what is under a grid of points across the composer,
clicks the field, then shrinks the viewport the way the keyboard does and samples again.
"""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4601
OUT = "/tmp/hittest"


def env_local():
    e = {}
    for line in open(f"{REPO}/.env.local"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            e[k] = v.strip().strip('"').strip("'")
    return e


def mint(secret):
    src = """
const { hkdf } = require('@panva/hkdf'); const { EncryptJWT } = require('jose'); const crypto = require('crypto');
(async () => { const salt = process.env.C;
  const key = await hkdf('sha256', process.env.S, salt, `Auth.js Generated Encryption Key (${salt})`, 64);
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'w@e.com', name:'W' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-w.cjs"
    open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    if r.returncode or not r.stdout.strip():
        print("  mint failed:", r.stderr[:200]); sys.exit(2)
    return r.stdout.strip()


def busy(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1):
            return True
    except OSError:
        return False


os.makedirs(OUT, exist_ok=True)
if busy(PORT):
    print("  port busy"); sys.exit(2)
token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/hitwk_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)

SAMPLER = """() => {
  const ta = document.querySelector('textarea');
  if (!ta) return {error:'no textarea'};
  const pill = ta.parentElement.getBoundingClientRect();
  const t = ta.getBoundingClientRect();
  const d = (el) => { if (!el) return null; const cs = getComputedStyle(el);
    return {tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,26),
            testid: el.getAttribute && el.getAttribute('data-testid')}; };
  const pts = [];
  for (const fy of [0.3, 0.5, 0.7]) for (const fx of [0.1, 0.3, 0.5, 0.7]) {
    const x = pill.x + pill.width*fx, y = pill.y + pill.height*fy;
    const el = document.elementFromPoint(x, y);
    pts.push({fx, hit: d(el), textarea: el === ta});
  }
  return {pill: {x:+pill.x.toFixed(1), y:+pill.y.toFixed(1), w:+pill.width.toFixed(1),
                 h:+pill.height.toFixed(1), bottom:+(pill.y+pill.height).toFixed(1)},
          textarea: {x:+t.x.toFixed(1), y:+t.y.toFixed(1), w:+t.width.toFixed(1), h:+t.height.toFixed(1)},
          vv: {innerH: window.innerHeight, visualH: (window.visualViewport||{}).height || null},
          points: pts};
}"""

try:
    for _ in range(60):
        if busy(PORT):
            break
        time.sleep(1)
    time.sleep(2)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            b = pw.webkit.launch()
        except Exception as e:
            print("  webkit unavailable:", str(e)[:300]); raise SystemExit(3)
        print("  engine: WebKit (Safari)")
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                            is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                          "path": "/", "httpOnly": True, "secure": False}])
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=90000)
        pg.wait_for_timeout(6000)
        a = pg.evaluate(SAMPLER)
        print("\n  --- before focus, 390x844 ---")
        print("   pill:", a["pill"], "\n   ", a["vv"])
        for p in a["points"]:
            print(f"    ({p['fx']}) -> {p['hit']['tag']} textarea={p['textarea']}")

        pg.mouse.click(a["textarea"]["x"] + a["textarea"]["w"]/2,
                       a["textarea"]["y"] + a["textarea"]["h"]/2)
        pg.wait_for_timeout(500)
        print("   after click, activeElement =",
              pg.evaluate("() => document.activeElement && document.activeElement.tagName.toLowerCase()"))

        # the keyboard: shrink the viewport the way iOS does, then re-sample
        pg.set_viewport_size({"width": 390, "height": 500})
        pg.wait_for_timeout(800)
        b2 = pg.evaluate(SAMPLER)
        print("\n  --- after the keyboard shrinks the viewport to 390x500 ---")
        print("   pill:", b2["pill"], "\n   ", b2["vv"])
        for p in b2["points"]:
            print(f"    ({p['fx']}) -> {p['hit']['tag']} textarea={p['textarea']}")
        pg.screenshot(path=f"{OUT}/webkit_keyboard.png")
        ctx.close(); b.close()
    print("\n  shot:", f"{OUT}/webkit_keyboard.png")
finally:
    os.killpg(os.getpgid(srv.pid), 15)
