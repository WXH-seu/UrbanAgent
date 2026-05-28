"""Names reserved for sandbox / environment operations (paper: W), not external T."""

from __future__ import annotations

# Must stay in sync with `_builtin_env_operation_metadata` in `urbanagent.agent`.
BUILTIN_ENV_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ask_user",
        "get_city_state",
        "create_dispatch_plan",
        "apply_dispatch_plan",
        "control_nearest_traffic_signal",
        "mark_incident",
    }
)
