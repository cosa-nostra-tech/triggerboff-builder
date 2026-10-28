#!/usr/bin/env python3
"""Is the chat page actually INTERACTIVE in WebKit? Hydration failure would make the whole bar
dead while looking perfectly normal. Tests: console/page errors, and whether typing into the
field updates its value (a controlled React input only updates when hydrated)."""
import os, socket, subprocess, sys, time, uuid, json

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4701


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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'x@e.com', name:'X' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-x.cjs"; open(mp, "w").write(src)
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
                       stdout=open(f"/tmp/inter_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
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
        errs, cons = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: cons.append(f"{m.type}: {m.text[:160]}") if m.type in ("error", "warning") else None)
        pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=90000)
        pg.wait_for_timeout(6000)
        print("  page errors  :", errs or "none")
        print("  console err/warn:", cons[:6] or "none")
        hydrated = pg.evaluate("() => !!(document.querySelector('textarea') && window.next)")
        print("  next runtime present:", hydrated)
        # Real keystrokes into a controlled React input only stick if hydration completed.
        pg.click("textarea")
        pg.wait_for_timeout(300)
        pg.keyboard.type("typing test")
        pg.wait_for_timeout(500)
        val = pg.evaluate("() => (document.querySelector('textarea')||{}).value || ''")
        print(f"  typed into field -> value = {val!r}   ({'HYDRATED' if val else 'NOT HYDRATED - the page is inert'})")
        # and does the send button respond at all?
        before = pg.evaluate("() => document.querySelectorAll('div,span').length")
        print("  element count:", before)
        ctx.close(); b.close()
finally:
    os.killpg(os.getpgid(srv.pid), 15)
