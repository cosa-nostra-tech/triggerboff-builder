#!/usr/bin/env python3
"""Hit-test the composer with a POPULATED conversation.

Every previous run loaded an empty chat. A user with messages has a scrolling message list above
the composer, and a modal/overlay or the list's own container can end up over the bar. This sends
several turns first (the local app has dummy credentials, so each turn appends the user's bubble
and then an error bubble — the point is the list becomes long and scrolls), then re-runs the same
grid hit test in WebKit.
"""
import os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4801
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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'p@e.com', name:'P' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-p.cjs"; open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    return r.stdout.strip()


def busy(p):
    try:
        socket.create_connection(("127.0.0.1", p), timeout=1); return True
    except OSError:
        return False


os.makedirs(OUT, exist_ok=True)
token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "start", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/pop_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)

SAMPLER = """() => {
  const ta = document.querySelector('textarea');
  if (!ta) return {error:'no textarea'};
  const pill = ta.parentElement.getBoundingClientRect();
  const t = ta.getBoundingClientRect();
  const d = (el) => { if (!el) return null; const cs = getComputedStyle(el);
    return {tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,24),
            testid: el.getAttribute && el.getAttribute('data-testid'),
            pos: cs.position, z: cs.zIndex}; };
  const pts = [];
  for (const fy of [0.3, 0.5, 0.7]) for (const fx of [0.1, 0.3, 0.5, 0.7]) {
    const x = pill.x + pill.width*fx, y = pill.y + pill.height*fy;
    const el = document.elementFromPoint(x, y);
    pts.push({fx, hit: d(el), ok: el === ta});
  }
  return {pill: {x:+pill.x.toFixed(1), y:+pill.y.toFixed(1), w:+pill.width.toFixed(1),
                 h:+pill.height.toFixed(1), bottom:+(pill.y+pill.height).toFixed(1)},
          textarea: {x:+t.x.toFixed(1), y:+t.y.toFixed(1), w:+t.width.toFixed(1), h:+t.height.toFixed(1)},
          offscreen: (pill.y + pill.height) > innerHeight + 1,
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
        b = pw.webkit.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                            is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                          "path": "/", "httpOnly": True, "secure": False}])
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=90000)
        pg.wait_for_timeout(5000)

        # build a conversation: several turns, so the list scrolls
        for i in range(4):
            pg.click("textarea")
            pg.keyboard.type(f"test message number {i+1} about a Marrickville property")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(2500)
        n = pg.evaluate("() => document.querySelectorAll('[data-message], div').length")
        print(f"  sent 4 turns; div count now {n}")
        a = pg.evaluate(SAMPLER)
        print("\n  --- composer with a populated conversation (WebKit 390x844) ---")
        print("   pill:", a["pill"], " offscreen:", a["offscreen"])
        bad = 0
        for p in a["points"]:
            if not p["ok"]:
                bad += 1
                print(f"    ({p['fx']}) -> {p['hit']['tag']}.{p['hit']['cls']} pos={p['hit']['pos']} z={p['hit']['z']}  <-- NOT the field")
        print(f"   points blocked: {bad}/12")
        pg.click("textarea")
        pg.keyboard.type("final check")
        pg.wait_for_timeout(400)
        print("   field accepts typing:", repr(pg.evaluate("() => document.querySelector('textarea').value")))
        pg.screenshot(path=f"{OUT}/populated.png")
        ctx.close(); b.close()
    print("  shot:", f"{OUT}/populated.png")
finally:
    os.killpg(os.getpgid(srv.pid), 15)
