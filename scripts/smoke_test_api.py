from __future__ import annotations

import json
import urllib.request


def request_json(method: str, url: str, data: dict | None = None):
    headers = {"Content-Type": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    base = "http://127.0.0.1:8000/api/v1"

    tractor = request_json(
        "POST",
        f"{base}/tractors",
        {
            "name": "Demo Tractor",
            "manufacturer": "Demo",
            "model": "T-100",
            "drive_mode": "2WD",
            "pto_power": 45.0,
            "wheelbase": 2.3,
            "front_axle_weight": 800,
            "rear_axle_weight": 1400,
            "hitch_distance_from_rear": 0.5,
            "cg_distance_from_rear": 1.1,
            "power_reserve": 20.0,
            "rear_wheel_rolling_radius": 0.58,
            "transmission_efficiency": 86.0,
            "tire_specification": {
                "tire_type": "Bias Ply",
                "front_overall_diameter": 850,
                "front_section_width": 220,
                "front_static_loaded_radius": 360,
                "front_rolling_radius": 380,
                "rear_overall_diameter": 1200,
                "rear_section_width": 300,
                "rear_static_loaded_radius": 540,
                "rear_rolling_radius": 580,
            },
        },
    )

    implement = request_json(
        "POST",
        f"{base}/implements",
        {
            "name": "Demo MB Plough",
            "manufacturer": "Demo",
            "implement_type": "MB Plough",
            "width": 1.5,
            "weight": 300,
            "cg_distance_from_hitch": 0.8,
            "vertical_horizontal_ratio": 0.5,
            "asae_param_a": 100,
            "asae_param_b": 50,
            "asae_param_c": 10,
        },
    )

    sim = request_json(
        "POST",
        f"{base}/simulations/run",
        {
            "name": "Demo Sim",
            "tractor_id": tractor["id"],
            "implement_id": implement["id"],
            "cone_index": 1200,
            "depth": 5,
            "speed": 4.0,
            "field_area": 2.0,
            "field_length": 200,
            "field_width": 100,
            "number_of_turns": 10,
            "soil_texture": "Medium",
            "soil_hardness": "Firm",
        },
    )

    print("tractor", tractor["id"])
    print("implement", implement["id"])
    print("simulation", sim["id"], "slip", sim.get("slip"), "status", sim.get("status_message"))


if __name__ == "__main__":
    main()

