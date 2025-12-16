# Tesla Wall Connector Dashboard

A real-time monitoring dashboard for Tesla Wall Connector Gen 3 chargers with ComEd hourly pricing integration. Track your EV charging energy usage and costs.

## Features

- **Real-time Monitoring**: Live power draw, voltage, current, and temperature data
- **Multi-Charger Support**: Monitor multiple Wall Connectors in one dashboard
- **ComEd Hourly Pricing**: Live electricity pricing with cost calculations
- **Session Tracking**: Automatic detection and logging of charging sessions with real-time cost tracking
- **Full Cost Estimation**: Includes supply + delivery rates for accurate cost calculations (based on ComEd bill analysis)
- **Real-time Session Cost**: See charging costs accumulate during active sessions using actual prices
- **Cost Analysis**: Hourly, daily, weekly, and monthly cost breakdowns
- **Optimal Charging**: Identify the cheapest times to charge
- **REST API**: Programmatic access to all data with export capabilities
- **Data Export**: CSV, JSON, and PDF report generation

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Tesla Wall Connector Gen 3 on your local network
- ComEd Hourly Pricing customer (for accurate pricing)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/Tesla-Wall-Connector-Dashboard.git
   cd Tesla-Wall-Connector-Dashboard
   ```

2. **Configure your environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Wall Connector IP(s)
   ```

3. **Start the services**
   ```bash
   docker-compose up -d
   ```

4. **Access the services**
   - Grafana: http://localhost:3000 (admin/your_grafana_password)
   - API: http://localhost:8000 (REST API & docs)
   - InfluxDB: http://localhost:8086

## Configuration

### Wall Connectors

Edit `.env` to add your Wall Connector(s):

```env
# Single charger
TWC_CHARGERS=garage:192.168.1.100

# Multiple chargers
TWC_CHARGERS=garage_left:192.168.1.100,garage_right:192.168.1.101
```

### Polling Intervals

Adjust how frequently data is collected:

```env
TWC_POLL_VITALS_INTERVAL=5      # Real-time data (seconds)
TWC_POLL_LIFETIME_INTERVAL=60   # Lifetime stats (seconds)
COMED_POLL_INTERVAL=300         # Price updates (seconds)
```

### Cost Calculation

The dashboard shows both supply costs (from ComEd Hourly Pricing API) and estimated full costs. The ComEd API only provides the **supply rate** - your actual bill includes additional charges.

**What the "Delivery Rate" setting should include:**
- **Delivery Services**: Distribution, transmission, environmental charges (~5-6¢/kWh)
- **Capacity Charge**: Based on your peak usage during PJM peak hours (varies by customer)
- **Taxes & Fees**: State/local taxes, regulatory fees (~1-2¢/kWh)

> **Note**: ComEd's website states "kWh prices do not include your personal Capacity Charge." The capacity charge is calculated based on your peak demand, not a flat per-kWh rate, so we approximate it.

**How to calculate your rate from your bill:**
1. Take your total bill amount
2. Subtract the supply charges (the hourly pricing portion)
3. Subtract fixed monthly charges (~$19-20 for customer charge + metering)
4. Divide by kWh used = your non-supply rate per kWh

```env
# Non-supply rate (delivery + capacity + taxes), typically 7-9¢/kWh
COMED_DELIVERY_PER_KWH=0.075
COMED_MONTHLY_FIXED=19.56
```

You can also adjust the `delivery_rate` variable directly in the Grafana dashboards without restarting services.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  TWC Collector  │────▶│    InfluxDB     │◀────│     Grafana     │
│    (Python)     │     │  (Time-Series)  │     │   (Dashboard)   │
└────────┬────────┘     └─────────────────┘     └────────┬────────┘
         │                       ▲                       │
    ┌────┴────┐                  │                       │
    ▼         ▼                  │                       │
┌───────┐  ┌───────┐     ┌──────┴────────┐              │
│  TWC  │  │ ComEd │     │   REST API    │──────────────┘
│  API  │  │  API  │     │   (FastAPI)   │  Export: CSV/JSON/PDF
└───────┘  └───────┘     └───────────────┘  WebSocket: Real-time
```

## Dashboards

### Live Overview
- Current power draw (real-time watts)
- Vehicle connection status (Connected/Not Connected)
- Charging status (Charging/Idle)
- Session energy counter (kWh)
- Lifetime energy (MWh)
- **Supply Rate**: Current ComEd hourly price with color-coded thresholds
- **Full Rate (Est.)**: Supply + delivery rate for true cost estimation
- **Session Cost**: Real-time cost tracking during active charging
- Power draw chart over time
- Temperature monitoring (PCBA, Handle, MCU)
- Grid voltage and frequency
- WiFi signal strength
- Uptime and firmware version

### Energy & Costs
- Total energy for selected time period
- Total charge sessions
- **Average Supply Price** and **Average Full Rate (Est.)**
- **Estimated Total Cost** (including delivery charges)
- Total charging time
- Hourly energy consumption (bar chart)
- Hourly price trends with color thresholds (green/yellow/orange/red)
- Cumulative energy tracking
- Best charging hours table (sorted by lowest price)
- Price by hour of day visualization

### Session History
- Total sessions, energy, and costs for selected period
- Daily cost breakdown chart (supply vs full cost)
- Daily energy usage chart
- Session history table with detailed cost breakdown
- Weekly and monthly summary tables

### Price Thresholds
Prices are color-coded for quick decision making:
- **Green**: < 4¢/kWh - Great time to charge!
- **Yellow**: 4-6¢/kWh - Moderate pricing
- **Orange**: 6-8¢/kWh - Getting expensive
- **Red**: > 8¢/kWh - Avoid if possible

### Alerting
Built-in alerts notify you of important events:
- **Low Price Alert**: When electricity is below 3¢/kWh (great time to charge)
- **High Price Alert**: When electricity is above 8¢/kWh (avoid charging)
- **Session Complete**: When a charging session finishes
- **Charger Offline**: When the Wall Connector stops responding
- **High Temperature**: When charger temperature exceeds 60°C

Configure notifications in `grafana/provisioning/alerting/contactpoints.yml` (supports email, Discord, Slack, Pushover, Telegram).

## REST API

The dashboard includes a REST API for programmatic access to your charging data.

**Access**: http://localhost:8000

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /chargers` | List all charger IDs |
| `GET /chargers/status` | Current status for all chargers |
| `GET /chargers/{id}/status` | Current status for specific charger |
| `GET /chargers/{id}/lifetime` | Lifetime statistics |
| `GET /chargers/{id}/info` | Firmware and hardware info |
| `GET /price/current` | Current electricity price |
| `GET /price/history` | Historical prices for date range |
| `GET /sessions` | Charging sessions for date range |
| `GET /sessions/summary` | Summary statistics |
| `GET /export/sessions.csv` | Export sessions as CSV |
| `GET /export/sessions.json` | Export sessions as JSON |
| `GET /export/prices.csv` | Export prices as CSV |
| `GET /export/report.pdf` | Generate PDF report |
| `WS /ws` | WebSocket for real-time updates |

### Interactive Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Example Usage

```bash
# Get current charger status
curl http://localhost:8000/chargers/status

# Get current price
curl http://localhost:8000/price/current

# Export last 30 days of sessions as CSV
curl -o sessions.csv "http://localhost:8000/export/sessions.csv"

# Generate PDF report for December
curl -o report.pdf "http://localhost:8000/export/report.pdf?start=2024-12-01&end=2024-12-31"
```

### WebSocket Real-time Updates

Connect to `ws://localhost:8000/ws` for real-time status updates every 5 seconds:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Charger status:', data.chargers);
  console.log('Current price:', data.price);
};
```

## Wall Connector API

The Tesla Wall Connector Gen 3 exposes a local REST API (no authentication required):

| Endpoint | Description |
|----------|-------------|
| `/api/1/vitals` | Real-time operational data (power, voltage, temps, session info) |
| `/api/1/lifetime` | Cumulative statistics (total energy, sessions, uptime) |
| `/api/1/version` | Firmware and hardware information |
| `/api/1/wifi_status` | Network status including MAC address |

**Note**: The API is read-only. Charging control must be done via the Tesla app or vehicle API.

**Power Sharing Limitation**: When using power sharing, only the leader unit is accessible via API. Follower units connect to the leader via a private 192.168.92.x network and cannot be polled directly.

See [docs/WALL_CONNECTOR_API.md](docs/WALL_CONNECTOR_API.md) for complete API reference with field descriptions.

## ComEd Hourly Pricing

Pricing data is fetched from ComEd's public API:
- Current hour average price
- 5-minute price updates
- Historical pricing data

## Troubleshooting

### Can't connect to Wall Connector
1. Verify the IP address: `curl http://YOUR_IP/api/1/vitals`
2. Ensure the charger is on the same network
3. Check firewall settings

### No data in Grafana
1. Check collector logs: `docker-compose logs collector`
2. Verify InfluxDB is running: `docker-compose ps`
3. Wait 30 seconds for initial data collection

### Session History shows "No data"
This is expected after initial deployment. The Session History dashboard displays completed charging sessions stored in the `twc_session` measurement, which only populates when charging sessions **complete** after the collector is running. Once your next charging session finishes, data will appear.

### Container startup issues
```bash
# View all logs
docker-compose logs

# Restart services
docker-compose restart

# Rebuild collector
docker-compose build collector
```

## Development

### Local Python development
```bash
cd collector
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m src.main
```

### Adding a new charger
1. Find the charger's IP address
2. Test connectivity: `curl http://NEW_IP/api/1/vitals`
3. Add to `.env`: `TWC_CHARGERS=garage:192.168.1.100,new_charger:NEW_IP`
4. Restart: `docker-compose restart collector`

## Unraid Deployment

For Unraid users, a dedicated deployment guide with automatic updates is available:

1. **Clone to Unraid**: `/mnt/user/appdata/twc-dashboard`
2. **Configure**: Copy `.env.example` to `.env` and `.secrets.example` to `.secrets`
3. **Deploy**: Use `docker-compose.unraid.yml` with Docker Compose Manager
4. **Auto-Updates**: Watchtower automatically pulls new images when you push to GitHub

See [docs/UNRAID_DEPLOYMENT.md](docs/UNRAID_DEPLOYMENT.md) for complete instructions.

### Automatic Updates

When you push changes to your GitHub repository:
1. GitHub Actions builds new Docker images
2. Images are published to GitHub Container Registry
3. Watchtower on Unraid detects and applies updates automatically

## Future Enhancements

- [ ] Home Assistant integration (MQTT sensors)
- [ ] Tesla Vehicle API integration (for charge control)
- [ ] Mobile-friendly dashboard views
- [ ] Year-over-year cost comparison

## Credits

Inspired by:
- [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard)
- [tesla-gen3wc-monitor](https://github.com/averysmalldog/tesla-gen3wc-monitor)
- [tesla-wall-connector](https://github.com/einarhauks/tesla-wall-connector)

## License

MIT License - See LICENSE file for details
