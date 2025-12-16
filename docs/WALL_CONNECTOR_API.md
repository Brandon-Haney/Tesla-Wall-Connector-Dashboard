# Tesla Wall Connector Gen 3 API Reference

The Tesla Wall Connector Gen 3 exposes a local REST API on port 80. The API is **read-only** and requires **no authentication**.

## Working Endpoints

### GET /api/1/vitals

Real-time operational data. This is the primary endpoint for monitoring.

**Response Fields:**

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `contactor_closed` | boolean | Whether the charging contactor is engaged | `false` |
| `vehicle_connected` | boolean | Whether a vehicle is plugged in | `true` |
| `session_s` | integer | Current session duration in seconds | `0` |
| `grid_v` | float | Grid voltage (V) | `243.4` |
| `grid_hz` | float | Grid frequency (Hz) | `59.971` |
| `vehicle_current_a` | float | Current flowing to vehicle (A) | `0.0` |
| `currentA_a` | float | Phase A current (A) | `0.2` |
| `currentB_a` | float | Phase B current (A) | `0.1` |
| `currentC_a` | float | Phase C current (A) | `0.0` |
| `currentN_a` | float | Neutral current (A) | `0.0` |
| `voltageA_v` | float | Phase A voltage (V) | `0.0` |
| `voltageB_v` | float | Phase B voltage (V) | `0.0` |
| `voltageC_v` | float | Phase C voltage (V) | `0.0` |
| `relay_coil_v` | float | Relay coil voltage (V) | `11.8` |
| `pcba_temp_c` | float | PCB assembly temperature (°C) | `24.1` |
| `handle_temp_c` | float | Charging handle temperature (°C) | `20.3` |
| `mcu_temp_c` | float | MCU temperature (°C) | `28.6` |
| `uptime_s` | integer | Uptime in seconds | `140876` |
| `input_thermopile_uv` | integer | Thermopile sensor reading (µV) | `-142` |
| `prox_v` | float | Proximity pilot voltage (V) | `2.285` |
| `pilot_high_v` | float | Pilot signal high voltage (V) | `11.989` |
| `pilot_low_v` | float | Pilot signal low voltage (V) | `11.989` |
| `session_energy_wh` | float | Energy delivered this session (Wh) | `0.0` |
| `config_status` | integer | Configuration status code | `5` |
| `evse_state` | integer | EVSE state code | `1` |
| `current_alerts` | array | Active alert codes | `[]` |

**EVSE State Codes:**
- `0` - Starting
- `1` - Idle (not connected)
- `2` - Connected, not charging
- `4` - Charging
- `5` - Error/Fault

---

### GET /api/1/lifetime

Cumulative statistics since installation.

**Response Fields:**

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `contactor_cycles` | integer | Total contactor open/close cycles | `65` |
| `contactor_cycles_loaded` | integer | Cycles under load | `63` |
| `alert_count` | integer | Total alerts triggered | `158` |
| `thermal_foldbacks` | integer | Thermal protection events | `0` |
| `avg_startup_temp` | float | Average startup temperature (°C) | `17.4` |
| `charge_starts` | integer | Total charging sessions started | `61` |
| `energy_wh` | integer | Total energy delivered (Wh) | `5424260` |
| `connector_cycles` | integer | Plug connect/disconnect cycles | `90` |
| `uptime_s` | integer | Total uptime in seconds | `6739474` |
| `charging_time_s` | integer | Total time spent charging (s) | `527618` |

---

### GET /api/1/wifi_status

Network connectivity information.

**Response Fields:**

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `wifi_ssid` | string | Connected WiFi network name | `"YourNetwork"` |
| `wifi_signal_strength` | integer | Signal strength (dBm, negative) | `-47` |
| `wifi_rssi` | integer | RSSI value | `-47` |
| `wifi_snr` | integer | Signal-to-noise ratio | `0` |
| `wifi_connected` | boolean | WiFi connection status | `true` |
| `wifi_infra_ip` | string | IP address on network | `"192.168.1.100"` |
| `internet` | boolean | Internet connectivity status | `true` |
| `wifi_mac` | string | MAC address | `"AA:BB:CC:DD:EE:FF"` |

---

### GET /api/1/version

Firmware and hardware information.

**Response Fields:**

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `firmware_version` | string | Current firmware version | `"25.34.1+ge48cc9be91ebc7"` |
| `part_number` | string | Hardware part number | `"1457768-01-G"` |
| `serial_number` | string | Unit serial number | `"ABC12345678"` |

---

## Non-Working Endpoints

The following endpoints were tested and returned errors:

| Endpoint | Status | Notes |
|----------|--------|-------|
| `/api/1/status` | 500 | Internal Server Error |
| `/api/1/config` | 500 | Internal Server Error |
| `/api/1/network` | 500 | Internal Server Error |
| `/api/1/power_sharing` | 500 | Internal Server Error |
| `/api/1/followers` | 500 | Internal Server Error |
| `/api/1/slaves` | 500 | Internal Server Error |
| `/api/1/devices` | 500 | Internal Server Error |
| `/api/1/group` | 500 | Internal Server Error |
| `/api/2/vitals` | 404 | Not Found |
| `/tedapi/v1` | 404 | Not Found |

---

## Power Sharing Limitations

When Wall Connectors are configured for **power sharing**, they form a private network:

- **Leader**: Connects to your home WiFi and is accessible via your network
- **Followers**: Connect ONLY to the leader via a private `192.168.92.x` subnet
- **Limitation**: Followers are NOT accessible from your home network

This means:
- You can only poll the leader unit via the local API
- Follower data (energy usage, session info) is not available via API
- Power sharing configuration/status is not exposed through any known endpoint

**Network Topology:**
```
Home Network (192.168.1.x)
    │
    └── Leader TWC (192.168.1.100)
            │
            └── Private Network (192.168.92.x)
                    │
                    └── Follower TWC (192.168.92.x) ← Not accessible
```

To monitor follower units, you would need to:
1. Configure followers to connect to home WiFi independently (breaks power sharing)
2. Use a router/switch that can bridge the 192.168.92.x subnet (complex networking)
3. Wait for Tesla to expose this data via the leader's API (future firmware update)

---

## Example Usage

### cURL
```bash
# Get real-time vitals
curl -s http://192.168.1.100/api/1/vitals | jq

# Get lifetime stats
curl -s http://192.168.1.100/api/1/lifetime | jq

# Get WiFi status
curl -s http://192.168.1.100/api/1/wifi_status | jq

# Get version info
curl -s http://192.168.1.100/api/1/version | jq
```

### Python (aiohttp)
```python
import aiohttp
import asyncio

async def get_vitals(ip: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://{ip}/api/1/vitals") as response:
            return await response.json()

# Usage
vitals = asyncio.run(get_vitals("192.168.1.100"))
print(f"Power: {vitals['vehicle_current_a'] * vitals['grid_v']:.0f}W")
```

---

## Notes

- **No Authentication**: The API requires no auth tokens or credentials
- **Read-Only**: There are no endpoints to control charging; use the Tesla app or vehicle API
- **Local Only**: The API is only accessible on the local network
- **Rate Limiting**: No known rate limits, but polling more than once per second is unnecessary
- **Firmware Updates**: API endpoints may change with firmware updates
