from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db
from app.core.performance_calculator import PerformanceInputs, calculate_performance
from app.crud.implement import implement_crud
from app.crud.operating_condition import operating_condition_crud
from app.crud.simulation import simulation_crud
from app.crud.tractor import tractor_crud
from app.crud.tire_specification import tire_crud
from app.middleware.auth import get_current_user
from app.models.enums import SoilTexture
from app.models.simulation import Simulation
from app.models.user import User
from app.schemas.common import DeleteResponse, PaginatedResponse
from app.schemas.simulation import SimulationRead, SimulationRunRequest

router = APIRouter()


def _fmt_decimal(value: Optional[Decimal], precision: int = 2) -> str:
    if value is None:
        return ""
    return f"{float(value):.{precision}f}"


def _build_simulation_csv_bytes(
    sim: SimulationRead,
    *,
    tractor_name: str | None = None,
    implement_name: str | None = None,
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["SIMULATION REPORT"])
    writer.writerow([])
    writer.writerow(["Field", "Value"])
    writer.writerow(["Simulation ID", str(sim.id)])
    writer.writerow(["Name", sim.name or ""])
    writer.writerow(["Created At", sim.created_at.isoformat() if sim.created_at else ""])
    writer.writerow(["Tractor", tractor_name or str(sim.tractor_id)])
    writer.writerow(["Implement", implement_name or str(sim.implement_id)])
    writer.writerow(["Tractor ID", str(sim.tractor_id)])
    writer.writerow(["Implement ID", str(sim.implement_id)])
    writer.writerow(["Operation Preset ID", str(sim.operating_conditions_preset_id) if sim.operating_conditions_preset_id else ""])
    writer.writerow(["Cone Index", _fmt_decimal(sim.cone_index)])
    writer.writerow(["Depth", _fmt_decimal(sim.depth)])
    writer.writerow(["Speed", _fmt_decimal(sim.speed)])
    writer.writerow(["Field Area", _fmt_decimal(sim.field_area)])
    writer.writerow(["Field Length", _fmt_decimal(sim.field_length)])
    writer.writerow(["Field Width", _fmt_decimal(sim.field_width)])
    writer.writerow(["Number of Turns", sim.number_of_turns if sim.number_of_turns is not None else ""])
    writer.writerow(["Soil Texture", sim.soil_texture or ""])
    writer.writerow(["Soil Hardness", sim.soil_hardness or ""])
    writer.writerow(["Draft Force", _fmt_decimal(sim.draft_force)])
    writer.writerow(["Drawbar Power", _fmt_decimal(sim.drawbar_power)])
    writer.writerow(["Slip", _fmt_decimal(sim.slip)])
    writer.writerow(["Traction Efficiency", _fmt_decimal(sim.traction_efficiency)])
    writer.writerow(["Power Utilization", _fmt_decimal(sim.power_utilization)])
    writer.writerow(["Field Capacity Theoretical", _fmt_decimal(sim.field_capacity_theoretical)])
    writer.writerow(["Field Capacity Actual", _fmt_decimal(sim.field_capacity_actual)])
    writer.writerow(["Field Efficiency", _fmt_decimal(sim.field_efficiency)])
    writer.writerow(["Fuel Consumption Per Hectare", _fmt_decimal(sim.fuel_consumption_per_hectare)])
    writer.writerow(["Overall Efficiency", _fmt_decimal(sim.overall_efficiency)])
    writer.writerow(["Ballast Front Required", _fmt_decimal(sim.ballast_front_required)])
    writer.writerow(["Ballast Rear Required", _fmt_decimal(sim.ballast_rear_required)])
    writer.writerow(["Status Message", sim.status_message or ""])
    writer.writerow(["Recommendations", sim.recommendations or ""])

    if sim.results:
        writer.writerow([])
        writer.writerow(["RESULTS"])
        writer.writerow(["Metric", "Value"])
        for key, value in sim.results.items():
            writer.writerow([key, value if value is not None else ""])

    return buf.getvalue().encode("utf-8-sig")


def _build_simulation_pdf_bytes(
    sim: SimulationRead,
    *,
    tractor_name: str | None = None,
    implement_name: str | None = None,
) -> bytes:
    try:
        from reportlab.lib import colors as rl_colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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
    title_style = ParagraphStyle("SimulationTitle", parent=styles["Title"], fontSize=16, spaceAfter=6)
    h2_style = ParagraphStyle("SectionHead", parent=styles["Heading2"], fontSize=11, spaceBefore=14, spaceAfter=4)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8)

    header_bg = rl_colors.HexColor("#1E6B3C")
    alt_bg = rl_colors.HexColor("#F4F7F5")
    border = rl_colors.HexColor("#D6E2DA")

    def table(data: list[list], col_widths=None) -> Table:
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, alt_bg]),
                ("GRID", (0, 0), (-1, -1), 0.3, border),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )
        return t

    elements = [
        Paragraph(f"Simulation Report — {sim.name or str(sim.id)[:8]}", title_style),
        Paragraph(
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            small,
        ),
        Spacer(1, 10),
        Paragraph("Simulation Overview", h2_style),
    ]

    page_w = A4[0] - 3 * cm
    overview = [
        ["Field", "Value"],
        ["Simulation ID", str(sim.id)],
        ["Created At", sim.created_at.isoformat() if sim.created_at else ""],
        ["Tractor", tractor_name or str(sim.tractor_id)],
        ["Implement", implement_name or str(sim.implement_id)],
        ["Tractor ID", str(sim.tractor_id)],
        ["Implement ID", str(sim.implement_id)],
        ["Cone Index", _fmt_decimal(sim.cone_index)],
        ["Depth", _fmt_decimal(sim.depth)],
        ["Speed", _fmt_decimal(sim.speed)],
        ["Field Area", _fmt_decimal(sim.field_area)],
        ["Field Length", _fmt_decimal(sim.field_length)],
        ["Field Width", _fmt_decimal(sim.field_width)],
        ["Turns", str(sim.number_of_turns) if sim.number_of_turns is not None else ""],
        ["Soil", " / ".join(filter(None, [sim.soil_texture or "", sim.soil_hardness or ""]))],
        ["Status Message", sim.status_message or ""],
        ["Recommendations", sim.recommendations or ""],
    ]
    elements.append(table(overview, col_widths=[page_w * 0.34, page_w * 0.66]))

    metrics = [
        ["Metric", "Value"],
        ["Draft Force", _fmt_decimal(sim.draft_force)],
        ["Drawbar Power", _fmt_decimal(sim.drawbar_power)],
        ["Slip", _fmt_decimal(sim.slip)],
        ["Traction Efficiency", _fmt_decimal(sim.traction_efficiency)],
        ["Power Utilization", _fmt_decimal(sim.power_utilization)],
        ["Field Capacity Theoretical", _fmt_decimal(sim.field_capacity_theoretical)],
        ["Field Capacity Actual", _fmt_decimal(sim.field_capacity_actual)],
        ["Field Efficiency", _fmt_decimal(sim.field_efficiency)],
        ["Fuel Consumption / ha", _fmt_decimal(sim.fuel_consumption_per_hectare)],
        ["Overall Efficiency", _fmt_decimal(sim.overall_efficiency)],
        ["Front Ballast", _fmt_decimal(sim.ballast_front_required)],
        ["Rear Ballast", _fmt_decimal(sim.ballast_rear_required)],
    ]
    elements.append(Paragraph("Performance Metrics", h2_style))
    elements.append(table(metrics, col_widths=[page_w * 0.55, page_w * 0.45]))

    if sim.results:
        results_data = [["Result Key", "Value"]]
        for key, value in sim.results.items():
            results_data.append([key, "" if value is None else str(value)])
        elements.append(Paragraph("Detailed Results", h2_style))
        elements.append(table(results_data, col_widths=[page_w * 0.42, page_w * 0.58]))

    doc.build(elements)
    return buf.getvalue()


@router.get("", response_model=PaginatedResponse[SimulationRead])
def list_simulations(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tractor_id: Optional[uuid.UUID] = Query(default=None),
    implement_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = simulation_crud.list(
        db, tractor_id=tractor_id, implement_id=implement_id, limit=limit, offset=offset
    )
    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/compare", response_model=list[SimulationRead])
def compare_simulations(
    ids: list[uuid.UUID] = Query(
        ...,
        description="Simulation IDs to compare (repeat query param).",
        max_length=10,
    ),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # simple compare: return list of SimulationRead for requested ids (client can compare)
    sims = []
    for id_ in ids:
        sim = simulation_crud.get(db, id=id_)
        if sim:
            sims.append(sim)
    if not sims:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No simulations found for given ids"
        )
    return sims


@router.get("/{id}", response_model=SimulationRead)
def get_simulation(
    id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    obj = simulation_crud.get(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return obj


@router.post("/run", response_model=SimulationRead, status_code=status.HTTP_201_CREATED)
def run_simulation(
    payload: SimulationRunRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tractor = tractor_crud.get_with_tires(db, id=payload.tractor_id)
    if not tractor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    implement = implement_crud.get(db, id=payload.implement_id)
    if not implement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Implement not found")
    tires = tire_crud.get_by_tractor_id(db, tractor_id=tractor.id)
    if not tires:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tractor is missing tire specifications",
        )

    # Resolve operating conditions
    if payload.operating_conditions_preset_id is not None:
        preset = operating_condition_crud.get(db, id=payload.operating_conditions_preset_id)
        if not preset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
        cone_index = preset.cone_index
        depth = preset.depth
        speed = preset.speed
        field_area = preset.field_area
        field_length = preset.field_length
        field_width = preset.field_width
        number_of_turns = preset.number_of_turns
        soil_texture = preset.soil_texture.value
        soil_hardness = preset.soil_hardness.value
    else:
        preset = None
        cone_index = payload.cone_index
        depth = payload.depth
        speed = payload.speed
        field_area = payload.field_area
        field_length = payload.field_length
        field_width = payload.field_width
        number_of_turns = payload.number_of_turns
        soil_texture = payload.soil_texture
        soil_hardness = payload.soil_hardness

    # Validate required numeric fields for algorithm
    required_tractor = [
        ("pto_power", tractor.pto_power),
        ("wheelbase", tractor.wheelbase),
        ("front_axle_weight", tractor.front_axle_weight),
        ("rear_axle_weight", tractor.rear_axle_weight),
        ("hitch_distance_from_rear", tractor.hitch_distance_from_rear),
        ("cg_distance_from_rear", tractor.cg_distance_from_rear),
        ("transmission_efficiency", tractor.transmission_efficiency),
        ("power_reserve", tractor.power_reserve),
    ]
    missing_tractor = [k for k, v in required_tractor if v is None]
    if missing_tractor:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tractor missing required fields for simulation: {missing_tractor}",
        )

    required_impl = [
        ("width", implement.width),
        ("weight", implement.weight),
        ("cg_distance_from_hitch", implement.cg_distance_from_hitch),
        ("vertical_horizontal_ratio", implement.vertical_horizontal_ratio),
        ("asae_param_a", implement.asae_param_a),
        ("asae_param_b", implement.asae_param_b),
        ("asae_param_c", implement.asae_param_c),
    ]
    missing_impl = [k for k, v in required_impl if v is None]
    if missing_impl:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Implement missing required fields for simulation: {missing_impl}",
        )

    required_cond = [
        ("soil_texture", soil_texture),
        ("cone_index", cone_index),
        ("depth", depth),
        ("speed", speed),
        ("field_area", field_area),
        ("field_width", field_width),
    ]
    missing_cond = [k for k, v in required_cond if v is None]
    if missing_cond:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Operating conditions missing required fields for simulation: {missing_cond}",
        )

    required_tires = [
        ("front_overall_diameter", tires.front_overall_diameter),
        ("rear_overall_diameter", tires.rear_overall_diameter),
        ("front_section_width", tires.front_section_width),
        ("rear_section_width", tires.rear_section_width),
    ]
    missing_tires = [k for k, v in required_tires if v is None]
    if missing_tires:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tire specs missing required fields for legacy simulation: {missing_tires}",
        )

    front_rr_m = (
        float(tires.front_rolling_radius) / 1000.0
        if tires.front_rolling_radius is not None
        else None
    )
    rear_rr_m = (
        float(tires.rear_rolling_radius) / 1000.0
        if tires.rear_rolling_radius is not None
        else None
    )
    if front_rr_m is None:
        if tires.front_static_loaded_radius is not None:
            front_rr_m = float(tires.front_static_loaded_radius) / 1000.0
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tire specs missing front rolling radius (front_rolling_radius/front_static_loaded_radius)",
            )
    if rear_rr_m is None:
        if tires.rear_static_loaded_radius is not None:
            rear_rr_m = float(tires.rear_static_loaded_radius) / 1000.0
        elif tractor.rear_wheel_rolling_radius is not None:
            rear_rr_m = float(tractor.rear_wheel_rolling_radius)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing rear rolling radius (tire rear_rolling_radius/rear_static_loaded_radius or tractor rear_wheel_rolling_radius)",
            )

    try:
        soil_texture_enum = soil_texture if isinstance(soil_texture, SoilTexture) else SoilTexture(str(soil_texture))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid soil_texture '{soil_texture}' for legacy DSS",
        )

    perf_inputs = PerformanceInputs(
        pto_power_kw=float(tractor.pto_power),
        wheelbase_m=float(tractor.wheelbase),
        front_axle_weight_kg=float(tractor.front_axle_weight),
        rear_axle_weight_kg=float(tractor.rear_axle_weight),
        hitch_distance_from_rear_m=float(tractor.hitch_distance_from_rear),
        cg_distance_from_rear_m=float(tractor.cg_distance_from_rear),
        transmission_efficiency_pct=float(tractor.transmission_efficiency),
        power_reserve_pct=float(tractor.power_reserve),
        front_rolling_radius_m=front_rr_m,
        rear_rolling_radius_m=rear_rr_m,
        front_overall_diameter_m=float(tires.front_overall_diameter) / 1000.0,
        rear_overall_diameter_m=float(tires.rear_overall_diameter) / 1000.0,
        front_section_width_m=float(tires.front_section_width) / 1000.0,
        rear_section_width_m=float(tires.rear_section_width) / 1000.0,
        implement_type=implement.implement_type,
        width_m=float(implement.width),
        weight_kg=float(implement.weight),
        cg_distance_from_hitch_m=float(implement.cg_distance_from_hitch),
        vertical_horizontal_ratio=float(implement.vertical_horizontal_ratio),
        asae_param_a=float(implement.asae_param_a),
        asae_param_b=float(implement.asae_param_b),
        asae_param_c=float(implement.asae_param_c),
        soil_texture=soil_texture_enum,
        cone_index_kpa=float(cone_index),
        depth_cm=float(depth),
        speed_kmh=float(speed),
        field_area_ha=float(field_area),
        field_width_m=float(field_width),
    )

    try:
        results = calculate_performance(perf_inputs)
    except (ValueError, ZeroDivisionError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # Persist simulation + key result columns
    sim = simulation_crud.create(
        db,
        obj_in=payload,
        extra={
            "operating_conditions_preset_id": payload.operating_conditions_preset_id,
            "cone_index": cone_index,
            "depth": depth,
            "speed": speed,
            "field_area": field_area,
            "field_length": field_length,
            "field_width": field_width,
            "number_of_turns": int(results.get("legacy_number_of_turns")) if results.get("legacy_number_of_turns") is not None else number_of_turns,
            "soil_texture": soil_texture,
            "soil_hardness": soil_hardness,
            "results": results,
            "draft_force": Decimal(str(results.get("draft_force"))) if results.get("draft_force") is not None else None,
            "drawbar_power": Decimal(str(results.get("drawbar_power"))) if results.get("drawbar_power") is not None else None,
            "slip": Decimal(str(results.get("slip"))) if results.get("slip") is not None else None,
            "traction_efficiency": Decimal(str(results.get("traction_efficiency"))) if results.get("traction_efficiency") is not None else None,
            "power_utilization": Decimal(str(results.get("power_utilization"))) if results.get("power_utilization") is not None else None,
            "field_capacity_theoretical": Decimal(str(results.get("field_capacity_theoretical"))) if results.get("field_capacity_theoretical") is not None else None,
            "field_capacity_actual": Decimal(str(results.get("field_capacity_actual"))) if results.get("field_capacity_actual") is not None else None,
            "field_efficiency": Decimal(str(results.get("field_efficiency"))) if results.get("field_efficiency") is not None else None,
            "fuel_consumption_per_hectare": Decimal(str(results.get("fuel_consumption_per_hectare"))) if results.get("fuel_consumption_per_hectare") is not None else None,
            "overall_efficiency": Decimal(str(results.get("overall_efficiency"))) if results.get("overall_efficiency") is not None else None,
            "ballast_front_required": Decimal(str(results.get("ballast_front_required"))) if results.get("ballast_front_required") is not None else None,
            "ballast_rear_required": Decimal(str(results.get("ballast_rear_required"))) if results.get("ballast_rear_required") is not None else None,
            "status_message": results.get("status_message"),
            "recommendations": results.get("recommendations"),
        },
    )
    return sim


@router.delete("/{id}", response_model=DeleteResponse)
def delete_simulation(
    id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    obj = simulation_crud.remove(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return {"ok": True, "id": id}


@router.get("/{id}/export")
def export_simulation(
    id: uuid.UUID,
    format: str = Query(default="csv", description="Export format: csv or pdf"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if format not in ("csv", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="format must be 'csv' or 'pdf'",
        )

    sim = db.scalars(
        select(Simulation)
        .where(Simulation.id == id)
        .options(
            selectinload(Simulation.tractor),
            selectinload(Simulation.implement),
        )
    ).first()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")

    sim_read = SimulationRead.model_validate(sim)
    tractor_name = sim.tractor.name if sim.tractor is not None else None
    implement_name = sim.implement.name if sim.implement is not None else None
    safe_id = str(id)[:8]

    if format == "csv":
        data = _build_simulation_csv_bytes(
            sim_read,
            tractor_name=tractor_name,
            implement_name=implement_name,
        )
        filename = f"simulation_{safe_id}.csv"
        media_type = "text/csv"
    else:
        try:
            data = _build_simulation_pdf_bytes(
                sim_read,
                tractor_name=tractor_name,
                implement_name=implement_name,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        filename = f"simulation_{safe_id}.pdf"
        media_type = "application/pdf"

    return StreamingResponse(
        iter([data]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

