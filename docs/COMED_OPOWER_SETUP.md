# ComEd Opower Integration Setup Guide

This guide walks you through setting up the ComEd Opower integration, which provides access to your actual electricity usage and costs from your smart meter.

## What This Provides

The Opower integration fetches **actual billed data** from ComEd, including:

- **Daily usage** - kWh consumed each day from your smart meter
- **Daily costs** - Actual billed costs including all fees, delivery charges, and taxes
- **Bill history** - Monthly bill summaries with charge breakdowns
- **Historical data** - Data back to June 2023 at 30-minute resolution

This allows you to:
- Compare EV charging costs vs total electricity costs
- See what percentage of your electricity goes to EV charging
- Validate dashboard cost calculations against actual bills
- Analyze historical usage patterns

## Prerequisites

1. **ComEd account** with online access at [secure.comed.com](https://secure.comed.com)
2. **Smart meter** installed (most ComEd customers have one)
3. **MFA enabled** on your ComEd account (required for authentication)
4. **Python 3.8+** installed locally (for running the setup script)

## Quick Start

1. Enable Opower in `.env`:
   ```
   OPOWER_ENABLED=true
   ```

2. Start the collector (it will wait for authentication):
   ```bash
   docker-compose up -d collector
   ```

3. Run the setup script locally:
   ```bash
   pip install httpx python-dotenv
   python scripts/comed_opower_setup.py
   ```

4. Enter your ComEd credentials and MFA code when prompted

5. **Done!** The collector auto-detects the cache file within 30 seconds and starts collecting data.

No restart needed - the collector watches for the cache file.

## How It Works

### Token Keep-Alive

When you complete the setup script with MFA, the collector caches your **session cookies** (not just the token). These cookies allow the collector to automatically request new tokens every 10 minutes, keeping your session alive indefinitely.

The collector:
- **Refreshes tokens every 10 minutes** to prevent expiry
- **Auto-detects the cache file** - no restart needed after running setup
- **Logs clear warnings** if the session is about to expire
- **Logs clear errors** with fix instructions if authentication fails

### Hot-Reload Detection

You don't need to restart the collector after running the setup script! The collector checks for the cache file every 30 seconds. When detected, you'll see:

```
============================================================
OPOWER: Cache file detected! Initializing...
============================================================
...
============================================================
OPOWER: Successfully initialized from cache file!
  Token refresh is now active (every 10 min)
  Data polling is now active (every hour)
============================================================
```

### Session Expiry Warnings

If the token refresh fails, you'll see clear error messages in the logs:

```
============================================================
OPOWER: TOKEN REFRESH FAILED!
Session will expire soon. To fix:
  1. Run locally: python scripts/comed_opower_setup.py --force
  2. Restart collector: docker-compose restart collector
============================================================
```

If the session fully expires:

```
============================================================
OPOWER: SESSION EXPIRED!
Meter data collection is now STOPPED.
To restore, run locally:
  python scripts/comed_opower_setup.py --force
Then restart: docker-compose restart collector
============================================================
```

## Authentication Methods

| Method | Pros | Cons |
|--------|------|------|
| **Username/Password** | Auto token refresh, hot-reload | Requires MFA on first run |
| **Bearer Token** | Simple, no MFA prompt | Expires in ~20 minutes, no auto-refresh |

### Option 1: Interactive Setup (Recommended)

This method runs an interactive script that prompts for your credentials and MFA code, then caches the session for automatic token refresh.

**Setup:**

1. Enable in `.env`:
   ```
   OPOWER_ENABLED=true
   ```

2. Start the collector:
   ```bash
   docker-compose up -d collector
   ```

3. Install Python dependencies locally (one-time):
   ```bash
   pip install httpx python-dotenv
   ```

4. Run the setup script **locally on your computer**:
   ```bash
   python scripts/comed_opower_setup.py
   ```

   The script will:
   - Prompt for your ComEd username and password
   - Send an MFA code to your email (or phone)
   - Prompt you to enter the code
   - Cache the session to `.comed_opower_cache.json` in the project root

5. **Done!** The collector auto-detects the cache file within 30 seconds.

**Why run locally instead of Docker?**

The setup script requires interactive input (credentials + MFA code). Running locally is simpler than Docker's interactive mode. The cache file is saved to the project root which Docker mounts as `/app/project/`, so the collector picks it up automatically.

### Option 2: Bearer Token (Manual)

If you prefer not to store your password, you can manually extract a bearer token from your browser. Note: This token expires in ~20 minutes and cannot be auto-refreshed.

1. Log into [secure.comed.com](https://secure.comed.com) in your browser
2. Navigate to "View My Usage"
3. Open DevTools (F12) → Network tab
4. Look for requests to `cec.opower.com`
5. Copy the `Authorization` header value (starts with `Bearer eyJ...`)
6. Add to `.secrets`:
   ```
   COMED_BEARER_TOKEN=Bearer eyJhbGciOiJSUzI1NiIsInR...
   ```

7. Enable in `.env`:
   ```
   OPOWER_ENABLED=true
   ```

**Note:** You'll need to repeat this every ~20 minutes. For persistent operation, use the username/password method.

## Running the Setup Script

The setup script handles initial authentication with MFA. **Run it locally** on your computer:

```bash
# Install dependencies (one-time)
pip install httpx python-dotenv

# Basic setup (uses email for MFA)
python scripts/comed_opower_setup.py

# Use SMS for MFA instead
python scripts/comed_opower_setup.py --mfa-method sms

# Force re-authentication (ignore cached session)
python scripts/comed_opower_setup.py --force

# Test that authentication works and fetch sample data
python scripts/comed_opower_setup.py --test

# Show current configuration status
python scripts/comed_opower_setup.py --status
```

## Configuration Reference

### .env Settings

```env
# Enable/disable Opower integration
OPOWER_ENABLED=true

# Data polling interval (seconds) - data updates daily, so hourly is sufficient
OPOWER_POLL_INTERVAL=3600

# Token refresh interval (seconds) - keeps session alive
OPOWER_TOKEN_REFRESH_INTERVAL=600

# MFA method when using username/password auth
OPOWER_MFA_METHOD=email  # or "sms"
```

### .secrets Settings (Optional)

The setup script prompts for credentials interactively, so you don't need to store them. However, if you prefer:

```env
# Optional: Pre-authenticated bearer token (expires in ~20 min)
# Only useful for quick testing
COMED_BEARER_TOKEN=Bearer eyJhbGciOi...
```

### Cache File

The setup script creates `.comed_opower_cache.json` in the project root. This file contains:
- Bearer token for Opower API calls
- Session cookies for token refresh
- Token expiry timestamp

This file is gitignored and should not be committed.

## Troubleshooting

### "Authentication failed: Credential submission failed"

- Verify your username and password are correct
- Try logging into [secure.comed.com](https://secure.comed.com) directly
- Ensure there are no extra spaces in your `.secrets` file

### "No MFA option available"

- Your ComEd account may not have MFA configured
- Log into ComEd's website and set up MFA in your account settings

### "Token refresh failed" in collector logs

The cached session has expired. Run the setup script again:
```bash
python scripts/comed_opower_setup.py --force
```
The collector will auto-detect the new cache file within 30 seconds.

### "Session expired" after a few days

ComEd sessions typically last 24-72 hours. The collector refreshes tokens every 10 minutes to prevent this, but if the collector was stopped during that time, the session may expire.

To restore:
```bash
python scripts/comed_opower_setup.py --force
```

### Collector not detecting cache file

1. Verify `OPOWER_ENABLED=true` in `.env`
2. Check that `.comed_opower_cache.json` exists in the project root
3. Check collector logs: `docker-compose logs -f collector | grep -i opower`

### Data not appearing in Grafana

1. Check collector logs for Opower errors:
   ```bash
   docker-compose logs collector | grep -i opower
   ```

2. Verify Opower is enabled:
   ```bash
   grep OPOWER_ENABLED .env
   ```

3. Check for recent data in InfluxDB:
   ```bash
   docker exec twc-influxdb influx query '
     from(bucket: "twc_dashboard")
     |> range(start: -7d)
     |> filter(fn: (r) => r._measurement == "comed_meter_usage")
     |> limit(n: 5)
   ' --org home --token $INFLUXDB_ADMIN_TOKEN
   ```

## Data Characteristics

- **Update frequency**: ComEd updates data once per day, typically overnight
- **Data delay**: 24-48 hours behind real-time
- **Resolution**: 30-minute intervals available (DAY, HOUR, HALF_HOUR)
- **Historical data**: Available back to June 2023
- **Rate plan**: Your rate plan (e.g., C-H70R for hourly pricing) is detected automatically

## How It Works

The integration uses ComEd's Opower platform, which provides the same data you see in the "View My Usage" section of the ComEd website.

**Authentication flow:**
1. Load ComEd login page (redirects to Azure AD B2C)
2. Submit credentials
3. Complete MFA verification
4. Get Opower bearer token
5. Cache session cookies for token refresh

**Runtime behavior:**
1. Collector checks for cache file every 30 seconds (when not authenticated)
2. When cache is found, initializes Opower and bootstraps historical data
3. Refreshes token every 10 minutes to keep session alive
4. Polls for new meter data every hour (configurable)

**Why not use the opower library?**

The popular [tronikos/opower](https://github.com/tronikos/opower) library uses ComEd's mobile app OAuth flow (`B2C_1A_SignIn_Mobile`), which ComEd has disabled. Our solution uses the web OAuth flow which works.

## Security Notes

- Credentials are stored in `.secrets` which is gitignored
- The cache file contains session cookies - treat it as sensitive
- Bearer tokens expire and cannot be used to change your account
- The collector only reads data; it cannot modify your ComEd account

## InfluxDB Measurements

After setup, data will appear in:

- `comed_meter_usage` - Daily/hourly usage from smart meter (kWh)
- `comed_meter_cost` - Daily/hourly costs (actual billed amounts)
- `comed_bill` - Monthly bill summaries

Dashboard integration for visualizing this data is planned for a future update.
