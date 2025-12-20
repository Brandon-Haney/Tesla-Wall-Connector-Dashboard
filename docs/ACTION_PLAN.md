# Tesla Wall Connector Dashboard - Action Plan

## Current Status: Phase 4.5 - Fleet API Migration (Retiring Local TWC API)

### What's Working
- Real-time data collection from Tesla Wall Connector Gen 3
- ComEd hourly pricing integration
- **Six Grafana dashboards**:
  - Charging Overview (home dashboard, Fleet API)
  - Fleet Wall Connectors (real-time status of all units)
  - Session History (Fleet API with daily/weekly/monthly summaries)
  - Energy & Costs (Fleet API)
  - Vehicle Status (Tessie API)
  - Live Overview (Legacy - local TWC API only)
- Docker-based deployment with InfluxDB, Grafana, and Python collector
- Multi-charger support
- AM/PM time formatting
- Color-coded pricing thresholds
- Real-time session cost tracking with incremental pricing
- Full cost estimation (supply + delivery rates)
- Completed session history with daily/weekly/monthly summaries
- Grafana unified alerting (low price, high price, session complete, charger offline, high temp)
- REST API with FastAPI (http://localhost:8000)
- Data export (CSV, JSON, PDF reports)
- WebSocket for real-time updates
- Interactive API documentation (Swagger UI, ReDoc)
- **Tessie API integration** - Vehicle data collection (SOC, charging state, range)
- **Secrets management** - `.secrets` file for API tokens (gitignored)
- **Vehicle Status dashboard** - Real-time Tesla vehicle data with battery, range, charging metrics
- **Dynamic temperature unit selection** - Dropdown to switch between °F and °C across all dashboards
- **Vehicle charging session tracking** - Sessions tracked from vehicle perspective with SOC changes
- **Battery health metrics** - Stores pack voltage, temps, energy remaining (when available via Fleet Telemetry)
- **Smart Charging Control** - Adaptive price-based charging that pauses during price spikes
  - Automatically bootstraps 30 days of price history from ComEd API
  - Calculates rolling statistics (percentiles) for adaptive thresholds
  - Stops charging when price > 90th percentile, resumes when < 75th percentile
  - Dashboard panels show status, thresholds, and action history
  - Two-tier config: `SMART_CHARGING_ENABLED` for monitoring, `SMART_CHARGING_CONTROL_ENABLED` for vehicle control
- **REST API Vehicle Endpoints** - Full vehicle data access via API
  - List vehicles, get status, query vehicle charging sessions
  - WebSocket includes vehicle status updates
- **Charge Amps Display** - Added to Vehicle Status and Live Overview dashboards
  - Thresholds: Green 32+ A (optimal), Yellow 24-31 A, Orange 1-23 A (limited)
- **Fleet API Wall Connector Integration** - Real-time data from ALL Wall Connectors via Tesla Fleet API
  - Auto-discovers energy_site_id from Tessie account
  - Polls `live_status` endpoint for leader AND follower units
  - Stores data in `twc_fleet_status` measurement
  - "Fleet Wall Connectors" dashboard showing both units
- **Fleet API Charge History** - Session history for ALL Wall Connectors via `telemetry_history?kind=charge`
  - Energy added (Wh), duration, start time
  - Which Wall Connector (DIN) handled each session
  - Which vehicle (target_id) was charged
- **Fleet Wall Connectors Dashboard Enhancements**
  - Added Fault column to "All Wall Connectors" table with color coding
  - Added "Today's Energy" and "Sessions Today" stat panels
  - Added "Last Session by Unit" table showing most recent session per Wall Connector
  - Added "This Week vs Last Week" comparison bar chart
- **Vehicle Status Dashboard - Fleet Charging Sessions**
  - Replaced "Charging Efficiency" section with "Fleet Charging Sessions" using direct Fleet API data
  - Panels: Last Session Energy/Duration/Avg Power, Sessions (7 Days), Energy (7 Days), Session Energy chart
  - Note: Efficiency tracking removed due to Tessie vehicle data timing mismatches (shows stale data during overnight charging)
- **Real-Time Pricing (5-Minute Data)**
  - Renamed "Hourly Price" charts to "Real-Time Price" across all dashboards
  - Smart charging now uses 5-minute prices for faster reaction to price spikes (was using hourly average)
  - Session cost calculations use 5-minute prices for real-time accuracy
  - ComEd hourly average still collected for backwards compatibility
- **Legacy Dashboard Reorganization**
  - Moved `grafana/dashboards/legacy/` to `grafana/dashboards-legacy/` (separate volume mount)
  - Fixed duplicate dashboard UID provisioning conflict
- **Immediate Session Recording (Step 4.5.9)** - Sessions appear in dashboard within seconds of charge completion
  - `FleetSessionTracker` integrates power readings during charging to calculate energy
  - When charging stops, session is written to `fleet_charge_session` with `source=live_status`
  - Includes: unit_name, vehicle_name, duration, energy, costs (calculated from real-time ComEd prices)
  - Minimum thresholds: 0.1 kWh and 60 seconds (filters out brief plug-ins)
  - Replaces delayed `telemetry_history` as primary session source (kept as backup for reconciliation)
- **Smart Charging Actions Log Fix** - Action column now displays properly with value mappings
  - Shows: "⏸ STOP (DRY-RUN)", "▶ RESUME (DRY-RUN)", "⏸ STOP", "▶ RESUME"
  - Color coded: orange/yellow for dry-run, red/green for live mode
- **Fleet Wall Connectors Dashboard Cleanup** - Filters out legacy "unknown" unit sessions
  - Today's Charging Sessions, Sessions Today, Today's Energy, Last Session by Unit, This Week vs Last Week
  - All queries now filter `unit_name != "unknown"` to exclude old Tessie vehicle charge data

---

## Project Phases

### Phase 1: Foundation (MVP) - COMPLETE

#### Step 1.1: Project Setup
- [x] Initialize Git repository
- [x] Create Docker Compose configuration
- [x] Set up development environment
- [x] Create configuration templates (.env.example)

#### Step 1.2: Wall Connector Collector
- [x] Implement TWC API client (Python async with aiohttp)
- [x] Test connectivity with Wall Connector(s)
- [x] Implement polling loop for vitals/lifetime/version/wifi endpoints
- [x] Add error handling and retry logic
- [x] Create Dockerfile for collector service
- [x] Session tracking (start/end detection)

#### Step 1.3: InfluxDB Setup
- [x] Configure InfluxDB 2.7 container
- [x] Create bucket and retention policies
- [x] Define measurement schemas (twc_vitals, twc_lifetime, twc_version, twc_wifi, comed_price, twc_session, twc_session_state)
- [x] Test data ingestion from collector

#### Step 1.4: Grafana Dashboards
- [x] Configure Grafana 10.2.0 with InfluxDB datasource
- [x] Create "Live Overview" dashboard
  - Current power draw
  - Session energy counter
  - Vehicle connected indicator
  - Charging status
  - Charger temperatures (PCBA, Handle, MCU)
  - Grid voltage and frequency
  - WiFi signal strength
  - Uptime, total sessions, firmware version
  - ComEd current price with threshold coloring
- [x] Create "Energy & Costs" dashboard
  - Total energy for period
  - Total charge sessions
  - Average price with threshold coloring
  - Total charging time
  - Hourly energy consumption bar chart
  - Hourly price chart with AM/PM and color thresholds
  - Cumulative energy over time
  - Best charging hours table (sorted by price)
  - Price by hour of day visualization
- [x] AM/PM time format configuration
- [x] Multi-charger dropdown selector

#### Step 1.5: ComEd Integration
- [x] Implement ComEd API client
- [x] Store pricing data in InfluxDB (5min and hourly_avg)
- [x] Add current price display to Live dashboard
- [x] Price trend visualization
- [x] Color-coded thresholds (green < 4¢, yellow 4-6¢, orange 6-8¢, red > 8¢)

---

### Phase 2: Analytics & Cost Tracking - NEARLY COMPLETE

#### Step 2.1: Session Detection
- [x] Implement session start/end detection logic (SessionTracker class)
- [x] Calculate session metrics (duration, energy, peak power)
- [x] Store session records in InfluxDB as separate measurement (`twc_session`)
- [x] Associate pricing data with sessions for cost calculation
- [x] Real-time session state tracking (`twc_session_state`) during active charging

#### Step 2.2: Cost Calculation Engine
- [x] Implement real-time cost calculation (incremental energy × current price)
- [x] Calculate per-session total cost with price averaging
- [x] Add ComEd delivery charges (configurable `delivery_rate` variable)
- [x] Create cost summary queries for dashboards
- [x] Dashboard panels for Supply Rate, Full Rate (Est.), Session Cost

#### Step 2.3: Enhanced Dashboards
- [x] Added Full Cost estimation panels (supply + delivery)
- [x] Real-time Session Cost display during charging
- [x] Session History dashboard (table of past sessions with costs)
- [x] Daily cost breakdown
- [x] Weekly/Monthly cost summaries
- [ ] Year-over-year comparison (requires historical data)

#### Step 2.4: Alerting
- [x] Configure Grafana unified alerting
- [x] Low price alerts (< 3¢/kWh - good time to charge)
- [x] High price alerts (> 8¢/kWh - avoid charging)
- [x] Session complete notifications
- [x] Charger offline alerts
- [x] High temperature alerts (> 60°C)

---

### Phase 3: Integration & Export - MOSTLY COMPLETE

#### Step 3.1: Home Assistant Integration (Skipped for now)
- [ ] Set up MQTT broker (Mosquitto)
- [ ] Implement MQTT sensor publishing
- [ ] Create HA sensor configuration
- [ ] Test HA automations based on price

#### Step 3.2: Data Export - COMPLETE
- [x] CSV export capability
- [x] JSON export capability
- [x] PDF report generation (monthly/yearly)
- [x] Enhanced PDF reports with charts (daily energy bar, cost pie, price trend)
- [x] Best/worst sessions analysis
- [x] Savings comparison vs fixed rate
- [x] Styled tables with colors and alternating rows

#### Step 3.3: API Gateway - COMPLETE
- [x] Create FastAPI service
- [x] REST endpoints for charger status
- [x] REST endpoints for sessions and pricing
- [x] Export endpoints (CSV, JSON, PDF)
- [x] WebSocket for real-time updates
- [x] Interactive API documentation (Swagger/ReDoc)

---

### Phase 4: Tesla Vehicle Integration (Tessie API) - IN PROGRESS

Tessie Pro Lifetime account provides free, unlimited API access to Tesla Fleet API with no polling costs.

#### Tessie API Types

Tessie offers **three APIs** that can be mixed and matched with a single access token:

1. **Tesla Fleet API Layer** - Tessie's wrapper around Tesla's official Fleet API
   - Instant access without Tesla developer registration
   - Unlimited vehicle_data polling at no additional cost
   - Automatic Vehicle Command Protocol signing
   - Good for: Standard vehicle data and commands

2. **Tesla Fleet Telemetry Streaming API** - Real-time data streaming
   - Direct vehicle data streaming via WebSocket
   - End-to-end encrypted telemetry
   - No need to set up your own Fleet Telemetry servers
   - Good for: Real-time battery metrics during charging

3. **Tessie API** - Native API with enhancements
   - Additional data endpoints beyond Tesla Fleet API
   - Automatic wake-up functionality
   - Automatic firmware error handling
   - Charging history with costs and locations
   - Good for: Simplified development, charge tracking

#### Charger Detection Capability

The vehicle's `charge_state` includes fields to identify the connected charger:
- `conn_charge_cable` - Cable type (e.g., "SAE" for J1772/TWC, "IEC" for European)
- `fast_charger_type` - Fast charger protocol (e.g., "MCSingleWireCAN")
- `fast_charger_brand` - Charger manufacturer/brand
- `fast_charger_present` - Boolean for DC fast charging
- `charger_phases` - Electrical phase configuration

**Important**: Tessie updates faster than the local TWC API and can distinguish between multiple Wall Connectors in a leader/follower power sharing configuration (the local TWC API can only poll the leader unit).

#### Charge History API (`GET /{vin}/charges`)

Tessie tracks all charging sessions automatically:
- `location` - Street address of charging
- `latitude`/`longitude` - GPS coordinates
- `is_supercharger` - Boolean for Supercharger sessions
- `energy_added` - kWh added
- `energy_used` - kWh consumed
- `starting_battery`/`ending_battery` - SOC %
- `cost` - Charge cost (if configured)
- `started_at`/`ended_at` - Unix timestamps

This allows correlating vehicle charging history with TWC data for complete tracking.

#### Useful Data from Tessie API

**Vehicle Identification**
- `vin` - Vehicle VIN for multi-vehicle support
- `display_name` - User-friendly vehicle name (e.g., "Model 3", "Model S")
- `vehicle_config.car_type` - Model type (model3, lychee/Model S, modely, tamarind/Model X)
- `state` - Vehicle state (asleep, online, driving, charging)

**Battery & Charging State** (`charge_state`)
- `battery_level` - Current SOC percentage (e.g., 90)
- `usable_battery_level` - Usable SOC percentage
- `battery_range` - Rated range in miles (e.g., 299.5)
- `est_battery_range` - Estimated range based on driving
- `ideal_battery_range` - Ideal range
- `charge_limit_soc` - Charge limit setting (e.g., 90%)
- `charging_state` - Status: "Charging", "Complete", "Disconnected", "Stopped"
- `charge_amps` - Current charge rate in amps (e.g., 48)
- `charger_power` - Charging power in kW
- `charger_voltage` - Charger voltage
- `charger_actual_current` - Actual current draw
- `charge_rate` - Miles/hour being added
- `charge_energy_added` - kWh added this session
- `charge_miles_added_rated` - Miles added this session
- `time_to_full_charge` - Hours remaining
- `minutes_to_full_charge` - Minutes remaining
- `conn_charge_cable` - Cable type ("SAE" = J1772/TWC)
- `charge_port_door_open` - Port door status
- `charge_port_latch` - Latch status ("Engaged", "Disengaged")
- `scheduled_charging_pending` - If scheduled charge is waiting
- `scheduled_charging_start_time` - Unix timestamp for scheduled start

**Battery Health/Telemetry** (Model S/X with Fleet Telemetry)
- `pack_voltage` - Battery pack voltage (e.g., 452.66V)
- `pack_current` - Battery pack current (e.g., -0.5A)
- `module_temp_min` / `module_temp_max` - Battery temps (e.g., 11.5°C - 12.5°C)
- `energy_remaining` - kWh remaining in pack (e.g., 72.08 kWh)
- `lifetime_energy_used` - Total kWh used lifetime (e.g., 44845 kWh)

**Climate State** (`climate_state`)
- `inside_temp` - Interior temp in °C
- `outside_temp` - Exterior temp in °C
- `battery_heater` - Battery heater active
- `is_preconditioning` - Preconditioning active

**Vehicle State** (`vehicle_state`)
- `odometer` - Total miles (e.g., 104167.56)
- `car_version` - Software version (e.g., "2025.44.3")
- `locked` - Lock status
- `sentry_mode` - Sentry mode status
- `vehicle_name` - Display name

**Location** (`drive_state`)
- `latitude` / `longitude` - GPS coordinates
- `heading` - Direction in degrees

#### Step 4.1: Tessie Integration Setup
- [x] Create `.secrets` file for Tessie API token (gitignored, not committed)
- [x] Update collector config to load secrets from `.secrets` file
- [x] Add Tessie client to collector service (async/aiohttp)
- [x] Implement vehicle discovery (list vehicles via `GET /vehicles`)
- [ ] Match vehicles to chargers by location (GPS proximity to home)
- [x] Add vehicle data polling (configurable interval, respect vehicle sleep state)

#### Step 4.2: Vehicle Data Collection
- [x] Store vehicle state in InfluxDB (`tesla_vehicle` measurement)
- [x] Track battery SOC over time
- [x] Track charging sessions from vehicle perspective (`tesla_session` measurement)
- [x] Store battery health metrics (pack voltage, temps, energy_remaining) - writes to `tesla_battery_health` if available via Fleet Telemetry

**Note:** Charging efficiency correlation was removed. The original design attempted to correlate TWC energy with vehicle `charge_energy_added`, but Tessie returns stale/cached vehicle data during overnight charging (vehicle shows "Disconnected" while actually charging). Fleet API `telemetry_history` provides authoritative session data and is used directly instead.

#### Step 4.3: Enhanced Dashboard - Vehicle View
- [x] Add "Vehicle Status" dashboard
  - Vehicle dropdown selector (filter by `display_name` tag)
  - Current SOC and range (gauge + stat panels)
  - Charge limit setting
  - Charging state and power
  - Time to full charge
  - Inside/outside temperatures (displayed in Fahrenheit)
  - Software version and odometer
  - Battery level, range, and charging power over time charts
  - Temperatures over time chart
  - Status indicators (plugged in, preconditioning, battery heater, DC fast charger)
  - Cable type with friendly labels (Wall Connector, Not Connected, IEC)
  - Charge amps and charger voltage
- [x] Add vehicle battery panel to Live Overview
  - New "Vehicle Status (Tessie)" row with vehicle dropdown
  - Battery gauge, range, charge state, power, energy added
  - Time to full, vehicle state, charge limit
- [x] Dynamic temperature unit selection
  - `temp_unit` dropdown variable (°F / °C) on Live Overview and Vehicle Status dashboards
  - Conditional Flux query converts temperatures based on selection
  - Panel titles dynamically show selected unit (e.g., "Inside Temp (°F)")
  - Time series charts show unit in Y-axis label
  - Thresholds optimized for Fahrenheit (default) but values adjust with unit
- [x] Fleet Charging Sessions section (replaced Charging Efficiency)
  - "Fleet Charging Sessions" row with Last Session Energy/Duration/Avg Power
  - Sessions (7 Days) count and Energy (7 Days) total
  - Session Energy bar chart showing energy per session over time
  - Uses Fleet API `fleet_charge_session` data directly (authoritative source)
  - Note: Original efficiency tracking removed due to Tessie vehicle data timing issues
- [x] Vehicle session history table
  - Recent vehicle charging sessions with start/end SOC, energy added, duration, charger type

**Note:** All vehicles (active subscription or not) are collected and stored. Inactive vehicles may show 0% SOC or stale data. Use the dashboard vehicle dropdown to filter to specific vehicles.

#### Step 4.4: Smart Charging Control (Adaptive Price-Based) - COMPLETE

The smart charging system uses **adaptive thresholds** based on rolling price statistics rather than hardcoded values. This ensures thresholds automatically adjust as electricity prices trend up or down over time.

**Core Concept:**
- Calculate rolling statistics (percentiles) from the last 30 days of price data
- Stop charging when price exceeds the 90th percentile (top 10% most expensive)
- Resume charging when price drops below the 75th percentile
- No hardcoded price values that become stale over time

**Example:**
| Metric | 2024 Value | 2027 Value (projected) |
|--------|------------|------------------------|
| 30-day average | 4.2¢/kWh | 6.8¢/kWh |
| Stop threshold (90th %ile) | 8.2¢/kWh | 12.1¢/kWh |
| Resume threshold (75th %ile) | 5.8¢/kWh | 8.9¢/kWh |

##### Step 4.4.1: Price History Bootstrap - COMPLETE
- [x] Add `get_price_data_days_available()` method to check existing data in InfluxDB
- [x] Add `get_oldest_price_data_time()` and `has_price_data_for_period()` query methods
- [x] Add `_bootstrap_price_history()` method to collector
  - On startup, check if we have 30 days of price data
  - If not, fetch historical data from ComEd API (supports date ranges)
  - Backfill in ~3-day chunks (API returns max ~1000 records per request)
  - Store in InfluxDB with `price_type: "5min"` tag
- [x] Add startup log indicating data quality ("27/30 days available")

##### Step 4.4.2: Rolling Price Statistics - COMPLETE
- [x] Create `PriceStatistics` class to calculate rolling stats
  - Query last 30 days of prices from InfluxDB via `get_price_values()`
  - Calculate: mean, median, std dev, percentiles (10th, 25th, 75th, 90th, 95th)
  - Cache results with 6-hour expiry, recalculate on-demand
- [x] Store statistics in InfluxDB (`comed_price_stats` measurement)
  - Fields: mean, median, std_dev, min, max, p10, p25, p75, p90, p95, count, days_available
  - Written on each recalculation
- [x] Add `get_current_percentile()` to calculate where current price falls in distribution
- [x] Add configuration for lookback period (default 30 days)
  ```env
  SMART_CHARGING_LOOKBACK_DAYS=30
  ```

##### Step 4.4.3: Smart Charging Controller - COMPLETE
- [x] Create `SmartChargingController` class in collector
  - Uses **5-minute prices** for faster reaction to price spikes (updated from hourly average)
  - Monitor current price vs calculated percentiles
  - Track charging state per vehicle
  - Decision logic:
    - If charging AND price > 90th percentile → STOP charging
    - If stopped due to price AND price < 75th percentile → RESUME charging
  - Hysteresis to prevent rapid on/off cycling (minimum 10 minutes between changes)
- [x] Add configuration options:
  ```env
  SMART_CHARGING_ENABLED=true
  SMART_CHARGING_CONTROL_ENABLED=false   # DRY-RUN mode (log but don't send commands)
  SMART_CHARGING_STOP_PERCENTILE=90      # Stop if price in top 10%
  SMART_CHARGING_RESUME_PERCENTILE=75    # Resume when back to normal
  SMART_CHARGING_MIN_INTERVAL=600        # Minimum seconds between start/stop commands
  ```
- [x] Use existing Tessie client methods: `start_charging()`, `stop_charging()`
- [x] Log all actions with timestamps and price context
- [x] **DRY-RUN Mode** (SMART_CHARGING_CONTROL_ENABLED=false):
  - Prominent logging: "⚡ DRY RUN - PRICE SPIKE DETECTED" / "⚡ DRY RUN - WOULD STOP/RESUME"
  - Simulated actions logged to InfluxDB (`stop_simulated`, `start_simulated`)
  - Dashboard shows "DRY-RUN" mode indicator and "Would Pause" status
  - Allows validating thresholds before enabling LIVE mode

##### Step 4.4.4: Smart Charging State Tracking - COMPLETE
- [x] Store smart charging state in InfluxDB (`smart_charging_state` measurement)
  - Fields: enabled, control_enabled, status, paused_by_price, simulated_pause, current_price_cents, current_percentile
  - Fields: stop_threshold_cents, stop_percentile, resume_threshold_cents, resume_percentile, days_of_data
  - Status values: "charging", "not_charging", "paused_by_price" (LIVE), "would_pause" (DRY-RUN), "unknown"
- [x] Store action history (`smart_charging_actions` measurement)
  - Tags: vin, display_name, action
  - Fields: price_cents, percentile, threshold_cents
  - Action values: "stop", "start" (LIVE mode) or "stop_simulated", "start_simulated" (DRY-RUN mode)

##### Step 4.4.5: Dashboard - Smart Charging Status - COMPLETE
- [x] Add "Smart Charging" row to Vehicle Status dashboard
  - **Mode indicator** - Shows "DRY-RUN" (yellow) or "LIVE" (green)
  - Status indicator with color-coded mappings (Charging/Paused/Not Charging/Would Pause)
  - Current price stat panel with threshold coloring
  - Stop threshold display (90th percentile)
  - Resume threshold display (75th percentile)
  - Days of price history indicator (red < 7, yellow 7-20, green 20+)
- [x] Add price percentile gauge
  - Shows where current price falls in 30-day distribution (0-100%)
  - Color coded: green (0-50), yellow (50-75), orange (75-90), red (90-100)
- [x] Smart Charging Actions Log table
  - Shows last 20 interventions
  - Shows "(DRY-RUN)" suffix for simulated actions
  - Columns: Time, Action (with colored icons), Price, Percentile, Threshold

##### Step 4.4.6: Alerting Integration (Optional)
- [ ] Alert when smart charging pauses due to price spike
- [ ] Alert when price drops to "optimal" range (below 25th percentile)
- [ ] Daily summary: "Smart charging saved you X interventions yesterday"

**Future Enhancements (not in initial implementation):**
- "Charge to X% by Y time at lowest cost" optimization
- Price forecast integration (predict prices for next 12-24 hours)
- Multi-vehicle prioritization during low-price windows

#### Step 4.5: Fleet Telemetry Streaming (Optional)
- [ ] Connect to Tessie WebSocket streaming endpoint
- [ ] Real-time battery metrics during charging
- [ ] Live SOC updates without polling

---

### Phase 4.5: Fleet API Migration - IN PROGRESS

**Goal:** Replace local Wall Connector API with Tesla Fleet API (via Tessie) for all primary functionality. The local API will be deprecated but kept for legacy/diagnostic purposes.

#### Why Migrate to Fleet API?

| Capability | Local TWC API | Fleet API | Winner |
|------------|:-------------:|:---------:|:------:|
| Real-time power | ✅ Leader only | ✅ ALL units | Fleet |
| Session energy | ✅ Leader only | ✅ ALL units | Fleet |
| Session history | ❌ No history | ✅ Full history | Fleet |
| Which vehicle charged | ❌ Unknown | ✅ VIN/target_id | Fleet |
| Multi-unit support | ❌ Leader only | ✅ Leader + followers | Fleet |
| Network dependency | Local network | Cloud (Tessie) | Fleet |
| Temperatures | ✅ | ❌ | Local |
| Per-phase voltage/current | ✅ | ❌ | Local |
| WiFi diagnostics | ✅ | ❌ | Local |
| Firmware version | ✅ | ❌ | Local |

**Decision:** Fleet API provides the data we actually need (power, sessions, multi-unit). Local API only provides diagnostics we don't monitor. Retire local API.

#### Fleet API Endpoints Used

| Endpoint | Purpose | Data |
|----------|---------|------|
| `GET /api/1/products` | Discover energy sites | energy_site_id |
| `GET /api/1/energy_sites/{id}/live_status` | Real-time status | power, state, connected VIN per unit |
| `GET /api/1/energy_sites/{id}/telemetry_history?kind=charge` | Charge sessions | energy_wh, duration, din, target_id |
| `GET /api/1/energy_sites/{id}/site_info` | Site configuration | installation info, settings |

#### Step 4.5.1: Fleet API Live Status - COMPLETE
- [x] Add `fleet_energy_site_id` configuration option
- [x] Auto-discover energy_site_id if not configured
- [x] Create `FleetWallConnector` and `FleetEnergySiteLiveStatus` models
- [x] Add Fleet API methods to TessieClient (`get_energy_site_live_status`, etc.)
- [x] Implement `_poll_fleet_twc()` in collector (30-second interval)
- [x] Store in `twc_fleet_status` measurement with unit_type tag (leader/follower)
- [x] Create "Fleet Wall Connectors" dashboard
- [x] Redesigned dashboard for dynamic scaling and friendly names:
  - Replaced hardcoded Leader/Follower panels with dynamic "All Wall Connectors" table
  - Uses `unit_name` for friendly names (e.g., "Garage Left", "Garage Right")
  - Fixed "Units Charging" to show 0 instead of "No data" when not charging
  - Added "Vehicles Connected" stat panel
  - Power History chart uses `unit_name` with Y-axis max of 25 kW
  - Added "Today's Charging Sessions" table with Unit, Vehicle, Duration, Energy
  - All tables use `organize` transformation for consistent column ordering
- [x] Enhanced dashboard with additional panels:
  - Added Fault column to "All Wall Connectors" table with color coding (green=No Fault, red=Fault)
  - Added "Today's Energy" stat panel (sum of energy from today's sessions)
  - Added "Sessions Today" stat panel (count of today's sessions)
  - Added "Last Session by Unit" table showing most recent session per Wall Connector
  - Added "This Week vs Last Week" comparison bar chart for energy trends

#### Step 4.5.2: Fleet API Charge History - COMPLETE
- [x] Add `get_energy_site_telemetry_history()` method to TessieClient
- [x] Create `FleetChargeSession` model for charge history data
- [x] Import historical sessions into `fleet_charge_session` measurement
- [x] Poll for new sessions periodically (hourly)
- [x] Calculate costs using historical ComEd prices (supply + delivery)
- [x] Vehicle name mapping via `target_id` (best-effort, shows truncated UUID when unknown)

**Note:** The Fleet API's `target_id` is a UUID that doesn't directly map to VIN. Vehicle names are now resolved via `TARGET_ID_VEHICLES` configuration (maps UUID to friendly name). If not configured, displays truncated UUID.

#### Step 4.5.3: New Primary Dashboard - COMPLETE
- [x] Create "Charging Overview" dashboard (replaces Live Overview)
  - Total power across all Wall Connectors (Fleet API)
  - Per-unit power breakdown (leader + followers)
  - Which vehicle is charging at which unit
  - Current ComEd price and session cost
  - Recent charging sessions table (Fleet API history)
- [x] Update dashboard navigation/home to point to new dashboard
  - Set via `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` environment variable
- [x] Add vehicle-to-charger mapping panel
  - Wall Connector Status table shows vehicle connected to each unit
- [x] Add Unit and Vehicle dropdown selectors for filtering
  - `unit_name` dropdown filters by Wall Connector
  - `vehicle_name` dropdown filters sessions by vehicle
- [x] Enhanced Charging Sessions table includes Vehicle column

**Additional fixes during implementation:**
- Fixed bug where initial Fleet API fetch wasn't using friendly names from config
- Added `VEHICLE_NAMES` config for vehicle names when Tessie returns empty display_name (vehicles asleep)
- Added `TARGET_ID_VEHICLES` config for mapping Fleet API charge history UUIDs to vehicle names
- All queries now respect unit_name and vehicle_name filters

#### Step 4.5.4: Session History Migration - COMPLETE
- [x] Migrated Session History dashboard to use Fleet API `fleet_charge_session` data
- [x] Include ALL Wall Connectors (not just leader)
- [x] Added Vehicle column showing which vehicle charged at each session
- [x] Costs calculated using ComEd prices at session time (supply + delivery)
- [x] Daily/weekly/monthly summaries across all units
- [x] Added `vehicle_name` filter dropdown for filtering by vehicle
- [x] Fixed `unit_name` dropdown query to only show actual units with data
- [x] All stat panels, charts, and tables respect both unit and vehicle filters

#### Step 4.5.5: Migrate Energy & Costs Dashboard to Fleet API - COMPLETE

The Energy & Costs dashboard currently uses Local TWC API data (`twc_vitals`, `twc_lifetime`) which only shows the leader unit. These panels need to be updated to use Fleet API data for complete coverage of all Wall Connectors.

**Panels to Migrate:**

| Panel | Current Source | New Source | Notes |
|-------|---------------|------------|-------|
| **Total Energy (Period)** | `twc_vitals.session_energy_wh` | `fleet_charge_session.energy_kwh` | Sum energy from all sessions in time range |
| **Total Charge Sessions** | `twc_lifetime.charge_starts` | `fleet_charge_session` | Count sessions in time range |
| **Total Charging Time** | `twc_lifetime.charging_hours` | `fleet_charge_session.duration_s` | Sum duration from all sessions |
| **Hourly Energy Consumption** | `twc_vitals.session_energy_wh` | `fleet_charge_session.energy_kwh` | Aggregate by hour using session start times |
| **Cumulative Energy (kWh)** | `twc_vitals.session_energy_wh` | `fleet_charge_session.energy_kwh` | Running sum over time |

**Implementation Details:**

1. **Total Energy (Period)**
   - Current: Uses `difference(nonNegative: true)` on `session_energy_wh` to calculate incremental energy
   - New Query:
     ```flux
     from(bucket: "twc_dashboard")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r._measurement == "fleet_charge_session")
       |> filter(fn: (r) => r._field == "energy_kwh")
       |> group()
       |> sum()
     ```

2. **Total Charge Sessions**
   - Current: Uses `twc_lifetime.charge_starts` (lifetime counter from local API)
   - New Query:
     ```flux
     from(bucket: "twc_dashboard")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r._measurement == "fleet_charge_session")
       |> filter(fn: (r) => r._field == "energy_kwh")
       |> group()
       |> count()
     ```

3. **Total Charging Time**
   - Current: Uses `twc_lifetime.charging_hours` (lifetime counter)
   - New Query:
     ```flux
     from(bucket: "twc_dashboard")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r._measurement == "fleet_charge_session")
       |> filter(fn: (r) => r._field == "duration_hours")
       |> group()
       |> sum()
     ```

4. **Hourly Energy Consumption**
   - Current: Aggregates `session_energy_wh` by hour with `difference()`
   - New Approach: Since `fleet_charge_session` stores completed sessions (not real-time), aggregate by session start time hour
   - New Query:
     ```flux
     from(bucket: "twc_dashboard")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r._measurement == "fleet_charge_session")
       |> filter(fn: (r) => r._field == "energy_kwh")
       |> aggregateWindow(every: 1h, fn: sum, createEmpty: true)
       |> keep(columns: ["_time", "_value", "unit_name"])
     ```

5. **Cumulative Energy (kWh)**
   - Current: Running sum of daily energy from `session_energy_wh`
   - New Query:
     ```flux
     from(bucket: "twc_dashboard")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r._measurement == "fleet_charge_session")
       |> filter(fn: (r) => r._field == "energy_kwh")
       |> aggregateWindow(every: 1d, fn: sum, createEmpty: true)
       |> cumulativeSum()
       |> keep(columns: ["_time", "_value", "unit_name"])
     ```

**Additional Enhancements:**
- [ ] Add `unit_name` filter dropdown (like Charging Overview)
- [ ] Show breakdown by Wall Connector unit
- [ ] Update "Best Charging Hours" to use Fleet data
- [ ] Update "Avg Price by Hour" if it references session data

**Migration Tasks:**
- [x] Update Total Energy (Period) query to use `fleet_charge_session`
- [x] Update Total Charge Sessions query to count `fleet_charge_session` records
- [x] Update Total Charging Time query to sum `fleet_charge_session.duration_hours`
- [x] Update Hourly Energy Consumption query
- [x] Update Cumulative Energy query
- [x] Add unit_name template variable for filtering
- [x] Update Est. Total Cost to use `fleet_charge_session.full_cost_dollars`
- [x] Update dashboard header to reflect Fleet API data source
- [ ] Test all panels with various time ranges
- [ ] Verify data matches between old and new queries during transition

#### Step 4.5.6: Dashboard Consistency & Standardization - COMPLETE

Fixed inconsistencies across all Grafana dashboards:

**High Priority Fixes:**
- [x] Replace hardcoded `"uid": "influxdb"` with `"uid": "${datasource}"` in charging-overview.json and fleet-wall-connectors.json
- [x] Add `datasource` template variable to dashboards that were missing it
- [x] Migrate session-history.json from `twc_session` (local API) to `fleet_charge_session` (Fleet API)
- [x] Standardize "Rate" vs "Price" terminology → Use "Hourly Price" (vendor-agnostic)
- [x] Fix threshold inconsistencies → All supply price panels use 4/6/8 (green/yellow/orange/red)

**Medium Priority Fixes:**
- [x] Standardize variable labels → "Delivery ¢/kWh" across all dashboards
- [x] Standardize table column naming → Time, Unit, Energy, Duration, Avg Power, Avg Price, Supply Cost, Full Cost
- [x] Add "organize" transformation to tables for consistent column ordering
- [x] Fix column widths to prevent layout shifts

**Query Cleanup (Fleet API data):**
- [x] Add `|> group() |> keep(columns: ["_value"])` to stat panels to strip verbose Fleet API tags
- [x] Add `|> keep(columns: ["_time", "_value", "unit_name"])` to time series for clean legends
- [x] Add `timeSrc: "_start"` to `aggregateWindow` to prevent "Data outside time range" warnings

#### Step 4.5.7: Deprecate Local API Dashboard - COMPLETE
- [x] Renamed "Live Overview" to "Live Overview (Legacy)" with deprecation tags
- [x] Added deprecation notice banner explaining Fleet API alternatives
- [x] Moved to "Legacy" folder in Grafana (new provisioning provider)
- [x] Reduced local API polling intervals (vitals: 30s, lifetime/wifi: 300s)
- [x] Added `LOCAL_TWC_ENABLED` config option to disable local polling entirely
- [x] Updated .env.example with comprehensive documentation

#### Step 4.5.8: Configuration Cleanup - NOT STARTED
- [ ] Make `TWC_CHARGERS` optional (not required if using Fleet API only)
- [ ] Add `FLEET_API_ENABLED=true` as primary toggle
- [ ] Add `LOCAL_TWC_ENABLED=false` to disable local polling
- [ ] Update .env.example with new recommended configuration

#### Step 4.5.9: Fix Fleet API Session Latency Issue - MOSTLY COMPLETE

**Problem Discovered (2025-12-08):**

The Tesla Fleet API `telemetry_history?kind=charge` endpoint has **severe undocumented latency** - charging sessions may not appear for hours or even days after completion. This was verified through testing:

| Data Source | Session Available | Latency | Wall Connector Centric? |
|-------------|-------------------|---------|------------------------|
| Fleet API `live_status` | ✅ Real-time power, state, VIN | Immediate | ✅ Yes (per-unit data) |
| Fleet API `telemetry_history` | ❌ Missing for hours/days | Hours to days | ✅ Yes (includes DIN) |
| Tessie `/{vin}/charges` | ✅ Session data | ~2 seconds | ❌ No (vehicle-centric) |

**Test Results (2025-12-08 ~9:05 AM):**
- Started charging Model 3 at 8:56 AM, stopped at 9:05 AM
- `live_status`: Showed "Charging, 11.9 kW" immediately, then "Connected, 0 kW" when stopped
- Tessie `/{vin}/charges`: Had the session within 2 seconds of stopping (0.52 kWh, 90%→90%)
- Fleet API `telemetry_history`: Still returned `null` for today's sessions 15+ minutes later

**Key Insight:**
The Fleet Wall Connectors dashboard should show sessions FROM the Wall Connectors, not from vehicles. Tessie `/{vin}/charges` is vehicle-centric (shows charges from anywhere in the world), which is wrong for this dashboard.

**Solution: Hybrid Approach - Build Sessions from live_status + Reconcile with telemetry_history**

Use real-time `live_status` power integration to build session records immediately when charging stops, then reconcile with `telemetry_history` when it eventually arrives.

**Implementation Plan:**

##### Step 4.5.9.1: Enhance FleetSessionTracker to Write Completed Sessions - COMPLETE
- [x] When `FleetSessionTracker` detects charging stopped (power drops to 0):
  - Calculate total energy by integrating power over time (already tracked)
  - Calculate duration from start_time to end_time
  - Calculate avg_power_kw from energy/duration
  - Write completed session to `fleet_charge_session` measurement
- [x] Include all relevant tags: `din`, `unit_name`, `energy_site_id`, `vin` (if known)
- [x] Add `source: "live_status"` tag to distinguish from `telemetry_history` sessions
- [x] Calculate costs using ComEd prices at session time (tracked during charging via FleetSessionTracker)

##### Step 4.5.9.2: Track Session Metadata During Charging - COMPLETE (already implemented)
- [x] Capture VIN when charging starts (from `live_status`)
- [x] Track peak power during session
- [x] Store start time when power first goes above threshold
- [ ] Handle brief power dips (don't end session for <60s power drop) - Future enhancement

##### Step 4.5.9.3: Reconcile with Fleet API telemetry_history - PARTIAL
- [x] Continue polling `telemetry_history` at configured interval (default 15 min)
- [x] Added `find_matching_live_status_session()` method to influx_writer.py
- [x] Added `reconciled` field to live_status sessions
- [ ] Full reconciliation logic (update energy value from telemetry_history) - Future enhancement

##### Step 4.5.9.4: Session Deduplication - PARTIAL
- [x] Use combination of (din, start_time ±5 min) to identify duplicate sessions
- [ ] Prefer `telemetry_history` energy values when available - Future enhancement
- [ ] Keep `live_status` timing (more accurate start/end times) - Future enhancement

##### Step 4.5.9.5: Handle Edge Cases - FUTURE
- [ ] **Collector restart during charging**: Check for in-progress sessions on startup
- [ ] **Network gaps**: live_status polling may miss data points - interpolate or mark as incomplete
- [ ] **Multiple short sessions**: Smart charging may cause rapid on/off - each is separate session
- [ ] **Power sharing changes**: Handle power reallocation between units mid-session

##### Step 4.5.9.6: Configuration - COMPLETE
- [x] `FLEET_CHARGE_HISTORY_INTERVAL` config option (default 900 = 15 minutes)
- [x] `FLEET_SESSION_MIN_ENERGY_KWH` - Minimum energy to record session (default 0.1 kWh)
- [x] `FLEET_SESSION_MIN_DURATION_S` - Minimum duration to record session (default 60s)

##### Step 4.5.9.7: Testing & Validation
- [ ] Verify sessions appear in dashboard within 1 minute of charge completion
- [ ] Verify energy values are reasonable (compare to vehicle display)
- [ ] Verify costs are calculated correctly using ComEd prices
- [ ] Verify reconciliation updates energy when telemetry_history arrives
- [ ] Test with both Wall Connectors charging simultaneously

**Data Flow:**
```
1. live_status polling (every 30s):
   - FleetSessionTracker integrates power for each unit
   - When charging stops → write session to fleet_charge_session (source: live_status)
   - Session appears in dashboard immediately

2. telemetry_history polling (every 15 min):
   - Check for new sessions from Fleet API
   - For each session:
     a. Look for matching live_status session (same din, similar time)
     b. If found: Update energy value from Wall Connector meter
     c. If not found: Write as new session (source: telemetry_history)
```

**Files to Modify:**
- `collector/src/main.py` - Enhance FleetSessionTracker, add reconciliation logic
- `collector/src/influx_writer.py` - Add method to write/update sessions from live_status
- `collector/src/config.py` - Add new config options

**Existing Code to Leverage:**
- `FleetSessionTracker` class in `main.py` - Already tracks power integration per unit
- `fleet_charge_session` measurement - Reuse for consistent dashboard queries
- `write_fleet_charge_sessions_batch()` - Template for session writing

---

### Phase 4.6: ComEd Opower Integration (Meter Data) - IN PROGRESS

**Goal:** Fetch actual electricity usage and cost data from ComEd's Opower portal to compare against our calculated charging costs.

#### Why Opower Integration?

Currently we only have:
- **ComEd Hourly Pricing API** - Real-time supply prices (what we pay per kWh)
- **Wall Connector Energy** - How much energy we used for charging

With Opower integration we get:
- **Actual billed usage** - Total home electricity consumption (not just EV charging)
- **Actual billed costs** - Real costs from ComEd including all fees
- **Historical data** - 30-minute interval data back to June 2023
- **Weather correlation** - Temperature data to correlate with usage
- **Bill history** - Actual billed amounts with charge breakdowns
- **Neighbor comparison** - How usage compares to similar homes

This enables:
- Comparing EV charging costs vs total electricity costs
- Understanding what percentage of electricity goes to EV charging
- Validating our cost calculations against actual bills
- Historical usage analysis and trends
- HVAC analysis with temperature correlation

#### Why Custom Solution (Not opower Library)

The [tronikos/opower](https://github.com/tronikos/opower) library uses ComEd's mobile app OAuth flow (`B2C_1A_SignIn_Mobile`), which ComEd has **disabled** - returns 400 Bad Request.

| Factor | opower Library | Custom Solution |
|--------|----------------|-----------------|
| **OAuth Flow** | Mobile (`B2C_1A_SignIn_Mobile`) | Web (`B2C_1A_SignIn`) |
| **Status** | ❌ 400 Error | ✅ Working |
| **API** | REST (DataBrowser-v1) | GraphQL (more data) |
| **Data Resolution** | Daily | 30-minute intervals |
| **Extra Data** | Usage only | Weather, bills, neighbors |

**Decision:** Use custom web OAuth solution with GraphQL API for richer data.

**Related Issues:**
- [home-assistant/core#134050](https://github.com/home-assistant/core/issues/134050) - ComEd login fails

#### Implementation Status

##### Step 4.6.1: Authentication & API Discovery - COMPLETE ✅
- [x] Analyzed browser HAR file to reverse-engineer auth flow
- [x] Implemented 10-step Azure AD B2C web OAuth flow
- [x] MFA support (email or SMS)
- [x] Session cookie caching for token refresh without MFA
- [x] Discovered 8 GraphQL endpoints via HAR analysis
- [x] Documented all endpoints in `docs/COMED_OPOWER_API.md`

##### Step 4.6.2: Test Script & Daemon - COMPLETE ✅
- [x] Created `scripts/comed_auth.py` - Full authentication + data fetch
- [x] Token caching with session cookies (`.comed_token_cache.json`)
- [x] Daemon mode for continuous operation (`--daemon`)
- [x] Test mode for session keep-alive validation (`--test`)
- [x] Debug mode for troubleshooting (`--debug`)
- [x] Successfully retrieving:
  - Daily/hourly usage data (30-min resolution available)
  - Daily cost data (actual billed costs)
  - Account metadata (rate plan, timezone, data range)

##### Step 4.6.3: Collector Integration - COMPLETE ✅
- [x] Create `collector/src/opower_client.py` wrapper
  - Implemented Azure AD B2C authentication flow
  - Load/save session cache from `.secrets` directory
  - Handle token refresh automatically
  - Fetch data at configurable intervals
- [x] Add new InfluxDB measurements:
  - `comed_meter_usage` - Actual electricity usage from meter
  - `comed_meter_cost` - Actual electricity costs (includes all fees)
  - `comed_bill` - Monthly bill summaries
- [x] Add configuration options to `.env` and `config.py`:
  - `OPOWER_ENABLED` - Enable/disable meter data integration
  - `OPOWER_POLL_INTERVAL` - Fetch interval (default 3600s)
  - `OPOWER_MFA_METHOD` - email or sms
- [x] Add credentials support in `.secrets`:
  - `COMED_USERNAME` / `COMED_PASSWORD` for credential-based auth
  - `COMED_BEARER_TOKEN` for pre-authenticated token mode
- [x] Integrate polling loop in collector `main.py`:
  - Initial bootstrap fetches 30 days of daily usage/cost + 12 months of bills
  - Incremental polling fetches new data daily
  - Handles authentication failures gracefully

##### Step 4.6.4: Dashboard Integration - PLANNED

**New Dashboard: "Meter & Bills" (Opower Data)**

Create a dedicated dashboard for ComEd meter data and bill analysis. This provides the "ground truth" data from your actual smart meter.

**Row 1: Current Status**
| Panel | Type | Description |
|-------|------|-------------|
| Opower Status | Stat | Shows "Connected" (green) or "Expired" (red) based on token status |
| Today's Usage | Stat | kWh used today from smart meter |
| Yesterday's Usage | Stat | kWh used yesterday |
| Month-to-Date Usage | Stat | Total kWh this billing period |
| Month-to-Date Cost | Stat | Estimated cost so far this month |
| Effective Rate | Stat | Your true all-in ¢/kWh (total cost ÷ total kWh) |

**Row 2: Daily Usage Chart**
| Panel | Type | Description |
|-------|------|-------------|
| Daily Electricity Usage | Time Series | Bar chart showing daily kWh from smart meter (last 30 days) |
| Daily Electricity Cost | Time Series | Bar chart showing daily cost in dollars (last 30 days) |

**Row 3: EV Charging Analysis**
| Panel | Type | Description |
|-------|------|-------------|
| EV % of Total Usage | Gauge | Percentage of home electricity used for EV charging |
| EV Charging (kWh) | Stat | Total EV charging energy this month (from Fleet API) |
| Home Usage (kWh) | Stat | Total home usage this month (from Opower) |
| Non-EV Usage (kWh) | Stat | Calculated: Home - EV |

**Row 4: Cost Comparison**
| Panel | Type | Description |
|-------|------|-------------|
| Calculated vs Actual | Bar Gauge | Side-by-side comparison of our calculated costs vs Opower actual costs |
| Cost Discrepancy | Stat | Difference in dollars (helps tune delivery_rate) |
| Calculated Rate | Stat | Our estimated effective rate |
| Actual Rate | Stat | True effective rate from Opower |

**Row 5: Bill History**
| Panel | Type | Description |
|-------|------|-------------|
| Monthly Bills | Table | Last 12 months: Month, kWh, Total Cost, Effective Rate, Estimated flag |
| Bill Trend | Time Series | Line chart of monthly bills over time |
| Average Monthly Bill | Stat | Rolling 12-month average bill amount |
| Average Monthly Usage | Stat | Rolling 12-month average kWh |

**Row 6: Rate Analysis**
| Panel | Type | Description |
|-------|------|-------------|
| Effective Rate History | Time Series | Daily effective rate (¢/kWh) over last 30 days |
| Rate Distribution | Histogram | Distribution of daily effective rates |
| Best/Worst Days | Table | Top 5 cheapest and most expensive days (by ¢/kWh) |

**Template Variables:**
- `datasource`: InfluxDB datasource (standard)
- `time_range`: Custom time range selector

**Data Sources:**
- `comed_meter_usage`: Daily/hourly kWh from smart meter
- `comed_meter_cost`: Daily costs with effective rates
- `comed_bill`: Monthly bill summaries
- `fleet_charge_session`: EV charging sessions for comparison

**Flux Query Examples:**

```flux
// Today's usage from smart meter
from(bucket: "twc_dashboard")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "comed_meter_usage")
  |> filter(fn: (r) => r._field == "kwh")
  |> sum()

// EV as percentage of total (this month)
ev_kwh = from(bucket: "twc_dashboard")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "fleet_charge_session")
  |> filter(fn: (r) => r._field == "energy_kwh")
  |> sum()

total_kwh = from(bucket: "twc_dashboard")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "comed_meter_usage")
  |> filter(fn: (r) => r._field == "kwh")
  |> sum()

// Percentage = (ev_kwh / total_kwh) * 100

// Monthly bill history
from(bucket: "twc_dashboard")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "comed_bill")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({
      _time: r._time,
      "Usage (kWh)": r.total_kwh,
      "Total Cost": r.total_cost_dollars,
      "Rate (¢/kWh)": r.effective_rate_cents
    }))
```

**Implementation Tasks:**
- [ ] Create `grafana/dashboards/meter-bills.json`
- [ ] Add dashboard to provisioning
- [ ] Test all panels with real Opower data
- [ ] Add to dashboard navigation

##### Step 4.6.5: First-Time Setup Flow - COMPLETE ✅
- [x] Document MFA setup process in `docs/COMED_OPOWER_SETUP.md`
- [x] Interactive setup wizard: `scripts/comed_opower_setup.py`
  - `--test` mode to verify configuration
  - `--status` mode to show current setup state
  - `--force` mode to re-authenticate
  - `--mfa-method` option for SMS or email
  - Runs locally (not in Docker) for easier MFA interaction
- [x] Store credentials securely in `.secrets` (updated `.secrets.example` with clear instructions)
- [x] Handle session expiry gracefully in collector
  - Clear error messages with prominent `====` banners
  - Exact fix instructions in error logs
  - Automatic cache migration from old location
- [x] Hot-reload cache file detection
  - Collector checks for cache file every 30 seconds when not authenticated
  - Auto-initializes Opower when cache file is detected (no restart needed)
  - Logs clear success message when cache is loaded
- [x] Token keep-alive mechanism
  - Refreshes token every 10 minutes (configurable via `OPOWER_TOKEN_REFRESH_INTERVAL`)
  - Warns when token is close to expiry
  - Logs prominent errors if refresh fails

#### Files Created/Modified

**Created Files:**
- `scripts/comed_auth.py` - Main authentication + data fetch script
- `scripts/.comed_token_cache.json` - Cached token + session cookies (gitignored)
- `docs/COMED_OPOWER_API.md` - Complete API reference documentation

**Files to Create (Collector Integration):**
- `collector/src/comed_client.py` - Client wrapper for collector
- `collector/src/config.py` - Add COMED_OPOWER_* config options

**Files to Modify:**
- `collector/src/main.py` - Add Opower polling to main loop
- `grafana/dashboards/energy-costs.json` - Add usage/cost panels

#### Available GraphQL Endpoints

| Endpoint | Status | Description |
|----------|--------|-------------|
| `WDB_GetMetadata` | ✅ Implemented | Rate plan, timezone, data range |
| `WDB_GetCostReadsForDayAndHour` | ✅ Implemented | Daily/hourly cost + usage |
| `WDB_GetUsageReadsForDayAndHourWithIntervalReads` | 🔲 TODO | 30-min intervals, peak periods |
| `WDB_GetWeather` | 🔲 TODO | Daily min/max/mean temperature |
| `WDB_GetCostUsageReadsForBills` | 🔲 TODO | Bill history with charge breakdowns |
| `WDB_GetNeighborComparisons` | 🔲 TODO | Compare vs similar neighbors |
| `WBAS_BillingAccounts` | 🔲 TODO | Full account details |
| `GetUsageInfo` (REST) | 🔲 TODO | Monthly summaries with YoY comparison |

See `docs/COMED_OPOWER_API.md` for complete endpoint documentation.

#### Key Technical Details

**Authentication Flow (10 Steps)**
1. Load ComEd login page → redirects to Azure B2C
2. Submit credentials
3. Confirm credentials
4. Select MFA method (email/SMS)
5. Confirm MFA selection
6. Send MFA code
7. User enters MFA code
8. Verify MFA code
9. Complete login → redirects to ComEd with OAuth code
10. Get Opower bearer token

**Session Persistence**
- Token cached in `.comed_token_cache.json`
- Includes 6 essential session cookies for token refresh
- Token lifetime: ~20 minutes
- Session can be refreshed without MFA while cookies valid

**Data Characteristics**
- Data delay: 24-48 hours
- Resolution: 30-minute intervals (HALF_HOUR)
- Historical data: Back to June 2023
- Rate plan: C-H70R (ComEd hourly pricing)

#### Sample Output

```
============================================================
COMED OPOWER AUTHENTICATION
============================================================
Username: user@example.com
MFA Method: email

Restored 6 session cookies
Using cached token (expires 2025-12-18 20:33:34)

Fetching data...

--- Usage Data (Last 7 Days) ---
  2025-12-12: 67.03 kWh
  2025-12-13: 36.03 kWh
  2025-12-14: 80.39 kWh
  2025-12-15: 34.58 kWh
  2025-12-16: 105.19 kWh
  2025-12-17: 49.63 kWh
  TOTAL: 372.85 kWh

--- Cost Data (Last 7 Days) ---
  2025-12-12: 67.03 kWh, $8.62
  2025-12-13: 36.03 kWh, $4.04
  2025-12-14: 80.39 kWh, $9.94
  2025-12-15: 34.58 kWh, $5.27
  2025-12-16: 105.19 kWh, $10.75
  2025-12-17: 49.63 kWh, $0.00
  TOTAL: $45.87

--- Account Metadata ---
  Rate Plan: C-H70R
  Resolution: HALF_HOUR
  Timezone: America/Chicago
```

**Note:** Costs from Opower include all fees (supply, delivery, taxes) - this is the "true" cost that appears on your bill.

---

### Phase 5: Advanced Features (Future) - NOT STARTED

#### Step 5.1: Charging Optimization
- [ ] Analyze historical pricing patterns
- [ ] Display optimal charging windows
- [ ] "What-if" scenario calculator
- [ ] Savings potential display
- [ ] ML-based price prediction (optional)

#### Step 5.2: Multi-Vehicle Optimization
- [ ] Prioritize vehicle charging based on SOC and schedule
- [ ] Load balancing recommendations when multiple vehicles present
- [ ] Per-vehicle cost tracking and reports

---

## Configuration Reference

### Environment Variables (.env)
```env
# InfluxDB
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=changeme_secure_password
INFLUXDB_ORG=home
INFLUXDB_BUCKET=twc_dashboard
INFLUXDB_ADMIN_TOKEN=changeme_influxdb_token

# Grafana
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=changeme_secure_password

# Wall Connectors (name:ip,name:ip)
TWC_CHARGERS=garage:192.168.1.100

# Polling Intervals (seconds)
TWC_POLL_VITALS_INTERVAL=5
TWC_POLL_LIFETIME_INTERVAL=60
TWC_POLL_VERSION_INTERVAL=300
TWC_POLL_WIFI_INTERVAL=60
COMED_POLL_INTERVAL=300

# ComEd Full Cost Calculation
# Delivery rate: ~7.5¢/kWh based on bill analysis
# Monthly fixed: ~$19.56 (customer charge + metering)
COMED_DELIVERY_PER_KWH=0.075
COMED_MONTHLY_FIXED=19.56

# Timezone
TZ=America/Chicago

# Tessie API (Phase 4)
# Tessie token is stored in .secrets file (not committed to git)
# See .secrets.example for format
TESSIE_ENABLED=true
TESSIE_POLL_INTERVAL=60

# Smart Charging (Phase 4.4)
# Uses adaptive percentile-based thresholds that adjust over time
SMART_CHARGING_ENABLED=true               # Enable data collection and dashboard panels
SMART_CHARGING_CONTROL_ENABLED=false      # Enable vehicle control (start/stop commands)
SMART_CHARGING_LOOKBACK_DAYS=30           # Days of price history for statistics
SMART_CHARGING_STOP_PERCENTILE=90         # Stop charging if price > this percentile
SMART_CHARGING_RESUME_PERCENTILE=75       # Resume charging when price < this percentile
SMART_CHARGING_MIN_INTERVAL=600           # Min seconds between start/stop commands
```

### Secrets File (.secrets)
```env
# This file contains sensitive API tokens
# DO NOT commit to git - it's in .gitignore
# Copy .secrets.example to .secrets and fill in your values

# Tessie API Token
# Generate at: https://dash.tessie.com/settings/api
TESSIE_ACCESS_TOKEN=your_tessie_token_here
```

### Access URLs
- Grafana: http://localhost:3080 (admin/changeme)
- REST API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- InfluxDB: http://localhost:8086

---

## Known Issues & Notes

1. **Time format** - AM/PM configured via Grafana environment variables in docker-compose.yml
2. **Multi-charger** - Use regex filter `=~ /${charger}/` in Flux queries for template variable support
3. **Session Cost** - Real-time session cost tracking requires a charging session to occur after deployment. If "No data" appears, it will populate during the next charge. Uses `twc_session_state` measurement during active charging.
4. **Session History** - The Session History dashboard will show "No data" until charging sessions complete after deployment. Data populates in `twc_session` measurement when sessions end.
5. **Full Cost Estimation** - Dashboard `delivery_rate` variable defaults to 7.5¢/kWh based on ComEd bill analysis. Adjust via Grafana dashboard variables if your delivery rate differs.
6. **Alerts** - Alert notifications require configuring a contact point in `grafana/provisioning/alerting/contactpoints.yml`. By default, alerts are logged but not sent externally. Alerts are stored in the "Tesla Wall Connector" folder.
7. **Temperature Units** - Dashboards include a `temp_unit` dropdown (°F / °C). Conversion is done in Flux queries since Grafana's `unit` field doesn't support variable interpolation. Panel titles show the selected unit dynamically. **Note:** Grafana axis labels do NOT support variable interpolation - use empty axis labels and put the unit in the panel title instead.
8. **Vehicle Data** - Vehicle panels use `r.display_name != ""` filter to exclude old records that don't have the display_name tag set. Temperature panels look back 24 hours and show cached data when the car is asleep (temps only update when car is awake).
9. **Stat Panels with String Values** - When displaying string values (like "Online", "Disconnected") in stat panels with value mappings, use `"reduceOptions": {"calcs": [], "fields": "/_value/", "values": true}` and `"textMode": "value"`. Do NOT include `"decimals"` for string fields. See CLAUDE.md for full documentation.
10. **Tessie Vehicle Data Timing** - Tessie returns cached/stale vehicle data during overnight charging when the vehicle is asleep. The vehicle may show "Disconnected" with 0 kW power even while actively charging according to Fleet API. This is why charging efficiency tracking was removed - Fleet API `telemetry_history` provides authoritative session data and is used directly instead of trying to correlate with vehicle data.

11. **Fleet API telemetry_history Latency** - The Tesla Fleet API `telemetry_history?kind=charge` endpoint has severe undocumented latency. Charging sessions may not appear for **hours or even days** after completion. This was verified through testing on 2025-12-08:
    - Started/stopped a 10-minute charge session
    - Tessie `/{vin}/charges` had the session within 2 seconds
    - Fleet API `telemetry_history` still returned `null` 15+ minutes later
    - Sessions from 6+ hours earlier were also missing
    - Tesla's official documentation has no information about update timing
    - **Workaround**: Use Tessie `/{vin}/charges` as primary session source, correlate with `live_status` VIN tracking for Wall Connector attribution. See Step 4.5.9 for implementation plan.

---

## Resources

### Documentation
- [InfluxDB 2.x Documentation](https://docs.influxdata.com/influxdb/v2/)
- [Grafana Documentation](https://grafana.com/docs/)
- [ComEd Hourly Pricing](https://hourlypricing.comed.com/)

### Reference Projects
- [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard)
- [tesla-wall-connector](https://github.com/einarhauks/tesla-wall-connector)

### Tessie API (Phase 4)
- [Tessie Developer Quick Start](https://developer.tessie.com/reference/quick-start)
- [Tessie Fleet API Access](https://developer.tessie.com/reference/access-tesla-fleet-api)
- [Tessie Fleet Telemetry](https://developer.tessie.com/reference/access-tesla-fleet-telemetry)
- [Tessie API Reference](https://developer.tessie.com/reference/about)
- [Tessie Dashboard - API Token](https://dash.tessie.com/settings/api)
