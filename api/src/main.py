"""Tesla Wall Connector Dashboard API."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from .config import settings
from .models import (
    ChargerStatus,
    ChargerLifetime,
    ChargerInfo,
    CurrentPrice,
    ChargingSession,
    SessionSummary,
    HealthStatus,
    VehicleStatus,
    VehicleSession,
    MeterUsage,
    MeterCost,
    BillSummary,
    MeterComparison,
)
from .influx_client import influx_client
from .export import (
    sessions_to_csv,
    sessions_to_json,
    prices_to_csv,
    summary_to_json,
    generate_pdf_report,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="REST API for Tesla Wall Connector Dashboard data",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


# =============================================================================
# Health & Info Endpoints
# =============================================================================

@app.get("/", tags=["Info"])
async def root():
    """API root - returns basic info."""
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthStatus, tags=["Info"])
async def health():
    """Check API health status."""
    return HealthStatus(
        status="healthy",
        influxdb_connected=influx_client.check_connection(),
        timestamp=datetime.utcnow(),
        version=settings.api_version,
    )


# =============================================================================
# Charger Endpoints
# =============================================================================

@app.get("/chargers", response_model=List[str], tags=["Chargers"])
async def list_chargers():
    """Get list of all charger IDs."""
    return influx_client.get_charger_ids()


@app.get("/chargers/status", response_model=List[ChargerStatus], tags=["Chargers"])
async def get_all_charger_status():
    """Get current status for all chargers."""
    return influx_client.get_charger_status()


@app.get("/chargers/{charger_id}/status", response_model=ChargerStatus, tags=["Chargers"])
async def get_charger_status(charger_id: str):
    """Get current status for a specific charger."""
    statuses = influx_client.get_charger_status(charger_id)
    if not statuses:
        raise HTTPException(status_code=404, detail="Charger not found")
    return statuses[0]


@app.get("/chargers/{charger_id}/lifetime", response_model=ChargerLifetime, tags=["Chargers"])
async def get_charger_lifetime(charger_id: str):
    """Get lifetime statistics for a charger."""
    stats = influx_client.get_charger_lifetime(charger_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Charger not found")
    return stats[0]


@app.get("/chargers/{charger_id}/info", response_model=ChargerInfo, tags=["Chargers"])
async def get_charger_info(charger_id: str):
    """Get version and hardware info for a charger."""
    infos = influx_client.get_charger_info(charger_id)
    if not infos:
        raise HTTPException(status_code=404, detail="Charger not found")
    return infos[0]


# =============================================================================
# Pricing Endpoints
# =============================================================================

@app.get("/price/current", response_model=CurrentPrice, tags=["Pricing"])
async def get_current_price():
    """Get current electricity price."""
    price = influx_client.get_current_price()
    if not price:
        raise HTTPException(status_code=404, detail="Price data not available")
    return price


@app.get("/price/history", tags=["Pricing"])
async def get_price_history(
    start: datetime = Query(..., description="Start date (ISO format)"),
    end: datetime = Query(..., description="End date (ISO format)"),
):
    """Get price history for a time range."""
    return influx_client.get_price_history(start, end)


# =============================================================================
# Sessions Endpoints
# =============================================================================

@app.get("/sessions", response_model=List[ChargingSession], tags=["Sessions"])
async def get_sessions(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
):
    """Get charging sessions for a time range."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_sessions(start, end, charger_id)


@app.get("/sessions/summary", response_model=SessionSummary, tags=["Sessions"])
async def get_session_summary(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
):
    """Get summary statistics for a time range."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_session_summary(start, end, charger_id)


# =============================================================================
# Vehicle Endpoints (Tessie Integration)
# =============================================================================

@app.get("/vehicles", tags=["Vehicles"])
async def list_vehicles():
    """Get list of all vehicles (VIN and display name)."""
    return influx_client.get_vehicle_ids()


@app.get("/vehicles/status", response_model=List[VehicleStatus], tags=["Vehicles"])
async def get_all_vehicle_status():
    """Get current status for all vehicles."""
    return influx_client.get_vehicle_status()


@app.get("/vehicles/{vin}/status", response_model=VehicleStatus, tags=["Vehicles"])
async def get_vehicle_status(vin: str):
    """Get current status for a specific vehicle."""
    statuses = influx_client.get_vehicle_status(vin)
    if not statuses:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return statuses[0]


@app.get("/vehicles/{vin}/sessions", response_model=List[VehicleSession], tags=["Vehicles"])
async def get_vehicle_sessions(
    vin: str,
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
):
    """Get charging sessions for a specific vehicle."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_vehicle_sessions(start, end, vin)


@app.get("/vehicles/sessions", response_model=List[VehicleSession], tags=["Vehicles"])
async def get_all_vehicle_sessions(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
):
    """Get charging sessions for all vehicles."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_vehicle_sessions(start, end)


# =============================================================================
# Meter Data Endpoints (ComEd Opower Integration)
# =============================================================================

@app.get("/meter/usage", response_model=List[MeterUsage], tags=["Meter Data"])
async def get_meter_usage(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    resolution: str = Query(default="DAY", description="Resolution: DAY, HOUR, or HALF_HOUR"),
):
    """Get actual meter usage from ComEd smart meter.

    Returns kWh readings from your smart meter at the specified resolution.
    Requires Opower integration to be configured.
    """
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_meter_usage(start, end, resolution)


@app.get("/meter/cost", response_model=List[MeterCost], tags=["Meter Data"])
async def get_meter_cost(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    resolution: str = Query(default="DAY", description="Resolution: DAY or HOUR"),
):
    """Get actual billed costs from ComEd.

    Returns actual costs including all fees, delivery charges, and taxes.
    This is the same data that appears on your ComEd bill.
    Requires Opower integration to be configured.
    """
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_meter_cost(start, end, resolution)


@app.get("/meter/bills", response_model=List[BillSummary], tags=["Meter Data"])
async def get_bills(
    start: datetime = Query(default=None, description="Start date (default: 1 year ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
):
    """Get monthly bill summaries from ComEd.

    Returns bill-level summaries including total usage, costs, and effective rate.
    Requires Opower integration to be configured.
    """
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=365)
    return influx_client.get_bills(start, end)


@app.get("/meter/comparison", response_model=MeterComparison, tags=["Meter Data"])
async def get_meter_comparison(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
):
    """Compare calculated EV charging costs vs actual meter costs.

    Returns a comparison showing:
    - Calculated EV charging energy and costs from dashboard
    - Actual whole-house usage and costs from meter
    - What percentage of your electricity goes to EV charging
    - Average rates from both sources

    Requires Opower integration to be configured.
    """
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)
    return influx_client.get_meter_comparison(start, end)


# =============================================================================
# Export Endpoints
# =============================================================================

@app.get("/export/sessions.csv", tags=["Export"])
async def export_sessions_csv(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
):
    """Export charging sessions as CSV."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)

    sessions = influx_client.get_sessions(start, end, charger_id)
    csv_content = sessions_to_csv(sessions)

    filename = f"twc_sessions_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/sessions.json", tags=["Export"])
async def export_sessions_json(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
):
    """Export charging sessions as JSON."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)

    sessions = influx_client.get_sessions(start, end, charger_id)
    json_content = sessions_to_json(sessions)

    filename = f"twc_sessions_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/prices.csv", tags=["Export"])
async def export_prices_csv(
    start: datetime = Query(default=None, description="Start date (default: 7 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
):
    """Export price history as CSV."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=7)

    prices = influx_client.get_price_history(start, end)
    csv_content = prices_to_csv(prices)

    filename = f"twc_prices_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/summary.json", tags=["Export"])
async def export_summary_json(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
):
    """Export summary statistics as JSON."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)

    summary = influx_client.get_session_summary(start, end, charger_id)
    json_content = summary_to_json(summary)

    filename = f"twc_summary_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/report.pdf", tags=["Export"])
async def export_report_pdf(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    charger_id: Optional[str] = Query(default=None, description="Filter by charger ID"),
    title: str = Query(default="Tesla Wall Connector Charging Report", description="Report title"),
):
    """Generate and download a PDF report."""
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)

    summary = influx_client.get_session_summary(start, end, charger_id)
    sessions = influx_client.get_sessions(start, end, charger_id)

    pdf_content = generate_pdf_report(summary, sessions, title)

    filename = f"twc_report_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/meter.csv", tags=["Export"])
async def export_meter_csv(
    start: datetime = Query(default=None, description="Start date (default: 30 days ago)"),
    end: datetime = Query(default=None, description="End date (default: now)"),
    resolution: str = Query(default="DAY", description="Resolution: DAY, HOUR, or HALF_HOUR"),
):
    """Export meter usage and cost data as CSV.

    Requires Opower integration to be configured.
    """
    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=30)

    usage = influx_client.get_meter_usage(start, end, resolution)
    costs = influx_client.get_meter_cost(start, end, resolution)

    # Build CSV with usage and cost data merged by timestamp
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "usage_kwh", "cost_cents", "effective_rate_cents"])

    # Create lookup dict for costs
    cost_map = {c.timestamp.isoformat(): c for c in costs}

    for u in usage:
        ts = u.timestamp.isoformat()
        cost = cost_map.get(ts)
        writer.writerow([
            ts,
            f"{u.kwh:.3f}",
            f"{cost.cost_cents:.2f}" if cost else "",
            f"{cost.effective_rate_cents:.2f}" if cost else "",
        ])

    csv_content = output.getvalue()
    filename = f"comed_meter_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates.

    Sends charger status, vehicle status, and price updates every 5 seconds.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Get current data
            statuses = influx_client.get_charger_status()
            price = influx_client.get_current_price()
            vehicles = influx_client.get_vehicle_status()

            # Build update message
            update = {
                "type": "status_update",
                "timestamp": datetime.utcnow().isoformat(),
                "chargers": [
                    {
                        "charger_id": s.charger_id,
                        "power_w": s.power_w,
                        "vehicle_connected": s.vehicle_connected,
                        "is_charging": s.contactor_closed and s.vehicle_current_a > 0,
                        "session_energy_wh": s.session_energy_wh,
                    }
                    for s in statuses
                ],
                "vehicles": [
                    {
                        "vin": v.vin,
                        "display_name": v.display_name,
                        "state": v.state,
                        "battery_level": v.battery_level,
                        "battery_range": v.battery_range,
                        "charging_state": v.charging_state,
                        "charger_power": v.charger_power,
                        "charge_amps": v.charge_amps,
                        "charge_energy_added": v.charge_energy_added,
                        "time_to_full_charge": v.time_to_full_charge,
                    }
                    for v in vehicles
                ],
                "price": {
                    "price_cents_kwh": price.price_cents_kwh,
                    "full_rate_cents_kwh": price.full_rate_cents_kwh,
                } if price else None,
            }

            await websocket.send_json(update)

            # Wait for next update or client message
            try:
                # Check for client messages with timeout
                await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            except asyncio.TimeoutError:
                # Timeout is expected, continue to send next update
                pass

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# =============================================================================
# Startup/Shutdown
# =============================================================================

@app.on_event("startup")
async def startup():
    """Run on startup."""
    logger.info(f"Starting {settings.api_title} v{settings.api_version}")
    if influx_client.check_connection():
        logger.info("InfluxDB connection successful")
    else:
        logger.warning("InfluxDB connection failed - some endpoints may not work")


@app.on_event("shutdown")
async def shutdown():
    """Run on shutdown."""
    logger.info("Shutting down API")
    influx_client.close()
