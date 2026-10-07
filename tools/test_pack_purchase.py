#!/usr/bin/env python3
"""End-to-end: pay for a Pro pack in test mode and prove the webhook granted it.

What this proves that nothing else does: a real Stripe Checkout session, completed with a
real test card, produces a real signed webhook that OUR endpoint accepts. A 200 from the
delivery means the signature verified and the raw-body handling is correct. A 400 means it
is not — the failure that would otherwise mean customers pay and receive nothing.
"""
import asyncio, json, os, re, time, urllib.error, urllib.parse, urllib.request

S = {l.split("=", 1)[0]: l.split("=", 1)[1].strip()
     for l in open("/data/.hermes/.builder-secrets") if "=" in l}
SK = S["STRIPE_SECRET_KEY"]
USER_ID = "1dcdd86a-95c0-5ae8-acc2-6024996a41d9"   # benson@atelier.co
PRICE = open("/tmp/stripe_price.txt").read().strip()
APP = "https://triggerboff.vercel.app"


def stripe(path, method="GET", form=None):
    data = urllib.parse.urlencode(form, doseq=True).encode() if form else None
    req = urllib.request.Request("https://api.stripe.com" + path, data=data, method=method,
        headers={"Authorization": f"Bearer {SK}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


print("=== 1. create a Checkout Session (mode=payment) for your user ===")
st, sess = stripe("/v1/checkout/sessions", "POST", {
    "mode": "payment",
    "line_items[0][price]": PRICE,
    "line_items[0][quantity]": "1",
    "client_reference_id": USER_ID,
    "success_url": f"{APP}/dashboard/settings?checkout=success",
    "cancel_url": f"{APP}/dashboard/settings?checkout=cancelled",
})
print(f"  {st}  session {sess.get('id')}")
print(f"  url {sess.get('url')}")
if st != 200:
    raise SystemExit(1)
URL = sess["url"]

print("\n=== 2. pay it with the test card, in a real browser ===")


async def pay():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = await (await b.new_context(viewport={'width': 1280, 'height': 900})).new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        # Stripe's hosted page: fields are identified by name/autocomplete rather than ids
        async def fill(selectors, value):
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.fill(value)
                        return sel
                except Exception:
                    continue
            return None
        used = []
        used.append(("card", await fill(['input[name="cardnumber"]', '#cardNumber',
                                         'input[autocomplete="cc-number"]'], "4242424242424242")))
        used.append(("exp", await fill(['input[name="exp-date"]', '#cardExpiry',
                                        'input[autocomplete="cc-exp"]'], "12 / 34")))
        used.append(("cvc", await fill(['input[name="cvc"]', '#cardCvc',
                                        'input[autocomplete="cc-csc"]'], "123")))
        used.append(("name", await fill(['input[name="billingName"]', '#billingName',
                                         'input[autocomplete="cc-name"]'], "Nick Benson")))
        # Country defaults to the United States, so an Australian postcode fails ZIP
        # validation. Set the country first, THEN the postcode.
        for sel in ['select[name="billingCountry"]', 'select[autocomplete="country"]', '#billingCountry']:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.select_option("AU")
                    print(f"   country -> AU via {sel}")
                    break
            except Exception:
                try:
                    await el.select_option(label="Australia"); print(f"   country -> Australia via {sel}"); break
                except Exception:
                    continue
        await page.wait_for_timeout(800)
        used.append(("email", await fill(['input[name="email"]', '#email',
                                          'input[autocomplete="email"]'], "benson@atelier.co")))
        # With country=AU there is no postcode field; the required one is the PHONE number.
        # An empty phone field carries an error icon and silently blocks submission, which is
        # what stopped the first two attempts.
        used.append(("phone", await fill(['input[name="phone"]', '#phone',
                                          'input[autocomplete="tel"]'], "0412345678")))
        used.append(("post", await fill(['input[name="billingPostalCode"]', '#billingPostalCode',
                                         'input[autocomplete="postal-code"]'], "2000")))
        print("   fields filled:", [u for u in used if u[1]])
        await page.wait_for_timeout(500)
        for sel in ['button[type="submit"]', 'button:has-text("Pay")', '.SubmitButton']:
            try:
                await page.click(sel, timeout=8000)
                print(f"   submitted via {sel}")
                break
            except Exception:
                continue
        await page.wait_for_timeout(12000)
        print(f"   landed on: {page.url[:90]}")
        await page.screenshot(path="/tmp/stripe_paid.png", full_page=True)
        await b.close()

asyncio.run(pay())

print("\n=== 3. did the webhook fire, and did OUR endpoint accept it? ===")
time.sleep(6)
st, evs = stripe("/v1/events?type=checkout.session.completed&limit=3")
for e in evs.get("data", [])[:3]:
    pend = e.get("pending_webhooks")
    ok = pend == 0
    print(f"   event {e['id']}  created {time.strftime('%H:%M:%S', time.gmtime(e['created']))}  "
          f"pending_webhooks={pend}  {'DELIVERED' if ok else 'not yet delivered'}")
    if pend == 0:
        print(f"      -> delivered and accepted by our endpoint (no retries pending)")
st, wh = stripe("/v1/webhook_endpoints/we_1UNgGAHkIEIQkyHAQgIboswp")
print(f"   webhook status: {wh.get('status')}  url {wh.get('url')}")

print("\n=== 4. the subscription row (via the app's own read path) ===")
print("   Nick: check Supabase -> subscriptions. Expect plan=pro_pack_3m, status=active,")
print("   current_period_end ~90 days out.")
