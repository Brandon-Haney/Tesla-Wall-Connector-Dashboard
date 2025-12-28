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
import hashlib
import json
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

# Determine script location and project root
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Cache file location - in project root (mounted into Docker as /app/)
CACHE_FILE = PROJECT_ROOT / ".comed_opower_cache.json"

# Azure AD B2C endpoints (Mobile OAuth flow)
B2C_MOBILE_POLICY = "B2C_1A_SignIn_Mobile"

# ComEd Mobile OAuth configuration (same as mobile app)
COMED_CLIENT_ID = "b587ed2d-28a5-462c-8c1f-835f9d73f7c3"
COMED_MOBILE_ID = "msauth.com.comed.mobile"
COMED_EU_DOMAIN = "eudapi.comed.com"
COMED_LOGIN_DOMAIN = "secure.comed.com"

# ComEd endpoints
COMED_SECURE_BASE = "https://secure.comed.com"
OPOWER_BASE = "https://cec.opower.com"

# Opower scope for token refresh
OPOWER_SCOPE = "https://euazurecomed.onmicrosoft.com/opower/opower_connect"

# Default headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
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
    """Check if we have a valid cached OAuth tokens.

    Returns:
        Cache data dict if valid, empty dict if not
    """
    if not CACHE_FILE.exists():
        return {}

    try:
        cache = json.loads(CACHE_FILE.read_text())

        # Detect old cache format (cookie-based)
        if "cookies" in cache and "refresh_token" not in cache:
            print_warning("Old cache format detected (cookie-based, ~6h expiry)")
            print_info("The new OAuth format uses refresh tokens that last 30-90 days.")
            print_info("Run with --force to re-authenticate: python scripts/comed_opower_setup.py --force")
            return {}

        # Check for refresh_token (required for new format)
        refresh_token = cache.get("refresh_token")
        if not refresh_token:
            print_warning("Cache missing refresh_token")
            return {}

        # Parse token expiry (for Opower token, not refresh token)
        expiry_str = cache.get("expiry")
        if expiry_str:
            expiry = datetime.fromisoformat(expiry_str)
            # Make timezone-aware if needed
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            cache["_expiry_dt"] = expiry
            cache["_time_remaining"] = (expiry - datetime.now(timezone.utc)).total_seconds()
        else:
            # No expiry means we can still use refresh_token to get new Opower token
            cache["_expiry_dt"] = None
            cache["_time_remaining"] = 0

        cache["_has_refresh_token"] = True
        return cache

    except Exception as e:
        print_warning(f"Could not read cache: {e}")
        return {}


class ComedAuthenticator:
    """Handles ComEd authentication via Azure AD B2C mobile OAuth flow.

    Uses B2C_1A_SignIn_Mobile policy with PKCE for long-lived refresh tokens
    (30-90 days) instead of web session cookies (~6 hours).
    """

    def __init__(self, username: str, password: str, mfa_method: str = "email"):
        self.username = username
        self.password = password
        self.mfa_method = mfa_method.lower()
        self.client = None

        # OAuth state
        self._code_verifier: Optional[str] = None
        self._code_challenge: Optional[str] = None
        self._base_url: Optional[str] = None
        self._settings: Dict = {}
        self._refresh_token: Optional[str] = None
        self._account: Optional[Dict] = None

        # MFA state
        self._display_email: Optional[str] = None
        self._display_phone: Optional[str] = None

        # Token state
        self.opower_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.account_uuid: Optional[str] = None
        self.utility_account_uuid: Optional[str] = None

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

    def _generate_pkce(self) -> Tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        code_verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return code_verifier, code_challenge

    def _load_javascript_var(self, html: str, var_name: str) -> Optional[Dict]:
        """Extract JSON from a JavaScript variable in HTML."""
        match = re.search(r"var " + var_name + r" = ({.*?});", html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _extract_mfa_options_from_sa_fields(self, sa_fields: Dict) -> Dict[str, str]:
        """Extract MFA options from SA_FIELDS."""
        options = {}
        for field in sa_fields.get("AttributeFields", []):
            field_id = field.get("ID", "")
            if field_id == "displayEmailAddress":
                options["email"] = field.get("PRE", "")
            elif field_id == "displayPhoneNumber":
                options["phone"] = field.get("PRE", "")
            elif field_id == "emailVerificationControl":
                for display in field.get("DISPLAY_FIELDS", []):
                    if display.get("ID") == "displayEmailAddress":
                        options["email"] = display.get("PRE", "")
        return options

    def _get_ajax_headers(self) -> Dict:
        """Get headers for AJAX requests."""
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-CSRF-TOKEN": self._settings.get("csrf", ""),
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": DEFAULT_HEADERS["User-Agent"],
        }

    async def _b2c_get(self, path: str, allow_redirects: bool = True) -> Tuple[str, str, Optional[str]]:
        """Make a GET request to B2C API endpoint."""
        api = self._settings.get("api", "SelfAsserted")
        url = f"https://{self._base_url}/api/{api}/{path}"

        params = {
            "csrf_token": self._settings.get("csrf", ""),
            "tx": self._settings.get("transId", ""),
            "p": self._settings.get("hosts", {}).get("policy", B2C_MOBILE_POLICY),
        }

        resp = await self.client.get(
            url,
            params=params,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
            follow_redirects=allow_redirects,
        )

        if resp.status_code != 200 and allow_redirects:
            raise Exception(f"B2C GET failed: {resp.status_code}")

        if allow_redirects:
            new_settings = self._load_javascript_var(resp.text, "SETTINGS")
            if new_settings:
                self._settings = new_settings

        final_host = str(resp.url.host) if resp.url else None
        return resp.text, str(resp.url.path), final_host

    async def _b2c_post(self, path: str, data: Dict, error_msg: str = "") -> Dict:
        """Make a POST request to B2C API endpoint."""
        api = self._settings.get("api", "SelfAsserted")
        url = f"https://{self._base_url}/{api}"
        if path:
            url = f"{url}/{path}"

        params = {
            "tx": self._settings.get("transId", ""),
            "p": self._settings.get("hosts", {}).get("policy", B2C_MOBILE_POLICY),
        }

        resp = await self.client.post(
            url,
            params=params,
            data=data,
            headers=self._get_ajax_headers(),
        )

        if resp.status_code != 200:
            raise Exception(f"B2C POST {error_msg} failed: {resp.status_code}")

        try:
            result = resp.json()
            if result.get("status") != "200":
                raise Exception(f"B2C POST {error_msg}: {result.get('message', result)}")
            return result
        except json.JSONDecodeError:
            if "error" in resp.text.lower():
                raise Exception(f"B2C POST {error_msg}: {resp.text[:200]}")
            return {}

    async def _post_oauth_token(self, data: Dict) -> Dict:
        """POST to the OAuth token endpoint."""
        url = f"https://{self._base_url}/oauth2/v2.0/token"

        resp = await self.client.post(
            url,
            data=data,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
            timeout=30.0
        )

        if resp.status_code != 200:
            raise Exception(f"OAuth token request failed: {resp.status_code}")

        return resp.json()

    async def authenticate(self, force_mfa: bool = False) -> bool:
        """Run the mobile OAuth authentication flow with MFA."""

        # Check for existing valid cache
        if not force_mfa and CACHE_FILE.exists():
            cache = check_cache()
            if cache and cache.get("_has_refresh_token"):
                print_info("Found cached OAuth tokens, testing refresh...")
                try:
                    self._refresh_token = cache.get("refresh_token")
                    self._base_url = cache.get("base_url")
                    self._account = cache.get("account")
                    self.account_uuid = cache.get("account_uuid")
                    self.utility_account_uuid = cache.get("utility_account_uuid")

                    # Try to refresh the tokens
                    await self._refresh_and_get_opower_token()
                    self._save_cache()
                    print_success("Token refresh successful!")
                    return True
                except Exception as e:
                    print_warning(f"Token refresh failed: {e}")
                    print_info("Will perform fresh authentication...")

        # Generate PKCE verifier and challenge
        self._code_verifier, self._code_challenge = self._generate_pkce()

        print("\nStep 1: Loading login page and initializing mobile OAuth...")
        resp = await self.client.get(
            f"https://{COMED_LOGIN_DOMAIN}/Pages/Login.aspx?/login",
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
        )

        settings = self._load_javascript_var(resp.text, "SETTINGS")
        if not settings:
            raise Exception("Failed to extract SETTINGS from login page")

        final_path = str(resp.url.path) if resp.url else ""
        if not final_path.endswith("/authorize"):
            raise Exception(f"Expected authorize endpoint, got: {final_path}")

        login_host = str(resp.url.host) if resp.url else ""
        tenant = settings.get("hosts", {}).get("tenant", "")
        policy = settings.get("hosts", {}).get("policy", "")

        self._base_url = login_host + tenant
        self._base_url = self._base_url.replace(policy, B2C_MOBILE_POLICY)

        # Load mobile OAuth page with PKCE
        params = urlencode({
            "p": B2C_MOBILE_POLICY,
            "client_id": COMED_CLIENT_ID,
            "nonce": "defaultNonce",
            "redirect_uri": f"{COMED_MOBILE_ID}://auth",
            "scope": "openid offline_access",
            "response_type": "code",
            "code_challenge": self._code_challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
        })

        url = f"https://{login_host}{final_path}?{params}"
        resp = await self.client.get(url, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]})

        if resp.status_code != 200:
            raise Exception(f"Failed to load mobile OAuth page: {resp.status_code}")

        settings = self._load_javascript_var(resp.text, "SETTINGS")
        if not settings:
            raise Exception("Failed to extract SETTINGS from mobile OAuth page")

        settings["api"] = "SelfAsserted"
        self._settings = settings

        print("Step 2: Submitting credentials...")
        await self._b2c_post("", {
            "request_type": "RESPONSE",
            "signInName": self.username,
            "password": self.password,
        }, "credentials")

        print("Step 3: Getting MFA options...")
        html, _, _ = await self._b2c_get("confirmed")
        sa_fields = self._load_javascript_var(html, "SA_FIELDS")
        if not sa_fields:
            raise Exception("Failed to get MFA options (no SA_FIELDS)")

        mfa_options = self._extract_mfa_options_from_sa_fields(sa_fields)
        self._display_email = mfa_options.get("email")
        self._display_phone = mfa_options.get("phone")

        if not self._display_email and not self._display_phone:
            raise Exception("No MFA options available")

        print(f"Step 4: Selecting MFA method ({self.mfa_method}) and sending code...")
        if self.mfa_method == "sms" and self._display_phone:
            mfa_selection = "Text"
            destination = self._display_phone
        else:
            mfa_selection = "Email"
            destination = self._display_email

        await self._b2c_post("", {
            "displayEmailAddress": self._display_email or "",
            "displayPhoneNumber": self._display_phone or "",
            "mfaEnabledRadio": mfa_selection,
            "request_type": "RESPONSE",
        }, "MFA selection")

        await self._b2c_get("confirmed")

        if mfa_selection == "Text":
            await self._b2c_post("DisplayControlAction/vbeta/textVerificationControl/SendCode",
                {"displayPhoneNumber": self._display_phone}, "send MFA code")
        else:
            await self._b2c_post("DisplayControlAction/vbeta/emailVerificationControl/SendCode",
                {"displayEmailAddress": self._display_email}, "send MFA code")

        print(f"\n>>> MFA code sent to {self.mfa_method}: {destination}")
        mfa_code = input(">>> Enter the MFA code: ").strip()

        if not mfa_code:
            raise Exception("MFA code is required")

        print("\nStep 5: Verifying MFA code...")
        if mfa_selection == "Text":
            verify_data = {"displayPhoneNumber": self._display_phone, "verificationCode": mfa_code}
            await self._b2c_post("DisplayControlAction/vbeta/textVerificationControl/VerifyCode",
                verify_data, "verify MFA code")
        else:
            verify_data = {"displayEmailAddress": self._display_email, "verificationCode": mfa_code}
            await self._b2c_post("DisplayControlAction/vbeta/emailVerificationControl/VerifyCode",
                verify_data, "verify MFA code")

        verify_data["request_type"] = "RESPONSE"
        await self._b2c_post("", verify_data, "final MFA submission")

        print("Step 6: Getting authorization code...")
        api = self._settings.get("api", "SelfAsserted")
        url = f"https://{self._base_url}/api/{api}/confirmed"
        params = {
            "csrf_token": self._settings.get("csrf", ""),
            "tx": self._settings.get("transId", ""),
            "p": B2C_MOBILE_POLICY,
        }

        resp = await self.client.get(
            url,
            params=params,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
            follow_redirects=False,
        )

        location = resp.headers.get("Location", "")
        if not location or "code=" not in location:
            raise Exception(f"No authorization code in redirect: {location[:100]}")

        code_match = re.search(r"code=([^&]+)", location)
        if not code_match:
            raise Exception("Failed to extract authorization code")

        auth_code = code_match.group(1)

        print("Step 7: Exchanging code for OAuth tokens...")
        result = await self._post_oauth_token({
            "grant_type": "authorization_code",
            "scope": f"openid offline_access {COMED_CLIENT_ID}",
            "client_id": COMED_CLIENT_ID,
            "code": auth_code,
            "code_verifier": self._code_verifier,
            "redirect_uri": COMED_MOBILE_ID,
        })

        self._refresh_token = result.get("refresh_token", "")
        if not self._refresh_token:
            raise Exception("No refresh_token in OAuth response")

        print("Step 8: Getting account information...")
        await self._get_account_info()

        print("Step 9: Getting Opower token...")
        await self._refresh_and_get_opower_token()

        print("Step 10: Getting customer info...")
        await self._get_customer_info()

        self._save_cache()

        return True

    async def _refresh_oauth_token(self) -> str:
        """Refresh the OAuth tokens."""
        result = await self._post_oauth_token({
            "grant_type": "refresh_token",
            "response_type": "token",
            "scope": f"openid offline_access {COMED_CLIENT_ID}",
            "client_id": COMED_CLIENT_ID,
            "refresh_token": self._refresh_token,
        })

        new_refresh_token = result.get("refresh_token", "")
        if new_refresh_token:
            self._refresh_token = new_refresh_token

        return result.get("access_token", "")

    async def _refresh_opower_token(self) -> str:
        """Get Opower token using refresh token."""
        account_number = ""
        if self._account:
            account_number = self._account.get("accountNumber", "")

        result = await self._post_oauth_token({
            "grant_type": "refresh_token",
            "response_type": "token",
            "scope": OPOWER_SCOPE,
            "client_id": COMED_CLIENT_ID,
            "refresh_token": self._refresh_token,
            "nonce": account_number,
        })

        return result.get("access_token", "")

    async def _refresh_and_get_opower_token(self):
        """Refresh OAuth tokens and get Opower token."""
        await self._refresh_oauth_token()
        opower_token = await self._refresh_opower_token()

        self.opower_token = f"Bearer {opower_token}"

        try:
            parts = opower_token.split(".")
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                exp = payload.get("exp")
                if exp:
                    self.token_expiry = datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=20)

    async def _get_account_info(self):
        """Get account information from ComEd API."""
        bearer_token = await self._refresh_oauth_token()

        resp = await self.client.get(
            f"https://{COMED_EU_DOMAIN}/mobile/custom/auth/accounts",
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Authorization": f"Bearer {bearer_token}",
            },
        )

        if resp.status_code != 200:
            raise Exception(f"Failed to get accounts: {resp.status_code}")

        result = resp.json()
        if not result.get("success"):
            raise Exception(f"Failed to get accounts: {result}")

        accounts = result.get("data", [])
        active_accounts = [a for a in accounts if a.get("status") == "Active"]

        if not active_accounts:
            raise Exception("No active accounts found")

        self._account = active_accounts[0]

    async def _get_customer_info(self):
        """Get customer info from Opower API."""
        url = f"{OPOWER_BASE}/ei/edge/apis/multi-account-v1/cws/cec/customers/current"
        resp = await self.client.get(url, headers={"Authorization": self.opower_token})

        if resp.status_code == 200:
            data = resp.json()
            self.account_uuid = data.get("uuid")
            utility_accounts = data.get("utilityAccounts", [])
            if utility_accounts:
                self.utility_account_uuid = utility_accounts[0].get("uuid")

    def _save_cache(self):
        """Save OAuth tokens to cache file."""
        cache = {
            "refresh_token": self._refresh_token,
            "base_url": self._base_url,
            "account": self._account,
            "token": self.opower_token,
            "expiry": self.token_expiry.isoformat() if self.token_expiry else None,
            "account_uuid": self.account_uuid,
            "utility_account_uuid": self.utility_account_uuid,
        }

        CACHE_FILE.write_text(json.dumps(cache, indent=2))
        print_success(f"OAuth tokens cached to: {CACHE_FILE.name}")
        print_info("Refresh tokens last 30-90 days (vs ~6 hours for old web sessions)")


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
        # With new OAuth format, try to refresh the token first
        if cache.get("_has_refresh_token"):
            print_info("Testing OAuth token refresh...")
            try:
                # Use the authenticator to refresh
                async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, headers=DEFAULT_HEADERS) as client:
                    refresh_token = cache.get("refresh_token")
                    base_url = cache.get("base_url")
                    account = cache.get("account", {})

                    # Refresh the OAuth token
                    resp = await client.post(
                        f"https://{base_url}/oauth2/v2.0/token",
                        data={
                            "grant_type": "refresh_token",
                            "response_type": "token",
                            "scope": f"openid offline_access {COMED_CLIENT_ID}",
                            "client_id": COMED_CLIENT_ID,
                            "refresh_token": refresh_token,
                        },
                        headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                    )

                    if resp.status_code != 200:
                        print_warning(f"OAuth refresh failed: {resp.status_code}")
                        print_info("Token may have expired, try re-authenticating")
                        return False

                    result = resp.json()
                    new_refresh_token = result.get("refresh_token", refresh_token)

                    # Get Opower token
                    resp = await client.post(
                        f"https://{base_url}/oauth2/v2.0/token",
                        data={
                            "grant_type": "refresh_token",
                            "response_type": "token",
                            "scope": OPOWER_SCOPE,
                            "client_id": COMED_CLIENT_ID,
                            "refresh_token": new_refresh_token,
                            "nonce": account.get("accountNumber", ""),
                        },
                        headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                    )

                    if resp.status_code != 200:
                        print_warning(f"Opower token refresh failed: {resp.status_code}")
                        return False

                    opower_token = resp.json().get("access_token", "")
                    token = f"Bearer {opower_token}"
                    print_success("OAuth token refresh successful!")

            except Exception as e:
                print_warning(f"Token refresh failed: {e}")
                # Fall back to cached token
                token = cache.get("token")
        else:
            token = cache.get("token")

        account_uuid = cache.get("account_uuid")
        remaining_min = cache.get("_time_remaining", 0) / 60
        if remaining_min > 0:
            print_info(f"Using cached Opower token (expires in {remaining_min:.1f} minutes)")

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
        has_refresh_token = cache.get("_has_refresh_token", False)

        print(f"  Status: VALID (OAuth)")
        if expiry:
            utc_str = expiry.strftime('%Y-%m-%d %H:%M:%S UTC')
            local_time = expiry.astimezone()
            local_str = local_time.strftime('%H:%M:%S %Z')
            print(f"  Opower token expires: {utc_str} ({local_str})")
        else:
            print(f"  Opower token expires: Will refresh on next use")

        if has_refresh_token:
            print(f"  OAuth refresh token: Present (30-90 day lifetime)")
            print(f"  Auto-refresh: Enabled")
        else:
            print("  OAuth refresh token: Missing!")

        print(f"  Location: {CACHE_FILE.name}")
    else:
        if CACHE_FILE.exists():
            print("  Status: EXPIRED or INVALID")
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
