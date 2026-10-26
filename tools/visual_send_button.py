#!/usr/bin/env python3
"""
visual_send_button.py — render the real chat page on a phone and LOOK at the composer.

The send button measured 82x82 and still reads as hard to press and hard to find, so the
numbers were measuring the wrong thing. This renders the actual page (minted session cookie,
same technique as responsive_audit) at three phone geometries and writes screenshots:

  * 390x844  — iPhone 14 portrait, the reported case
  * 390x520  — what is left of the viewport once the iOS keyboard is up
  * 320x568  — iPhone SE, the smallest phone still in use

It also reports where the composer and the button actually sit by printing their rects, and
crops the composer so the shape of it can be seen rather than inferred.
"""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4211
OUT = "/tmp/composer"


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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'v@e.com', name:'Visual' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-visual.cjs"
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
            print(f"  port {port} already answering — refusing to measure against it"); sys.exit(2)
    except OSError:
        pass


def wait(port, secs=90):
    end = time.time() + secs
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


os.makedirs(OUT, exist_ok=True)
free(PORT)
token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/visual_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    if not wait(PORT):
        print("  server never came up:", open(f"/tmp/visual_{PORT}.log").read()[-300:]); sys.exit(2)
    time.sleep(2)

    from playwright.sync_api import sync_playwright
    GEOMS = [("iphone14", 390, 844), ("keyboard_open", 390, 520), ("iphone_se", 320, 568)]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, w, h in GEOMS:
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=3,
                                is_mobile=True, has_touch=True,
                                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
            ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                              "path": "/", "httpOnly": True, "secure": False}])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(1500)
            if "/dashboard/chat" not in pg.url:
                print(f"  {name}: not on chat ({pg.url})"); ctx.close(); continue

            # Type something so the button is in its enabled, full-opacity state — which is the
            # state the user is in when they are trying to hit it.
            pg.fill("textarea", "is 14 Addison Rd marrickville worth a look")
            pg.wait_for_timeout(300)

            info = pg.evaluate("""() => {
              const btn = document.querySelector('button[aria-label="Send"]');
              const ta  = document.querySelector('textarea');
              const r = btn.getBoundingClientRect();
              const t = ta.getBoundingClientRect();
              const cs = getComputedStyle(btn);
              const parent = btn.parentElement.getBoundingClientRect();
              return {
                btn: {x:+r.x.toFixed(1), y:+r.y.toFixed(1), w:+r.width.toFixed(1), h:+r.height.toFixed(1)},
                ta:  {x:+t.x.toFixed(1), y:+t.y.toFixed(1), w:+t.width.toFixed(1), h:+t.height.toFixed(1)},
                composer: {x:+parent.x.toFixed(1), y:+parent.y.toFixed(1),
                           w:+parent.width.toFixed(1), h:+parent.height.toFixed(1),
                           bottom:+ (parent.y+parent.height).toFixed(1)},
                viewport: {w: innerWidth, h: innerHeight},
                gapBelowComposer: +(innerHeight - (parent.y + parent.height)).toFixed(1),
                btnBottomToViewport: +(innerHeight - (r.y + r.height)).toFixed(1),
                bg: cs.backgroundColor, opacity: cs.opacity,
                textareaFont: getComputedStyle(ta).fontSize,
                disabled: btn.disabled,
              };
            }""")
            print(f"\n  === {name}  {w}x{h} ===")
            print("   ", json.dumps(info))
            pg.screenshot(path=f"{OUT}/{name}_full.png")
            # crop the bottom 130px — the composer band
            pg.screenshot(path=f"{OUT}/{name}_composer.png",
                          clip={"x": 0, "y": max(0, h - 130), "width": w, "height": min(130, h)})
            pg.evaluate("() => document.querySelector('textarea').blur()")
            pg.wait_for_timeout(200)
            pg.screenshot(path=f"{OUT}/{name}_idle.png",
                          clip={"x": 0, "y": max(0, h - 130), "width": w, "height": min(130, h)})
            ctx.close()
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), 15)
    print("\n  server stopped. screenshots in", OUT)
