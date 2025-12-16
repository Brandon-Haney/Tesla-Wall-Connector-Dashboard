# Tesla Wall Connector Dashboard - Technical Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DOCKER COMPOSE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐       │
│  │                 │     │                 │     │                 │       │
│  │  TWC Collector  │────▶│    InfluxDB     │◀────│    Telegraf     │       │
│  │   (Python)      │     │   (Time-Series) │     │   (Optional)    │       │
│  │                 │     │                 │     │                 │       │
│  └────────┬────────┘     └────────┬────────┘     └─────────────────┘       │
│           │                       │                                         │
│           │              ┌────────┴────────┐                               │
│           │              │                 │                               │
│           │              │     Grafana     │◀──── Web Browser              │
│           │              │   (Dashboard)   │      (Port 3000)              │
│           │              │                 │                               │
│           │              └─────────────────┘                               │
│           │                                                                 │
│  ┌────────┴────────┐     ┌─────────────────┐                               │
│  │                 │     │                 │                               │
│  │  ComEd Poller   │────▶│     Redis       │                               │
│  │   (Python)      │     │    (Cache)      │                               │
│  │                 │     │                 │                               │
│  └─────────────────┘     └─────────────────┘                               │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐       │
│  │                        API Gateway (FastAPI)                     │       │
│  │  - REST API for external integrations                           │       │
│  │  - WebSocket for real-time updates                              │       │
│  │  - Export endpoints (CSV, JSON, PDF)                            │       │
│  │  - Home Assistant MQTT bridge                                   │       │
│  └─────────────────────────────────────────────────────────────────┘       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                    │                                    │
                    ▼                                    ▼
        ┌───────────────────┐                ┌───────────────────┐
        │  Tesla Wall       │                │  ComEd Hourly     │
        │  Connector(s)     │                │  Pricing API      │
        │  (Local Network)  │                │  (Internet)       │
        └───────────────────┘                └───────────────────┘
```

---

## Component Details

### 1. TWC Collector (Python Service)

**Purpose:** Poll Tesla Wall Connector(s) and write metrics to InfluxDB

**Technology:** Python 3.11+ with asyncio

**Key Libraries:**
- `aiohttp` - Async HTTP client for Wall Connector API
- `influxdb-client` - InfluxDB 2.x Python client
- `pydantic` - Data validation and settings

**Polling Strategy:**
```
vitals     → Every 5 seconds (configurable)
lifetime   → Every 60 seconds (configurable)
version    → Every 5 minutes (configurable)
wifi_status → Every 60 seconds (configurable)
```

**Data Model (InfluxDB Measurements):**

```
twc_vitals
├── tags: charger_id, charger_name
└── fields: vehicle_connected, contactor_closed, session_energy_wh,
            vehicle_current_a, grid_v, grid_hz, voltageA_v, voltageB_v,
            voltageC_v, currentA_a, currentB_a, currentC_a, pcba_temp_c,
            handle_temp_c, mcu_temp_c, session_s, evse_state

twc_lifetime
├── tags: charger_id, charger_name
└── fields: energy_wh, charge_starts, charging_time_s, contactor_cycles,
            uptime_s, connector_cycles, alert_count, thermal_foldbacks

twc_session (derived/calculated)
├── tags: charger_id, charger_name
└── fields: start_time, end_time, duration_s, energy_wh, avg_power_w,
            peak_power_w, total_cost, avg_price
```

### 2. ComEd Poller (Python Service)

**Purpose:** Fetch ComEd hourly pricing and cache for cost calculations

**Technology:** Python 3.11+ with asyncio

**Polling Strategy:**
```
currenthouraverage → Every 5 minutes
5minutefeed        → Every 5 minutes (last 24 hours)
Historical backfill → On startup, then daily
```

**Data Model:**

```
comed_price
├── tags: price_type (5min, hourly_avg)
└── fields: price_cents_kwh

comed_price_hourly (downsampled)
├── tags: none
└── fields: avg_price, min_price, max_price
```

### 3. InfluxDB 2.x

**Purpose:** Time-series data storage

**Configuration:**
- Bucket: `twc_dashboard`
- Retention: 2 years (raw data), infinite (downsampled)
- Downsampling Tasks:
  - 5-second data → 1-minute averages after 7 days
  - 1-minute data → 5-minute averages after 30 days
  - 5-minute data → 1-hour averages after 1 year

### 4. Grafana

**Purpose:** Visualization and dashboards

**Dashboards:**

| Dashboard | Description |
|-----------|-------------|
| Live Overview | Real-time power flow, current session, live pricing |
| Session History | Table of all charging sessions with costs |
| Daily Analysis | 24-hour energy and cost breakdown |
| Weekly Trends | 7-day comparison and patterns |
| Monthly Summary | Monthly totals, averages, top charging times |
| Yearly Overview | Annual statistics and year-over-year comparison |
| Price Analysis | ComEd pricing trends, optimal charging windows |
| System Health | Charger temperatures, uptime, errors |

**Alerting:**
- Grafana Alerting for notifications
- Channels: Email, Slack, Discord, Pushover, Webhook

### 5. API Gateway (FastAPI)

**Purpose:** External integrations and data export

**Endpoints:**

```
GET  /api/v1/chargers                    - List configured chargers
GET  /api/v1/chargers/{id}/status        - Current charger status
GET  /api/v1/chargers/{id}/sessions      - Charging session history
GET  /api/v1/price/current               - Current ComEd price
GET  /api/v1/price/history               - Historical pricing
GET  /api/v1/cost/session/{id}           - Cost breakdown for session
GET  /api/v1/cost/summary                - Cost summary (day/week/month/year)
GET  /api/v1/export/csv                  - Export data as CSV
GET  /api/v1/export/json                 - Export data as JSON
GET  /api/v1/export/report               - Generate PDF report
WS   /api/v1/ws/live                     - WebSocket for real-time updates
```

### 6. Redis (Optional)

**Purpose:** Caching and session state management

**Use Cases:**
- Cache current ComEd price (reduce API calls)
- Store active session state for quick lookups
- Rate limiting for API Gateway
- Pub/Sub for real-time WebSocket broadcasts

### 7. Home Assistant Integration

**Options:**

**Option A: MQTT Discovery**
- Publish sensors to MQTT with HA discovery
- Auto-configures entities in Home Assistant
- Supports: current_power, session_energy, price, vehicle_connected

**Option B: REST Sensor**
- HA polls our API Gateway
- More flexible but requires manual configuration

**Option C: Custom Integration**
- Full-featured HA integration (HACS installable)
- Most work but best user experience

**Recommended:** Start with MQTT Discovery, add Custom Integration later

---

## Directory Structure

```
tesla-wall-connector-dashboard/
├── docker-compose.yml           # Main orchestration
├── .env.example                 # Environment template
├── setup.sh                     # Setup script (Linux/Mac)
├── setup.ps1                    # Setup script (Windows)
├── README.md                    # Project documentation
│
├── docs/
│   ├── REQUIREMENTS.md          # Requirements document
│   ├── ARCHITECTURE.md          # This file
│   ├── SETUP.md                 # Installation guide
│   └── API.md                   # API documentation
│
├── collector/                   # TWC Collector service
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py              # Entry point
│   │   ├── config.py            # Configuration
│   │   ├── twc_client.py        # Wall Connector API client
│   │   ├── comed_client.py      # ComEd API client
│   │   ├── influx_writer.py     # InfluxDB writer
│   │   ├── session_tracker.py   # Session detection/tracking
│   │   └── models.py            # Data models
│   └── tests/
│
├── api/                         # API Gateway service
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app
│   │   ├── routers/
│   │   ├── services/
│   │   └── models/
│   └── tests/
│
├── grafana/                     # Grafana configuration
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── influxdb.yml
│   │   └── dashboards/
│   │       └── dashboards.yml
│   └── dashboards/
│       ├── live-overview.json
│       ├── session-history.json
│       ├── daily-analysis.json
│       ├── weekly-trends.json
│       ├── monthly-summary.json
│       ├── yearly-overview.json
│       └── price-analysis.json
│
├── influxdb/                    # InfluxDB configuration
│   ├── config/
│   └── scripts/
│       └── init.sh              # Initialization script
│
└── homeassistant/               # Home Assistant integration
    ├── mqtt_sensors.yaml        # MQTT sensor config
    └── custom_component/        # Future: custom integration
```

---

## Technology Stack Summary

| Component | Technology | Version | Rationale |
|-----------|------------|---------|-----------|
| Container Runtime | Docker + Compose | Latest | Industry standard, easy deployment |
| Time-Series DB | InfluxDB | 2.x | Native for metrics, powerful queries |
| Dashboard | Grafana | 10.x | Proven, extensible, great visualizations |
| Collector | Python | 3.11+ | Async support, existing TWC libraries |
| API Framework | FastAPI | 0.100+ | Modern, fast, auto-documentation |
| Cache | Redis | 7.x | Optional, for performance |
| Message Broker | MQTT (Mosquitto) | 2.x | For Home Assistant integration |

---

## Deployment Options

### Option 1: Docker Compose (Recommended)
- Single `docker-compose up -d` command
- All services orchestrated together
- Works on Windows, Linux, Mac, Raspberry Pi

### Option 2: Kubernetes/Helm
- For advanced users with existing k8s cluster
- Helm chart provided for easy deployment

### Option 3: Bare Metal
- Individual service installation
- More control, more complexity
- Documentation provided but not primary focus

---

## Security Considerations

1. **Network Isolation**
   - Services communicate on internal Docker network
   - Only Grafana (3000) and API (8000) exposed externally
   - Wall Connector communication stays on LAN

2. **Authentication**
   - Grafana: Built-in auth (local users, LDAP, OAuth)
   - API: Optional JWT authentication
   - InfluxDB: Token-based authentication

3. **Secrets Management**
   - Environment variables for sensitive config
   - Docker secrets support for production
   - No hardcoded credentials

4. **Data Privacy**
   - All data stored locally
   - No cloud dependencies (except ComEd API)
   - Optional: Encrypt data at rest

---

## Scalability Notes

**Current Design Supports:**
- Up to 10 Wall Connectors
- 2+ years of data retention
- Multiple concurrent dashboard users

**If Scaling Needed:**
- InfluxDB clustering for high-availability
- Multiple collector instances with load balancing
- Grafana behind reverse proxy with caching

---

## Future Enhancements

1. **Tesla Vehicle API Integration**
   - Control charging from dashboard
   - Automatic scheduling based on price

2. **Solar/Powerwall Integration**
   - Import data from pyPowerwall
   - Show solar-charged vs grid-charged

3. **Multi-Utility Support**
   - Abstract pricing provider
   - Support other utilities beyond ComEd

4. **Machine Learning**
   - Predict optimal charging times
   - Anomaly detection for charger health

5. **Mobile App**
   - React Native companion app
   - Push notifications
