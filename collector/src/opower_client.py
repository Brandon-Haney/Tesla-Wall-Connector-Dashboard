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

# Azure AD B2C endpoints (Mobile OAuth flow)
# The mobile OAuth flow uses refresh tokens that last 30-90 days,
# unlike the web session flow which expires after ~6 hours.
B2C_MOBILE_POLICY = "B2C_1A_SignIn_Mobile"

# ComEd Mobile OAuth configuration
# These values are from the ComEd mobile app (same as tronikos/opower library)
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


class OpowerAuthError(Exception):
    """Authentication error with ComEd Opower."""
    pass


class OpowerClient:
    """ComEd Opower API client.

    Handles authentication and data fetching from the Opower platform.
    Uses mobile OAuth flow (B2C_1A_SignIn_Mobile) with refresh tokens that
    last 30-90 days, instead of web session cookies that expire after ~6 hours.

    Authentication state is cached in a JSON file to persist across restarts.

    Attributes:
        username: ComEd account email
        password: ComEd account password
        mfa_method: MFA method ('email' or 'sms')
        cache_path: Path to token cache file
    """

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

        # OAuth token storage (mobile OAuth flow)
        self._refresh_token: Optional[str] = None
        self._base_url: Optional[str] = None  # Azure AD B2C endpoint (changes after login)
        self._account: Optional[Dict] = None  # Account info from ComEd API

        # PKCE code verifier/challenge (generated per auth attempt)
        self._code_verifier: Optional[str] = None
        self._code_challenge: Optional[str] = None

        # B2C authentication state (for MFA flow)
        self._settings: Dict = {}  # SETTINGS from B2C page (contains csrf, transId, etc.)
        self._display_email: Optional[str] = None
        self._display_phone: Optional[str] = None

        # MFA callback (set by caller to provide MFA code)
        self._mfa_callback: Optional[callable] = None

        # Track if we need initial MFA
        self._needs_mfa: bool = False
        self._mfa_pending: bool = False

        # Track when we last warned about token expiry (to avoid log spam)
        self._last_expiry_warning: Optional[datetime] = None

        # Diagnostic tracking
        self._cache_loaded_at: Optional[datetime] = None
        self._last_refresh_attempt: Optional[datetime] = None
        self._last_refresh_success: Optional[datetime] = None
        self._refresh_attempt_count: int = 0
        self._refresh_success_count: int = 0
        self._refresh_failure_count: int = 0

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

    def _generate_pkce(self) -> Tuple[str, str]:
        """Generate PKCE code verifier and challenge.

        Returns:
            (code_verifier, code_challenge) tuple
        """
        # Generate random verifier (43-128 characters)
        code_verifier = secrets.token_urlsafe(32)
        # Create S256 challenge
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return code_verifier, code_challenge

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
        """Load cached OAuth tokens.

        The new cache format uses OAuth refresh_token instead of session cookies.
        Old cache format (with "cookies" key) is detected and rejected.

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
            # Log cache file age
            cache_mtime = datetime.fromtimestamp(self.cache_path.stat().st_mtime, tz=timezone.utc)
            cache_age = datetime.now(timezone.utc) - cache_mtime
            cache_age_hours = cache_age.total_seconds() / 3600
            logger.info(f"OPOWER: Cache file age: {cache_age_hours:.1f} hours (modified: {cache_mtime.strftime('%Y-%m-%d %H:%M:%S')} UTC)")

            cache = json.loads(self.cache_path.read_text())

            # Detect old cache format (cookie-based, ~6 hour expiry)
            if "cookies" in cache and "refresh_token" not in cache:
                logger.warning("=" * 60)
                logger.warning("OPOWER: OLD CACHE FORMAT DETECTED")
                logger.warning("  The cache uses the old web session format that expires after ~6 hours.")
                logger.warning("  The new OAuth format uses refresh tokens that last 30-90 days.")
                logger.warning("")
                logger.warning("  To migrate, run locally:")
                logger.warning("    python scripts/comed_opower_setup.py --force")
                logger.warning("")
                logger.warning("  Then copy .comed_opower_cache.json to your server.")
                logger.warning("=" * 60)
                # Delete the old cache file to force re-auth
                self.cache_path.unlink()
                logger.info("OPOWER: Deleted old cache file to force re-authentication")
                return False

            # Load OAuth tokens (new format)
            refresh_token = cache.get("refresh_token")
            if not refresh_token:
                logger.warning("OPOWER: Cache missing refresh_token - re-authentication required")
                return False

            self._refresh_token = refresh_token
            self._base_url = cache.get("base_url")
            self._account = cache.get("account")
            self.opower_token = cache.get("token")
            self.account_uuid = cache.get("account_uuid")
            self.utility_account_uuid = cache.get("utility_account_uuid")

            # Parse token expiry
            expiry_str = cache.get("expiry")
            if expiry_str:
                self.token_expiry = datetime.fromisoformat(expiry_str)
            else:
                self.token_expiry = None

            # Check if Opower token is still valid (refresh tokens last much longer)
            now = datetime.now(timezone.utc)
            if self.token_expiry and self.token_expiry <= now + timedelta(minutes=2):
                # Opower token expired, but we can refresh it using refresh_token
                logger.info("OPOWER: Opower token expired, will refresh using OAuth refresh_token")
                # Don't return False - let the caller refresh the token

            # Track when we loaded the cache
            self._cache_loaded_at = datetime.now(timezone.utc)

            # Reset expiry warning flag on successful load
            self._last_expiry_warning = None

            # Log status
            if self.token_expiry:
                utc_str = self.token_expiry.strftime('%Y-%m-%d %H:%M:%S UTC')
                local_str = self.token_expiry.astimezone().strftime('%H:%M:%S %Z')
                logger.info(f"OPOWER: Cache loaded (token expires {utc_str} / {local_str})")
            else:
                logger.info("OPOWER: Cache loaded (refresh_token available, will get new token)")

            return True

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"OPOWER: Failed to load cache: {e}")
            return False

    def _save_cache(self):
        """Save OAuth tokens to cache file.

        The new format stores OAuth refresh_token instead of session cookies.
        This allows tokens to be refreshed for 30-90 days without re-authentication.
        """
        cache = {
            # OAuth tokens (new format)
            "refresh_token": self._refresh_token,
            "base_url": self._base_url,
            "account": self._account,
            # Opower token (short-lived, ~20 min)
            "token": self.opower_token,
            "expiry": self.token_expiry.isoformat() if self.token_expiry else None,
            # Account info
            "account_uuid": self.account_uuid,
            "utility_account_uuid": self.utility_account_uuid,
        }

        # Ensure parent directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, indent=2))
        logger.debug(f"OPOWER: OAuth tokens cached to {self.cache_path}")

    async def authenticate(self, force_mfa: bool = False) -> bool:
        """Authenticate with ComEd using mobile OAuth flow.

        Uses the mobile OAuth flow (B2C_1A_SignIn_Mobile) which provides
        refresh tokens that last 30-90 days instead of ~6 hour sessions.

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
            # If we have a refresh token but expired Opower token, refresh it
            if self._refresh_token and (not self.opower_token or not self.is_authenticated):
                logger.info("OPOWER: Refreshing expired token from cache...")
                if await self.refresh_token():
                    return True
            elif self.is_authenticated:
                return True

        # Need to authenticate - start mobile OAuth flow
        logger.info("Starting ComEd mobile OAuth authentication...")
        self._needs_mfa = True

        try:
            # Step 1: Load login page and initialize mobile OAuth with PKCE
            await self._step1_load_login_and_mobile_oauth()

            # Step 2: Submit credentials
            await self._step2_submit_credentials()

            # Step 3: Confirm and get MFA options
            await self._step3_confirm_and_get_mfa_options()

            # Step 4: Select MFA method and send code
            await self._step4_select_mfa_and_send_code()

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
        """Complete MFA verification and get OAuth tokens.

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
            # Step 5: Verify MFA code
            await self._step5_verify_mfa_code(mfa_code)

            # Step 6: Get authorization code from redirect
            auth_code = await self._step6_get_authorization_code()

            # Step 7: Exchange code for OAuth tokens (including refresh_token)
            await self._step7_exchange_code_for_tokens(auth_code)

            # Step 8: Get account information
            await self._step8_get_account_info()

            # Step 9: Get Opower access token
            await self._step9_get_opower_token()

            # Get customer info (account_uuid, utility_account_uuid)
            await self._get_customer_info()

            # Save OAuth tokens to cache
            self._save_cache()

            self._needs_mfa = False
            self._mfa_pending = False
            logger.info("Authentication complete (OAuth tokens saved)")
            return True

        except Exception as e:
            self._mfa_pending = False
            logger.error(f"MFA verification failed: {e}")
            raise OpowerAuthError(f"MFA verification failed: {e}")

    async def _post_oauth_token(self, data: Dict) -> Dict:
        """POST to the OAuth token endpoint.

        Args:
            data: Form data for the token request

        Returns:
            JSON response from the token endpoint

        Raises:
            OpowerAuthError: If the request fails
        """
        if not self._base_url:
            raise OpowerAuthError("No OAuth base URL - authentication required")

        url = f"https://{self._base_url}/oauth2/v2.0/token"

        try:
            resp = await self.client.post(
                url,
                data=data,
                headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                timeout=30.0
            )

            if resp.status_code != 200:
                logger.error(f"OAuth token request failed: {resp.status_code}")
                logger.error(f"Response: {resp.text[:500] if resp.text else 'empty'}")
                raise OpowerAuthError(f"OAuth token request failed: {resp.status_code}")

            return resp.json()

        except httpx.RequestError as e:
            raise OpowerAuthError(f"OAuth token request error: {e}")

    async def _refresh_oauth_token(self) -> str:
        """Refresh the OAuth refresh token.

        This exchanges the current refresh_token for a new access_token
        and a new refresh_token (refresh tokens are rotated).

        Returns:
            The new access_token
        """
        logger.debug("Refreshing OAuth refresh_token...")

        result = await self._post_oauth_token({
            "grant_type": "refresh_token",
            "response_type": "token",
            "scope": f"openid offline_access {COMED_CLIENT_ID}",
            "client_id": COMED_CLIENT_ID,
            "refresh_token": self._refresh_token,
        })

        # Update the refresh token (it rotates with each use)
        new_refresh_token = result.get("refresh_token", "")
        if new_refresh_token:
            self._refresh_token = new_refresh_token
            logger.debug("OAuth refresh_token rotated")

        access_token = result.get("access_token", "")
        if not access_token:
            raise OpowerAuthError("No access_token in OAuth response")

        return access_token

    async def _refresh_opower_token(self) -> str:
        """Get a new Opower access token using the OAuth refresh token.

        This requests an Opower-scoped access token that can be used
        to query the Opower GraphQL API.

        Returns:
            The Opower access token
        """
        logger.debug("Getting Opower token via OAuth...")

        # Get account number for nonce (required by ComEd API)
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

        access_token = result.get("access_token", "")
        if not access_token:
            raise OpowerAuthError("No access_token in Opower OAuth response")

        return access_token

    async def refresh_token(self) -> bool:
        """Refresh the Opower token using OAuth refresh_token.

        This uses the mobile OAuth flow which doesn't depend on server-side
        session state. The refresh_token lasts 30-90 days and is rotated
        with each use.

        Returns:
            True if token refreshed successfully, False otherwise
        """
        await self.connect()

        try:
            # Diagnostic: Track refresh attempt
            now = datetime.now(timezone.utc)
            self._refresh_attempt_count += 1
            self._last_refresh_attempt = now

            # Log timing diagnostics
            if self._cache_loaded_at:
                time_since_cache_load = (now - self._cache_loaded_at).total_seconds() / 60
                logger.info(f"OPOWER: Time since cache load: {time_since_cache_load:.1f} min")
            if self._last_refresh_success:
                time_since_last_success = (now - self._last_refresh_success).total_seconds() / 60
                logger.info(f"OPOWER: Time since last successful refresh: {time_since_last_success:.1f} min")
            logger.info(f"OPOWER: Refresh stats - attempts: {self._refresh_attempt_count}, success: {self._refresh_success_count}, failures: {self._refresh_failure_count}")

            if not self._refresh_token:
                raise OpowerAuthError("No refresh_token available - authentication required")

            # Step 1: Refresh the OAuth token (also refreshes the refresh_token)
            logger.info("OPOWER: Refreshing OAuth tokens...")
            await self._refresh_oauth_token()

            # Step 2: Get a new Opower access token
            logger.info("OPOWER: Getting new Opower access token...")
            opower_token = await self._refresh_opower_token()

            # Update state
            self.opower_token = f"Bearer {opower_token}"

            # Decode token expiry from JWT
            try:
                parts = opower_token.split(".")
                if len(parts) >= 2:
                    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                    exp = payload.get("exp")
                    if exp:
                        self.token_expiry = datetime.fromtimestamp(exp, tz=timezone.utc)
            except Exception:
                # Default to 20 minutes if we can't decode
                self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=20)

            # Save updated tokens to cache
            self._save_cache()

            # Update diagnostics
            self._refresh_success_count += 1
            self._last_refresh_success = datetime.now(timezone.utc)

            if self.token_expiry:
                utc_str = self.token_expiry.strftime('%H:%M:%S UTC')
                local_str = self.token_expiry.astimezone().strftime('%H:%M:%S %Z')
                logger.info(f"OPOWER: Token refresh SUCCESS (expires {utc_str} / {local_str})")
            else:
                logger.info("OPOWER: Token refresh SUCCESS")

            return True

        except Exception as e:
            import traceback
            self._refresh_failure_count += 1
            logger.error(f"OPOWER: Token refresh FAILED: {type(e).__name__}: {e}")
            logger.error(f"OPOWER: Failure stats - attempt #{self._refresh_attempt_count}, failures: {self._refresh_failure_count}")
            if self._cache_loaded_at:
                time_since_cache = (datetime.now(timezone.utc) - self._cache_loaded_at).total_seconds() / 60
                logger.error(f"OPOWER: Time since cache load: {time_since_cache:.1f} min")
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

    def _load_javascript_var(self, html: str, var_name: str) -> Optional[Dict]:
        """Extract JSON from a JavaScript variable in HTML.

        Args:
            html: The HTML content
            var_name: The JavaScript variable name (e.g., 'SETTINGS', 'SA_FIELDS')

        Returns:
            Parsed JSON dict or None if not found
        """
        match = re.search(r"var " + var_name + r" = ({.*?});", html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _extract_mfa_options_from_sa_fields(self, sa_fields: Dict) -> Dict[str, str]:
        """Extract MFA options from SA_FIELDS.

        Args:
            sa_fields: The SA_FIELDS dict from B2C page

        Returns:
            Dict with 'email' and/or 'phone' keys
        """
        options = {}
        for field in sa_fields.get("AttributeFields", []):
            field_id = field.get("ID", "")
            if field_id == "displayEmailAddress":
                options["email"] = field.get("PRE", "")
            elif field_id == "displayPhoneNumber":
                options["phone"] = field.get("PRE", "")
            elif field_id == "emailVerificationControl":
                # Forced MFA flow - email is nested
                for display in field.get("DISPLAY_FIELDS", []):
                    if display.get("ID") == "displayEmailAddress":
                        options["email"] = display.get("PRE", "")
        return options

    def _get_ajax_headers(self) -> Dict:
        """Get headers for AJAX requests to B2C endpoints."""
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-CSRF-TOKEN": self._settings.get("csrf", ""),
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": DEFAULT_HEADERS["User-Agent"],
        }

    async def _b2c_get(self, path: str, allow_redirects: bool = True) -> Tuple[str, str, Optional[str]]:
        """Make a GET request to B2C API endpoint.

        Args:
            path: API path (e.g., 'confirmed')
            allow_redirects: Whether to follow redirects

        Returns:
            (response_text, final_path, final_host)
        """
        if not self._base_url:
            raise OpowerAuthError("No B2C base URL set")

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
            raise OpowerAuthError(f"B2C GET failed: {resp.status_code}")

        # Update settings from response
        if allow_redirects:
            new_settings = self._load_javascript_var(resp.text, "SETTINGS")
            if new_settings:
                self._settings = new_settings

        # Return redirect location for non-redirect requests
        final_host = str(resp.url.host) if resp.url else None
        return resp.text, str(resp.url.path), final_host

    async def _b2c_post(self, path: str, data: Dict, error_msg: str = "") -> Dict:
        """Make a POST request to B2C API endpoint.

        Args:
            path: API path or empty string for base SelfAsserted
            data: Form data to post
            error_msg: Error context for logging

        Returns:
            JSON response
        """
        if not self._base_url:
            raise OpowerAuthError("No B2C base URL set")

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
            raise OpowerAuthError(f"B2C POST {error_msg} failed: {resp.status_code}")

        try:
            result = resp.json()
            if result.get("status") != "200":
                raise OpowerAuthError(f"B2C POST {error_msg}: {result.get('message', result)}")
            return result
        except json.JSONDecodeError:
            if "error" in resp.text.lower():
                raise OpowerAuthError(f"B2C POST {error_msg}: {resp.text[:200]}")
            return {}

    async def _step1_load_login_and_mobile_oauth(self):
        """Step 1: Load login page and initialize mobile OAuth flow with PKCE.

        This visits the ComEd login page to get the Azure AD B2C authorize endpoint,
        then starts the mobile OAuth flow with PKCE code challenge.
        """
        logger.debug("Step 1: Loading login page and initializing mobile OAuth...")

        # Generate PKCE verifier and challenge
        self._code_verifier, self._code_challenge = self._generate_pkce()

        # Visit login page to get redirected to B2C authorize endpoint
        resp = await self.client.get(
            f"https://{COMED_LOGIN_DOMAIN}/Pages/Login.aspx?/login",
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
        )

        # Extract SETTINGS from the response
        settings = self._load_javascript_var(resp.text, "SETTINGS")
        if not settings:
            raise OpowerAuthError("Failed to extract SETTINGS from login page")

        # Check if we got redirected to authorize endpoint
        final_path = str(resp.url.path) if resp.url else ""
        if not final_path.endswith("/authorize"):
            raise OpowerAuthError(f"Expected authorize endpoint, got: {final_path}")

        # Get the B2C base URL from the redirect
        login_host = str(resp.url.host) if resp.url else ""
        tenant = settings.get("hosts", {}).get("tenant", "")
        policy = settings.get("hosts", {}).get("policy", "")

        # Build mobile OAuth base URL (replace web policy with mobile policy)
        self._base_url = login_host + tenant
        self._base_url = self._base_url.replace(policy, B2C_MOBILE_POLICY)

        logger.debug(f"  B2C base URL: {self._base_url}")

        # Now load the mobile OAuth page with PKCE challenge
        await self._load_mobile_oauth_page(login_host + final_path)

    async def _load_mobile_oauth_page(self, authorize_path: str):
        """Load the mobile OAuth authorize page with PKCE.

        Args:
            authorize_path: The authorize endpoint path (e.g., 'host/tenant/oauth2/v2.0/authorize')
        """
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

        url = f"https://{authorize_path}?{params}"

        resp = await self.client.get(
            url,
            headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
        )

        if resp.status_code != 200:
            raise OpowerAuthError(f"Failed to load mobile OAuth page: {resp.status_code}")

        # Extract SETTINGS
        settings = self._load_javascript_var(resp.text, "SETTINGS")
        if not settings:
            raise OpowerAuthError("Failed to extract SETTINGS from mobile OAuth page")

        # Mobile app uses SelfAsserted API
        settings["api"] = "SelfAsserted"
        self._settings = settings

        logger.debug(f"  Mobile OAuth initialized (csrf: {settings.get('csrf', '')[:20]}...)")

    async def _step2_submit_credentials(self):
        """Step 2: Submit username and password."""
        logger.debug("Step 2: Submitting credentials...")

        await self._b2c_post(
            "",
            {
                "request_type": "RESPONSE",
                "signInName": self.username,
                "password": self.password,
            },
            "credentials",
        )

    async def _step3_confirm_and_get_mfa_options(self) -> Dict[str, str]:
        """Step 3: Confirm credentials and get MFA options.

        Returns:
            Dict with 'email' and/or 'phone' keys
        """
        logger.debug("Step 3: Confirming credentials and getting MFA options...")

        html, _, _ = await self._b2c_get("confirmed")

        # Extract SA_FIELDS which contains MFA options
        sa_fields = self._load_javascript_var(html, "SA_FIELDS")
        if not sa_fields:
            raise OpowerAuthError("Failed to get MFA options (no SA_FIELDS)")

        mfa_options = self._extract_mfa_options_from_sa_fields(sa_fields)
        self._display_email = mfa_options.get("email")
        self._display_phone = mfa_options.get("phone")

        logger.debug(f"  MFA options: email={self._display_email}, phone={self._display_phone}")

        if not self._display_email and not self._display_phone:
            raise OpowerAuthError("No MFA options available")

        return mfa_options

    async def _step4_select_mfa_and_send_code(self):
        """Step 4: Select MFA method and send verification code."""
        logger.debug(f"Step 4: Selecting MFA method ({self.mfa_method}) and sending code...")

        # Select MFA method
        if self.mfa_method == "sms" and self._display_phone:
            mfa_selection = "Text"  # Mobile app uses "Text" not "Phone"
        else:
            mfa_selection = "Email"

        await self._b2c_post(
            "",
            {
                "displayEmailAddress": self._display_email or "",
                "displayPhoneNumber": self._display_phone or "",
                "mfaEnabledRadio": mfa_selection,
                "request_type": "RESPONSE",
            },
            "MFA selection",
        )

        # Confirm MFA selection
        await self._b2c_get("confirmed")

        # Send verification code
        if mfa_selection == "Text":
            verify_path = "DisplayControlAction/vbeta/textVerificationControl/SendCode"
            verify_data = {"displayPhoneNumber": self._display_phone}
        else:
            verify_path = "DisplayControlAction/vbeta/emailVerificationControl/SendCode"
            verify_data = {"displayEmailAddress": self._display_email}

        await self._b2c_post(verify_path, verify_data, "send MFA code")

        logger.info(f"MFA code sent via {self.mfa_method}")

    async def _step5_verify_mfa_code(self, code: str):
        """Step 5: Verify MFA code."""
        logger.debug("Step 5: Verifying MFA code...")

        if self.mfa_method == "sms" and self._display_phone:
            verify_path = "DisplayControlAction/vbeta/textVerificationControl/VerifyCode"
            verify_data = {
                "displayPhoneNumber": self._display_phone,
                "verificationCode": code,
            }
        else:
            verify_path = "DisplayControlAction/vbeta/emailVerificationControl/VerifyCode"
            verify_data = {
                "displayEmailAddress": self._display_email,
                "verificationCode": code,
            }

        await self._b2c_post(verify_path, verify_data, "verify MFA code")

        # Final submission with code
        verify_data["request_type"] = "RESPONSE"
        await self._b2c_post("", verify_data, "final MFA submission")

    async def _step6_get_authorization_code(self) -> str:
        """Step 6: Complete authentication and get authorization code.

        Returns:
            The authorization code from the redirect URI
        """
        logger.debug("Step 6: Getting authorization code...")

        # Request confirmed endpoint WITHOUT following redirects
        # The redirect URL will contain our authorization code
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

        # Get the redirect location which contains the authorization code
        location = resp.headers.get("Location", "")
        if not location or "code=" not in location:
            raise OpowerAuthError(f"No authorization code in redirect: {location[:100]}")

        # Extract code from redirect URI (format: msauth.com.comed.mobile://auth/?code=...)
        code_match = re.search(r"code=([^&]+)", location)
        if not code_match:
            raise OpowerAuthError("Failed to extract authorization code")

        auth_code = code_match.group(1)
        logger.debug(f"  Got authorization code: {auth_code[:20]}...")

        return auth_code

    async def _step7_exchange_code_for_tokens(self, auth_code: str):
        """Step 7: Exchange authorization code for OAuth tokens.

        Args:
            auth_code: The authorization code from the redirect URI
        """
        logger.debug("Step 7: Exchanging authorization code for tokens...")

        # Exchange code for tokens using PKCE
        result = await self._post_oauth_token({
            "grant_type": "authorization_code",
            "scope": f"openid offline_access {COMED_CLIENT_ID}",
            "client_id": COMED_CLIENT_ID,
            "code": auth_code,
            "code_verifier": self._code_verifier,
            "redirect_uri": COMED_MOBILE_ID,
        })

        # Store the refresh token (this is the long-lived token!)
        self._refresh_token = result.get("refresh_token", "")
        if not self._refresh_token:
            raise OpowerAuthError("No refresh_token in OAuth response")

        logger.debug("  Got OAuth tokens (refresh_token stored)")

    async def _step8_get_account_info(self):
        """Step 8: Get account information from ComEd API."""
        logger.debug("Step 8: Getting account information...")

        # First refresh to get a bearer token
        bearer_token = await self._refresh_oauth_token()

        # Get account list from ComEd API
        resp = await self.client.get(
            f"https://{COMED_EU_DOMAIN}/mobile/custom/auth/accounts",
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Authorization": f"Bearer {bearer_token}",
            },
        )

        if resp.status_code != 200:
            raise OpowerAuthError(f"Failed to get accounts: {resp.status_code}")

        result = resp.json()
        if not result.get("success"):
            raise OpowerAuthError(f"Failed to get accounts: {result}")

        # Find active account
        accounts = result.get("data", [])
        active_accounts = [a for a in accounts if a.get("status") == "Active"]

        if not active_accounts:
            raise OpowerAuthError("No active accounts found")

        if len(active_accounts) > 1:
            logger.info(f"Found {len(active_accounts)} active accounts, using first one")

        self._account = active_accounts[0]
        logger.debug(f"  Account: {self._account.get('accountNumber', 'unknown')}")

    async def _step9_get_opower_token(self):
        """Step 9: Get Opower access token."""
        logger.debug("Step 9: Getting Opower access token...")

        # Get Opower token using refresh token
        opower_token = await self._refresh_opower_token()

        self.opower_token = f"Bearer {opower_token}"

        # Decode token expiry from JWT
        try:
            parts = opower_token.split(".")
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
