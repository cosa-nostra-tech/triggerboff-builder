#!/usr/bin/env python3
"""Verify the composer on phone widths in WebKit, with screenshots.

The button is now a flex item, so the things that must hold are structural rather than numeric:
the whole button is inside the pill, the disc has visible margin on every side, the tappable
region equals the button, tapping the disc sends, and tapping the field focuses it. Also writes
a crop of the composer per width so it can be looked at rather than inferred.
"""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
OUT = "/tmp/composer_mobile"
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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'m@e.com', name:'M' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-m.cjs"; open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    return r.stdout.strip()


def busy(p):
    try:
        socket.create_connection(("127.0.0.1", p), timeout=1); return True
    except OSError:
        return False


token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/vcm_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    for _ in range(60):
        if busy(PORT):
            break
        time.sleep(1)
    time.sleep(2)
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        b = pw.webkit.launch()
        for name, w, h in (("se-320", 320, 568), ("iphone-390", 390, 844), ("max-430", 430, 932)):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=3,
                                is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                              "path": "/", "httpOnly": True, "secure": False}])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=90000)
            pg.wait_for_timeout(5000)
            pg.click("textarea")
            pg.keyboard.type("is 14 Addison Rd worth a look")
            pg.wait_for_timeout(400)
            m = pg.evaluate("""() => {
              const btn = document.querySelector('button[aria-label="Send"]');
              const ta = document.querySelector('textarea');
              const pill = ta.parentElement;
              const disc = btn.querySelector('span[aria-hidden]');
              const R = (e) => { const r = e.getBoundingClientRect();
                return {x:+r.x.toFixed(1), y:+r.y.toFixed(1), w:+r.width.toFixed(1),
                        h:+r.height.toFixed(1), right:+(r.x+r.width).toFixed(1),
                        bottom:+(r.y+r.height).toFixed(1)}; };
              const pr = R(pill), br = R(btn), dr = R(disc);
              // effective tap region around the disc centre
              const cx = dr.x + dr.w/2, cy = dr.y + dr.h/2;
              const own = (x,y) => { const el = document.elementFromPoint(x,y); return !!(el && btn.contains(el)); };
              const ext = (dx,dy) => { let n=0; for (let i=1;i<=60;i++){ if(own(cx+dx*i, cy+dy*i)) n=i; else break; } return n; };
              const L=ext(-1,0), Rt=ext(1,0), U=ext(0,-1), D=ext(0,1);
              return {pill:pr, button:br, disc:dr,
                      gapDiscRight:+(pr.right-dr.right).toFixed(1),
                      gapDiscBottom:+(pr.bottom-dr.bottom).toFixed(1),
                      gapDiscTop:+(dr.y-pr.y).toFixed(1),
                      buttonInsidePill: br.right<=pr.right+0.5 && br.bottom<=pr.bottom+0.5 && br.y>=pr.y-0.5 && br.x>=pr.x-0.5,
                      tapRegion:{L,R:Rt,U,D,w:L+Rt+1,h:U+D+1},
                      discCentre:{x:+cx.toFixed(1), y:+cy.toFixed(1)},
                      viewport:{w:innerWidth,h:innerHeight}};
            }""")
            # functional: tap the disc -> the composer clears (send ran)
            dc = m["discCentre"]
            pg.fill("textarea", "tap test")
            pg.wait_for_timeout(200)
            pg.mouse.click(dc["x"], dc["y"])
            pg.wait_for_timeout(600)
            after_tap = pg.input_value("textarea")
            # functional: tap the field -> focuses
            pg.click("textarea")
            focused = pg.evaluate("() => document.activeElement && document.activeElement.tagName.toLowerCase()")

            ok = (m["buttonInsidePill"] and m["gapDiscRight"] >= 4 and m["gapDiscBottom"] >= 4
                  and m["gapDiscTop"] >= 3 and m["tapRegion"]["w"] >= 44 and m["tapRegion"]["h"] >= 44
                  and after_tap == "" and focused == "textarea")
            results.append((name, ok, m, after_tap, focused))
            print(f"\n  === {name} {w}x{h} ===  {'PASS' if ok else 'FAIL'}")
            print(f"    pill {m['pill']}")
            print(f"    button {m['button']}   inside pill: {m['buttonInsidePill']}")
            print(f"    disc {m['disc']}  gaps r/b/t: {m['gapDiscRight']}/{m['gapDiscBottom']}/{m['gapDiscTop']}")
            print(f"    tap region {m['tapRegion']['w']}x{m['tapRegion']['h']}  (needs >=44x44)")
            print(f"    tap disc -> input {after_tap!r}   tap field -> {focused}")
            pg.evaluate("() => window.scrollTo(0,0)")
            pg.wait_for_timeout(200)
            pg.screenshot(path=f"{OUT}/{name}.png",
                          clip={"x": 0, "y": h - 110, "width": w, "height": 110})
            ctx.close()
        b.close()
    bad = [r for r in results if not r[1]]
    print(f"\n  {len(results)-len(bad)}/{len(results)} widths PASS")
    print("  screenshots:", OUT)
finally:
    os.killpg(os.getpgid(srv.pid), 15)
