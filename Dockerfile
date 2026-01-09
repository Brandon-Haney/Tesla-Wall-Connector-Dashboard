# Tesla Wall Connector Dashboard - All-in-One Image
# Contains: InfluxDB, Grafana, Collector, and API
FROM python:3.11-slim-bookworm

LABEL maintainer="Brandon Haney"
LABEL description="Tesla Wall Connector Dashboard - All-in-One"
LABEL org.opencontainers.image.source="https://github.com/Brandon-Haney/Tesla-Wall-Connector-Dashboard"

# Environment defaults
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Chicago \
    # InfluxDB settings
    INFLUXDB_URL=http://localhost:8086 \
    INFLUXDB_ORG=home \
    INFLUXDB_BUCKET=twc_dashboard \
    INFLUXDB_ADMIN_USER=admin \
    INFLUXDB_ADMIN_PASSWORD=changeme \
    INFLUXDB_ADMIN_TOKEN=twc-dashboard-token \
    # Grafana settings
    GRAFANA_ADMIN_USER=admin \
    GRAFANA_ADMIN_PASSWORD=changeme \
    GF_PATHS_DATA=/var/lib/grafana \
    GF_PATHS_PROVISIONING=/etc/grafana/provisioning \
    GF_SECURITY_ADMIN_USER=admin \
    GF_INSTALL_PLUGINS=grafana-clock-panel,grafana-piechart-panel \
    GF_UNIFIED_ALERTING_ENABLED=true \
    GF_ALERTING_ENABLED=false \
    GF_DATE_FORMATS_DEFAULT_TIMEZONE=browser \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/home-charging.json \
    # TWC Collector settings
    TWC_CHARGERS=garage:192.168.1.100 \
    TWC_POLL_VITALS_INTERVAL=5 \
    TWC_POLL_LIFETIME_INTERVAL=60 \
    TWC_POLL_VERSION_INTERVAL=300 \
    TWC_POLL_WIFI_INTERVAL=60 \
    COMED_ENABLED=true \
    COMED_POLL_INTERVAL=300 \
    COMED_DELIVERY_PER_KWH=0.075 \
    TESSIE_ENABLED=false \
    TESSIE_POLL_INTERVAL=60 \
    SMART_CHARGING_ENABLED=false \
    SMART_CHARGING_CONTROL_ENABLED=false \
    OPOWER_ENABLED=false \
    OPOWER_POLL_INTERVAL=3600

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg2 \
    apt-transport-https \
    software-properties-common \
    supervisor \
    adduser \
    libfontconfig1 \
    musl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install InfluxDB 2.x via APT repository
RUN mkdir -p /etc/apt/keyrings && \
    curl --silent --location -o /tmp/influxdata-archive.key https://repos.influxdata.com/influxdata-archive.key && \
    cat /tmp/influxdata-archive.key | gpg --dearmor > /etc/apt/keyrings/influxdata-archive.gpg && \
    echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' > /etc/apt/sources.list.d/influxdata.list && \
    apt-get update && \
    apt-get install -y influxdb2 influxdb2-cli && \
    rm -rf /var/lib/apt/lists/* /tmp/influxdata-archive.key

# Install Grafana via APT repository
RUN wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/trusted.gpg.d/grafana.gpg && \
    echo "deb [signed-by=/etc/apt/trusted.gpg.d/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list && \
    apt-get update && \
    apt-get install -y grafana && \
    rm -rf /var/lib/apt/lists/*

# Create app directories
RUN mkdir -p /app/collector /app/api /app/config /data/influxdb /data/grafana

# Install Python dependencies for collector
COPY collector/requirements.txt /app/collector/requirements.txt
RUN pip install --no-cache-dir -r /app/collector/requirements.txt

# Install Python dependencies for API
COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

# Copy application code
COPY collector/src /app/collector/src
COPY api/src /app/api/src

# Copy Grafana provisioning and dashboards
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /var/lib/grafana/dashboards
COPY grafana/dashboards-legacy /var/lib/grafana/dashboards-legacy

# Copy configuration files
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set permissions
RUN chown -R grafana:grafana /var/lib/grafana /etc/grafana/provisioning && \
    chmod -R 755 /var/lib/grafana/dashboards

# Create volumes for persistent data
VOLUME ["/data/influxdb", "/data/grafana", "/app/config"]

# Expose ports
# 8086 - InfluxDB
# 3000 - Grafana
# 8000 - API
EXPOSE 8086 3000 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:3000/api/health && curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
