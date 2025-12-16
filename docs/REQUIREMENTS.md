# Tesla Wall Connector Dashboard - Requirements Document

## Project Overview

A monitoring dashboard for Tesla Wall Connectors (Gen 3) that tracks energy usage, integrates with ComEd hourly pricing, and provides actionable insights for EV charging cost optimization.

---

## Research Summary

### Tesla Wall Connector Gen 3 API

**Available Endpoints (Local Network, No Authentication Required):**

| Endpoint | Description |
|----------|-------------|
| `http://<IP>/api/1/vitals` | Real-time operational data |
| `http://<IP>/api/1/lifetime` | Cumulative lifetime statistics |
| `http://<IP>/api/1/version` | Firmware and device information |
| `http://<IP>/api/1/wifi_status` | Network connectivity status |

**Key Metrics Available:**

*Real-time (vitals):*
- `vehicle_connected` - Boolean, vehicle plugged in
- `contactor_closed` - Boolean, actively charging
- `session_energy_wh` - Energy delivered this session
- `vehicle_current_a` - Current being supplied
- `grid_v`, `grid_hz` - Grid voltage and frequency
- `voltageA_v`, `voltageB_v`, `voltageC_v` - Phase voltages
- `currentA_a`, `currentB_a`, `currentC_a` - Phase currents
- `pcba_temp_c`, `handle_temp_c`, `mcu_temp_c` - Temperatures
- `session_s` - Session duration in seconds
- `evse_state` - Charger state code
- `current_alerts` - Active alerts array

*Lifetime:*
- `energy_wh` - Total lifetime energy delivered
- `charge_starts` - Number of charging sessions
- `charging_time_s` - Total charging time
- `contactor_cycles` - Contactor cycle count
- `uptime_s` - Total uptime

**Charging Control Limitation:**
> **IMPORTANT:** The Wall Connector Gen 3 API is **READ-ONLY**. There is no documented or reverse-engineered method to start/stop charging or adjust amperage via the local API.

**Alternative Control Methods:**
1. **Tesla Vehicle API** (via TeslaPy) - Can start/stop charging on the *vehicle* side
2. **Tesla App Scheduled Charging** - Configure charge windows in the Tesla app
3. **Smart Circuit Breaker** - Physical relay control (not recommended)

### ComEd Hourly Pricing API

**Endpoints (No Authentication Required):**

| Endpoint | Description |
|----------|-------------|
| `https://hourlypricing.comed.com/api?type=currenthouraverage` | Current hour average price |
| `https://hourlypricing.comed.com/api?type=5minutefeed` | Last 24 hours, 5-minute intervals |
| `https://hourlypricing.comed.com/api?type=5minutefeed&datestart=YYYYMMDDhhmm&dateend=YYYYMMDDhhmm` | Historical range query |

**Response Format (JSON):**
```json
[
  {"millisUTC": 1701648000000, "price": "3.5"},
  {"millisUTC": 1701648300000, "price": "3.7"}
]
```

**Supported Output Formats:** JSON (default), Text, RSS

---

## Functional Requirements

### FR-1: Data Collection
- [ ] FR-1.1: Poll Tesla Wall Connector vitals at configurable interval (default: 5 seconds)
- [ ] FR-1.2: Poll Tesla Wall Connector lifetime stats at configurable interval (default: 60 seconds)
- [ ] FR-1.3: Poll ComEd hourly pricing at 5-minute intervals
- [ ] FR-1.4: Support multiple Wall Connectors (garage may have 2+)
- [ ] FR-1.5: Store all data in time-series database with configurable retention

### FR-2: Dashboard Views
- [ ] FR-2.1: **Live View** - Real-time power flow, current session stats, current electricity price
- [ ] FR-2.2: **Session History** - List of charging sessions with duration, energy, and cost
- [ ] FR-2.3: **Hourly Analysis** - Energy usage and costs broken down by hour
- [ ] FR-2.4: **Daily Summary** - Daily totals with cost breakdown
- [ ] FR-2.5: **Weekly Summary** - Weekly trends and comparisons
- [ ] FR-2.6: **Monthly Summary** - Monthly totals, averages, and cost analysis
- [ ] FR-2.7: **Yearly Summary** - Annual overview and year-over-year comparison
- [ ] FR-2.8: **Price Overlay** - Show ComEd pricing alongside energy usage

### FR-3: Cost Calculation
- [ ] FR-3.1: Calculate real-time charging cost using ComEd 5-minute pricing
- [ ] FR-3.2: Calculate session total cost with actual prices during charging
- [ ] FR-3.3: Calculate "what-if" scenarios (e.g., "if I charged at 2 AM instead")
- [ ] FR-3.4: Show potential savings from optimal charging times
- [ ] FR-3.5: Support ComEd hourly pricing fees and delivery charges

### FR-4: Alerts & Notifications
- [ ] FR-4.1: Alert when price drops below configurable threshold
- [ ] FR-4.2: Alert when charging session completes
- [ ] FR-4.3: Alert on Wall Connector errors/faults
- [ ] FR-4.4: Daily/weekly cost summary notifications

### FR-5: Data Export
- [ ] FR-5.1: Export data to CSV format
- [ ] FR-5.2: Export data to JSON format
- [ ] FR-5.3: Export Grafana dashboard snapshots
- [ ] FR-5.4: Generate PDF reports (monthly/yearly)
- [ ] FR-5.5: API endpoint for external integrations

### FR-6: Charging Optimization (Future/Conditional)
- [ ] FR-6.1: Display optimal charging windows based on price forecast
- [ ] FR-6.2: Integration with Tesla Vehicle API for charge scheduling (requires Tesla account)
- [ ] FR-6.3: Time-of-use (TOU) recommendations
- [ ] FR-6.4: Consider weather/solar data for optimization (if solar present)

### FR-7: Home Assistant Integration
- [ ] FR-7.1: Expose sensors via MQTT or REST API
- [ ] FR-7.2: Provide Home Assistant add-on or integration config
- [ ] FR-7.3: Support HA automations based on price thresholds

---

## Non-Functional Requirements

### NFR-1: Performance
- System should handle 10+ samples per second from Wall Connector
- Dashboard should load within 3 seconds
- Data queries should complete within 1 second for standard time ranges

### NFR-2: Reliability
- Data collection should continue through network interruptions (queue and retry)
- System should auto-recover from crashes
- No data loss during system restarts

### NFR-3: Storage
- Support minimum 2 years of historical data
- Automatic data downsampling for older records
- Configurable retention policies

### NFR-4: Security
- Dashboard accessible only on local network by default
- Optional authentication for remote access
- No credentials stored in plain text

### NFR-5: Deployment
- Docker-based deployment for easy setup
- Support Windows, Linux, and Raspberry Pi
- One-command installation script

---

## Technical Constraints

1. **Wall Connector API is read-only** - Cannot directly control charging
2. **Wall Connector must be on same network** - Local API only, no cloud access
3. **ComEd pricing is Illinois/Chicago area only** - May need alternative for other utilities
4. **Firmware variations** - API responses may vary slightly between firmware versions

---

## Out of Scope (Phase 1)

- Solar/Powerwall integration (separate project exists)
- Multi-site/remote location monitoring
- Mobile app (web dashboard is mobile-responsive)
- Direct Wall Connector firmware updates
- Non-Tesla EV charger support

---

## Success Metrics

1. Accurate cost tracking within 1% of actual ComEd bill
2. Dashboard uptime > 99.9%
3. Data collection gap < 1 minute per day
4. User can identify optimal charging windows at a glance
5. Monthly reports generated automatically

---

## Reference Projects

| Project | Relevance |
|---------|-----------|
| [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard) | Architecture pattern (Grafana + InfluxDB + Docker) |
| [tesla-gen3wc-monitor](https://github.com/averysmalldog/tesla-gen3wc-monitor) | Wall Connector polling approach (Polly + Go) |
| [tesla-wall-connector](https://github.com/einarhauks/tesla-wall-connector) | Python async API wrapper |
| [TeslaHourlyOptimizer](https://github.com/jhu321/TeslaHourlyOptimizer) | ComEd + Tesla vehicle charging optimization |
| [homebridge-comed-hourlypricing](https://github.com/hjdhjd/homebridge-comed-hourlypricing) | ComEd API integration pattern |
