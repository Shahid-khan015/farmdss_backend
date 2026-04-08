from __future__ import annotations

import json
import math
import urllib.error
import urllib.request


def request_json(method: str, url: str, data: dict | None = None):
    headers = {"Content-Type": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def approx_equal(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def main():
    base = "http://127.0.0.1:8000/api/v1"

    # Legacy-required tractor + tire inputs.
    tractor = request_json(
        "POST",
        f"{base}/tractors",
        {
            "name": "Legacy Regression Tractor",
            "manufacturer": "Test",
            "model": "LR-100",
            "drive_mode": "2WD",
            "pto_power": 45.0,
            "rated_engine_speed": 2200,
            "max_engine_torque": 180.0,
            "wheelbase": 2.3,
            "front_axle_weight": 900.0,
            "rear_axle_weight": 1500.0,
            "hitch_distance_from_rear": 0.5,
            "cg_distance_from_rear": 1.2,
            "rear_wheel_rolling_radius": 0.58,
            "transmission_efficiency": 86.0,
            "power_reserve": 20.0,
            "tire_specification": {
                "tire_type": "Bias Ply",
                "front_overall_diameter": 900,
                "front_section_width": 240,
                "front_static_loaded_radius": 380,
                "front_rolling_radius": 400,
                "rear_overall_diameter": 1300,
                "rear_section_width": 340,
                "rear_static_loaded_radius": 540,
                "rear_rolling_radius": 580,
            },
        },
    )

    # MB Plough to validate Fi mapping exactly.
    implement = request_json(
        "POST",
        f"{base}/implements",
        {
            "name": "Legacy Regression MB",
            "manufacturer": "Test",
            "implement_type": "MB Plough",
            "width": 1.5,
            "weight": 320.0,
            "cg_distance_from_hitch": 0.8,
            "vertical_horizontal_ratio": 0.5,
            "asae_param_a": 100.0,
            "asae_param_b": 50.0,
            "asae_param_c": 10.0,
        },
    )

    common = {
        "name": "Legacy Case",
        "tractor_id": tractor["id"],
        "implement_id": implement["id"],
        "cone_index": 1200,
        "depth": 15.0,
        "speed": 5.0,
        "field_area": 2.0,
        "field_length": 200.0,
        "field_width": 100.0,
        "number_of_turns": 10,
        "soil_hardness": "Firm",
    }

    cases = [
        ("fine_mb", {**common, "soil_texture": "Fine"}),
        ("coarse_mb", {**common, "soil_texture": "Coarse"}),
        ("medium_mb", {**common, "soil_texture": "Medium"}),
        ("fine_mb_depth20", {**common, "soil_texture": "Fine", "depth": 20.0}),
        ("fine_mb_speed6", {**common, "soil_texture": "Fine", "speed": 6.0}),
    ]

    sims: dict[str, dict] = {}
    for key, payload in cases:
        try:
            sims[key] = request_json("POST", f"{base}/simulations/run", payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            print(f"{key}: HTTP {e.code} {body}")
            return

    # Validate draft equation and direct derived equations.
    A = 100.0
    B = 50.0
    C = 10.0
    W = 1.5
    fi = {"Fine": 1.0, "Coarse": 0.45, "Medium": 0.70}
    for key, payload in cases:
        sim = sims[key]
        d = float(payload["depth"])
        v = float(payload["speed"])
        f = fi[payload["soil_texture"]]
        expected_draft = f * (A + B * v + C * v * v) * W * d
        got_draft = float(sim["draft_force"])
        expected_pdb = expected_draft * v / 3600.0
        got_pdb = float(sim["drawbar_power"])
        got_fc_th = float(sim["field_capacity_theoretical"])
        expected_fc_th = W * v / 10.0
        print(
            f"{key}: draft={got_draft:.4f} exp={expected_draft:.4f} "
            f"pdb={got_pdb:.6f} exp_pdb={expected_pdb:.6f} "
            f"fc_th={got_fc_th:.6f} exp_fc_th={expected_fc_th:.6f}"
        )
        if not approx_equal(got_draft, expected_draft, tol=1e-4):
            print(f"FAIL: {key} draft mismatch")
            return
        if not approx_equal(got_pdb, expected_pdb, tol=1e-6):
            print(f"FAIL: {key} drawbar_power mismatch")
            return
        if not approx_equal(got_fc_th, expected_fc_th, tol=1e-6):
            print(f"FAIL: {key} field_capacity_theoretical mismatch")
            return

    # Validate Fi ratios for MB Plough
    fine_draft = float(sims["fine_mb"]["draft_force"])
    coarse_draft = float(sims["coarse_mb"]["draft_force"])
    medium_draft = float(sims["medium_mb"]["draft_force"])
    if not approx_equal(coarse_draft / fine_draft, 0.45, tol=1e-4):
        print("FAIL: coarse/fine Fi ratio mismatch")
        return
    if not approx_equal(medium_draft / fine_draft, 0.70, tol=1e-4):
        print("FAIL: medium/fine Fi ratio mismatch")
        return

    # Validate depth linearity
    depth15 = float(sims["fine_mb"]["draft_force"])
    depth20 = float(sims["fine_mb_depth20"]["draft_force"])
    if not approx_equal(depth20 / depth15, 20.0 / 15.0, tol=1e-4):
        print("FAIL: depth linearity mismatch")
        return

    # Validate speed effect using explicit equation ratio
    speed5 = float(sims["fine_mb"]["draft_force"])
    speed6 = float(sims["fine_mb_speed6"]["draft_force"])
    expected_ratio = (A + B * 6 + C * 36) / (A + B * 5 + C * 25)
    if not approx_equal(speed6 / speed5, expected_ratio, tol=1e-4):
        print("FAIL: speed equation ratio mismatch")
        return

    print("PASS: legacy regression checks passed")
    print("Simulation IDs:")
    for key in sims:
        print(f"  {key}: {sims[key]['id']}")


if __name__ == "__main__":
    main()

