"""ComEd Opower API client for electricity usage and cost data.

This client authenticates with ComEd via Azure AD B2C and fetches actual
meter data from the Opower platform via GraphQL API.

Authentication requires MFA on first login. After initial authentication,
session cookies are cached and can be used to refresh the token without MFA.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from .models import OpowerUsageRead, OpowerCostRead, OpowerBillSummary, OpowerMetadata

logger = logging.getLogger("twc-collector.opower")

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


class OpowerAuthError(Exception):
    """Authentication error with ComEd Opower."""
    pass


class OpowerClient:
    """ComEd Opower API client.

    Handles authentication and data fetching from the Opower platform.
    Authentication state is cached in a JSON file to persist across restarts.

    Attributes:
        username: ComEd account email
        password: ComEd account password
        mfa_method: MFA method ('email' or 'sms')
        cache_path: Path to token cache file
    """

    # Essential cookies for token refresh (session persistence)
    ESSENTIAL_COOKIES = {
        '.AspNet.cookie', '.AspNet.cookieC1', '.AspNet.cookieC2',
        'ASP.NET_SessionId', 'ARRAffinity', 'ARRAffinitySameSite'
    }

    def __init__(
        self,
        username: str,
        password: str,
        mfa_method: str = "email",
        cache_path: Optional[Path] = None,
    ):
        """Initialize Opower client.

        Args:
            username: ComEd account email
            password: ComEd account password
            mfa_method: MFA method ('email' or 'sms')
            cache_path: Path to token cache file (default: .comed_opower_cache.json in project root)
        """
        self.username = username
        self.password = password
        self.mfa_method = mfa_method.lower()

        # Default cache path - check multiple locations
        if cache_path is None:
            # Docker: project root mounted at /app/project/
            docker_path = Path("/app/project/.comed_opower_cache.json")
            # Local development
            local_path = Path(".comed_opower_cache.json")

            if docker_path.exists():
                cache_path = docker_path
            elif local_path.exists():
                cache_path = local_path
            else:
                # Default to Docker path (will be checked periodically)
                cache_path = docker_path
        self.cache_path = cache_path

        # State
        self.client: Optional[httpx.AsyncClient] = None
        self.opower_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.account_uuid: Optional[str] = None
        self.utility_account_uuid: Optional[str] = None

        # B2C authentication state
        self._csrf_token: Optional[str] = None
        self._tx: Optional[str] = None
        self._display_email: Optional[str] = None
        self._display_phone: Optional[str] = None

        # MFA callback (set by caller to provide MFA code)
        self._mfa_callback: Optional[callable] = None

        # Track if we need initial MFA
        self._needs_mfa: bool = False
        self._mfa_pending: bool = False

        # Track when we last warned about token expiry (to avoid log spam)
        self._last_expiry_warning: Optional[datetime] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self):
        """Initialize HTTP client."""
        if self.client is None:
            self.client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers=DEFAULT_HEADERS,
            )

    async def close(self):
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None

    def set_mfa_callback(self, callback: callable):
        """Set callback function to provide MFA code.

        The callback will be called with (mfa_method, masked_destination)
        and should return the MFA code string.

        Example:
            def get_mfa_code(method, destination):
                return input(f"Enter {method} code sent to {destination}: ")
            client.set_mfa_callback(get_mfa_code)
        """
        self._mfa_callback = callback

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid token."""
        if not self.opower_token or not self.token_expiry:
            return False
        # Consider token valid if more than 2 minutes remaining
        return self.token_expiry > datetime.now(timezone.utc) + timedelta(minutes=2)

    @property
    def needs_mfa(self) -> bool:
        """Check if initial MFA authentication is needed."""
        return self._needs_mfa

    def _load_cache(self) -> bool:
        """Load cached token and session cookies.

        Returns:
            True if valid cache was loaded, False otherwise
        """
        # Check multiple possible cache locations
        possible_paths = [
            self.cache_path,
            Path("/app/project/.comed_opower_cache.json"),
            Path(".comed_opower_cache.json"),
        ]

        cache_path = None
        for path in possible_paths:
            if path.exists() and path.is_file():
                cache_path = path
                break

        if not cache_path:
            return False

        # Update self.cache_path if we found a different location
        self.cache_path = cache_path

        try:
            cache = json.loads(self.cache_path.read_text())
            expiry = datetime.fromisoformat(cache.get("expiry", ""))

            # Check if token is still valid
            now = datetime.now(timezone.utc)
            if expiry <= now + timedelta(minutes=2):
                expired_ago = now - expiry
                hours_ago = expired_ago.total_seconds() / 3600

                # Only show full warning once per hour to avoid log spam
                show_full_warning = (
                    self._last_expiry_warning is None or
                    (now - self._last_expiry_warning).total_seconds() >= 3600
                )

                if show_full_warning:
                    logger.warning("=" * 60)
                    logger.warning("OPOWER: TOKEN EXPIRED!")
                    logger.warning(f"  Token expired: {expiry.strftime('%Y-%m-%d %H:%M:%S')} UTC ({hours_ago:.1f} hours ago)")
                    logger.warning("  Meter data collection is STOPPED until re-authenticated.")
                    logger.warning("")
                    logger.warning("  To restore, run locally:")
                    logger.warning("    python scripts/comed_opower_setup.py")
                    logger.warning("")
                    logger.warning("  Then copy .comed_opower_cache.json to your server.")
                    logger.warning("  The collector will auto-detect within 30 seconds.")
                    logger.warning("=" * 60)
                    self._last_expiry_warning = now

                return False

            # Warn if token expires within 1 hour (only once per check cycle)
            time_remaining = expiry - now
            if time_remaining < timedelta(hours=1):
                # Only warn once about upcoming expiry
                if self._last_expiry_warning is None or (now - self._last_expiry_warning).total_seconds() >= 900:
                    minutes_remaining = time_remaining.total_seconds() / 60
                    logger.warning(f"OPOWER: Token expires in {minutes_remaining:.0f} minutes!")
                    logger.warning("  Consider refreshing soon: python scripts/comed_opower_setup.py")
                    self._last_expiry_warning = now

            self.opower_token = cache["token"]
            self.token_expiry = expiry
            self.account_uuid = cache.get("account_uuid")
            self.utility_account_uuid = cache.get("utility_account_uuid")

            # Restore session cookies
            cookies = cache.get("cookies", {})
            for name, cookie_data in cookies.items():
                self.client.cookies.set(
                    name,
                    cookie_data["value"],
                    domain=cookie_data.get("domain", ""),
                    path=cookie_data.get("path", "/"),
                )

            if cookies:
                logger.info(f"Restored {len(cookies)} session cookies from cache")

            # Reset expiry warning flag on successful load
            self._last_expiry_warning = None

            logger.info(f"OPOWER: Authenticated (token expires {expiry.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
            return True

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to load cache: {e}")
            return False

    def _save_cache(self):
        """Save token and session cookies to cache file."""
        # Only save essential cookies
        cookies = {}
        for cookie in self.client.cookies.jar:
            if cookie.name in self.ESSENTIAL_COOKIES and cookie.domain and 'comed.com' in cookie.domain:
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

        # Ensure parent directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, indent=2))
        logger.debug(f"Token cached to {self.cache_path}")

    async def authenticate(self, force_mfa: bool = False) -> bool:
        """Authenticate with ComEd and get Opower token.

        Args:
            force_mfa: Force new authentication with MFA even if cache is valid

        Returns:
            True if authenticated successfully, False if MFA is needed

        Raises:
            OpowerAuthError: If authentication fails
        """
        await self.connect()

        # Try to use cached token first
        if not force_mfa and self._load_cache():
            return True

        # Need to authenticate - start B2C flow
        logger.info("Starting ComEd authentication...")
        self._needs_mfa = True

        try:
            # Step 1-3: Load login page and submit credentials
            await self._step1_load_login_page()
            await self._step2_submit_credentials()
            await self._step3_confirm_credentials()

            # Step 4-6: MFA selection and send code
            await self._step4_select_mfa_method()
            await self._step5_confirm_mfa_selection()
            await self._step6_send_mfa_code()

            # Mark that we're waiting for MFA
            self._mfa_pending = True

            # If we have an MFA callback, use it
            if self._mfa_callback:
                destination = self._display_phone if self.mfa_method == "sms" else self._display_email
                mfa_code = self._mfa_callback(self.mfa_method, destination)
                if mfa_code:
                    return await self.complete_mfa(mfa_code)

            # Otherwise, caller needs to call complete_mfa() with the code
            logger.info(f"MFA code sent to {self.mfa_method}. Call complete_mfa() with the code.")
            return False

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise OpowerAuthError(f"Authentication failed: {e}")

    async def complete_mfa(self, mfa_code: str) -> bool:
        """Complete MFA verification and get token.

        Args:
            mfa_code: The MFA code received via email/SMS

        Returns:
            True if authentication completed successfully

        Raises:
            OpowerAuthError: If MFA verification fails
        """
        if not self._mfa_pending:
            raise OpowerAuthError("No MFA authentication in progress")

        try:
            # Step 7-9: Verify MFA and complete login
            await self._step7_verify_mfa_code(mfa_code)
            await self._step8_final_mfa_submission(mfa_code)
            await self._step9_complete_login()

            # Step 10: Get Opower token
            await self._step10_get_opower_token()

            # Get account info
            await self._get_customer_info()

            # Save to cache
            self._save_cache()

            self._needs_mfa = False
            self._mfa_pending = False
            logger.info("Authentication complete")
            return True

        except Exception as e:
            self._mfa_pending = False
            logger.error(f"MFA verification failed: {e}")
            raise OpowerAuthError(f"MFA verification failed: {e}")

    async def refresh_token(self) -> bool:
        """Refresh the Opower token using existing session cookies.

        This doesn't require MFA if session cookies are still valid.

        Returns:
            True if token refreshed successfully, False otherwise
        """
        await self.connect()

        try:
            # Keep session alive
            logger.debug("Refresh: Calling GetSession to keep session alive...")
            session_resp = await self.client.get(f"{COMED_SECURE_BASE}/api/Services/MyAccountService.svc/GetSession")
            logger.debug(f"Refresh: GetSession returned {session_resp.status_code}")

            if session_resp.status_code != 200:
                logger.warning(f"Refresh: GetSession failed with status {session_resp.status_code}")
                # Try to get token anyway - session might still be valid

            # Get new token
            logger.debug("Refresh: Calling GetOpowerToken...")
            await self._step10_get_opower_token()
            self._save_cache()
            logger.debug("Refresh: Token refreshed and cached successfully")
            return True

        except Exception as e:
            import traceback
            logger.warning(f"Token refresh failed: {type(e).__name__}: {e}")
            logger.debug(f"Token refresh traceback:\n{traceback.format_exc()}")
            return False

    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid token, refreshing if needed.

        Returns:
            True if authenticated, False if MFA is required
        """
        if self.is_authenticated:
            return True

        # Try to load from cache
        await self.connect()
        if self._load_cache() and self.is_authenticated:
            return True

        # Try to refresh token
        if await self.refresh_token():
            return True

        # Need full authentication with MFA
        return await self.authenticate()

    # =========================================================================
    # B2C Authentication Steps
    # =========================================================================

    def _extract_csrf_token(self, html: str) -> Optional[str]:
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

    def _extract_tx(self, html: str) -> Optional[str]:
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

    async def _step1_load_login_page(self):
        """Step 1: Load the B2C login page via ComEd's login flow."""
        logger.debug("Step 1: Loading login page...")

        resp = await self.client.get(f"{COMED_SECURE_BASE}/pages/login.aspx")
        html = resp.text

        self._csrf_token = self._extract_csrf_token(html)
        self._tx = self._extract_tx(html)

        if not self._csrf_token or not self._tx:
            raise OpowerAuthError("Failed to extract CSRF token or TX from login page")

        logger.debug(f"  CSRF: {self._csrf_token[:20]}... TX: {self._tx[:30]}...")

    async def _step2_submit_credentials(self):
        """Step 2: Submit username and password."""
        logger.debug("Step 2: Submitting credentials...")

        url = self._get_b2c_url("/SelfAsserted")
        data = {
            "request_type": "RESPONSE",
            "signInName": self.username,
            "password": self.password,
        }

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise OpowerAuthError(f"Credential submission failed: {resp.status_code}")

        try:
            result = resp.json()
            if result.get("status") != "200":
                raise OpowerAuthError(f"Credential error: {result}")
        except json.JSONDecodeError:
            if "error" in resp.text.lower():
                raise OpowerAuthError(f"Credential error: {resp.text[:200]}")

    async def _step3_confirm_credentials(self):
        """Step 3: Confirm credentials and get MFA selection page."""
        logger.debug("Step 3: Confirming credentials...")

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

        logger.debug(f"  MFA options: email={self._display_email}, phone={self._display_phone}")

    async def _step4_select_mfa_method(self):
        """Step 4: Select MFA method (email or SMS)."""
        logger.debug(f"Step 4: Selecting MFA method ({self.mfa_method})...")

        url = self._get_b2c_url("/SelfAsserted")

        if self.mfa_method == "sms" and self._display_phone:
            data = {
                "request_type": "RESPONSE",
                "mfaEnabledRadio": "Phone",
                "displayPhoneNumber": self._display_phone,
            }
        elif self._display_email:
            data = {
                "request_type": "RESPONSE",
                "mfaEnabledRadio": "Email",
                "displayEmailAddress": self._display_email,
            }
        else:
            raise OpowerAuthError("No MFA option available")

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())
        if resp.status_code != 200:
            raise OpowerAuthError(f"MFA selection failed: {resp.status_code}")

    async def _step5_confirm_mfa_selection(self):
        """Step 5: Confirm MFA selection."""
        logger.debug("Step 5: Confirming MFA selection...")

        url = self._get_b2c_url("/api/CombinedSigninAndSignup/confirmed")
        headers = {
            "X-CSRF-TOKEN": self._csrf_token or "",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = await self.client.get(url, headers=headers)

        new_csrf = self._extract_csrf_token(resp.text)
        if new_csrf:
            self._csrf_token = new_csrf

    async def _step6_send_mfa_code(self):
        """Step 6: Request MFA code to be sent."""
        logger.debug("Step 6: Requesting MFA code...")

        if self.mfa_method == "sms":
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/phoneVerificationControl/SendCode"
            data = {"request_type": "RESPONSE", "displayPhoneNumber": self._display_phone}
        else:
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/SendCode"
            data = {"request_type": "RESPONSE", "displayEmailAddress": self._display_email}

        url = self._get_b2c_url(endpoint)
        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise OpowerAuthError(f"Failed to send MFA code: {resp.status_code}")

        logger.info(f"MFA code sent via {self.mfa_method}")

    async def _step7_verify_mfa_code(self, code: str):
        """Step 7: Verify the MFA code."""
        logger.debug(f"Step 7: Verifying MFA code...")

        if self.mfa_method == "sms":
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/phoneVerificationControl/VerifyCode"
            data = {
                "request_type": "RESPONSE",
                "displayPhoneNumber": self._display_phone,
                "verificationCode": code,
            }
        else:
            endpoint = "/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/VerifyCode"
            data = {
                "request_type": "RESPONSE",
                "displayEmailAddress": self._display_email,
                "verificationCode": code,
            }

        url = self._get_b2c_url(endpoint)
        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())

        if resp.status_code != 200:
            raise OpowerAuthError(f"MFA verification failed: {resp.status_code}")

    async def _step8_final_mfa_submission(self, code: str):
        """Step 8: Final MFA submission to complete authentication."""
        logger.debug("Step 8: Final MFA submission...")

        url = self._get_b2c_url("/SelfAsserted")

        if self.mfa_method == "sms":
            data = {
                "request_type": "RESPONSE",
                "displayPhoneNumber": self._display_phone,
                "verificationCode": code,
                "extension_isMFAEnabled": "True",
            }
        else:
            data = {
                "request_type": "RESPONSE",
                "displayEmailAddress": self._display_email,
                "verificationCode": code,
                "extension_isMFAEnabled": "True",
            }

        resp = await self.client.post(url, data=data, headers=self._get_ajax_headers())
        if resp.status_code != 200:
            raise OpowerAuthError(f"Final MFA submission failed: {resp.status_code}")

        # Update CSRF from cookies
        for cookie in self.client.cookies.jar:
            if cookie.name == "x-ms-cpim-csrf":
                self._csrf_token = cookie.value
                break

    async def _step9_complete_login(self):
        """Step 9: Complete login via confirmed endpoint and OAuth redirect."""
        logger.debug("Step 9: Completing login...")

        tx_value = self._tx if self._tx.startswith("StateProperties=") else f"StateProperties={self._tx}"
        params = {"csrf_token": self._csrf_token, "tx": tx_value, "p": B2C_POLICY}
        confirmed_url = f"{B2C_BASE}/api/SelfAsserted/confirmed?{urlencode(params)}"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }

        await self.client.get(confirmed_url, headers=headers, timeout=60.0)

        # Verify we got the auth cookie
        has_auth_cookie = any(".AspNet.cookie" in c.name for c in self.client.cookies.jar)
        if not has_auth_cookie:
            logger.warning("Auth cookie not found after login")

    async def _step10_get_opower_token(self):
        """Step 10: Get Opower bearer token."""
        logger.debug("Step 10: Getting Opower token...")

        url = f"{COMED_SECURE_BASE}/api/Services/OpowerService.svc/GetOpowerToken"
        headers = {"Content-Type": "application/json; charset=UTF-8"}

        resp = await self.client.post(url, json={}, headers=headers, timeout=60.0)

        if resp.status_code != 200:
            raise OpowerAuthError(f"Failed to get Opower token: {resp.status_code}")

        result = resp.json()
        token = result.get("d") or result.get("token") or result.get("access_token")
        if not token:
            raise OpowerAuthError(f"No token in response: {result}")

        self.opower_token = f"Bearer {token}" if not token.startswith("Bearer") else token

        # Decode token expiry
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                exp = payload.get("exp")
                if exp:
                    self.token_expiry = datetime.fromtimestamp(exp, tz=timezone.utc)
                    logger.debug(f"  Token expires: {self.token_expiry}")
        except Exception:
            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=20)

    async def _get_customer_info(self):
        """Get customer info from Opower API."""
        url = f"{OPOWER_BASE}/ei/edge/apis/multi-account-v1/cws/cec/customers/current"
        headers = {"Authorization": self.opower_token}

        resp = await self.client.get(url, headers=headers)

        if resp.status_code != 200:
            raise OpowerAuthError(f"Failed to get customer info: {resp.status_code}")

        data = resp.json()
        self.account_uuid = data.get("uuid")

        utility_accounts = data.get("utilityAccounts", [])
        if utility_accounts:
            self.utility_account_uuid = utility_accounts[0].get("uuid")

        logger.debug(f"  Account UUID: {self.account_uuid}")
        logger.debug(f"  Utility Account UUID: {self.utility_account_uuid}")

    # =========================================================================
    # GraphQL API Methods
    # =========================================================================

    async def _graphql_query(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query against Opower API."""
        if not await self.ensure_authenticated():
            raise OpowerAuthError("Not authenticated")

        url = f"{OPOWER_BASE}/ei/edge/apis/dsm-graphql-v1/cws/graphql"

        headers = {
            "Authorization": self.opower_token,
            "Content-Type": "application/json",
            "opower-selected-entities": f'["urn:opower:customer:uuid:{self.account_uuid}"]',
        }

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = await self.client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise OpowerAuthError(f"GraphQL query failed: {resp.status_code}")

        return resp.json()

    def _format_time_interval(self, start: datetime, end: datetime) -> str:
        """Format time interval as ISO 8601 interval."""
        tz_offset = "-06:00"  # Chicago timezone
        return f"{start.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}/{end.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}"

    async def get_usage_data(
        self,
        start_date: datetime,
        end_date: datetime,
        resolution: str = "DAY"
    ) -> List[OpowerUsageRead]:
        """Get energy usage data.

        Args:
            start_date: Start of date range
            end_date: End of date range
            resolution: "DAY", "HOUR", or "HALF_HOUR"

        Returns:
            List of OpowerUsageRead objects
        """
        query = """
        query GetUsageReads($timeInterval: TimeInterval, $resolution: ReadResolution, $saUuid: String) {
          billingAccountByAuthContext(forceLegacyData: true) {
            serviceAgreementsConnection(onlyActive: true, matching: $saUuid) {
              edges {
                node {
                  servicePointsConnection {
                    edges {
                      node {
                        readStreams(timeInterval: $timeInterval, readResolution: $resolution) {
                          netUsage {
                            unit
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

        variables = {
            "resolution": resolution,
            "timeInterval": self._format_time_interval(start_date, end_date),
            "saUuid": self.utility_account_uuid,
        }

        result = await self._graphql_query(query, variables)

        # Parse response - use safe navigation
        reads = []
        try:
            data = result.get("data") or {}
            billing = data.get("billingAccountByAuthContext") or {}
            sa_conn = billing.get("serviceAgreementsConnection") or {}
            sa_edges = sa_conn.get("edges") or []
            sa_node = sa_edges[0].get("node") if sa_edges else {} or {}
            sp_conn = sa_node.get("servicePointsConnection") or {}
            sp_edges = sp_conn.get("edges") or []
            sp_node = sp_edges[0].get("node") if sp_edges else {} or {}
            read_streams = sp_node.get("readStreams") or {}
            net_usage = read_streams.get("netUsage") or []
            raw_reads = net_usage[0].get("reads") if net_usage else [] or []

            for read in raw_reads:
                interval = read.get("timeInterval", "")
                measured = read.get("measuredAmount") or {}
                kwh = measured.get("value", 0) or 0

                # Parse timestamp
                timestamp = None
                if interval:
                    try:
                        # Format: "2025-12-16T00:00:00-06:00/2025-12-17T00:00:00-06:00"
                        start_str = interval.split("/")[0]
                        timestamp = datetime.fromisoformat(start_str)
                    except (ValueError, IndexError):
                        pass

                if timestamp:
                    reads.append(OpowerUsageRead(
                        timestamp=timestamp,
                        kwh=kwh,
                        resolution=resolution,
                    ))

        except (KeyError, IndexError, TypeError, AttributeError) as e:
            logger.warning(f"Error parsing usage data: {e}")

        return reads

    async def get_cost_data(
        self,
        start_date: datetime,
        end_date: datetime,
        resolution: str = "DAY"
    ) -> List[OpowerCostRead]:
        """Get energy cost data.

        Args:
            start_date: Start of date range
            end_date: End of date range
            resolution: "DAY" or "HOUR"

        Returns:
            List of OpowerCostRead objects
        """
        query = """
        query WDB_GetCostReadsForDayAndHour($timeInterval: TimeInterval, $resolution: ReadResolution, $saUuid: String) {
          billingAccountByAuthContext(forceLegacyData: true) {
            serviceAgreementsConnection(onlyActive: true, matching: $saUuid) {
              edges {
                node {
                  ratePlan { code }
                  servicePointsConnection {
                    edges {
                      node {
                        readStreams(timeInterval: $timeInterval, readResolution: $resolution) {
                          netUsage {
                            unit
                            reads {
                              timeInterval
                              measuredAmount { value }
                              monetaryAmount { value currency }
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

        variables = {
            "resolution": resolution,
            "timeInterval": self._format_time_interval(start_date, end_date),
            "saUuid": self.utility_account_uuid,
        }

        result = await self._graphql_query(query, variables)

        # Parse response - use safe navigation
        reads = []
        try:
            data = result.get("data") or {}
            billing = data.get("billingAccountByAuthContext") or {}
            sa_conn = billing.get("serviceAgreementsConnection") or {}
            sa_edges = sa_conn.get("edges") or []
            sa_node = sa_edges[0].get("node") if sa_edges else {} or {}
            sp_conn = sa_node.get("servicePointsConnection") or {}
            sp_edges = sp_conn.get("edges") or []
            sp_node = sp_edges[0].get("node") if sp_edges else {} or {}
            read_streams = sp_node.get("readStreams") or {}
            net_usage = read_streams.get("netUsage") or []
            raw_reads = net_usage[0].get("reads") if net_usage else [] or []

            for read in raw_reads:
                interval = read.get("timeInterval", "")
                measured = read.get("measuredAmount") or {}
                monetary = read.get("monetaryAmount") or {}
                kwh = measured.get("value", 0) or 0
                cost = monetary.get("value", 0) or 0

                timestamp = None
                if interval:
                    try:
                        start_str = interval.split("/")[0]
                        timestamp = datetime.fromisoformat(start_str)
                    except (ValueError, IndexError):
                        pass

                if timestamp:
                    reads.append(OpowerCostRead(
                        timestamp=timestamp,
                        kwh=kwh,
                        cost_dollars=cost,
                        resolution=resolution,
                    ))

        except (KeyError, IndexError, TypeError, AttributeError) as e:
            logger.warning(f"Error parsing cost data: {e}")

        return reads

    async def get_metadata(self) -> Optional[OpowerMetadata]:
        """Get account metadata including rate plan and data resolution."""
        query = """
        query WDB_GetMetadata($forceLegacyData: Boolean, $first: Int, $lastForServicePoints: Int, $aliased: Boolean) {
          billingAccountByAuthContext(forceLegacyData: $forceLegacyData) {
            customerClass
            uuid
            serviceAgreementsConnection(first: $first, onlyActive: true, aliased: $aliased) {
              edges {
                node {
                  uuid
                  serviceType
                  ratePlan { code }
                  servicePointsConnection(last: $lastForServicePoints) {
                    edges {
                      node {
                        uuid
                        premise {
                          timeZone
                        }
                        registers {
                          readResolution
                          availableReadsTimeInterval
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

        variables = {
            "first": 75,
            "lastForServicePoints": 50,
            "aliased": False,
            "forceLegacyData": True,
        }

        result = await self._graphql_query(query, variables)

        try:
            data = result.get("data") or {}
            billing = data.get("billingAccountByAuthContext") or {}
            sa_conn = billing.get("serviceAgreementsConnection") or {}
            sa_edges = sa_conn.get("edges") or []
            sa = sa_edges[0].get("node") if sa_edges else {} or {}

            rate_plan_obj = sa.get("ratePlan") or {}
            rate_plan = rate_plan_obj.get("code")

            sp_conn = sa.get("servicePointsConnection") or {}
            sp_edges = sp_conn.get("edges") or []
            sp = sp_edges[0].get("node") if sp_edges else {} or {}

            registers_list = sp.get("registers") or []
            registers = registers_list[0] if registers_list else {}
            premise = sp.get("premise") or {}

            return OpowerMetadata(
                rate_plan=rate_plan,
                read_resolution=registers.get("readResolution"),
                available_data_range=registers.get("availableReadsTimeInterval"),
                timezone=premise.get("timeZone"),
            )

        except (KeyError, IndexError, TypeError, AttributeError) as e:
            logger.warning(f"Error parsing metadata: {e}")
            return None

    async def get_bill_history(self, months: int = 12) -> List[OpowerBillSummary]:
        """Get billing history.

        Args:
            months: Number of months of history to fetch

        Returns:
            List of OpowerBillSummary objects
        """
        query = """
        query WDB_GetCostUsageReadsForBills($last: Int, $timeInterval: TimeInterval) {
          billingAccountByAuthContext(forceLegacyData: true) {
            bills(last: $last, during: $timeInterval, orderBy: ASCENDING) {
              timeInterval
              segments {
                usageInterval
                estimated
                usageCharges { value }
                currentAmount { value }
                serviceQuantities {
                  unit
                  serviceQuantity { value }
                }
              }
            }
          }
        }
        """

        end_date = datetime.now()
        start_date = end_date - timedelta(days=months * 30)

        variables = {
            "last": months,
            "timeInterval": self._format_time_interval(start_date, end_date),
        }

        result = await self._graphql_query(query, variables)

        bills = []
        try:
            data = result.get("data") or {}
            billing = data.get("billingAccountByAuthContext") or {}
            raw_bills = billing.get("bills") or []

            for bill in raw_bills:
                segments = bill.get("segments") or []
                if not segments:
                    continue

                segment = segments[0] if segments else {}

                # Parse usage interval
                interval = segment.get("usageInterval", "")
                bill_date = None
                if interval:
                    try:
                        start_str = interval.split("/")[0]
                        bill_date = datetime.fromisoformat(start_str)
                    except (ValueError, IndexError):
                        pass

                # Get total kWh from service quantities
                total_kwh = 0
                service_quantities = segment.get("serviceQuantities") or []
                for sq in service_quantities:
                    if sq.get("unit") == "KWH":
                        sq_obj = sq.get("serviceQuantity") or {}
                        total_kwh = sq_obj.get("value", 0) or 0
                        break

                current_amount = segment.get("currentAmount") or {}
                usage_charges = segment.get("usageCharges") or {}

                bills.append(OpowerBillSummary(
                    bill_date=bill_date,
                    total_kwh=total_kwh,
                    total_cost_dollars=current_amount.get("value", 0) or 0,
                    usage_charges_dollars=usage_charges.get("value", 0) or 0,
                    is_estimated=segment.get("estimated", False),
                ))

        except (KeyError, IndexError, TypeError, AttributeError) as e:
            logger.warning(f"Error parsing bill history: {e}")

        return bills
