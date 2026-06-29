#!/usr/bin/env python3
"""
bot-hosting.net 免费计划全自动续期
支持 proxy 代理、Playwright、capsolver
"""

import os, sys, json, re, time, logging, argparse, urllib.request, ssl
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
    }
    log.info(f"Plan: {info['tier']} | Status: {info['status']}")
    log.info(f"Renew opens: {info['opens_at']} UTC")
    log.info(f"Renew due:   {info['due_at']} UTC")
    return info

def is_available(info):
    # Allow testing even when not yet available (use --force)
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

def renew_playwright(cookie, proxy=None, capsolver_key=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed")
        return False, None
    
    pw_kwargs = {}
    if proxy:
        pw_kwargs["proxy"] = {"server": proxy}
        log.info(f"Using proxy: {proxy}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US",
            viewport={"width": 1280, "height": 800},
            **pw_kwargs
        )
        ctx.add_cookies([{
            "name": "session_token", "value": cookie,
            "domain": "bot-hosting.net", "path": "/",
        }])
        
        page = ctx.new_page()
        log.info("Loading billing page...")
        page.goto(f"{BASE}/a/billings", wait_until="networkidle")
        page.wait_for_timeout(3000)
        
        renew_btn = page.query_selector("button:has-text('Renew')")
        if not renew_btn:
            log.info("No Renew button found")
            browser.close()
            return None, None
        
        btn_text = renew_btn.inner_text()
        is_disabled = renew_btn.get_attribute("disabled") is not None
        log.info(f"Button: '{btn_text}' (disabled={is_disabled})")
        
        if is_disabled:
            m = re.search(r'(\d+):(\d+):(\d+)', btn_text)
            if m:
                log.info(f"⏳ Still counting: {m.group(1)}h {m.group(2)}m")
            browser.close()
            return False, btn_text
        
        log.info("✅ Renew button is ACTIVE!")
        
        # Try capsolver if configured
        if capsolver_key:
            log.info("Solving Turnstile with capsolver...")
            solved = solve_capsolver(page, capsolver_key)
            if solved:
                page.wait_for_timeout(1000)
        
        # Click Renew
        page.query_selector("button:has-text('Renew')").click()
        page.wait_for_timeout(3000)
        
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass
        
        content = page.content()
        url = page.url
        browser.close()
        
        if "Expires" in content and "Active" in content:
            log.info("✅ Renewal SUCCESS!")
            return True, url
        else:
            log.info(f"Renewal result uncertain, URL: {url}")
            return None, url

def solve_capsolver(page, api_key):
    import urllib.request as ureq
    
    site_key = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]') ||
                    document.querySelector('.cf-turnstile');
        return el ? el.getAttribute('data-sitekey') : null;
    }""")
    
    if not site_key:
        log.info("No Turnstile site key found - might auto-pass")
        return False
    
    log.info(f"Turnstile site key: {site_key}")
    payload = json.dumps({
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": "https://bot-hosting.net/a/billings",
            "websiteKey": site_key,
        }
    }).encode()
    
    try:
        req = ureq.Request("https://api.capsolver.com/createTask",
            data=payload,
            headers={"Content-Type": "application/json"})
        result = json.loads(ureq.urlopen(req, timeout=30).read())
        if result.get("errorId") != 0:
            log.error(f"Capsolver: {result.get('errorDescription')}")
            return False
        
        task_id = result["taskId"]
        log.info(f"Capsolver task: {task_id}")
        
        for i in range(60):
            req = ureq.Request("https://api.capsolver.com/getTaskResult",
                data=json.dumps({"clientKey": api_key, "taskId": task_id}).encode(),
                headers={"Content-Type": "application/json"})
            result = json.loads(ureq.urlopen(req, timeout=30).read())
            if result.get("status") == "ready":
                token = result["solution"]["token"]
                page.evaluate(f"() => {{ turnstile?.reset?.(); }};")
                log.info("✅ Turnstile solved!")
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
    
    # Check subscription status via API
    info = check_status(cookie)
    
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
        return
    
    log.info("🎯 Attempting renewal with Playwright...")
    log.info(f"Proxy: {proxy or 'none'}")
    log.info(f"Capsolver: {'yes' if capsolver else 'no'}")
    
    success, info = renew_playwright(cookie, proxy, capsolver)
    
    if success:
        log.info("🎉 Renewal completed!")
    elif success is False:
        log.info("⏳ Too early, will retry")
    else:
        log.warning("⚠️ Check billing page manually")


if __name__ == "__main__":
    main()
