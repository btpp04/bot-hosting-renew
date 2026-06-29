#!/usr/bin/env python3
"""
bot-hosting.net Free Plan Auto-Renewal Script

Usage:
  python3 renew.py [--cookie COOKIE]
  
The script uses a session cookie to authenticate and renew your free subscription.
Set SESSION_COOKIE env var or pass --cookie.
"""

import os
import sys
import re
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot-hosting-renew")

BASE_URL = "https://bot-hosting.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BotHostingRenew:
    def __init__(self, session_cookie: str):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Set the session cookie
        # Try common SvelteKit session cookie names
        self.session_cookie_value = session_cookie

    def _set_cookies(self):
        """Set cookies for the session."""
        # The session cookie name could be one of several patterns.
        # We'll try to detect the right one by checking the response.
        for name in ["__session", "session", "connect.sid", "token"]:
            self.session.cookies.set(name, self.session_cookie_value, domain="bot-hosting.net", path="/")

    def request(self, method: str, path: str, **kwargs):
        """Make a request with session cookies."""
        url = f"{BASE_URL}{path}"
        kwargs.setdefault("allow_redirects", True)
        kwargs.setdefault("timeout", 30)
        
        # Set multiple potential cookie names
        for name in ["session_token", "__session", "session", "connect.sid", "token", "sessionid"]:
            self.session.cookies.set(name, self.session_cookie_value, domain="bot-hosting.net", path="/")
        
        log.debug(f"{method} {url}")
        return self.session.request(method, url, **kwargs)

    def check_auth(self) -> bool:
        """Check if the session cookie is valid by accessing the dashboard."""
        r = self.request("GET", "/a")
        if r.status_code != 200:
            log.error(f"Dashboard returned {r.status_code}")
            return False
        
        # If we're redirected to login, the cookie is invalid
        if "/login" in r.url:
            log.error("Session cookie invalid - redirected to login page")
            return False
        
        log.info(f"Authenticated! Dashboard loaded: {r.url}")
        return True

    def get_billing_page(self) -> str:
        """Fetch the billing page to find renewal options."""
        r = self.request("GET", "/a/billings")
        if r.status_code != 200:
            log.error(f"Billing page returned {r.status_code}")
            return ""
        
        log.info(f"Billing page loaded: {r.url}")
        return r.text

    def get_deployments(self):
        """Access the dashboard to see deployments in grace period."""
        r = self.request("GET", "/a")
        html = r.text
        
        # Look for grace period warnings in the response
        # SvelteKit embeds page data in script tags
        data_matches = re.findall(
            r'<script>\s*window\.__sveltekit_data\s*=\s*({.*?});\s*</script>',
            html, re.DOTALL
        )
        
        # Also look for __DATA__ JSON (SvelteKit convention)
        data_r = self.request("GET", "/a/__data.json")
        if data_r.status_code == 200:
            try:
                data = data_r.json()
                log.info(f"Dashboard data: {json.dumps(data, indent=2)[:1000]}")
                return data
            except:
                pass
        
        log.info(f"Dashboard loaded ({len(html)} bytes)")
        return None

    def renew_free_plan(self) -> bool:
        """
        Attempt to renew the free plan.
        
        The free plan on bot-hosting.net says "Manual renewal every 7 days".
        This likely involves:
          1. Going to /a/billings
          2. Selecting the Free plan
          3. Confirming renewal
        
        Since it's a SvelteKit SPA, we need to find the right API endpoint
        or form submission.
        """
        # Try loading billing page
        html = self.get_billing_page()
        if not html:
            return False
        
        # Try SvelteKit data endpoint
        data_r = self.request("GET", "/a/billings/__data.json")
        log.info(f"Billing data endpoint: {data_r.status_code}")
        if data_r.status_code == 200:
            try:
                data = data_r.json()
                log.info(f"Billing data: {json.dumps(data, indent=2)[:2000]}")
                
                # If the data contains subscription/plan info, look for renewal action
                if isinstance(data, dict):
                    self._process_billing_data(data)
            except:
                pass
        
        # Try common billing API patterns
        endpoints = [
            ("GET", "/api/billing"),
            ("GET", "/api/subscription"),
            ("GET", "/api/billings"),
            ("POST", "/api/billing/renew"),
            ("POST", "/api/subscription/renew"),
            ("GET", "/api/user/subscription"),
        ]
        
        for method, ep in endpoints:
            r = self.request(method, ep)
            log.info(f"{method} {ep}: {r.status_code}")
            if r.status_code == 200:
                try:
                    data = r.json()
                    log.info(f"  Response: {json.dumps(data, indent=2)[:500]}")
                except:
                    log.info(f"  Body: {r.text[:300]}")
        
        return False

    def _process_billing_data(self, data: dict):
        """Process billing data to find renewal info."""
        # SvelteKit __data.json has a specific structure
        # Look for subscription/plan/grace info
        if "nodes" in data:
            for node in data["nodes"]:
                if isinstance(node, dict):
                    log.info(f"Data node keys: {list(node.keys())[:10]}")
                    if "subscription" in node:
                        log.info(f"Subscription: {json.dumps(node['subscription'], indent=2)[:500]}")
                    if "grace" in node:
                        log.info(f"Grace: {json.dumps(node['grace'], indent=2)[:500]}")
                    if "plans" in node:
                        log.info(f"Plans: {json.dumps(node['plans'], indent=2)[:500]}")
        
        # Look for subscription data directly
        if "subscription" in data:
            sub = data["subscription"]
            log.info(f"Current plan: {sub.get('accountTierName')} / {sub.get('tier')}")
            log.info(f"Status: {sub.get('status')}")
            if sub.get("grace"):
                log.info(f"Grace period! Deadline: {sub['grace'].get('deadlineIso')}")

    def try_playwright_approach(self):
        """Fallback: generate a Playwright script URL."""
        log.info("Pure requests approach limited. Consider using:")
        log.info("  playwright install chromium")
        log.info("  python3 renew_playwright.py")
        return False


def main():
    parser = argparse.ArgumentParser(description="Bot-Hosting.net Auto Renew")
    parser.add_argument("--cookie", help="Session cookie value")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cookie = args.cookie or os.environ.get("SESSION_COOKIE")
    if not cookie:
        log.error("No session cookie provided. Set SESSION_COOKIE env var or use --cookie")
        log.error("")
        log.error("To get your session cookie:")
        log.error("  1. Log in to https://bot-hosting.net in Chrome/Firefox")
        log.error("  2. Open DevTools → Application → Cookies → bot-hosting.net")
        log.error("  3. Look for __session or session cookie, copy its Value")
        log.error("  4. Set as SESSION_COOKIE env var or GitHub secret")
        sys.exit(1)

    renewer = BotHostingRenew(cookie)
    
    # Step 1: Check auth
    if not renewer.check_auth():
        log.error("Authentication failed. Cookie may be expired or incorrect.")
        log.error("Please re-login to bot-hosting.net and get a fresh cookie.")
        sys.exit(1)
    
    # Step 2: Try to renew
    log.info("Cookie valid! Attempting renewal...")
    renewer.get_deployments()
    renewer.renew_free_plan()


if __name__ == "__main__":
    main()
