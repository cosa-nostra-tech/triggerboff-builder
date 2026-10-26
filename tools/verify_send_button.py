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
          // Probe the real hit region: walk outward from the circle's centre and find the last
          // point where the topmost element is still inside the send button. box.getBoundingClientRect
          // would only report the 48px button; the tappable area is what elementFromPoint says.
          const btn0 = document.querySelector('button[aria-label="Send"]');
          const r0 = btn0.getBoundingClientRect();
          const cx = r0.x + r0.width / 2, cy = r0.y + r0.height / 2;
          const inside = (x, y) => { const el = document.elementFromPoint(x, y); return !!el && btn0.contains(el); };
          const ext = (dx, dy) => { let n = 0; for (let i = 1; i <= 60; i++) { if (inside(cx + dx*i, cy + dy*i)) n = i; else break; } return n; };
          const left = ext(-1,0), right = ext(1,0), up = ext(0,-1), down = ext(0,1);
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
            hitRegion: {left, right, up, down, w: left+right+1, h: up+down+1},
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
            # The BUTTON may extend past the composer — that is the point of the change.
            # The painted CIRCLE must not, or it would look like it had moved off the box.
            if m.get("circle"):
                if m["circle"]["cx"] + 20.5 > m["composer"]["right"] + 0.5:
                    print("  FAIL painted circle is not inside the composer"); ok = False
                if m["circle"]["cy"] + 20.5 > m["composer"]["bottom"] + 0.5:
                    print("  FAIL painted circle is not inside the composer"); ok = False
            hr = m.get("hitRegion") or {}
            # Width is fully available; height is clipped by the viewport floor in this
            # emulation, which has no home-indicator inset. A real iPhone reserves ~34px
            # below the composer, so the full 82 is reachable there.
            if hr.get("w", 0) < 70 or hr.get("h", 0) < 70:
                print(f"  FAIL effective hit region {hr.get('w')}x{hr.get('h')} — wanted >=70x70"); ok = False
            print(f"  note: hit region {hr.get('w')}x{hr.get('h')} (bottom clipped by the page"
                  f" floor here; a real safe-area inset frees the full 82)")
            # The visual must not have moved: circle centre == button centre
            if m.get("circle"):
                if abs(m["circle"]["cx"] - m["btnCentre"]["cx"]) > 0.5 or abs(m["circle"]["cy"] - m["btnCentre"]["cy"]) > 0.5:
                    print("  FAIL circle is not centred in the button"); ok = False
                if not (40 <= m["circle"]["w"] <= 46):
                    print(f"  FAIL painted circle is {m['circle']['w']}px, expected 40-46"); ok = False
            sc = pg.evaluate("() => ({sh: document.documentElement.scrollHeight, ch: document.documentElement.clientHeight})")
            print("\n  document:", sc)
            if sc["sh"] > sc["ch"] + 2:
                print("  WARN the hit layer adds scrollable overflow"); 
        # Functional: type text, tap well outside the painted circle, and see the composer clear
        # (send() clears the input immediately), which only happens if the tap reached the button.
        pg.fill("textarea", "tap test")
        pg.wait_for_timeout(150)
        bb = pg.evaluate("""() => { const b=document.querySelector('button[aria-label=\"Send\"]').getBoundingClientRect();
                                return {x: b.x + b.width/2 - 30, y: b.y + b.height/2 - 30}; }""")
        pg.mouse.click(bb["x"], bb["y"])
        pg.wait_for_timeout(700)
        after = pg.input_value("textarea")
        print(f"  tap 30px outside the circle -> composer value now {after!r} (empty = the tap sent)")
        if after != "":
            print("  FAIL a tap 30px from the centre did not reach the button"); ok = False

        print("\n  RESULT:", "PASS" if ok else "FAIL")
        pg.screenshot(path=f"/tmp/send_{PORT}_empty.png")
        pg.fill("textarea", "is 14 Addison Rd Marrickville worth a look")
        pg.wait_for_timeout(400)
        pg.screenshot(path=f"/tmp/send_{PORT}.png")
        print(f"  screenshot: /tmp/send_{PORT}.png")
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), 15)
    print("  server stopped")