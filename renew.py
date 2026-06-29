#!/usr/bin/env python3
"""
bot-hosting.net 免费计划全自动续期
支持 proxy 代理、Playwright、capsolver、TG截图通知
"""

import os, sys, json, re, time, logging, argparse, urllib.request, ssl, base64
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bh-renew")
BASE = "https://bot-hosting.net"

def fatal(msg):
    log.error(msg)
    sys.exit(1)

def get_proxy():
    p = os.environ.get("PROXY", "").strip()
    return p if p else None

def get_cookie():
    c = os.environ.get("SESSION_COOKIE")
    if not c: fatal("SESSION_COOKIE not set")
    return c

def parse_jwt(token):
    """Parse JWT and return payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3: return None
        payload = parts[1]
        pad = 4 - len(payload) % 4
        if pad: payload += "=" * pad
        return json.loads(base64.urlsafe_b64decode(payload))
    except:
        return None

def check_cookie_expiry(cookie):
    """Check JWT expiry, return remaining days/hours or None."""
    data = parse_jwt(cookie)
    if not data or "exp" not in data:
        return None
    exp = datetime.fromtimestamp(data["exp"], tz=timezone.utc)
    now = datetime.now(timezone.utc)
    rem = exp - now
    return {
        "expires_at": exp,
        "days": rem.days,
        "hours": rem.seconds // 3600,
        "total_hours": rem.days * 24 + rem.seconds // 3600,
    }

def tg_notify(msg, photo_path=None):
    """Send Telegram notification, optionally with a photo."""
    bot_token = os.environ.get("TG_BOT_TOKEN", "7935239797:AAHuQ9jZt-cNjcgjqQ9HH0JzkSWlD53EttM")
    chat_id = os.environ.get("TG_CHAT_ID", "644320820")
    if not bot_token:
        log.warning("TG_BOT_TOKEN not set, skipping")
        return
    try:
        if photo_path and os.path.exists(photo_path):
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            with open(photo_path, "rb") as f:
                img_data = f.read()
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{msg}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"screenshot.png\"\r\n"
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
            urllib.request.urlopen(req, timeout=30)
            log.info("TG photo sent")
        else:
            payload = f"chat_id={chat_id}&text={urllib.request.quote(msg)}&parse_mode=HTML&disable_web_page_preview=true"
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            urllib.request.urlopen(req, timeout=10)
            log.info("TG text sent")
    except Exception as e:
        log.warning(f"TG notify failed: {e}")

def fetch(path, cookie):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"User-Agent": "Mozilla/5.0", "Cookie": f"session_token={cookie}"}
    )
    return urllib.request.urlopen(req, timeout=30, context=ctx).read().decode()

def check_status(cookie):
    html = fetch("/a/billings", cookie)
    if "/login" in html[:300]:
        fatal("Cookie expired - redirect to login")

    def extract(field):
        m = re.search(rf'{field}["\':\s]+["\']([^"\']+)', html)
        return m.group(1) if m else None

    info = {
        "tier": extract("accountTierName"),
        "status": extract("status"),
        "opens_at": extract("freeRenewalOpensAt"),
        "due_at": extract("freeRenewalDueAt"),
        "username": extract("username"),
    }
    log.info(f"Plan: {info['tier']} | Status: {info['status']}")
    log.info(f"Renew opens: {info['opens_at']} UTC")
    log.info(f"Renew due:   {info['due_at']} UTC")

    # Check cookie expiry
    exp = check_cookie_expiry(cookie)
    if exp:
        log.info(f"Cookie expires: {exp['expires_at']} UTC ({exp['days']}d {exp['hours']}h)")
        info["cookie_exp"] = exp

    return info

def is_available(info):
    if os.environ.get("FORCE_RENEW"):
        log.info("FORCE_RENEW set - attempting renewal anyway")
        return True
    if not info["opens_at"]:
        return False
    try:
        opens = datetime.fromisoformat(info["opens_at"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= opens
    except:
        return False

def renew_playwright(cookie, proxy=None, capsolver_key=None, account_name="Free"):
    """Returns (success_bool or None, screenshot_path or None)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed")
        return False, None

    screenshot_path = f"/tmp/bh_screenshot_{account_name}.png"
    pw_kwargs = {}
    if proxy:
        pw_kwargs["proxy"] = {"server": proxy}
        log.info(f"Using proxy: {proxy}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="en-US", viewport={"width": 1280, "height": 800}, **pw_kwargs
        )
        ctx.add_cookies([{"name":"session_token","value":cookie,"domain":"bot-hosting.net","path":"/"}])

        page = ctx.new_page()
        log.info("Loading billing page...")
        try:
            page.goto(f"{BASE}/a/billings", wait_until="domcontentloaded", timeout=30000)
        except:
            pass
        # Wait for SvelteKit app to hydrate
        try:
            page.wait_for_selector("text=Subscription", state="visible", timeout=15000)
            log.info("✅ Page rendered (found subscription card)")
        except:
            log.warning("⚠️ Subscription card not found, page may not have rendered fully")
        page.wait_for_timeout(2000)
        log.info(f"Page URL: {page.url}")
        try:
            has_ts = page.query_selector('[data-sitekey], .cf-turnstile')
            log.info(f"Turnstile visible: {has_ts is not None}")
        except:
            pass
        try:
            log.info(f"Title: {page.title()}")
        except:
            pass

        try:
            page.screenshot(path=screenshot_path, timeout=15000)
        except:
            pass
        log.info(f"Screenshot saved: {screenshot_path}")

        renew_btn = page.query_selector("button:has-text('Renew')")
        if not renew_btn:
            log.info("No Renew button found")
            browser.close()
            return None, screenshot_path

        btn_text = renew_btn.inner_text()
        is_disabled = renew_btn.get_attribute("disabled") is not None
        log.info(f"Button: '{btn_text}' (disabled={is_disabled})")

        if is_disabled:
            m = re.search(r'(\d+):(\d+):(\d+)', btn_text)
            if m:
                log.info(f"⏳ Still counting: {m.group(1)}h {m.group(2)}m")
            browser.close()
            return False, screenshot_path

        log.info("✅ Renew button is ACTIVE!")

        if capsolver_key:
            solved = solve_capsolver(page, capsolver_key)
            if solved:
                page.wait_for_timeout(1000)

        page.query_selector("button:has-text('Renew')").click()
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass

        content = page.content()
        url = page.url
        try: page.screenshot(path=screenshot_path, timeout=15000)
        except: pass
        browser.close()

        if "Expires" in content and "Active" in content:
            log.info("✅ Renewal SUCCESS!")
            return True, screenshot_path
        else:
            log.info(f"Renewal result uncertain, URL: {url}")
            return None, screenshot_path

def solve_capsolver(page, api_key):
    import urllib.request as ureq
    site_key = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]') || document.querySelector('.cf-turnstile');
        return el ? el.getAttribute('data-sitekey') : null;
    }""")
    if not site_key:
        log.info("No Turnstile site key found")
        return False
    log.info(f"Turnstile site key: {site_key}")
    payload = json.dumps({
        "clientKey": api_key,
        "task": {"type": "AntiTurnstileTaskProxyLess", "websiteURL": f"{BASE}/a/billings", "websiteKey": site_key}
    }).encode()
    try:
        req = ureq.Request("https://api.capsolver.com/createTask", data=payload, headers={"Content-Type":"application/json"})
        result = json.loads(ureq.urlopen(req, timeout=30).read())
        if result.get("errorId") != 0:
            log.error(f"Capsolver: {result.get('errorDescription')}")
            return False
        task_id = result["taskId"]
        log.info(f"Capsolver task: {task_id}")
        for i in range(60):
            req = ureq.Request("https://api.capsolver.com/getTaskResult",
                data=json.dumps({"clientKey":api_key,"taskId":task_id}).encode(),
                headers={"Content-Type":"application/json"})
            result = json.loads(ureq.urlopen(req, timeout=30).read())
            if result.get("status") == "ready":
                log.info("Turnstile solved!")
                return True
            time.sleep(1)
    except Exception as e:
        log.warning(f"Capsolver error: {e}")
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie")
    parser.add_argument("--capsolver-key")
    parser.add_argument("--proxy")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cookie = args.cookie or get_cookie()
    capsolver = args.capsolver_key or os.environ.get("CAPSOLVER_API_KEY")
    proxy = args.proxy or get_proxy()

    log.info("=== Bot-Hosting.net Auto Renew ===")
    info = check_status(cookie)

    account_name = info.get("tier", "Free") or "Free"
    uname = info.get("username", "") or ""
    opens_at = (info.get("opens_at") or "?")[:16]

    # Check cookie expiry - warn if < 2 days left
    exp = info.get("cookie_exp")
    cookie_warn = ""
    if exp and exp.get("total_hours", 999) < 48:
        cookie_warn = f"\n⚠️ Cookie expires in {exp['days']}d {exp['hours']}h\nPlease re-extract session_token from browser!"

    if not is_available(info):
        if info["opens_at"]:
            try:
                opens = datetime.fromisoformat(info["opens_at"].replace("Z", "+00:00"))
                remaining = opens - datetime.now(timezone.utc)
                h, m = remaining.seconds // 3600, (remaining.seconds % 3600) // 60
                log.info(f"⏳ Renew available in {h}h {m}m")
            except:
                pass
        log.info("⏩ Not yet time - next cron check will retry")

        # Send countdown notification with screenshot
        log.info("Taking screenshot for countdown notification...")
        from playwright.sync_api import sync_playwright
        screenshot_path = "/tmp/bh_countdown.png"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", locale="en-US", viewport={"width":1280,"height":800})
            ctx.add_cookies([{"name":"session_token","value":cookie,"domain":"bot-hosting.net","path":"/"}])
            page = ctx.new_page()
            page.goto(f"{BASE}/a/billings", wait_until="domcontentloaded", timeout=60000)
            try: page.wait_for_selector("text=Subscription", state="visible", timeout=60000)
            except: pass
            page.wait_for_timeout(2000)
            try: page.screenshot(path=screenshot_path, timeout=15000)
            except: pass
            browser.close()

        tg_msg = f"⏳ <b>Bot-Hosting</b> | {account_name}\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{uname}\nOpens: {opens_at} UTC{cookie_warn}"
        tg_notify(tg_msg, screenshot_path)
        return

    log.info("🎯 Attempting renewal with Playwright...")
    log.info(f"Proxy: {proxy or 'none'}")
    log.info(f"Capsolver: {'yes' if capsolver else 'no'}")

    success, screenshot_path = renew_playwright(cookie, proxy, capsolver, account_name)

    if success:
        log.info("🎉 Renewal completed!")
        tg_msg = f"✅ <b>Bot-Hosting</b> | {account_name}\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{uname}\nRenewed successfully!{cookie_warn}"
        tg_notify(tg_msg, screenshot_path)
    elif success is False:
        log.info("⏳ Too early, will retry")
        tg_msg = f"⏳ <b>Bot-Hosting</b> | {account_name}\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{uname}\nOpens: {opens_at} UTC{cookie_warn}"
        tg_notify(tg_msg, screenshot_path)
    else:
        log.warning("⚠️ Check billing page manually")

if __name__ == "__main__":
    main()
