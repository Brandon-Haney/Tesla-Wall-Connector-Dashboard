"""Export functionality for CSV, JSON, and PDF generation."""

import csv
import json
import io
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

from .models import ChargingSession, SessionSummary, EnergyDataPoint
from .config import settings


# Color scheme
COLORS = {
    'primary': colors.HexColor('#1a73e8'),      # Blue
    'secondary': colors.HexColor('#34a853'),    # Green
    'warning': colors.HexColor('#fbbc04'),      # Yellow
    'danger': colors.HexColor('#ea4335'),       # Red
    'light_gray': colors.HexColor('#f8f9fa'),
    'dark_gray': colors.HexColor('#5f6368'),
    'border': colors.HexColor('#dadce0'),
}


def sessions_to_csv(sessions: List[ChargingSession]) -> str:
    """Convert sessions to CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Charger ID",
        "Start Time",
        "End Time",
        "Duration (minutes)",
        "Energy (kWh)",
        "Supply Cost ($)",
        "Full Cost ($)",
        "Avg Price (cents/kWh)",
        "Peak Power (kW)",
    ])

    # Data rows
    for session in sessions:
        writer.writerow([
            session.charger_id,
            session.start_time.isoformat() if session.start_time else "",
            session.end_time.isoformat() if session.end_time else "",
            round(session.duration_s / 60, 1),
            round(session.energy_wh / 1000, 2),
            round(session.supply_cost_cents / 100, 2),
            round(session.full_cost_cents / 100, 2),
            round(session.avg_price_cents, 2),
            round(session.peak_power_w / 1000, 2),
        ])

    return output.getvalue()


def energy_data_to_csv(data: List[EnergyDataPoint]) -> str:
    """Convert energy data points to CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Timestamp",
        "Energy (Wh)",
        "Power (W)",
        "Price (cents/kWh)",
    ])

    # Data rows
    for point in data:
        writer.writerow([
            point.timestamp.isoformat(),
            round(point.energy_wh, 2),
            round(point.power_w, 2),
            round(point.price_cents, 2) if point.price_cents else "",
        ])

    return output.getvalue()


def prices_to_csv(prices: List[Dict[str, Any]]) -> str:
    """Convert price history to CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Timestamp",
        "Supply Price (cents/kWh)",
        "Full Rate (cents/kWh)",
    ])

    # Data rows
    for price in prices:
        writer.writerow([
            price["timestamp"],
            round(price["price_cents_kwh"], 2),
            round(price["full_rate_cents_kwh"], 2),
        ])

    return output.getvalue()


def sessions_to_json(sessions: List[ChargingSession]) -> str:
    """Convert sessions to JSON format."""
    data = []
    for session in sessions:
        data.append({
            "charger_id": session.charger_id,
            "start_time": session.start_time.isoformat() if session.start_time else None,
            "end_time": session.end_time.isoformat() if session.end_time else None,
            "duration_minutes": round(session.duration_s / 60, 1),
            "energy_kwh": round(session.energy_wh / 1000, 3),
            "supply_cost_dollars": round(session.supply_cost_cents / 100, 2),
            "full_cost_dollars": round(session.full_cost_cents / 100, 2),
            "avg_price_cents_kwh": round(session.avg_price_cents, 2),
            "peak_power_kw": round(session.peak_power_w / 1000, 2),
            "is_active": session.is_active,
        })
    return json.dumps(data, indent=2)


def summary_to_json(summary: SessionSummary) -> str:
    """Convert summary to JSON format."""
    data = {
        "period": {
            "start": summary.start_date.isoformat(),
            "end": summary.end_date.isoformat(),
        },
        "total_sessions": summary.total_sessions,
        "total_energy_kwh": round(summary.total_energy_wh / 1000, 2),
        "total_supply_cost_dollars": round(summary.total_supply_cost_cents / 100, 2),
        "total_full_cost_dollars": round(summary.total_full_cost_cents / 100, 2),
        "avg_price_cents_kwh": round(summary.avg_price_cents, 2),
        "total_charging_hours": round(summary.total_duration_s / 3600, 1),
    }
    return json.dumps(data, indent=2)


def _create_daily_energy_chart(sessions: List[ChargingSession]) -> Optional[bytes]:
    """Create a bar chart of daily energy consumption."""
    if not sessions:
        return None

    # Aggregate energy by day
    daily_energy = defaultdict(float)
    for session in sessions:
        if session.start_time:
            day = session.start_time.date()
            daily_energy[day] += session.energy_wh / 1000  # Convert to kWh

    if not daily_energy:
        return None

    # Sort by date
    sorted_days = sorted(daily_energy.keys())
    dates = sorted_days
    energies = [daily_energy[d] for d in sorted_days]

    # Create figure
    fig, ax = plt.subplots(figsize=(8, 3.5))

    # Bar chart
    bar_colors = ['#1a73e8' if e > 0 else '#dadce0' for e in energies]
    bars = ax.bar(dates, energies, color=bar_colors, edgecolor='white', linewidth=0.5)

    # Styling
    ax.set_ylabel('Energy (kWh)', fontsize=10, color='#5f6368')
    ax.set_title('Daily Energy Consumption', fontsize=12, fontweight='bold', pad=10)

    # Format x-axis
    if len(dates) > 14:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

    plt.xticks(rotation=45, ha='right', fontsize=8)
    plt.yticks(fontsize=8)

    # Grid
    ax.yaxis.grid(True, linestyle='--', alpha=0.3)
    ax.set_axisbelow(True)

    # Remove top and right spines
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#dadce0')
    ax.spines['bottom'].set_color('#dadce0')

    # Add value labels on bars
    for bar, energy in zip(bars, energies):
        if energy > 0:
            height = bar.get_height()
            ax.annotate(f'{energy:.1f}',
                       xy=(bar.get_x() + bar.get_width() / 2, height),
                       xytext=(0, 3),
                       textcoords="offset points",
                       ha='center', va='bottom', fontsize=7, color='#5f6368')

    plt.tight_layout()

    # Save to bytes
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _create_cost_breakdown_chart(summary: SessionSummary) -> Optional[bytes]:
    """Create a pie chart showing supply vs delivery cost breakdown."""
    supply_cost = summary.total_supply_cost_cents / 100
    delivery_cost = (summary.total_full_cost_cents - summary.total_supply_cost_cents) / 100

    if supply_cost <= 0 and delivery_cost <= 0:
        return None

    # Create figure
    fig, ax = plt.subplots(figsize=(4, 3.5))

    # Data
    sizes = [supply_cost, delivery_cost]
    labels = [f'Supply\n${supply_cost:.2f}', f'Delivery\n${delivery_cost:.2f}']
    colors_list = ['#1a73e8', '#34a853']
    explode = (0.02, 0.02)

    # Pie chart
    wedges, texts, autotexts = ax.pie(
        sizes, explode=explode, labels=labels, colors=colors_list,
        autopct='%1.1f%%', startangle=90,
        textprops={'fontsize': 9},
        wedgeprops={'edgecolor': 'white', 'linewidth': 2}
    )

    # Style percentage labels
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(10)

    ax.set_title('Cost Breakdown', fontsize=12, fontweight='bold', pad=10)

    plt.tight_layout()

    # Save to bytes
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _create_price_trend_chart(sessions: List[ChargingSession]) -> Optional[bytes]:
    """Create a line chart showing average price trend."""
    if not sessions:
        return None

    # Aggregate price by day
    daily_prices = defaultdict(list)
    for session in sessions:
        if session.start_time and session.avg_price_cents > 0:
            day = session.start_time.date()
            daily_prices[day].append(session.avg_price_cents)

    if not daily_prices:
        return None

    # Calculate daily averages
    sorted_days = sorted(daily_prices.keys())
    dates = sorted_days
    avg_prices = [sum(daily_prices[d]) / len(daily_prices[d]) for d in sorted_days]

    # Create figure
    fig, ax = plt.subplots(figsize=(8, 3))

    # Line chart
    ax.plot(dates, avg_prices, color='#1a73e8', linewidth=2, marker='o',
            markersize=4, markerfacecolor='white', markeredgewidth=2)

    # Fill under the line
    ax.fill_between(dates, avg_prices, alpha=0.1, color='#1a73e8')

    # Styling
    ax.set_ylabel('Price (cents/kWh)', fontsize=10, color='#5f6368')
    ax.set_title('Average Charging Price Trend', fontsize=12, fontweight='bold', pad=10)

    # Format x-axis
    if len(dates) > 14:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.xticks(rotation=45, ha='right', fontsize=8)
    plt.yticks(fontsize=8)

    # Grid
    ax.yaxis.grid(True, linestyle='--', alpha=0.3)
    ax.set_axisbelow(True)

    # Remove top and right spines
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#dadce0')
    ax.spines['bottom'].set_color('#dadce0')

    plt.tight_layout()

    # Save to bytes
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_pdf_report(
    summary: SessionSummary,
    sessions: List[ChargingSession],
    title: str = "Tesla Wall Connector Charging Report",
    comparison_rate: float = 15.0,  # Fixed rate for savings comparison (cents/kWh)
) -> bytes:
    """Generate an enhanced PDF report for charging data."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=22,
        alignment=TA_CENTER,
        spaceAfter=5,
        textColor=COLORS['primary'],
    )

    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=11,
        alignment=TA_CENTER,
        spaceAfter=20,
        textColor=COLORS['dark_gray'],
    )

    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=15,
        spaceAfter=10,
        textColor=COLORS['primary'],
        borderPadding=(0, 0, 5, 0),
    )

    subsection_style = ParagraphStyle(
        'SubsectionHeader',
        parent=styles['Heading3'],
        fontSize=11,
        spaceBefore=10,
        spaceAfter=5,
        textColor=COLORS['dark_gray'],
    )

    elements = []

    # ===== HEADER =====
    elements.append(Paragraph(title, title_style))

    period_text = f"{summary.start_date.strftime('%B %d, %Y')} - {summary.end_date.strftime('%B %d, %Y')}"
    elements.append(Paragraph(period_text, subtitle_style))

    elements.append(HRFlowable(
        width="100%", thickness=2, color=COLORS['primary'],
        spaceBefore=5, spaceAfter=15
    ))

    # ===== KEY METRICS SUMMARY =====
    elements.append(Paragraph("Summary", section_style))

    # Calculate additional metrics
    avg_energy_per_session = (summary.total_energy_wh / 1000 / summary.total_sessions) if summary.total_sessions > 0 else 0
    avg_cost_per_session = (summary.total_full_cost_cents / 100 / summary.total_sessions) if summary.total_sessions > 0 else 0
    avg_duration_per_session = (summary.total_duration_s / 60 / summary.total_sessions) if summary.total_sessions > 0 else 0

    # Calculate savings vs fixed rate
    fixed_rate_cost = (summary.total_energy_wh / 1000) * comparison_rate / 100  # dollars
    actual_cost = summary.total_full_cost_cents / 100
    savings = fixed_rate_cost - actual_cost
    savings_percent = (savings / fixed_rate_cost * 100) if fixed_rate_cost > 0 else 0

    # Main metrics in a nice grid
    metrics_data = [
        ['Total Sessions', 'Total Energy', 'Total Cost', 'Charging Time'],
        [
            str(summary.total_sessions),
            f"{summary.total_energy_wh / 1000:.1f} kWh",
            f"${summary.total_full_cost_cents / 100:.2f}",
            f"{summary.total_duration_s / 3600:.1f} hours"
        ],
    ]

    metrics_table = Table(metrics_data, colWidths=[1.7 * inch] * 4)
    metrics_table.setStyle(TableStyle([
        # Header row
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('TEXTCOLOR', (0, 0), (-1, 0), COLORS['dark_gray']),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        # Value row
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, 1), 16),
        ('TEXTCOLOR', (0, 1), (-1, 1), COLORS['primary']),
        ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
        # Padding
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        # Background
        ('BACKGROUND', (0, 0), (-1, -1), COLORS['light_gray']),
        ('BOX', (0, 0), (-1, -1), 1, COLORS['border']),
    ]))
    elements.append(metrics_table)
    elements.append(Spacer(1, 15))

    # Secondary metrics
    secondary_data = [
        ['Avg per Session', 'Supply Cost', 'Delivery Cost', 'Avg Price'],
        [
            f"{avg_energy_per_session:.1f} kWh / ${avg_cost_per_session:.2f}",
            f"${summary.total_supply_cost_cents / 100:.2f}",
            f"${(summary.total_full_cost_cents - summary.total_supply_cost_cents) / 100:.2f}",
            f"{summary.avg_price_cents:.2f} ¢/kWh"
        ],
    ]

    secondary_table = Table(secondary_data, colWidths=[1.7 * inch] * 4)
    secondary_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('TEXTCOLOR', (0, 0), (-1, 0), COLORS['dark_gray']),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, 1), 11),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(secondary_table)

    # Savings comparison box
    if summary.total_energy_wh > 0:
        elements.append(Spacer(1, 15))

        if savings >= 0:
            savings_text = f"You saved ${savings:.2f} ({savings_percent:.1f}%) compared to a {comparison_rate}¢/kWh fixed rate!"
            savings_color = COLORS['secondary']
        else:
            savings_text = f"Cost ${-savings:.2f} ({-savings_percent:.1f}%) more than a {comparison_rate}¢/kWh fixed rate"
            savings_color = COLORS['danger']

        savings_style = ParagraphStyle(
            'Savings',
            parent=styles['Normal'],
            fontSize=11,
            alignment=TA_CENTER,
            textColor=savings_color,
            fontName='Helvetica-Bold',
        )
        elements.append(Paragraph(savings_text, savings_style))

    # ===== CHARTS =====
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("Charts", section_style))

    # Daily energy chart
    energy_chart = _create_daily_energy_chart(sessions)
    if energy_chart:
        img = Image(io.BytesIO(energy_chart), width=7 * inch, height=2.8 * inch)
        elements.append(img)
        elements.append(Spacer(1, 10))

    # Cost breakdown and price trend side by side
    cost_chart = _create_cost_breakdown_chart(summary)
    price_chart = _create_price_trend_chart(sessions)

    if cost_chart or price_chart:
        chart_row = []
        if cost_chart:
            chart_row.append(Image(io.BytesIO(cost_chart), width=3.2 * inch, height=2.5 * inch))
        if price_chart:
            chart_row.append(Image(io.BytesIO(price_chart), width=3.8 * inch, height=2.2 * inch))

        if len(chart_row) == 2:
            chart_table = Table([chart_row], colWidths=[3.4 * inch, 4 * inch])
            chart_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ]))
            elements.append(chart_table)
        elif chart_row:
            elements.append(chart_row[0])

    # ===== SESSION DETAILS =====
    if sessions:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("Session Details", section_style))

        # Best and worst sessions
        sorted_by_price = sorted([s for s in sessions if s.avg_price_cents > 0],
                                  key=lambda x: x.avg_price_cents)

        # Only show best/worst table if we have at least 2 sessions to compare
        if len(sorted_by_price) >= 2:
            elements.append(Paragraph("Best & Worst Charging Times", subsection_style))

            best_worst_data = [['', 'Date/Time', 'Energy', 'Price', 'Cost']]

            # Best sessions (up to 3)
            num_best = min(3, len(sorted_by_price))
            for i, session in enumerate(sorted_by_price[:num_best]):
                best_worst_data.append([
                    f"#{i+1} Best",
                    session.start_time.strftime("%m/%d %I:%M %p") if session.start_time else "-",
                    f"{session.energy_wh / 1000:.1f} kWh",
                    f"{session.avg_price_cents:.1f}¢",
                    f"${session.full_cost_cents / 100:.2f}",
                ])

            # Worst sessions (up to 3, avoiding duplicates with best)
            # If we have 6+ sessions, show 3 worst; otherwise show remaining non-best sessions
            worst_sessions = sorted_by_price[max(num_best, len(sorted_by_price) - 3):]
            num_worst = len(worst_sessions)
            for i, session in enumerate(reversed(worst_sessions)):
                best_worst_data.append([
                    f"#{i+1} Worst",
                    session.start_time.strftime("%m/%d %I:%M %p") if session.start_time else "-",
                    f"{session.energy_wh / 1000:.1f} kWh",
                    f"{session.avg_price_cents:.1f}¢",
                    f"${session.full_cost_cents / 100:.2f}",
                ])

            # Build table style dynamically based on actual row count
            table_style = [
                # Header
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 0), (-1, 0), COLORS['primary']),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                # All cells
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
                ('ALIGN', (0, 1), (1, -1), 'LEFT'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('BOX', (0, 0), (-1, -1), 1, COLORS['border']),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, COLORS['border']),
            ]

            # Add background colors only for rows that exist
            last_best_row = num_best  # Row index (1-based after header)
            if num_best > 0:
                table_style.append(('BACKGROUND', (0, 1), (-1, last_best_row), colors.HexColor('#e6f4ea')))
            if num_worst > 0:
                first_worst_row = last_best_row + 1
                last_worst_row = first_worst_row + num_worst - 1
                table_style.append(('BACKGROUND', (0, first_worst_row), (-1, last_worst_row), colors.HexColor('#fce8e6')))

            best_worst_table = Table(best_worst_data, colWidths=[1 * inch, 1.5 * inch, 1 * inch, 0.8 * inch, 1 * inch])
            best_worst_table.setStyle(TableStyle(table_style))
            elements.append(best_worst_table)
            elements.append(Spacer(1, 15))

        # Full session table (only if there are sessions)
        if sessions:
            elements.append(Paragraph("All Sessions", subsection_style))

            session_header = ['Date', 'Duration', 'Energy', 'Avg Price', 'Supply', 'Full Cost']
            session_rows = [session_header]

            for session in sessions[:30]:  # Limit to 30 sessions
                session_rows.append([
                    session.start_time.strftime("%m/%d %I:%M %p") if session.start_time else "-",
                    f"{session.duration_s / 60:.0f} min",
                    f"{session.energy_wh / 1000:.2f} kWh",
                    f"{session.avg_price_cents:.1f}¢",
                    f"${session.supply_cost_cents / 100:.2f}",
                    f"${session.full_cost_cents / 100:.2f}",
                ])

            # Build table style - only add alternating colors for rows that exist
            table_style = [
                # Header
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 0), (-1, 0), COLORS['primary']),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                # Data rows
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
                ('ALIGN', (0, 1), (0, -1), 'LEFT'),
                # Borders and padding
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('BOX', (0, 0), (-1, -1), 1, COLORS['border']),
                ('LINEBELOW', (0, 0), (-1, 0), 1, COLORS['primary']),
            ]

            # Add alternating row colors only for rows that exist
            for i in range(2, len(session_rows), 2):
                table_style.append(('BACKGROUND', (0, i), (-1, i), COLORS['light_gray']))

            session_table = Table(
                session_rows,
                colWidths=[1.3 * inch, 0.8 * inch, 1 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch],
            )
            session_table.setStyle(TableStyle(table_style))
            elements.append(session_table)

            if len(sessions) > 30:
                elements.append(Spacer(1, 5))
                note_style = ParagraphStyle(
                    'Note',
                    parent=styles['Normal'],
                    fontSize=8,
                    textColor=COLORS['dark_gray'],
                    alignment=TA_CENTER,
                )
                elements.append(Paragraph(
                    f"Showing 30 of {len(sessions)} sessions. Export CSV for complete data.",
                    note_style
                ))
        else:
            # No sessions to display
            no_data_style = ParagraphStyle(
                'NoData',
                parent=styles['Normal'],
                fontSize=10,
                textColor=COLORS['dark_gray'],
                alignment=TA_CENTER,
            )
            elements.append(Spacer(1, 10))
            elements.append(Paragraph("No charging sessions found for the selected period.", no_data_style))

    # ===== FOOTER =====
    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(
        width="100%", thickness=1, color=COLORS['border'],
        spaceBefore=10, spaceAfter=10
    ))

    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        alignment=TA_CENTER,
        textColor=COLORS['dark_gray'],
    )
    footer_text = f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    elements.append(Paragraph(footer_text, footer_style))
    elements.append(Paragraph("Tesla Wall Connector Dashboard", footer_style))

    doc.build(elements)
    return buffer.getvalue()
