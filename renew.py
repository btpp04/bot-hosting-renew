#!/usr/bin/env python3
"""
bot-hosting.net Free Plan Auto-Renewal Script

Checks subscription status and performs free plan renewal when available.

Usage:
  python3 renew.py
  SESSION_COOKIE=<cookie> python3 renew.py

The session cookie is required. Set it as SESSION_COOKIE env var or in .env file.
Supports Turnstile captcha solving via capsolver (set CAPSOLVER_API_KEY).
If no captcha solver is configured, prints manual renewal instructions.
"""

import os
import sys
import json
import re
import logging
import argparse
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bh-renew")


def get_session_cookie():
    """Get session cookie from env or arg."""
    cookie = os.environ.get("SESSION_COOKIE")
    if not cookie:
        log.error("SESSION_COOKIE not set")
        print("""
Usage: SESSION_COOKIE='<your-cookie>' python3 renew.py
  or:  python3 renew.py --help
""")
        sys.exit(1)
    return cookie


def parse_turnstile_token(html):
    """Try to extract a Turnstile token from the page."""
    # Look for Turnstile token in script data
    m = re.search(r'cf-turnstile-response["\']?\s*[:=]\s*["\']([^"\']+)', html)
    if m:
        return m.group(1)
    return None


def check_renewal_available(billing_html):
    """Parse the billing page to check if renewal is available."""
    # Extract the embedded data JSON from the page
    # Look for the subscription data pattern
    m = re.search(r'freeRenewalOpensAt["\':]+\s*["\']([^"\']+)', billing_html)
    opens_at_str = m.group(1) if m else None
    
    m = re.search(r'freeRenewalDueAt["\':]+\s*["\']([^"\']+)', billing_html)
    due_at_str = m.group(1) if m else None
    
    # Check button state
    has_disabled = 'disabled=""' in billing_html or 'disabled' in billing_html[:5000]
    has_countdown = 'Renew in' in billing_html
    
    return {
        "opens_at": opens_at_str,
        "due_at": due_at_str,
        "button_disabled": has_disabled,
        "show_countdown": has_countdown,
    }


def playwrite_renew(cookie):
    """
    Use Playwright to handle Turnstile + click Renew button.
    Falls back to manual instructions if Playwright not available.
    """
    # Check if playwright is installed
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed - manual renewal instructions below.")
        return False

    capsolver_key = os.environ.get("CAPSOLVER_API_KEY")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        # Add session cookie
        context.add_cookies([{
            "name": "session_token",
            "value": cookie,
            "domain": "bot-hosting.net",
            "path": "/",
        }])
        
        page = context.new_page()
        
        # Navigate to billing page
        log.info("Opening billing page...")
        page.goto("https://bot-hosting.net/a/billings", wait_until="networkidle")
        
        # Wait for the page to render
        page.wait_for_timeout(2000)
        
        # Check button state
        renew_btn = page.query_selector('button:has-text("Renew in")')
        if not renew_btn:
            renew_btn = page.query_selector('button:has-text("Renew")')
        
        if not renew_btn:
            log.info("No Renew button found. Page content:")
            log.info(page.content()[:1000])
            browser.close()
            return False
        
        btn_text = renew_btn.inner_text()
        is_disabled = renew_btn.get_attribute("disabled") is not None
        
        log.info(f"Renew button: '{btn_text}' (disabled={is_disabled})")
        
        if is_disabled:
            log.info("Renewal not yet available - button is disabled with countdown.")
            browser.close()
            return False
        
        # Handle Turnstile
        log.info("Renewal available! Solving Turnstile...")
        
        if capsolver_key:
            # Use capsolver service
            log.info("Using capsolver for Turnstile...")
            turnstile_token = solve_turnstile_capsolver(page, capsolver_key)
            if not turnstile_token:
                log.error("Failed to solve Turnstile")
                browser.close()
                return False
            
            # Set the token by evaluating JS
            page.evaluate(f"""
                (() => {{
                    const container = document.getElementById('turnstile-container');
                    if (!container) return;
                    // Trigger the Turnstile callback with our token
                    // The actual callback function is set by the page
                    window._turnstile_callback && window._turnstile_callback('{turnstile_token}');
                }})();
            """)
            page.wait_for_timeout(500)
        
        # Click Renew
        log.info("Clicking Renew button...")
        renew_btn.click()
        page.wait_for_timeout(3000)
        page.wait_for_load_state("networkidle")
        
        # Check result
        current_url = page.url
        page_content = page.content()
        
        if "success" in current_url or "thank" in page_content.lower():
            log.info("✅ Renewal successful!")
            browser.close()
            return True
        
        # Check if the button changed
        if "Active" in page_content or "Expires" in page_content:
            log.info("✅ Renewal appears successful!")
            browser.close()
            return True
        
        log.info(f"After click URL: {current_url}")
        log.info("Renewal status uncertain - please verify manually.")
        browser.close()
        return False


def solve_turnstile_capsolver(page, api_key):
    """Solve Turnstile captcha using capsolver API."""
    import requests as req
    
    site_key = page.evaluate("""
        (() => {
            const el = document.querySelector('[data-sitekey]');
            return el ? el.getAttribute('data-sitekey') : null;
        })()
    """)
    
    if not site_key:
        log.error("Could not find Turnstile site key")
        return None
    
    log.info(f"Turnstile site key: {site_key}")
    
    # Submit to capsolver
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": "https://bot-hosting.net/a/billings",
            "websiteKey": site_key,
        }
    }
    
    resp = req.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
    result = resp.json()
    
    if result.get("errorId") != 0:
        log.error(f"Capsolver error: {result.get('errorDescription')}")
        return None
    
    task_id = result["taskId"]
    log.info(f"Capsolver task created: {task_id}")
    
    # Poll for result
    for i in range(30):
        resp = req.post("https://api.capsolver.com/getTaskResult", json={
            "clientKey": api_key,
            "taskId": task_id
        }, timeout=30)
        result = resp.json()
        
        if result.get("status") == "ready":
            token = result["solution"]["token"]
            log.info("Turnstile solved!")
            return token
        
        log.info(f"Waiting for captcha solution ({i+1}/30)...")
        import time
        time.sleep(2)
    
    log.error("Capsolver timeout")
    return None


def print_manual_instructions(data):
    """Print instructions for manual renewal."""
    opens_at = data.get("opens_at")
    due_at = data.get("due_at")
    
    log.info("=" * 50)
    log.info("MANUAL RENEWAL REQUIRED")
    log.info("=" * 50)
    log.info("")
    
    if opens_at:
        opens_dt = datetime.fromisoformat(opens_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if opens_dt > now:
            remaining = opens_dt - now
            hours = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            log.info(f"Renewal opens in: {hours}h {mins}m")
            log.info(f"Opens at (UTC): {opens_at}")
    
    if due_at:
        log.info(f"Due at (UTC):    {due_at}")
    
    log.info("")
    log.info("To renew manually:")
    log.info("  1. Open https://bot-hosting.net/a/billings")
    log.info("  2. Wait for the countdown to finish")
    log.info("  3. Solve the Turnstile captcha (click checkbox)")
    log.info("  4. Click 'Renew' button")
    log.info("")
    log.info("Or set CAPSOLVER_API_KEY to automate captcha solving.")


def main():
    parser = argparse.ArgumentParser(description="bot-hosting.net auto renewal")
    parser.add_argument("--cookie", help="Session cookie (session_token)")
    parser.add_argument("--playwright", action="store_true", help="Use Playwright for GUI automation")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cookie = args.cookie or os.environ.get("SESSION_COOKIE")
    if not cookie:
        log.error("SESSION_COOKIE not set")
        sys.exit(1)

    # Step 1: Check auth via API
    import urllib.request, ssl

    ctx = ssl.create_default_context()
    
    def fetch_api(path):
        req = urllib.request.Request(
            f'https://bot-hosting.net{path}',
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
                'Cookie': f'session_token={cookie}'
            })
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        return resp.read().decode()

    # Get dashboard data
    log.info("Checking authentication...")
    try:
        billing_html = fetch_api('/a/billings')
        if '/login' in billing_html[:200]:
            log.error("Session cookie invalid - redirected to login")
            sys.exit(1)
    except Exception as e:
        log.error(f"Failed to access billing page: {e}")
        sys.exit(1)

    # Check renewal status from embedded data
    log.info("✅ Authenticated! Checking renewal status...")
    
    # Extract subscription data from the embedded JS
    m = re.search(r'freeRenewalOpensAt["\':]+\s*["\']([^"\']+)', billing_html)
    opens_at = m.group(1) if m else "unknown"
    m = re.search(r'freeRenewalDueAt["\':]+\s*["\']([^"\']+)', billing_html)
    due_at = m.group(1) if m else "unknown"
    m = re.search(r'accountTierName["\':]+\s*["\']([^"\']+)', billing_html)
    tier = m.group(1) if m else "unknown"
    m = re.search(r'status["\':]+\s*["\']([^"\']+)', billing_html)
    status = m.group(1) if m else "unknown"
    
    log.info(f"Plan: {tier} | Status: {status}")
    log.info(f"Free renewal opens: {opens_at} (UTC)")
    log.info(f"Free renewal due:   {due_at} (UTC)")
    
    # Check if renewal is available
    now = datetime.now(timezone.utc)
    try:
        opens_dt = datetime.fromisoformat(opens_at.replace("Z", "+00:00"))
        if now >= opens_dt:
            log.info("✅ Renewal is available now!")
            
            if args.playwright:
                playwrite_renew(cookie)
            else:
                # Print instructions
                log.info("Renewal available but captcha required.")
                log.info("Use --playwright to attempt automated renewal.")
                log.info("Or renew manually at: https://bot-hosting.net/a/billings")
        else:
            remaining = opens_dt - now
            h, m_rem = remaining.seconds // 3600, (remaining.seconds % 3600) // 60
            log.info(f"⏳ Renewal opens in {h}h {m_rem}m (at {opens_at} UTC)")
    except:
        log.warning("Could not parse renewal date")
    
    log.info("✅ Check complete")


if __name__ == "__main__":
    main()
