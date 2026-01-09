#!/bin/bash
set -e

# Create necessary directories
mkdir -p /var/log/supervisor /var/log/grafana /data/influxdb /data/grafana/plugins /app/project
chown -R grafana:grafana /data/grafana /var/log/grafana

# Symlink config files to expected locations for collector compatibility
if [ -f /app/config/.comed_opower_cache.json ]; then
    ln -sf /app/config/.comed_opower_cache.json /app/project/.comed_opower_cache.json
fi

# Function to safely load env files (handles values with special characters)
load_env_file() {
    local file="$1"
    if [ -f "$file" ]; then
        echo "[entrypoint] Loading config from $file"
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip empty lines and comments
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            # Only process lines that look like VAR=value
            if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
                key="${BASH_REMATCH[1]}"
                value="${BASH_REMATCH[2]}"
                # Remove surrounding quotes if present
                value="${value#\"}"
                value="${value%\"}"
                value="${value#\'}"
                value="${value%\'}"
                export "$key=$value"
            fi
        done < "$file"
    fi
}

# Load environment config if mounted
load_env_file /app/config/.env

# Load secrets if mounted (loaded after .env so secrets can override)
load_env_file /app/config/.secrets

# Set Grafana admin password from environment
export GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-changeme}"
export GF_SECURITY_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"

# Map INFLUXDB_ADMIN_TOKEN to INFLUXDB_TOKEN for collector compatibility
export INFLUXDB_TOKEN="${INFLUXDB_ADMIN_TOKEN:-twc-dashboard-token}"

# Initialize InfluxDB if not already set up
INFLUX_INIT_FLAG="/data/influxdb/.initialized"

if [ ! -f "$INFLUX_INIT_FLAG" ]; then
    echo "[entrypoint] First run - initializing InfluxDB..."

    # Start InfluxDB temporarily for setup
    /usr/bin/influxd --bolt-path=/data/influxdb/influxd.bolt --engine-path=/data/influxdb/engine --store=bolt &
    INFLUX_PID=$!

    # Wait for InfluxDB to be ready
    echo "[entrypoint] Waiting for InfluxDB to start..."
    for i in {1..30}; do
        if curl -s http://localhost:8086/health > /dev/null 2>&1; then
            echo "[entrypoint] InfluxDB is ready"
            break
        fi
        sleep 1
    done

    # Run setup
    echo "[entrypoint] Running InfluxDB setup..."
    influx setup \
        --username "${INFLUXDB_ADMIN_USER:-admin}" \
        --password "${INFLUXDB_ADMIN_PASSWORD:-changeme}" \
        --org "${INFLUXDB_ORG:-home}" \
        --bucket "${INFLUXDB_BUCKET:-twc_dashboard}" \
        --token "${INFLUXDB_ADMIN_TOKEN:-twc-dashboard-token}" \
        --force

    # Create initialization flag
    touch "$INFLUX_INIT_FLAG"
    echo "[entrypoint] InfluxDB initialization complete"

    # Stop the temporary InfluxDB instance
    kill $INFLUX_PID 2>/dev/null || true
    wait $INFLUX_PID 2>/dev/null || true
    sleep 2
else
    echo "[entrypoint] InfluxDB already initialized"
fi

# Update Grafana datasource with current token
echo "[entrypoint] Updating Grafana datasource configuration..."
cat > /etc/grafana/provisioning/datasources/influxdb.yml << EOF
apiVersion: 1

datasources:
  - name: InfluxDB
    uid: influxdb
    type: influxdb
    access: proxy
    url: http://localhost:8086
    jsonData:
      version: Flux
      organization: ${INFLUXDB_ORG:-home}
      defaultBucket: ${INFLUXDB_BUCKET:-twc_dashboard}
      tlsSkipVerify: true
    secureJsonData:
      token: ${INFLUXDB_ADMIN_TOKEN:-twc-dashboard-token}
    isDefault: true
    editable: false
EOF

echo "[entrypoint] Starting services via supervisord..."
exec "$@"
