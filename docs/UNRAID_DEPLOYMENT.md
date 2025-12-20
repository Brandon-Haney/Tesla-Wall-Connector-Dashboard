# Unraid Deployment Guide

This guide walks you through deploying the Tesla Wall Connector Dashboard on an Unraid server.

## Prerequisites

- Unraid 6.9+ with Docker enabled
- Docker Compose Manager plugin (from Community Applications)
- Your Wall Connector on the same network as Unraid
- (Optional) Tessie API token for Fleet API features

## Quick Start

### 1. Clone the Repository

SSH into your Unraid server or use the terminal:

```bash
cd /mnt/user/appdata
git clone https://github.com/Brandon-Haney/Tesla-Wall-Connector-Dashboard.git twc-dashboard
cd twc-dashboard
```

### 2. Deploy

**Option A: Using Docker Compose Manager (Recommended)**

1. Open Unraid web UI
2. Go to Docker → Add New Stack
3. Name: `twc-dashboard`
4. Compose file: `/mnt/user/appdata/twc-dashboard/docker-compose.yml`
5. Click "Compose Up"

**Option B: Command Line**

```bash
cd /mnt/user/appdata/twc-dashboard
docker compose up -d
```

### 3. Access the Dashboard

- **Grafana**: http://YOUR_UNRAID_IP:3080
  - Username: `admin`
  - Password: `changeme`
- **API Docs**: http://YOUR_UNRAID_IP:8000/docs
- **InfluxDB**: http://YOUR_UNRAID_IP:8086

### 4. Configure Your Setup (Optional)

To customize settings (Wall Connector IP, passwords, Tessie integration):

```bash
cp .env.example .env
nano .env
```

**Common settings in `.env`:**
```env
# Your Wall Connector IP address
TWC_CHARGERS=garage:192.168.1.100

# Your timezone (optional, defaults to America/Chicago)
TZ=America/Chicago

# Custom passwords (recommended for security)
GRAFANA_ADMIN_PASSWORD=your_secure_password
INFLUXDB_ADMIN_PASSWORD=your_secure_password
```

> **Note**: The dashboard works without `.env` using default credentials. Create `.env` only if you need to customize settings or secure your installation.

**For Tessie/Fleet API features**, copy and edit `.secrets`:
```bash
cp .secrets.example .secrets
nano .secrets
```

Add your Tessie token:
```
TESSIE_ACCESS_TOKEN=your_tessie_token_here
```

Then enable in `.env`:
```env
TESSIE_ENABLED=true
```

Restart to apply changes:
```bash
docker compose restart collector
```

---

## Updating

To update to the latest version:

```bash
cd /mnt/user/appdata/twc-dashboard
git pull
docker compose up -d --build
```

The `--build` flag ensures the collector and API containers are rebuilt with the new code.

---

## Data Migration from Windows

If you're migrating from an existing Windows installation and want to preserve historical data:

### Export Data from Windows

```powershell
# On your Windows machine
mkdir C:\twc-backup

# Export InfluxDB data
docker exec twc-influxdb influx backup /tmp/backup --bucket twc_dashboard --org home --token YOUR_TOKEN
docker cp twc-influxdb:/tmp/backup C:\twc-backup\influxdb-backup
```

### Transfer to Unraid

Copy the backup folder to Unraid using:
- SMB share (easiest)
- SCP: `scp -r C:\twc-backup\influxdb-backup root@UNRAID_IP:/mnt/user/appdata/twc-backup/`
- USB drive

### Import on Unraid

```bash
# Start just InfluxDB first
docker compose up -d influxdb

# Wait for it to be healthy
sleep 30

# Copy backup into container
docker cp /mnt/user/appdata/twc-backup/influxdb-backup twc-influxdb:/tmp/backup

# Restore the data
docker exec twc-influxdb influx restore /tmp/backup --org home --token YOUR_TOKEN

# Start remaining services
docker compose up -d
```

---

## Network Configuration

### Same Subnet (Recommended)

If your Unraid server is on the same network as your Wall Connector, no additional configuration is needed.

### Different VLANs

If your Wall Connector is on a different VLAN:

1. Ensure routing is configured between VLANs
2. Update `TWC_CHARGERS` in `.env` with the correct IP
3. You may need to add the container to a custom Docker network with VLAN access

### Using Fleet API Only

If you can't access the local Wall Connector API from Unraid, you can disable it and rely solely on Fleet API:

```env
# In .env
LOCAL_TWC_ENABLED=false
TESSIE_ENABLED=true
```

The Fleet API provides all charging data via the cloud, so local network access to the Wall Connector becomes optional.

---

## Troubleshooting

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f collector
```

### Check Service Status

```bash
docker compose ps
```

### Restart Services

```bash
docker compose restart
```

### Common Issues

**"No data" in dashboards**
- Check collector logs for connection errors
- Verify Wall Connector IP is correct and accessible
- Wait a few minutes for data to accumulate

**Grafana login fails**
- Default credentials: admin / changeme
- If using custom .env, check `GRAFANA_ADMIN_PASSWORD`

**Fleet API not working**
- Verify Tessie token in `.secrets` file
- Check `TESSIE_ENABLED=true` in `.env`
- Look for auth errors in collector logs

**Build failures**
- Check Docker logs: `docker compose logs`
- Ensure you have enough disk space for building images
- Try rebuilding: `docker compose build --no-cache`

---

## Backup Strategy

### Recommended Backup Items

Add these paths to your Unraid backup schedule:

```
/mnt/user/appdata/twc-dashboard/.env
/mnt/user/appdata/twc-dashboard/.secrets
```

### InfluxDB Data Backup

For periodic data backups:

```bash
# Create backup
docker exec twc-influxdb influx backup /tmp/backup --bucket twc_dashboard --org home --token YOUR_TOKEN
docker cp twc-influxdb:/tmp/backup /mnt/user/appdata/twc-backup/influxdb-$(date +%Y%m%d)

# Keep last 7 days
find /mnt/user/appdata/twc-backup -name "influxdb-*" -mtime +7 -exec rm -rf {} \;
```

Consider adding this to a User Script that runs weekly.

---

## Updating Dashboard Configuration

When you want to modify Grafana dashboards or add features:

1. Make changes in your local repository
2. Commit and push to GitHub
3. Pull changes on Unraid:
   ```bash
   cd /mnt/user/appdata/twc-dashboard
   git pull
   docker compose restart grafana
   ```

Dashboard JSON files are mounted directly, so changes take effect after a Grafana restart.

---

## Ports Reference

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3080 | Dashboard UI |
| API | 8000 | REST API & WebSocket |
| InfluxDB | 8086 | Database (optional external access) |

If these ports conflict with other services, modify them in `docker-compose.yml`:

```yaml
ports:
  - "3001:3000"  # Changed Grafana from 3080 to 3001
```
