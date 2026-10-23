#!/usr/bin/env python3
"""
verify_send_button.py — measure the send button and composer on a phone viewport.

The composer lives behind auth, so this mints a session cookie exactly the way
responsive_audit.py does, runs the REAL app from a local production build, and measures
the two things that were wrong:

  * the Send button's tap target, which was 41x41 (both Apple and Material set a 44px
    floor), and
  * the composer textarea's font-size, which was 11.2px — under the 16px threshold that
    makes iOS Safari zoom the whole page on focus, sliding the button out from under
    the thumb.

Runs against 127.0.0.1 so it can use the local .env.local secret. Fresh port each run:
a stale `next start` will happily serve pre-patch markup and make a broken fix look good.
"""
import json, os, re, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4137


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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'b@e.com', name:'Audit' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-send.cjs"
    open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    if r.returncode or not r.stdout.strip():
        print("  mint failed:", r.stderr[:200]); sys.exit(2)
    return r.stdout.strip()


def free(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            print(f"  port {port} already answering — refusing to measure against it")
            sys.exit(2)
    except OSError:
        return


def wait(port, secs=90):
    end = time.time() + secs
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


free(PORT)
e = env_local()
token = mint(e["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/verify_send_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    if not wait(PORT):
        print("  server never came up; log:", open(f"/tmp/verify_send_{PORT}.log").read()[-300:])
        sys.exit(2)
    time.sleep(2)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                            is_mobile=True, has_touch=True,
                            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        ctx.add_cookies([{"name": "authjs.session-token", "value": token,
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True, "secure": False}])
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="networkidle", timeout=60000)
        if "/dashboard/chat" not in pg.url or "login" in pg.url.lower():
            print("  HARNESS FAILURE: did not reach the chat; url =", pg.url)
            print("  cookies:", [c["name"] for c in ctx.cookies()])
            print("  server log tail:", open(f"/tmp/verify_send_{PORT}.log").read()[-400:])
            sys.exit(2)
        pg.wait_for_timeout(1200)

        m = pg.evaluate("""() => {
          const btn = document.querySelector('button[aria-label="Send"]');
          const ta  = document.querySelector('textarea');
          if (!btn || !ta) return {missing: !btn ? 'button' : 'textarea'};
          const r = btn.getBoundingClientRect();
          const cs = getComputedStyle(btn), ts = getComputedStyle(ta);
          const circle = btn.querySelector('span[aria-hidden]');
          const cr = circle ? circle.getBoundingClientRect() : null;
          return {
            btn:  {w:+r.width.toFixed(2), h:+r.height.toFixed(2), x:+r.x.toFixed(2), y:+r.y.toFixed(2)},
            circle: cr ? {w:+cr.width.toFixed(2), h:+cr.height.toFixed(2),
                          cx:+(cr.x+cr.width/2).toFixed(2), cy:+(cr.y+cr.height/2).toFixed(2)} : null,
            btnCentre: {cx:+(r.x+r.width/2).toFixed(2), cy:+(r.y+r.height/2).toFixed(2)},
            touchAction: cs.touchAction,
            taFont: cs ? ts.fontSize : null,
            taPadRight: ts.paddingRight,
            composer: (() => {const p=btn.parentElement.getBoundingClientRect();
                              return {w:+p.width.toFixed(2), h:+p.height.toFixed(2),
                                      right:+ (p.x+p.width).toFixed(2), bottom:+(p.y+p.height).toFixed(2)};})(),
            viewport: {w: innerWidth, h: innerHeight},
          };
        }""")
        print("  measured at 390x844 (iPhone UA, touch):")
        print("   ", json.dumps(m, indent=2))
        # Assertions
        ok = True
        if m.get("missing"): print("  FAIL missing element:", m["missing"]); ok = False
        else:
            if m["btn"]["w"] < 44 or m["btn"]["h"] < 44:
                print(f"  FAIL tap target {m['btn']['w']}x{m['btn']['h']} < 44"); ok = False
            f = float(m["taFont"].replace("px", ""))
            if f < 16:
                print(f"  FAIL composer font {f}px < 16 — iOS will zoom on focus"); ok = False
            if m["touchAction"] != "manipulation":
                print(f"  FAIL touch-action = {m['touchAction']}"); ok = False
            gap_r = m["composer"]["right"] - (m["btn"]["x"] + m["btn"]["w"])
            if m["btn"]["x"] + m["btn"]["w"] > m["composer"]["right"]:
                print("  FAIL button extends past the composer's right edge"); ok = False
        print("\n  RESULT:", "PASS" if ok else "FAIL")
        pg.screenshot(path=f"/tmp/send_{PORT}.png")
        print(f"  screenshot: /tmp/send_{PORT}.png")
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), 15)
    print("  server stopped")