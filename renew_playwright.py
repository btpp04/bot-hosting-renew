#!/usr/bin/env python3
"""
Playwright-based bot-hosting.net Free Plan Auto-Renewal
Autofills session cookie and navigates billing page.
"""

import os
import sys
import json
import time
import logging
import argparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bh-renew-pw")

BASE_URL = "https://bot-hosting.net"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", help="Session cookie value")
    parser.add_argument("--cookie-name", default="__session",
                        help="Session cookie name (default: __session)")
    args = parser.parse_args()

    cookie_value = args.cookie or os.environ.get("SESSION_COOKIE")
    if not cookie_value:
        log.error("No session cookie. Set SESSION_COOKIE env var or use --cookie")
        sys.exit(1)

    cookie_name = args.cookie_name or os.environ.get("SESSION_COOKIE_NAME", "__session")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # Add session cookie
        context.add_cookies([{
            "name": cookie_name,
            "value": cookie_value,
            "domain": "bot-hosting.net",
            "path": "/",
        }])

        page = context.new_page()

        # Step 1: Check auth by loading dashboard
        log.info("Checking authentication...")
        page.goto(f"{BASE_URL}/a", wait_until="networkidle")

        if "/login" in page.url:
            # Try other common cookie names
            log.warning(f"Cookie '{cookie_name}' didn't work, trying alternatives...")
            for alt_name in ["session", "connect.sid", "token", "sessionid", "__session"]:
                if alt_name == cookie_name:
                    continue
                context.add_cookies([{
                    "name": alt_name,
                    "value": cookie_value,
                    "domain": "bot-hosting.net",
                    "path": "/",
                }])
            
            page.goto(f"{BASE_URL}/a", wait_until="networkidle")
            if "/login" in page.url:
                log.error("Authentication failed with all cookie names")
                browser.close()
                sys.exit(1)

        log.info(f"Authenticated! Dashboard: {page.url}")

        # Step 2: Look for grace period alerts
        grace_el = page.query_selector('[class*="danger"]')
        if grace_el and "not renewed" in (grace_el.inner_text() or "").lower():
            log.info("Found grace period warning!")

        # Step 3: Go to billing page
        log.info("Navigating to billing page...")
        page.goto(f"{BASE_URL}/a/billings", wait_until="networkidle")
        time.sleep(2)

        # Save page content for analysis
        content = page.content()
        with open("/tmp/bh_billing_page.html", "w") as f:
            f.write(content)
        log.info(f"Billing page saved ({len(content)} bytes)")

        # Look for renewal buttons
        buttons = page.query_selector_all("button, a")
        renew_btn = None
        for btn in buttons:
            text = (btn.inner_text() or "").lower()
            if any(w in text for w in ["renew", "续期", "extend", "free", "select plan"]):
                log.info(f"Found potential renewal button: '{btn.inner_text()}'")
                renew_btn = btn
                break

        if renew_btn:
            log.info("Clicking renewal button...")
            renew_btn.click()
            time.sleep(3)
            page.wait_for_load_state("networkidle")
            
            # Check result
            if "success" in page.url.lower() or "thank" in page.content().lower():
                log.info("✅ Renewal successful!")
            else:
                log.info(f"After click URL: {page.url}")
                # Maybe need to confirm
                confirm_btn = page.query_selector("button:has-text('Confirm'), button:has-text('Yes')")
                if confirm_btn:
                    confirm_btn.click()
                    time.sleep(3)
                    log.info("✅ Confirmed renewal")
        else:
            log.info("No 'Renew' button found. Page may already be active or need manual check.")

        browser.close()


if __name__ == "__main__":
    main()
