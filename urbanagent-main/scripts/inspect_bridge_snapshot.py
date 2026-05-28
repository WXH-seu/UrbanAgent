"""Connect to CarlaBridge /agent and print the raw + translated snapshot.

Usage:
    python scripts/inspect_bridge_snapshot.py --url http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from urbanagent import CarlaBridgeSandboxClient
from urbanagent.carla_bridge import carla_snapshot_to_city_state


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--namespace", default="/agent")
    parser.add_argument(
        "--wait-snapshots",
        type=int,
        default=1,
        help="How many state_snapshot frames to wait for before reporting.",
    )
    parser.add_argument(
        "--snapshot-timeout",
        type=float,
        default=15.0,
        help="Per-snapshot wait timeout in seconds.",
    )
    args = parser.parse_args()

    captured: list[dict] = []

    client = CarlaBridgeSandboxClient(args.url, namespace=args.namespace)
    await client.connect()
    print(f"connected: bridge_session_id={client.bridge_session_id} run_id={client.run_id}")

    sio = client._sio
    assert sio is not None

    @sio.on("state_snapshot", namespace=args.namespace)
    async def _capture(data):
        env = data if isinstance(data, dict) and "payload" in data else {"payload": data}
        captured.append(env)

    deadline = time.monotonic() + args.snapshot_timeout * max(1, args.wait_snapshots)
    while len(captured) < args.wait_snapshots and time.monotonic() < deadline:
        await asyncio.sleep(0.1)

    if not captured:
        print("ERROR: no state_snapshot received within timeout")
        await client.close()
        return 1

    for i, env in enumerate(captured[: args.wait_snapshots]):
        payload = env.get("payload") or {}
        print(f"\n===== snapshot #{i} =====")
        print(f"frame={env.get('frame')} sim_time={env.get('sim_time')} ts={env.get('timestamp')}")
        print(f"bridge_session_id={payload.get('bridge_session_id')} run_id={payload.get('run_id')}")
        print(
            f"raw counts: vehicles={len(payload.get('vehicles') or [])} "
            f"uavs={len(payload.get('uavs') or [])} "
            f"traffic_lights={len(payload.get('traffic_lights') or [])} "
            f"incidents={len(payload.get('incidents') or [])} "
            f"in_flight_commands={len(payload.get('in_flight_commands') or [])}"
        )
        print("--- raw vehicles (up to 10) ---")
        print(json.dumps((payload.get("vehicles") or [])[:10], ensure_ascii=False, indent=2))
        print("--- raw uavs (up to 10) ---")
        print(json.dumps((payload.get("uavs") or [])[:10], ensure_ascii=False, indent=2))
        print("--- raw incidents ---")
        print(json.dumps(payload.get("incidents") or [], ensure_ascii=False, indent=2))

        translated = carla_snapshot_to_city_state(
            dict(payload),
            timestamp=str(env.get("timestamp", "")),
        )
        print("--- translated CityState.resources ---")
        for r in translated.resources:
            print(
                f"  id={r.id} kind={r.kind} status={r.status} "
                f"caps={r.capabilities} pos=({r.position.x:.1f},{r.position.y:.1f},{r.position.z:.1f})"
            )
        print(f"translated incidents={len(translated.incidents)} signals={len(translated.traffic_signals)}")

    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
