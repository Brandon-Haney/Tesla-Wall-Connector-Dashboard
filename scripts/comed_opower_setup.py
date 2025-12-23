#!/usr/bin/env python3
"""
ComEd Opower Setup Wizard

Interactive setup script for ComEd Opower integration. Handles initial
authentication with MFA and caches the session for the collector to use.

This script can be run LOCALLY (on your computer) - you don't need Docker!
After authentication, the cache file is saved to project root which Docker mounts.

Requirements (install locally):
    pip install httpx

Usage:
    python scripts/comed_opower_setup.py              # Interactive setup
    python scripts/comed_opower_setup.py --test       # Verify setup works
    python scripts/comed_opower_setup.py --status     # Show current status
    python scripts/comed_opower_setup.py --force      # Force re-authentication
    python scripts/comed_opower_setup.py --mfa-method sms  # Use SMS for MFA

See docs/COMED_OPOWER_SETUP.md for detailed instructions.
"""

import argparse
import asyncio
import base64
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

# Determine script location and project root
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Cache file location - in project root (mounted into Docker as /app/)
CACHE_FILE = PROJECT_ROOT / ".comed_opower_cache.json"

# Azure AD B2C endpoints
B2C_BASE = "https://secure1.comed.com/euazurecomed.onmicrosoft.com/B2C_1A_SignIn"
B2C_POLICY = "B2C_1A_SignIn"

# ComEd endpoints
COMED_SECURE_BASE = "https://secure.comed.com"
OPOWER_BASE = "https://cec.opower.com"

# Default headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Essential cookies for token refresh
ESSENTIAL_COOKIES = {
    '.AspNet.cookie', '.AspNet.cookieC1', '.AspNet.cookieC2',
    'ASP.NET_SessionId', 'ARRAffinity', 'ARRAffinitySameSite'
}


def print_banner(text: str):
    """Print a banner with text."""
    print()
    print("=" * 60)
    print(text)
    print("=" * 60)


def print_success(text: str):
    """Print success message."""
    print(f"\n[OK] {text}")


def print_error(text: str):
    """Print error message."""
    print(f"\n[ERROR] {text}")


def print_warning(text: str):
    """Print warning message."""
    print(f"\n[WARNING] {text}")


def print_info(text: str):
    """Print info message."""
    print(f"[INFO] {text}")


def load_credentials() -> tuple:
    """Load credentials from .secrets file or environment.

    Returns:
        (username, password, bearer_token) - any may be None
    """
    import os

    username = os.getenv("COMED_USERNAME")
    password = os.getenv("COMED_PASSWORD")
    bearer_token = os.getenv("COMED_BEARER_TOKEN")

    # Try loading from .secrets file if not in environment
    secrets_file = PROJECT_ROOT / ".secrets"
    if secrets_file.exists():
        try:
            with open(secrets_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key == "COMED_USERNAME" and not username:
                            username = value
                        elif key == "COMED_PASSWORD" and not password:
                            password = value
                        elif key == "COMED_BEARER_TOKEN" and not bearer_token:
                            bearer_token = value
        except Exception as e:
            print_warning(f"Could not read .secrets file: {e}")

    return username, password, bearer_token


def check_cache() -> dict:
    """Check if we have a valid cached session.

    Returns:
        Cache data dict if valid, empty dict if not
    """
    if not CACHE_FILE.exists():
        return {}

    try:
        cache = json.loads(CACHE_FILE.read_text())
        expiry_str = cache.get("expiry")
        if not expiry_str:
            return {}

        expiry = datetime.fromisoformat(expiry_str)
        # Make timezone-aware if needed
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        if expiry <= now:
            return {}  # Expired

        cache["_expiry_dt"] = expiry
        cache["_time_remaining"] = (expiry - now).total_seconds()
        return cache

    except Exception as e:
        print_warning(f"Could not read cache: {e}")
        return {}


class ComedAuthenticator:
    """Handles ComEd authentication via Azure AD B2C."""

    def __init__(self, username: str, password: str, mfa_method: str = "email"):
        self.username = username
        self.password = password
        self.mfa_method = mfa_method.lower()
        self.client = None

        # B2C state
        self._csrf_token = None
        self._tx = None
        self._display_email = None
        self._display_phone = None

        # Token state
        self.opower_token = None
        self.token_expiry = None
        self.account_uuid = None
        self.utility_account_uuid = None

    async def __aenter__(self):
        import httpx
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers=DEFAULT_HEADERS,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def _extract_csrf_token(self, html: str):
        """Extract CSRF token from HTML page."""
        patterns = [
            r'"csrf"\s*:\s*"([^"]+)"',
            r'name="csrf"\s+value="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def _extract_tx(self, html: str):
        """Extract transaction ID (tx) from HTML page."""
        patterns = [
            r'"transId"\s*:\s*"([^"]+)"',
            r'StateProperties=([^"&]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def _extract_mfa_options(self, html: str) -> dict:
        """Extract MFA options (email/phone) from B2C page."""
        options = {}

        # Extract masked email
        email_match = re.search(r'displayEmailAddress["\s:]+value["\s:]+([^"]+)"', html)
        if not email_match:
            email_match = re.search(r'([a-z]\*+@[a-z]+\.[a-z]+)', html, re.IGNORECASE)
        if email_match:
            options['email'] = email_match.group(1)

        # Extract masked phone
        phone_match = re.search(r'displayPhoneNumber["\s:]+value["\s:]+([^"]+)"', html)
        if not phone_match:
            phone_match = re.search(r'(\*{3}-\*{3}-\d{4})', html)
        if phone_match:
            options['phone'] = phone_match.group(1)

        return options

    def _get_b2c_url(self, endpoint: str) -> str:
        """Build B2C URL with required query parameters."""
        tx_value = self._tx if self._tx.startswith("StateProperties=") else f"StateProperties={self._tx}"
        params = {"tx": tx_value, "p": B2C_POLICY}
        return f"{B2C_BASE}{endpoint}?{urlencode(params)}"

    def _get_ajax_headers(self) -> dict:
        """Get headers for AJAX requests."""
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-CSRF-TOKEN": self._csrf_token or "",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def authenticate(self, force_mfa: bool = False) -> bool:
        """Run the full authentication flow with MFA."""

        # Check for existing valid cache
        if not force_mfa and CACHE_FILE.exists():
            cache = check_cache()
            if cache:
                remaining_min = cache.get("_time_remaining", 0) / 60
                print_info(f"Using cached session (expires in {remaining_min:.1f} minutes)")
                self.opower_token = cache.get("token")
                self.token_expiry = cache.get("_expiry_dt")
                self.account_uuid = cache.get("account_uuid")
                return True

        print("\nStep 1: Loading login page...")
        resp = await self.client.get(f"{COMED_SECURE_BASE}/pages/login.aspx")
        html = resp.text

        self._csrf_token = self._extract_csrf_token(html)
        self._tx = self._extract_tx(html)

        if not self._csrf_token or not self._tx:
            raise Exception("Failed to extract CSRF token or TX from login page")

        print("Step 2: Submitting credentials...")
        url = self._get_b2c_url("/SelfAsserted")
        data = {
            "request_type": "RESPONSE",
            "signInName": self.username,
            "password": self.password,
        }

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise Exception(f"Credential submission failed: {resp.status_code}")

        try:
            result = resp.json()
            if result.get("status") != "200":
                raise Exception(f"Invalid credentials: {result.get('message', result)}")
        except json.JSONDecodeError:
            if "error" in resp.text.lower():
                raise Exception(f"Credential error: {resp.text[:200]}")

        print("Step 3: Confirming credentials...")
        url = self._get_b2c_url("/api/CombinedSigninAndSignup/confirmed")
        headers = {
            "X-CSRF-TOKEN": self._csrf_token or "",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = await self.client.get(url, headers=headers)
        html = resp.text

        # Update CSRF token
        new_csrf = self._extract_csrf_token(html)
        if new_csrf:
            self._csrf_token = new_csrf

        # Extract MFA options
        mfa_options = self._extract_mfa_options(html)
        self._display_email = mfa_options.get('email')
        self._display_phone = mfa_options.get('phone')

        print(f"Step 4: Selecting MFA method ({self.mfa_method})...")
        url = self._get_b2c_url("/SelfAsserted")

        if self.mfa_method == "sms" and self._display_phone:
            data = {
                "request_type": "RESPONSE",
                "mfaEnabledRadio": "Phone",
                "displayPhoneNumber": self._display_phone,
            }
            destination = self._display_phone
        elif self._display_email:
            data = {
                "request_type": "RESPONSE",
                "mfaEnabledRadio": "Email",
                "displayEmailAddress": self._display_email,
            }
            destination = self._display_email
        else:
            raise Exception("No MFA option available")

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())
        if resp.status_code != 200:
            raise Exception(f"MFA selection failed: {resp.status_code}")

        print("Step 5: Confirming MFA selection...")
        url = self._get_b2c_url("/api/CombinedSigninAndSignup/confirmed")
        headers = {
            "X-CSRF-TOKEN": self._csrf_token or "",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = await self.client.get(url, headers=headers)

        new_csrf = self._extract_csrf_token(resp.text)
        if new_csrf:
            self._csrf_token = new_csrf

        print("Step 6: Requesting MFA code...")
        if self.mfa_method == "sms":
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/phoneVerificationControl/SendCode"
            data = {"request_type": "RESPONSE", "displayPhoneNumber": self._display_phone}
        else:
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/SendCode"
            data = {"request_type": "RESPONSE", "displayEmailAddress": self._display_email}

        url = self._get_b2c_url(endpoint)
        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise Exception(f"Failed to send MFA code: {resp.status_code}")

        print(f"\n>>> MFA code sent to {self.mfa_method}: {destination}")
        mfa_code = input(">>> Enter the MFA code: ").strip()

        if not mfa_code:
            raise Exception("MFA code is required")

        print("\nStep 7: Verifying MFA code...")
        if self.mfa_method == "sms":
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/phoneVerificationControl/VerifyCode"
            data = {
                "request_type": "RESPONSE",
                "displayPhoneNumber": self._display_phone,
                "verificationCode": mfa_code,
            }
        else:
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/VerifyCode"
            data = {
                "request_type": "RESPONSE",
                "displayEmailAddress": self._display_email,
                "verificationCode": mfa_code,
            }

        url = self._get_b2c_url(endpoint)
        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise Exception(f"MFA verification failed: {resp.status_code}")

        print("Step 8: Final MFA submission...")
        url = self._get_b2c_url("/SelfAsserted")

        if self.mfa_method == "sms":
            data = {
                "request_type": "RESPONSE",
                "displayPhoneNumber": self._display_phone,
                "verificationCode": mfa_code,
                "extension_isMFAEnabled": "True",
            }
        else:
            data = {
                "request_type": "RESPONSE",
                "displayEmailAddress": self._display_email,
                "verificationCode": mfa_code,
                "extension_isMFAEnabled": "True",
            }

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())
        if resp.status_code != 200:
            raise Exception(f"Final MFA submission failed: {resp.status_code}")

        # Update CSRF from cookies
        for cookie in self.client.cookies.jar:
            if cookie.name == "x-ms-cpim-csrf":
                self._csrf_token = cookie.value
                break

        print("Step 9: Completing login...")
        tx_value = self._tx if self._tx.startswith("StateProperties=") else f"StateProperties={self._tx}"
        params = {"csrf_token": self._csrf_token, "tx": tx_value, "p": B2C_POLICY}
        confirmed_url = f"{B2C_BASE}/api/SelfAsserted/confirmed?{urlencode(params)}"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }

        await self.client.get(confirmed_url, headers=headers, timeout=60.0)

        print("Step 10: Getting Opower token...")
        url = f"{COMED_SECURE_BASE}/api/Services/OpowerService.svc/GetOpowerToken"
        headers = {"Content-Type": "application/json; charset=UTF-8"}

        resp = await self.client.post(url, json={}, headers=headers, timeout=60.0)

        if resp.status_code != 200:
            raise Exception(f"Failed to get Opower token: {resp.status_code}")

        result = resp.json()
        token = result.get("d") or result.get("token") or result.get("access_token")
        if not token:
            raise Exception(f"No token in response: {result}")

        self.opower_token = f"Bearer {token}" if not token.startswith("Bearer") else token

        # Decode token expiry
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                exp = payload.get("exp")
                if exp:
                    self.token_expiry = datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=20)

        # Get account info
        print("Step 11: Getting account info...")
        url = f"{OPOWER_BASE}/ei/edge/apis/multi-account-v1/cws/cec/customers/current"
        headers = {"Authorization": self.opower_token}

        resp = await self.client.get(url, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            self.account_uuid = data.get("uuid")
            utility_accounts = data.get("utilityAccounts", [])
            if utility_accounts:
                self.utility_account_uuid = utility_accounts[0].get("uuid")

        # Save cache
        self._save_cache()

        return True

    def _save_cache(self):
        """Save token and session cookies to cache file."""
        # Only save essential cookies
        cookies = {}
        for cookie in self.client.cookies.jar:
            if cookie.name in ESSENTIAL_COOKIES and cookie.domain and 'comed.com' in cookie.domain:
                cookies[cookie.name] = {
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                }

        cache = {
            "token": self.opower_token,
            "expiry": self.token_expiry.isoformat() if self.token_expiry else None,
            "account_uuid": self.account_uuid,
            "utility_account_uuid": self.utility_account_uuid,
            "cookies": cookies,
        }

        CACHE_FILE.write_text(json.dumps(cache, indent=2))
        print_success(f"Session cached to: {CACHE_FILE.name}")


async def run_authentication(username: str, password: str, mfa_method: str = "email", force: bool = False):
    """Run the full authentication flow with MFA."""
    try:
        import httpx
    except ImportError:
        print_error("httpx not installed!")
        print("\nInstall required packages with:")
        print("    pip install httpx")
        return False

    print_banner("COMED OPOWER AUTHENTICATION")
    print(f"Username: {username}")
    print(f"MFA Method: {mfa_method}")

    async with ComedAuthenticator(username, password, mfa_method) as auth:
        try:
            await auth.authenticate(force_mfa=force)

            print_banner("AUTHENTICATION SUCCESSFUL!")
            if auth.token_expiry:
                # Show expiry in both UTC and local time for clarity
                utc_str = auth.token_expiry.strftime('%Y-%m-%d %H:%M:%S UTC')
                local_time = auth.token_expiry.astimezone()
                local_str = local_time.strftime('%H:%M:%S %Z')
                time_remaining = (auth.token_expiry - datetime.now(timezone.utc)).total_seconds() / 60
                print(f"\nToken expires: {utc_str} ({local_str})")
                print(f"Time remaining: ~{time_remaining:.0f} minutes")
            else:
                print("\nToken expires: Unknown")
            print(f"Account UUID: {auth.account_uuid}")
            print("\nNext steps:")
            print("1. If running on a remote server, copy the cache file:")
            print(f"   scp {CACHE_FILE.name} root@YOUR_SERVER:/path/to/project/")
            print("2. The collector will auto-detect the cache file within 30 seconds")
            print("\nThe collector will automatically refresh the token every 10 minutes")
            print("to keep your session alive indefinitely.")
            return True

        except Exception as e:
            import traceback
            print_error(str(e) if str(e) else "Unknown error")
            print("\nFull error details:")
            traceback.print_exc()
            return False


async def test_connection():
    """Test that we can connect to Opower with current credentials."""
    try:
        import httpx
    except ImportError:
        print_error("httpx not installed!")
        print("Install with: pip install httpx")
        return False

    print_banner("TESTING OPOWER CONNECTION")

    # Check cache first
    cache = check_cache()

    if not cache:
        # Try bearer token from .secrets
        _, _, bearer_token = load_credentials()
        if bearer_token:
            print_info("Using bearer token from .secrets...")
            token = bearer_token
            account_uuid = None
        else:
            print_error("No valid session found!")
            print("\nRun the setup to authenticate:")
            print("    python scripts/comed_opower_setup.py")
            return False
    else:
        token = cache.get("token")
        account_uuid = cache.get("account_uuid")
        remaining_min = cache.get("_time_remaining", 0) / 60
        print_info(f"Using cached session (expires in {remaining_min:.1f} minutes)")

    # Test the token
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{OPOWER_BASE}/ei/edge/apis/multi-account-v1/cws/cec/customers/current",
                headers={"Authorization": token},
                timeout=30.0
            )

            if resp.status_code == 200:
                data = resp.json()
                account_uuid = data.get("uuid", account_uuid)
                utility_accounts = data.get("utilityAccounts", [])

                print_success("Connection successful!")
                print(f"  Account UUID: {account_uuid}")
                if utility_accounts:
                    ua = utility_accounts[0]
                    print(f"  Utility Account: {ua.get('uuid', 'unknown')[:12]}...")

                # Try to fetch some usage data
                await _test_fetch_usage(client, token, account_uuid)
                return True

            elif resp.status_code == 401:
                print_error("Token expired or invalid")
                print("\nRe-authenticate with:")
                print("    python scripts/comed_opower_setup.py --force")
                return False
            else:
                print_error(f"API returned status {resp.status_code}")
                return False

    except Exception as e:
        print_error(f"Connection failed: {e}")
        return False


async def _test_fetch_usage(client, token: str, account_uuid: str):
    """Helper to test fetching usage data."""
    print("\nFetching recent usage data...")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    query = """
    query GetUsageReads($timeInterval: TimeInterval, $resolution: ReadResolution) {
      billingAccountByAuthContext(forceLegacyData: true) {
        serviceAgreementsConnection(onlyActive: true) {
          edges {
            node {
              servicePointsConnection {
                edges {
                  node {
                    readStreams(timeInterval: $timeInterval, readResolution: $resolution) {
                      netUsage {
                        reads {
                          timeInterval
                          measuredAmount { value }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    tz_offset = "-06:00"  # Chicago
    time_interval = f"{start_date.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}/{end_date.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}"

    try:
        resp = await client.post(
            f"{OPOWER_BASE}/ei/edge/apis/dsm-graphql-v1/cws/graphql",
            json={
                "query": query,
                "variables": {"resolution": "DAY", "timeInterval": time_interval}
            },
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "opower-selected-entities": f'["urn:opower:customer:uuid:{account_uuid}"]',
            },
            timeout=30.0
        )

        if resp.status_code == 200:
            result = resp.json()
            reads = (result.get("data", {})
                    .get("billingAccountByAuthContext", {})
                    .get("serviceAgreementsConnection", {})
                    .get("edges", [{}])[0]
                    .get("node", {})
                    .get("servicePointsConnection", {})
                    .get("edges", [{}])[0]
                    .get("node", {})
                    .get("readStreams", {})
                    .get("netUsage", [{}])[0]
                    .get("reads", []))

            if reads:
                total_kwh = sum(r.get("measuredAmount", {}).get("value", 0) or 0 for r in reads)
                print(f"  Last 7 days: {len(reads)} days, {total_kwh:.1f} kWh total")
                print("\n  Recent daily usage:")
                for read in reads[-5:]:
                    interval = read.get("timeInterval", "")
                    kwh = read.get("measuredAmount", {}).get("value", 0) or 0
                    date = interval.split("T")[0] if "T" in interval else interval[:10]
                    print(f"    {date}: {kwh:.1f} kWh")
            else:
                print("  No usage data available for the past 7 days")
        else:
            print(f"  Could not fetch usage data (status {resp.status_code})")

    except Exception as e:
        print(f"  Could not fetch usage data: {e}")


def show_status():
    """Show current Opower configuration status."""
    print_banner("COMED OPOWER STATUS")

    # Check credentials
    username, password, bearer_token = load_credentials()

    print("Configuration (.secrets file):")
    if username and password:
        print(f"  Username: {username}")
        print(f"  Password: {'*' * min(len(password), 8)}")
    elif bearer_token:
        print(f"  Bearer Token: {bearer_token[:40]}...")
    else:
        print("  [NOT CONFIGURED]")
        print("\n  Credentials are optional - the setup script will prompt you.")

    # Check cache
    print("\nSession Cache:")
    cache = check_cache()

    if cache:
        remaining = cache.get("_time_remaining", 0)
        minutes = remaining / 60
        expiry = cache.get("_expiry_dt")

        print(f"  Status: VALID")
        if expiry:
            utc_str = expiry.strftime('%Y-%m-%d %H:%M:%S UTC')
            local_time = expiry.astimezone()
            local_str = local_time.strftime('%H:%M:%S %Z')
            print(f"  Expires: {utc_str} ({local_str})")
        else:
            print(f"  Expires: Unknown")
        print(f"  Time remaining: ~{minutes:.0f} minutes")
        print(f"  Location: {CACHE_FILE.name}")

        # Check for cookies (needed for refresh)
        cookies = cache.get("cookies", {})
        if cookies:
            print(f"  Session cookies: {len(cookies)} (can refresh token)")
        else:
            print("  Session cookies: None (cannot refresh)")
    else:
        if CACHE_FILE.exists():
            print("  Status: EXPIRED")
            print("  Run: python scripts/comed_opower_setup.py --force")
        else:
            print("  Status: NOT FOUND")
            print("  Run: python scripts/comed_opower_setup.py")

    # Check .env
    print("\nDocker Configuration (.env file):")
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        env_content = env_file.read_text().lower()
        if "opower_enabled=true" in env_content:
            print("  OPOWER_ENABLED: true")
        elif "opower_enabled" in env_content:
            print("  OPOWER_ENABLED: false (disabled)")
        else:
            print("  OPOWER_ENABLED: not set")
            print("  Add to .env: OPOWER_ENABLED=true")
    else:
        print("  .env file not found")


async def main():
    parser = argparse.ArgumentParser(
        description="ComEd Opower Setup Wizard - Run locally to authenticate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script runs on your LOCAL machine (not in Docker).
After authentication, copy the cache file to your Docker host.

Examples:
  python scripts/comed_opower_setup.py              # Authenticate with MFA
  python scripts/comed_opower_setup.py --test       # Test the connection
  python scripts/comed_opower_setup.py --status     # Show current status
  python scripts/comed_opower_setup.py --force      # Force re-authentication
  python scripts/comed_opower_setup.py --mfa-method sms  # Use SMS for MFA

Requirements:
  pip install httpx

For detailed instructions, see docs/COMED_OPOWER_SETUP.md
        """
    )
    parser.add_argument("--test", action="store_true",
                       help="Test that the connection works")
    parser.add_argument("--status", action="store_true",
                       help="Show current configuration status")
    parser.add_argument("--force", action="store_true",
                       help="Force re-authentication (ignore cache)")
    parser.add_argument("--mfa-method", choices=["email", "sms"], default="email",
                       help="MFA method to use (default: email)")

    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.test:
        success = await test_connection()
        sys.exit(0 if success else 1)

    # Check for credentials (from .secrets file or environment)
    username, password, bearer_token = load_credentials()

    # If no credentials found, prompt interactively
    if not username or not password:
        if bearer_token:
            print_banner("COMED OPOWER SETUP")
            print("Found bearer token but no username/password.")
            print("\nTesting the bearer token...")
            success = await test_connection()
            if success:
                print("\nBearer token works! However, it will expire in ~20 minutes")
                print("and cannot be refreshed without completing MFA setup.")
                print("\nWould you like to complete MFA setup now for persistent operation?")

            # Ask if they want to continue with interactive setup
            response = input("\nEnter your ComEd username (or press Enter to skip): ").strip()
            if response:
                username = response
                password = input("Enter your ComEd password: ").strip()
            else:
                sys.exit(0 if success else 1)
        else:
            print_banner("COMED OPOWER SETUP")
            print("Enter your ComEd account credentials.\n")

            username = input("ComEd Username (email): ").strip()
            if not username:
                print_error("Username is required")
                sys.exit(1)

            password = input("ComEd Password: ").strip()
            if not password:
                print_error("Password is required")
                sys.exit(1)

    # Run authentication
    success = await run_authentication(username, password, args.mfa_method, args.force)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
