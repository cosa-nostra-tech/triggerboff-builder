#!/usr/bin/env python3
"""
render_composer.py — screenshot the real chat composer so it can be LOOKED at.

Renders the actual app on this box (dev server, minted session cookie — the documented way to
reach an auth-gated page, not an auth bypass) and writes PNGs of the whole viewport plus a crop
of the composer band, at desktop and phone sizes.

Prints measured geometry as well, but the point is the images.
"""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4300
OUT = "/tmp/composer_shots"
os.makedirs(OUT, exist_ok=True)


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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'render@e.com', name:'Render' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-render.cjs"
    open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    if r.returncode or not r.stdout.strip():
        print("  mint failed:", r.stderr[:300]); sys.exit(2)
    return r.stdout.strip()


def port_busy(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1):
            return True
    except OSError:
        return False


if port_busy(PORT):
    print(f"  port {PORT} already serving — refusing to reuse it"); sys.exit(2)

token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "dev", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/render_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    for _ in range(90):
        if port_busy(PORT):
            break
        time.sleep(1)
    else:
        print("  dev server never started:", open(f"/tmp/render_{PORT}.log").read()[-400:]); sys.exit(2)
    print("  dev server up")
    time.sleep(3)

    from playwright.sync_api import sync_playwright
    SHOTS = [("desktop", 1440, 900), ("phone", 390, 844)]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, w, h in SHOTS:
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                is_mobile=(name == "phone"), has_touch=(name == "phone"),
                                user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                                            "Mobile/15E148 Safari/604.1") if name == "phone"
                                else "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36")
            ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                              "path": "/", "httpOnly": True, "secure": False}])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=120000)
            pg.wait_for_timeout(4000)
            if "/dashboard/chat" not in pg.url:
                print(f"  {name}: bounced to {pg.url}")
                ctx.close(); continue
            has_ta = pg.evaluate("() => !!document.querySelector('textarea')")
            print(f"  {name}: on chat, textarea present = {has_ta}")

            # type text so the submit is in its normal enabled state
            # wait for the mobile layout to settle rather than trusting a timer
            for _ in range(60):
                st = pg.evaluate("""() => {
                  const b = document.querySelector('button[aria-label="Send"]');
                  const ta = document.querySelector('textarea');
                  if (!b || !ta) return null;
                  const p = b.parentElement.getBoundingClientRect();
                  return {pill:+p.width.toFixed(1), ta:+ta.getBoundingClientRect().width.toFixed(1),
                          mobile: matchMedia('(max-width: 899px)').matches};
                }""")
                if st and st["pill"] > 150 and st["ta"] > 150:
                    print(f"    layout settled: {st}"); break
                time.sleep(0.5)
            else:
                print("    WARNING: composer never reached a sane width")

            pg.fill("textarea", "is 14 Addison Rd Marrickville worth a look")
            pg.wait_for_timeout(400)
            geo = pg.evaluate("""() => {
              const btn = document.querySelector('button[aria-label="Send"]');
              const ta = document.querySelector('textarea');
              const pill = btn.parentElement;
              const r = btn.getBoundingClientRect(), p = pill.getBoundingClientRect(),
                    t = ta.getBoundingClientRect();
              return {
                button: {x:+r.x.toFixed(1), y:+r.y.toFixed(1), w:+r.width.toFixed(1), h:+r.height.toFixed(1),
                         right:+ (r.x+r.width).toFixed(1), bottom:+(r.y+r.height).toFixed(1)},
                pill:   {x:+p.x.toFixed(1), y:+p.y.toFixed(1), w:+p.width.toFixed(1), h:+p.height.toFixed(1),
                         right:+(p.x+p.width).toFixed(1), bottom:+(p.y+p.height).toFixed(1)},
                textarea:{x:+t.x.toFixed(1), w:+t.width.toFixed(1)},
                viewport:{w:innerWidth, h:innerHeight},
                buttonOverflowsPill: {right: +(r.x+r.width - (p.x+p.width)).toFixed(1),
                                      bottom: +(r.y+r.height - (p.y+p.height)).toFixed(1)},
              };
            }""")
            print(f"    {json.dumps(geo)}")
            pg.screenshot(path=f"{OUT}/{name}_full.png")
            band = min(150, h)
            pg.screenshot(path=f"{OUT}/{name}_composer.png",
                          clip={"x": 0, "y": h - band, "width": w, "height": band})
            ctx.close()
        b.close()
    print("  shots in", OUT)
finally:
    os.killpg(os.getpgid(srv.pid), 15)
    print("  dev server stopped")
