#!/bin/bash
set -e

# Create necessary directories
mkdir -p /var/log/supervisor /var/log/grafana /data/influxdb /data/grafana/plugins
chown -R grafana:grafana /data/grafana /var/log/grafana

# Load secrets if mounted
if [ -f /app/config/.secrets ]; then
    echo "[entrypoint] Loading secrets from /app/config/.secrets"
    set -a
    source /app/config/.secrets
    set +a
fi

# Set Grafana admin password from environment
export GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-changeme}"
export GF_SECURITY_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"

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
