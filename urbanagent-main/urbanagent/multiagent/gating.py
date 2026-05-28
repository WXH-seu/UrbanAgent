"""G1: decide whether the multi-agent pipeline should run (LLM or rules)."""
from __future__ import annotations

import json
from typing import Any

from urbanagent.llm.types import Message
from urbanagent.multiagent.llm_json import try_loads_json_object
from urbanagent.multiagent.schemas import GateDecision
from urbanagent.types import CityState


def _brief_state(state: CityState) -> dict[str, Any]:
    return {
        "incidents": [
            {
                "id": i.id,
                "kind": i.kind,
                "severity": i.severity,
                "status": i.status,
            }
            for i in state.incidents
        ],
        "timestamp": state.timestamp,
    }


def rule_gate(query: str, state: CityState) -> GateDecision:
    for inc in state.incidents:
        if inc.kind != "fire":
            continue
        if inc.status not in {"open", "responding"}:
            continue
        if inc.severity in {"high", "critical"}:
            return GateDecision(
                should_intervene=True,
                priority="high",
                reason="open high-severity fire incident in world state",
                trigger_kind="fire_severity",
            )
    if "火" in query or "fire" in query.lower():
        return GateDecision(
            should_intervene=True,
            priority="normal",
            reason="query mentions fire response",
            trigger_kind="query_keyword",
        )
    return GateDecision(
        should_intervene=False,
        priority="idle",
        reason="no gate rule matched",
        trigger_kind="none",
    )


async def llm_gate(query: str, state: CityState, llm) -> GateDecision:
    payload = {"query": query, "world_brief": _brief_state(state)}
    resp = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "你是 UrbanAgent 多智能体门控 G1。根据 query 与 world_brief，"
                    "只输出 JSON：{ \"should_intervene\": boolean, \"priority\": string, "
                    "\"reason\": string, \"trigger_kind\": string }。\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
            )
        ],
        temperature=0.0,
        max_tokens=400,
        system="Output only one JSON object. Be conservative: intervene for emergencies.",
    )
    data = try_loads_json_object(resp.content)
    return GateDecision(
        should_intervene=bool(data.get("should_intervene")),
        priority=str(data.get("priority", "normal")).strip() or "normal",
        reason=str(data.get("reason", "")).strip(),
        trigger_kind=str(data.get("trigger_kind", "")).strip(),
    )


async def run_gate(
    query: str,
    state: CityState,
    *,
    llm,
    use_llm: bool,
) -> GateDecision:
    if use_llm and llm is not None:
        try:
            return await llm_gate(query, state, llm)
        except Exception:
            return rule_gate(query, state)
    return rule_gate(query, state)
