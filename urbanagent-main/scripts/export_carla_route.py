from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--town", default="Town10HD")
    parser.add_argument("--sampling-resolution", type=float, default=2.0)

    parser.add_argument("--start-x", type=float, required=True)
    parser.add_argument("--start-y", type=float, required=True)
    parser.add_argument("--start-z", type=float, default=0.0)

    parser.add_argument("--end-x", type=float, required=True)
    parser.add_argument("--end-y", type=float, required=True)
    parser.add_argument("--end-z", type=float, default=0.0)

    parser.add_argument("--out", default="route_waypoints.json")

    args = parser.parse_args()

    import carla
    from agents.navigation.global_route_planner import GlobalRoutePlanner

    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)

    world = client.load_world(args.town)
    carla_map = world.get_map()

    grp = GlobalRoutePlanner(
        carla_map,
        sampling_resolution=args.sampling_resolution,
    )

    start = carla.Location(
        x=args.start_x,
        y=args.start_y,
        z=args.start_z,
    )
    end = carla.Location(
        x=args.end_x,
        y=args.end_y,
        z=args.end_z,
    )

    trace = grp.trace_route(start, end)

    route_waypoints = [
        {
            "x": float(wp.transform.location.x),
            "y": float(wp.transform.location.y),
            "z": float(wp.transform.location.z),
        }
        for wp, _ in trace
    ]

    payload = {
        "town": args.town,
        "sampling_resolution": args.sampling_resolution,
        "start": {"x": args.start_x, "y": args.start_y, "z": args.start_z},
        "end": {"x": args.end_x, "y": args.end_y, "z": args.end_z},
        "route_waypoints": route_waypoints,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"exported {len(route_waypoints)} waypoints -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())