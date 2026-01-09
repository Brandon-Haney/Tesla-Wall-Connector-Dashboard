# Unraid Deployment Guide

This guide walks you through deploying the Tesla Wall Connector Dashboard on an Unraid server.

## Deployment Options

| Method | Best For | Complexity |
|--------|----------|------------|
| **Docker Hub (All-in-One)** | Simple single-container setup | Easy |
| **Docker Compose** | Advanced users, customization | Moderate |

---

## Option 1: Docker Hub All-in-One (Recommended)

A single container with InfluxDB, Grafana, Collector, and API bundled together.

**Image**: `brandonhaney/twc-dashboard:latest`

### Step 1: Create Config Directory

SSH into Unraid or use the terminal:

```bash
mkdir -p /mnt/user/appdata/twc-dashboard/config
mkdir -p /mnt/user/appdata/twc-dashboard/influxdb
```

### Step 2: Create Configuration File

Create `/mnt/user/appdata/twc-dashboard/config/.env`:

```bash
nano /mnt/user/appdata/twc-dashboard/config/.env
```

Add your configuration:

```env
# Required
TZ=America/Chicago
TWC_CHARGERS=garage:192.168.1.100

# Security - CHANGE THESE!
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=your_secure_password
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=your_secure_password
INFLUXDB_ADMIN_TOKEN=your-secret-token-here

# InfluxDB
INFLUXDB_ORG=home
INFLUXDB_BUCKET=twc_dashboard

# Optional features
COMED_ENABLED=true
TESSIE_ENABLED=false
OPOWER_ENABLED=false
```

See `docker/.env.example` in the repo for all available options.

### Step 3: Create Secrets File (Optional)

For Tessie integration, create `/mnt/user/appdata/twc-dashboard/config/.secrets`:

```
TESSIE_ACCESS_TOKEN=your_tessie_token_here
```

### Step 4: Add Container in Unraid UI

1. Go to **Docker** tab
2. Click **Add Container**
3. Toggle **Advanced View** (top right)

**Basic Config:**

| Field | Value |
|-------|-------|
| Name | `TWC-Dashboard` |
| Repository | `brandonhaney/twc-dashboard:latest` |
| Network Type | `bridge` |

**Port Mappings** (click "Add another Path, Port, Variable..."):

| Config Type | Name | Container Port | Host Port |
|-------------|------|----------------|-----------|
| Port | Grafana | `3000` | `3080` |
| Port | API | `8000` | `8880` |
| Port | InfluxDB | `8086` | `8886` |

**Path Mappings:**

| Config Type | Name | Container Path | Host Path |
|-------------|------|----------------|-----------|
| Path | InfluxDB Data | `/data/influxdb` | `/mnt/user/appdata/twc-dashboard/influxdb` |
| Path | Config | `/app/config` | `/mnt/user/appdata/twc-dashboard/config` |

### Step 5: Start and Access

1. Click **Apply**
2. Wait for the container to start (first run initializes databases)
3. Access Grafana: `http://YOUR_UNRAID_IP:3080`
4. Login with credentials from your `.env` file

### Updating

```bash
docker pull brandonhaney/twc-dashboard:latest
docker restart TWC-Dashboard
```

---

## Option 2: Docker Compose

For users who want more control or to customize individual services.

### Prerequisites

- Unraid 6.9+ with Docker enabled
- Docker Compose Manager plugin (from Community Applications)

### Step 1: Clone the Repository

```bash
cd /mnt/user/appdata
git clone https://github.com/Brandon-Haney/Tesla-Wall-Connector-Dashboard.git twc-dashboard
cd twc-dashboard
```

### Step 2: Configure

```bash
cp .env.example .env
nano .env
```

For Tessie integration:
```bash
cp .secrets.example .secrets
nano .secrets
```

### Step 3: Deploy

**Using Docker Compose Manager:**

1. Open Unraid web UI
2. Go to Docker -> Add New Stack
3. Name: `twc-dashboard`
4. Compose file: `/mnt/user/appdata/twc-dashboard/docker-compose.yml`
5. Click "Compose Up"

**Using Command Line:**

```bash
cd /mnt/user/appdata/twc-dashboard
docker compose up -d
```

### Step 4: Access

- **Grafana**: http://YOUR_UNRAID_IP:3080
  - Username: `admin`
  - Password: `changeme` (or from `.env`)
- **API Docs**: http://YOUR_UNRAID_IP:8000/docs
- **InfluxDB**: http://YOUR_UNRAID_IP:8086

### Updating

```bash
cd /mnt/user/appdata/twc-dashboard
git pull
docker compose up -d --build
```

---

## Opower Setup (ComEd Meter Data)

To add actual meter data from your ComEd smart meter:

1. Enable in your `.env`:
   ```env
   OPOWER_ENABLED=true
   ```

2. Run the setup script on a machine with a browser (your local PC):
   ```bash
   pip install httpx opower
   python scripts/comed_opower_setup.py
   ```

3. Copy the generated `.comed_opower_cache.json` to your config folder:
   ```
   /mnt/user/appdata/twc-dashboard/config/.comed_opower_cache.json
   ```

The collector auto-detects the cache file within 30 seconds.

---

## Network Configuration

### Same Subnet (Recommended)

If your Unraid server is on the same network as your Wall Connector, no additional configuration is needed.

### Different VLANs

If your Wall Connector is on a different VLAN:

1. Ensure routing is configured between VLANs
2. Update `TWC_CHARGERS` in `.env` with the correct IP
3. You may need to use `Network Type: Host` instead of bridge

### Using Fleet API Only

If you can't access the local Wall Connector API from Unraid:

```env
LOCAL_TWC_ENABLED=false
TESSIE_ENABLED=true
```

---

## Troubleshooting

### View Logs

**All-in-One:**
```bash
docker logs TWC-Dashboard
docker exec TWC-Dashboard cat /var/log/supervisor/collector.log | tail -50
```

**Docker Compose:**
```bash
docker compose logs -f collector
```

### Common Issues

**"No data" in dashboards**
- Check collector logs for connection errors
- Verify Wall Connector IP is correct and reachable
- Wait a few minutes for data to accumulate

**Grafana login fails**
- Check `GRAFANA_ADMIN_PASSWORD` in your `.env`
- On first run, credentials come from `.env`

**"unauthorized" errors in Grafana panels**
- InfluxDB token mismatch
- Delete influxdb data folder and restart to reinitialize:
  ```bash
  docker stop TWC-Dashboard
  rm -rf /mnt/user/appdata/twc-dashboard/influxdb/*
  docker start TWC-Dashboard
  ```

**Opower not detecting cache file**
- Ensure file is at `/mnt/user/appdata/twc-dashboard/config/.comed_opower_cache.json`
- Check logs: `docker exec TWC-Dashboard cat /var/log/supervisor/collector.log | grep -i opower`

---

## Ports Reference

| Service | Container Port | Recommended Host Port |
|---------|---------------|----------------------|
| Grafana | 3000 | 3080 |
| API | 8000 | 8880 |
| InfluxDB | 8086 | 8886 |

These avoid common Unraid app ports (Sonarr 8989, Radarr 7878, Portainer 9000, etc.)

---

## Backup

### Config Files

Add to your backup schedule:
```
/mnt/user/appdata/twc-dashboard/config/.env
/mnt/user/appdata/twc-dashboard/config/.secrets
/mnt/user/appdata/twc-dashboard/config/.comed_opower_cache.json
```

### InfluxDB Data

For Docker Hub all-in-one:
```bash
docker exec TWC-Dashboard influx backup /tmp/backup --bucket twc_dashboard --org home --token YOUR_TOKEN
docker cp TWC-Dashboard:/tmp/backup /mnt/user/appdata/twc-backup/
```
