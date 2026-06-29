#!/usr/bin/env python3
"""
bot-hosting.net 免费计划全自动续期脚本
自动检测续期窗口 + 解决 Turnstile 验证码 + 点击续期按钮
"""

import os, sys, json, re, time, logging, argparse, urllib.request, ssl
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bh-renew")

BASE = "https://bot-hosting.net"

def fatal(msg):
    log.error(msg)
    sys.exit(1)

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
    """Get subscription status from billing page"""
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
    return info, html

def is_renewal_available(info):
    """Check if renewal can be performed now"""
    if not info["opens_at"]:
        return False
    try:
        opens = datetime.fromisoformat(info["opens_at"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= opens
    except:
        return False

def renew_with_playwright(cookie, capsolver_key=None):
    """Use Playwright to solve Turnstile and click Renew"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US",
            viewport={"width": 1280, "height": 800}
        )
        ctx.add_cookies([{
            "name": "session_token", "value": cookie,
            "domain": "bot-hosting.net", "path": "/",
        }])
        
        page = ctx.new_page()
        page.goto(f"{BASE}/a/billings", wait_until="networkidle")
        page.wait_for_timeout(3000)
        
        # Check button state
        renew_btn = page.query_selector("button:has-text('Renew')")
        if not renew_btn:
            log.info("No Renew button found")
            browser.close()
            return False
        
        btn_text = renew_btn.inner_text()
        is_disabled = renew_btn.get_attribute("disabled") is not None
        log.info(f"Button: '{btn_text}' (disabled={is_disabled})")
        
        if is_disabled:
            # Extract countdown
            m = re.search(r'(\d+):(\d+):(\d+)', btn_text)
            if m:
                log.info(f"Renew in {m.group(1)}h {m.group(2)}m {m.group(3)}s")
            browser.close()
            return False
        
        # Handle Turnstile
        log.info("Solving Turnstile...")
        
        if capsolver_key:
            success = solve_turnstile_capsolver(page, capsolver_key)
            if not success:
                log.error("Capsolver failed")
                browser.close()
                return False
            page.wait_for_timeout(1000)
        else:
            # Try auto-wait for Turnstile (some invisible modes auto-solve)
            log.info("No capsolver key - trying Turnstile auto-solve...")
            page.wait_for_timeout(2000)
        
        # Click Renew
        log.info("Clicking Renew button...")
        renew_btn = page.query_selector("button:has-text('Renew')")
        if renew_btn:
            renew_btn.click()
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle")
            
            content = page.content()
            if "Expires" in content and "Active" in content:
                log.info("✅ Renewal appears successful!")
                browser.close()
                return True
        
        log.info("Renewal click done, verify manually")
        browser.close()
        return None

def solve_turnstile_capsolver(page, api_key):
    """Solve Turnstile via capsolver"""
    try:
        import requests as req
    except ImportError:
        log.error("requests required for capsolver")
        return False
    
    site_key = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]') ||
                    document.querySelector('.cf-turnstile') ||
                    document.querySelector('#turnstile-container [data-sitekey]');
        return el ? el.getAttribute('data-sitekey') : null;
    }""")
    
    if not site_key:
        log.warning("Turnstile site key not found on page")
        return False
    
    log.info(f"Site key: {site_key}")
    
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": "https://bot-hosting.net/a/billings",
            "websiteKey": site_key,
        }
    }
    
    try:
        r = req.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
        result = r.json()
        if result.get("errorId") != 0:
            log.error(f"Capsolver: {result.get('errorDescription')}")
            return False
        
        task_id = result["taskId"]
        log.info(f"Task: {task_id}, waiting...")
        
        for i in range(60):
            r = req.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": api_key, "taskId": task_id
            }, timeout=30)
            result = r.json()
            if result.get("status") == "ready":
                token = result["solution"]["token"]
                # Inject token into page
                page.evaluate(f"""
                    (() => {{
                        const container = document.getElementById('turnstile-container');
                        if (container) {{
                            // Trigger Turnstile callback
                            const widget = turnstile?.getResponse?.();
                            if (widget) return;
                            // Try calling onToken callback 
                            Object.keys(window).forEach(k => {{
                                if (typeof window[k] === 'function' && k.includes('Turnstile')) {{
                                    window[k]('{token}');
                                }}
                            }});
                        }}
                    }})();
                """)
                log.info("Turnstile solved!")
                return True
            time.sleep(1)
        
        log.error("Capsolver timeout")
        return False
    except Exception as e:
        log.error(f"Capsolver error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", help="Session cookie")
    parser.add_argument("--capsolver-key", help="Capsolver API key")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cookie = args.cookie or get_cookie()
    capsolver = args.capsolver_key or os.environ.get("CAPSOLVER_API_KEY")
    
    # Check status
    info, _ = check_status(cookie)
    
    # Check if renewal is available
    if not is_renewal_available(info):
        if info["opens_at"]:
            try:
                opens = datetime.fromisoformat(info["opens_at"].replace("Z", "+00:00"))
                remaining = opens - datetime.now(timezone.utc)
                h, m = remaining.seconds // 3600, (remaining.seconds % 3600) // 60
                log.info(f"⏳ Renew in {h}h {m}m (next check at opens)")
            except:
                pass
        log.info("Not yet time - scheduled check will try again")
        
        # Even if not available, try Playwright once to confirm button state
        if os.environ.get("TRY_PLAYWRIGHT"):
            renew_with_playwright(cookie, capsolver)
        return True

    # Renewal available
    log.info("✅ Renewal window is OPEN! Attempting...")
    
    if capsolver:
        log.info("Capsolver key configured - will automate Turnstile")
    
    result = renew_with_playwright(cookie, capsolver)
    
    if result:
        log.info("✅ Renewal completed successfully!")
    else:
        log.warning("⚠️ Automated renewal incomplete. Check billing page.")
    
    return result


if __name__ == "__main__":
    main()
