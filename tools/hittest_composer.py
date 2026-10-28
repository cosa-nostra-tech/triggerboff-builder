#!/usr/bin/env python3
"""
hittest_composer.py — what is actually receiving taps on the composer?

"The entire chat bar is hard to press. Even just to activate the field." If tapping the input
does not focus it, some element is on top of it. Reading the source cannot answer this; the
browser can, by asking which element is the topmost at a grid of points across the composer and
by clicking it and reading document.activeElement.

Prints, per point: the tag / class / data-testid of whatever is hit, and whether it is inside the
composer. Anything that is NOT the textarea or its pill is the culprit.
"""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4501
OUT = "/tmp/hittest"
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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'hit@e.com', name:'Hit' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-hit.cjs"
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


if busy(PORT):
    print(f"  port {PORT} busy"); sys.exit(2)
token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/hittest_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    for _ in range(60):
        if busy(PORT):
            break
        time.sleep(1)
    time.sleep(2)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, w, h in (("phone", 390, 844), ("desktop", 1440, 900)):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                is_mobile=(name == "phone"), has_touch=(name == "phone"))
            ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                              "path": "/", "httpOnly": True, "secure": False}])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=90000)
            pg.wait_for_timeout(5000)

            probe = pg.evaluate("""() => {
              const ta = document.querySelector('textarea');
              if (!ta) return {error: 'no textarea'};
              const pill = ta.parentElement.getBoundingClientRect();
              const t = ta.getBoundingClientRect();
              const describe = (el) => {
                if (!el) return null;
                const cs = getComputedStyle(el);
                return {tag: el.tagName.toLowerCase(),
                        cls: String(el.className||'').slice(0,50),
                        testid: el.getAttribute && el.getAttribute('data-testid'),
                        aria: el.getAttribute && el.getAttribute('aria-label'),
                        pe: cs.pointerEvents, z: cs.zIndex, pos: cs.position};
              };
              const pts = [];
              // a grid across the pill: three heights x five positions, plus the textarea centre
              for (const fy of [0.25, 0.5, 0.75]) {
                for (const fx of [0.08, 0.3, 0.5, 0.7, 0.9]) {
                  const x = pill.x + pill.width * fx, y = pill.y + pill.height * fy;
                  const el = document.elementFromPoint(x, y);
                  pts.push({fx, fy, x: Math.round(x), y: Math.round(y),
                            hit: describe(el),
                            insideComposer: !!(el && (el === ta || el.contains(ta) || ta.contains(el)
                                              || el.contains(ta.parentElement))),
                            hitTextarea: el === ta});
                }
              }
              // what sits above the composer vertically?
              const above = [];
              for (let dy = 4; dy <= 60; dy += 8) {
                const el = document.elementFromPoint(pill.x + pill.width * 0.5, pill.y - dy);
                above.push({dy, hit: describe(el)});
              }
              return {pill: {x:+pill.x.toFixed(1), y:+pill.y.toFixed(1),
                             w:+pill.width.toFixed(1), h:+pill.height.toFixed(1)},
                      textarea: {x:+t.x.toFixed(1), y:+t.y.toFixed(1),
                                 w:+t.width.toFixed(1), h:+t.height.toFixed(1)},
                      viewport: {w: innerWidth, h: innerHeight},
                      points: pts, above};
            }""")
            print(f"\n  === {name} {w}x{h} ===")
            if probe.get("error"):
                print("   ", probe); ctx.close(); continue
            print("   pill:", probe["pill"], " textarea:", probe["textarea"])
            for p in probe["points"]:
                flag = "" if p["hitTextarea"] else ("  <-- NOT the textarea" if not p["insideComposer"] else "  (pill elem)")
                print(f"    ({p['fx']:.2f},{p['fy']:.2f}) -> {p['hit']['tag']}."
                      f"{p['hit']['cls'][:28]} testid={p['hit']['testid']} pe={p['hit']['pe']}{flag}")
            print("   above the composer:")
            for a in probe["above"]:
                print(f"    -{a['dy']}px -> {a['hit']['tag']}.{a['hit']['cls'][:30]}")

            # the actual user action: click the middle of the field and see if it focuses
            t = probe["textarea"]
            pg.mouse.click(t["x"] + t["w"] / 2, t["y"] + t["h"] / 2)
            pg.wait_for_timeout(400)
            focused = pg.evaluate("() => { const a = document.activeElement; return a ? a.tagName.toLowerCase() + '.' + String(a.className||'').slice(0,30) : null; }")
            typed = None
            pg.keyboard.type("hello")
            pg.wait_for_timeout(300)
            typed = pg.evaluate("() => (document.querySelector('textarea')||{}).value || ''")
            print(f"   click on field centre -> activeElement={focused}  textarea value after typing={typed!r}")
            pg.screenshot(path=f"{OUT}/{name}.png")
            ctx.close()
        b.close()
    print("\n  shots in", OUT)
finally:
    os.killpg(os.getpgid(srv.pid), 15)
