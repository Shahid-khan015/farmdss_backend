"""
CSV and PDF export for session summary reports.

Public API:
    build_csv_bytes(report)  -> bytes   (UTF-8 CSV)
    build_pdf_bytes(report)  -> bytes   (PDF via reportlab)
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from app.schemas.session import SessionSummaryReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_float(value: Optional[float], precision: int = 2) -> str:
    if value is None:
        return ""
    return f"{value:.{precision}f}"


def _fmt_duration(minutes: Optional[float]) -> str:
    if minutes is None:
        return ""
    mins = max(0, int(minutes))
    h = mins // 60
    m = mins % 60
    return f"{h}h {m}m" if h else f"{m}m"


def _fmt_distance(metres: Optional[float]) -> str:
    if metres is None:
        return ""
    if metres >= 1000:
        return f"{metres / 1000:.2f} km"
    return f"{metres:.0f} m"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def build_csv_bytes(report: SessionSummaryReport) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)

    # --- Session metadata ---
    w.writerow(["SESSION REPORT"])
    w.writerow([])
    w.writerow(["Field", "Value"])
    w.writerow(["Session ID", report.session_id])
    w.writerow(["Operation Type", report.operation_type])
    w.writerow(["Status", report.status])
    w.writerow(["Operator", report.operator_name or report.operator_id])
    w.writerow(["Started At", _fmt_ts(report.started_at)])
    w.writerow(["Ended At", _fmt_ts(report.ended_at)])
    w.writerow(["Duration", _fmt_duration(report.duration_minutes)])
    w.writerow(["Area Covered (ha)", _fmt_float(report.area_ha)])
    w.writerow(["Total Distance", _fmt_distance(report.total_distance_m)])
    w.writerow(["Total Cost (INR)", _fmt_float(report.total_cost_inr)])
    w.writerow(["Charge / ha (INR)", _fmt_float(report.charge_per_ha_applied)])
    w.writerow(["Cost Note", report.cost_note or ""])
    w.writerow(["Total Alerts", report.total_alerts])
    w.writerow(["Unacknowledged Alerts", report.unacknowledged_alerts])

    # --- Sensor metrics ---
    if report.metrics:
        w.writerow([])
        w.writerow(["SENSOR METRICS"])
        w.writerow(["Feed Key", "Label", "Unit", "Samples", "Avg", "Min", "Max", "Last"])
        for m in report.metrics:
            w.writerow([
                m.feed_key,
                m.label,
                m.unit,
                m.samples,
                _fmt_float(m.avg_value),
                _fmt_float(m.min_value),
                _fmt_float(m.max_value),
                _fmt_float(m.last_value),
            ])

    # --- Preset compliance ---
    if report.preset_summaries:
        w.writerow([])
        w.writerow(["PRESET COMPLIANCE"])
        w.writerow(["Parameter", "Target", "Actual", "Unit", "Deviation %", "Status"])
        for p in report.preset_summaries:
            w.writerow([
                p.parameter_name,
                _fmt_float(p.target_value),
                _fmt_float(p.actual_value),
                p.unit,
                _fmt_float(p.deviation_pct),
                p.status,
            ])

    # --- Alerts ---
    if report.alerts:
        w.writerow([])
        w.writerow(["ALERTS"])
        w.writerow(["Feed Key", "Type", "Severity", "Actual Value", "Message", "Acknowledged", "Time"])
        for a in report.alerts:
            w.writerow([
                a.feed_key,
                a.alert_type,
                a.alert_status,
                _fmt_float(a.actual_value),
                a.message,
                "Yes" if a.acknowledged else "No",
                _fmt_ts(a.created_at),
            ])

    # --- Field observations ---
    if report.field_observations:
        w.writerow([])
        w.writerow(["FIELD OBSERVATIONS"])
        w.writerow(["Type", "Value", "Unit", "Lat", "Lon", "Notes", "Recorded At"])
        for o in report.field_observations:
            w.writerow([
                o.obs_type,
                _fmt_float(o.value),
                o.unit,
                _fmt_float(o.lat, 6),
                _fmt_float(o.lon, 6),
                o.notes or "",
                _fmt_ts(o.recorded_at),
            ])

    return buf.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def build_pdf_bytes(report: SessionSummaryReport) -> bytes:
    try:
        from reportlab.lib import colors as rl_colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required for PDF export. Install it with: pip install reportlab"
        ) from exc

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=16,
        spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        "SectionHead",
        parent=styles["Heading2"],
        fontSize=11,
        spaceBefore=14,
        spaceAfter=4,
    )
    normal = styles["Normal"]
    small = ParagraphStyle("Small", parent=normal, fontSize=8)

    _HEADER_BG = rl_colors.HexColor("#1A3C5E")
    _ALT_BG = rl_colors.HexColor("#F0F4F8")
    _BORDER = rl_colors.HexColor("#C8D3DC")
    _WHITE = rl_colors.white
    _RED = rl_colors.HexColor("#C00000")
    _ORANGE = rl_colors.HexColor("#C55A11")

    def _table(data: list[list], col_widths=None) -> Table:
        t = Table(data, colWidths=col_widths, repeatRows=1)
        row_count = len(data)
        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), _WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_WHITE, _ALT_BG]),
            ("GRID", (0, 0), (-1, -1), 0.3, _BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        t.setStyle(TableStyle(base_style))
        return t

    elements = []

    # Title
    op_type = report.operation_type
    started = _fmt_ts(report.started_at)
    elements.append(Paragraph(f"Session Report — {op_type}", title_style))
    elements.append(Paragraph(f"Generated: {_fmt_ts(datetime.now(timezone.utc))}", small))
    elements.append(Spacer(1, 10))

    # Overview table
    elements.append(Paragraph("Session Overview", h2_style))
    overview_data = [
        ["Field", "Value"],
        ["Session ID", report.session_id],
        ["Status", report.status.capitalize()],
        ["Operator", report.operator_name or report.operator_id],
        ["Started", _fmt_ts(report.started_at)],
        ["Ended", _fmt_ts(report.ended_at)],
        ["Duration", _fmt_duration(report.duration_minutes)],
        ["Area Covered", f"{_fmt_float(report.area_ha)} ha" if report.area_ha else "—"],
        ["Total Distance", _fmt_distance(report.total_distance_m) if report.total_distance_m else "—"],
    ]
    if report.total_cost_inr is not None:
        overview_data.append(["Total Cost", f"₹ {_fmt_float(report.total_cost_inr)}"])
    if report.charge_per_ha_applied is not None:
        overview_data.append(["Rate / ha", f"₹ {_fmt_float(report.charge_per_ha_applied)}"])
    if report.cost_note:
        overview_data.append(["Cost Note", report.cost_note])
    overview_data.append(["Total Alerts", str(report.total_alerts)])
    overview_data.append(["Unacknowledged", str(report.unacknowledged_alerts)])

    page_w = A4[0] - 3 * cm
    elements.append(_table(overview_data, col_widths=[page_w * 0.38, page_w * 0.62]))
    elements.append(Spacer(1, 6))

    # Sensor metrics
    if report.metrics:
        elements.append(Paragraph("Sensor Metrics", h2_style))
        metric_data = [["Feed", "Label", "Unit", "Samples", "Avg", "Min", "Max", "Last"]]
        for m in report.metrics:
            metric_data.append([
                m.feed_key,
                m.label,
                m.unit,
                str(m.samples),
                _fmt_float(m.avg_value),
                _fmt_float(m.min_value),
                _fmt_float(m.max_value),
                _fmt_float(m.last_value),
            ])
        cws = [page_w * f for f in (0.15, 0.15, 0.08, 0.09, 0.12, 0.12, 0.12, 0.12, 0.05)]
        elements.append(_table(metric_data, col_widths=cws[:len(metric_data[0])]))
        elements.append(Spacer(1, 6))

    # Preset compliance
    if report.preset_summaries:
        elements.append(Paragraph("Preset Compliance", h2_style))
        preset_data = [["Parameter", "Target", "Actual", "Unit", "Deviation %", "Status"]]
        preset_style_extras = []
        for i, p in enumerate(report.preset_summaries, start=1):
            preset_data.append([
                p.parameter_name,
                _fmt_float(p.target_value),
                _fmt_float(p.actual_value),
                p.unit,
                _fmt_float(p.deviation_pct),
                p.status,
            ])
            if p.status == "critical":
                preset_style_extras.append(("TEXTCOLOR", (5, i), (5, i), _RED))
            elif p.status == "warning":
                preset_style_extras.append(("TEXTCOLOR", (5, i), (5, i), _ORANGE))
        cws2 = [page_w * f for f in (0.25, 0.13, 0.13, 0.10, 0.20, 0.19)]
        pt = _table(preset_data, col_widths=cws2)
        for extra in preset_style_extras:
            pt.setStyle(TableStyle([extra]))
        elements.append(pt)
        elements.append(Spacer(1, 6))

    # Alerts
    if report.alerts:
        elements.append(Paragraph("Alerts", h2_style))
        alert_data = [["Feed Key", "Type", "Severity", "Value", "Message", "Acked", "Time"]]
        alert_style_extras = []
        for i, a in enumerate(report.alerts, start=1):
            alert_data.append([
                a.feed_key,
                a.alert_type,
                a.alert_status,
                _fmt_float(a.actual_value),
                Paragraph(a.message, small),
                "Yes" if a.acknowledged else "No",
                _fmt_ts(a.created_at)[:16],
            ])
            if a.alert_status == "critical":
                alert_style_extras.append(("TEXTCOLOR", (2, i), (2, i), _RED))
            elif a.alert_status == "warning":
                alert_style_extras.append(("TEXTCOLOR", (2, i), (2, i), _ORANGE))
        cws3 = [page_w * f for f in (0.13, 0.09, 0.09, 0.07, 0.36, 0.06, 0.20)]
        at = _table(alert_data, col_widths=cws3)
        for extra in alert_style_extras:
            at.setStyle(TableStyle([extra]))
        elements.append(at)
        elements.append(Spacer(1, 6))

    # Field observations
    if report.field_observations:
        elements.append(Paragraph("Field Observations", h2_style))
        obs_data = [["Type", "Value", "Unit", "Lat", "Lon", "Notes", "Recorded"]]
        for o in report.field_observations:
            obs_data.append([
                o.obs_type,
                _fmt_float(o.value),
                o.unit,
                _fmt_float(o.lat, 5) if o.lat else "",
                _fmt_float(o.lon, 5) if o.lon else "",
                o.notes or "",
                _fmt_ts(o.recorded_at)[:16],
            ])
        cws4 = [page_w * f for f in (0.13, 0.09, 0.08, 0.10, 0.10, 0.28, 0.22)]
        elements.append(_table(obs_data, col_widths=cws4))

    doc.build(elements)
    return buf.getvalue()
