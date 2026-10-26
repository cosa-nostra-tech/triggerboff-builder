#!/usr/bin/env python3
"""Why is the composer 2px wide at 390px? Dump the ancestor chain and the media query."""
import json, os, socket, subprocess, sys, time, uuid

REPO = "/data/repos/triggerboff"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4303


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
  process.stdout.write(await new EncryptJWT({ sub: process.env.U, email:'d@e.com', name:'Diag' })
    .setProtectedHeader({ alg:'dir', enc:'A256CBC-HS512' }).setIssuedAt().setExpirationTime('30d')
    .setJti(crypto.randomUUID()).encrypt(key));
})().catch(e => { console.error(e.message); process.exit(1); });
"""
    mp = f"{REPO}/.mint-diag.cjs"
    open(mp, "w").write(src)
    r = subprocess.run(["node", mp], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "S": secret, "C": "authjs.session-token", "U": str(uuid.uuid4())})
    os.remove(mp)
    return r.stdout.strip()


def busy(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1):
            return True
    except OSError:
        return False


token = mint(env_local()["NEXTAUTH_SECRET"])
srv = subprocess.Popen(["npx", "next", "dev", "-p", str(PORT)], cwd=REPO,
                       stdout=open(f"/tmp/diag_{PORT}.log", "w"), stderr=subprocess.STDOUT,
                       env={**os.environ, "NODE_ENV": "development"}, preexec_fn=os.setsid)
try:
    for _ in range(90):
        if busy(PORT):
            break
        time.sleep(1)
    time.sleep(3)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                            is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "authjs.session-token", "value": token, "domain": "127.0.0.1",
                          "path": "/", "httpOnly": True, "secure": False}])
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/dashboard/chat", wait_until="load", timeout=120000)
        pg.wait_for_timeout(8000)
        out = pg.evaluate("""() => {
          const b = document.querySelector('button[aria-label="Send"]');
          const chain = [];
          let n = b;
          while (n && n !== document.body && chain.length < 10) {
            const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
            chain.push({tag:n.tagName.toLowerCase(),
                        cls:String(n.className||'').slice(0,60),
                        w:+r.width.toFixed(1), h:+r.height.toFixed(1), x:+r.x.toFixed(1),
                        display:cs.display, position:cs.position,
                        flex:cs.flex, minWidth:cs.minWidth, overflow:cs.overflow, padding:cs.padding});
            n = n.parentElement;
          }
          return {
            innerWidth: innerWidth,
            docScrollW: document.documentElement.scrollWidth,
            bodyScrollW: document.body.scrollWidth,
            mq899: matchMedia('(max-width: 899px)').matches,
            mq900: matchMedia('(max-width: 900px)').matches,
            viewportMeta: (document.querySelector('meta[name=viewport]')||{}).content || null,
            composerChain: chain,
          };
        }""")
        print(json.dumps(out, indent=1))
        pg.screenshot(path="/tmp/composer_shots/diag_phone.png", full_page=True)
        ctx.close(); b.close()
finally:
    os.killpg(os.getpgid(srv.pid), 15)
    print("  stopped")
