"""Meta agent: G2 cognition, G3 decomposition, G5 integration (+ optional LLM rerank), G8 report."""
from __future__ import annotations

import json
import re
from typing import Any, cast

from urbanagent.dispatch import DispatchPolicy
from urbanagent.fire_goto import apply_fire_goto_offset_to_actions
from urbanagent.llm.types import Message
from urbanagent.multiagent.llm_json import try_loads_json_object
from urbanagent.multiagent.schemas import CommittedBatch, SubAgentRole, SubGoal, SubPlan
from urbanagent.multiagent.subagents.default_agents import (
    integrate_actions_deterministic,
    new_batch_id,
)
from urbanagent.multiagent.toolkit import SubAgentToolkit
from urbanagent.schemas import UrbanConstraint, UrbanTask
from urbanagent.types import CityState, UrbanAction


def _extract_incident_id(query: str) -> str | None:
    m = re.search(r"incident-[a-zA-Z]+-\d+", query)
    return m.group(0) if m else None


def rule_cognition(query: str, state: CityState) -> UrbanTask:
    incident_id = _extract_incident_id(query)
    fire_incidents = [
        i
        for i in state.incidents
        if i.kind == "fire" and i.status in {"open", "responding"}
    ]
    if incident_id is None and fire_incidents:
        incident_id = fire_incidents[0].id
    incident = next((i for i in state.incidents if i.id == incident_id), None) if incident_id else None
    severity = incident.severity if incident is not None else "high"
    constraints = [
        UrbanConstraint(
            name="fire_suppression_required",
            kind="hard",
            expression="dispatch fire suppression resources",
        ),
        UrbanConstraint(
            name="aerial_recon_required",
            kind="hard",
            expression="dispatch aerial reconnaissance",
        ),
        UrbanConstraint(name="reserve_ratio", kind="hard", expression="respect station reserves"),
    ]
    if severity in {"high", "critical"}:
        constraints.append(
            UrbanConstraint(
                name="police_control_required",
                kind="hard",
                expression="police traffic control",
            )
        )
        constraints.append(
            UrbanConstraint(
                name="traffic_control_required",
                kind="hard",
                expression="traffic signal emergency mode",
            )
        )
    missing: list[str] = []
    if incident_id is None:
        missing.append("incident_id")
    elif incident is None:
        missing.append(f"known incident {incident_id}")
    return UrbanTask(
        intent="fire_emergency_dispatch",
        entities={
            "incident_id": incident_id,
            "severity": severity,
            "location": incident.position if incident else None,
        },
        constraints=constraints,
        missing_information=missing,
        rationale="rule cognition for multi-agent MVP",
        source="rule",
    )


async def llm_cognition(query: str, state: CityState, llm) -> UrbanTask:
    brief = {
        "incidents": [
            {
                "id": i.id,
                "kind": i.kind,
                "severity": i.severity,
                "status": i.status,
            }
            for i in state.incidents
        ],
        "resources": [
            {"id": r.id, "kind": r.kind, "status": r.status, "capabilities": r.capabilities}
            for r in state.resources
        ],
    }
    resp = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "UrbanAgent 元认知 G2。用户请求与状态摘要如下。只输出 JSON："
                    "intent, entities(object 含 incident_id), constraints(array of {name, kind, expression}), "
                    "missing_information(array), rationale。\n"
                    "missing_information 只在『无法从请求或状态中确定 incident_id』时才填写；"
                    "若 incident_id 已知，请返回空数组——下游会根据资源是否存在自行降级，不需要在此阻塞。\n"
                    f"请求:\n{query}\n\n状态:\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
                ),
            )
        ],
        temperature=0.1,
        max_tokens=2048,
        system=(
            "Return strict JSON only. Do not invent incident_id not in state. "
            "missing_information must be empty when incident_id is resolvable; "
            "it is not a wish list for extra context."
        ),
    )
    data = try_loads_json_object(resp.content)
    constraints = [
        UrbanConstraint(
            name=str(c.get("name", "c")).strip() or "constraint",
            kind="soft" if str(c.get("kind", "")).strip() == "soft" else "hard",
            expression=str(c.get("expression", "")).strip(),
        )
        for c in data.get("constraints", [])
        if isinstance(c, dict)
    ]
    if not constraints:
        raise ValueError("LLM cognition returned no constraints")
    entities = dict(data.get("entities", {}))
    incident_id_raw = entities.get("incident_id")
    incident_id = str(incident_id_raw).strip() if incident_id_raw else ""
    if not incident_id:
        incident_id = _extract_incident_id(query) or ""
        if incident_id:
            entities["incident_id"] = incident_id
    known_incident = (
        next((i for i in state.incidents if i.id == incident_id), None)
        if incident_id
        else None
    )
    missing: list[str] = []
    if not incident_id:
        missing.append("incident_id")
    elif known_incident is None:
        missing.append(f"known incident {incident_id}")
    return UrbanTask(
        intent=str(data.get("intent", "fire_emergency_dispatch")).strip(),
        entities=entities,
        constraints=constraints,
        missing_information=missing,
        rationale=str(data.get("rationale", "")).strip(),
        source="llm",
    )


def rule_decompose(task: UrbanTask, state: CityState, toolkit: SubAgentToolkit) -> dict[str, SubGoal]:
    if task.missing_information:
        return {}
    incident_id = str(task.entities.get("incident_id", ""))
    incident = toolkit.find_incident(state, incident_id)
    if incident is None:
        return {}
    goals: dict[str, SubGoal] = {}
    sig = toolkit.nearest_signal(state, incident.position)
    goals["traffic_signal"] = SubGoal(
        role="traffic_signal",
        incident_id=incident_id,
        narrative="Switch nearest corridor signal for emergency preemption.",
        allowed_resource_ids=[],
        allowed_signal_ids=[sig.id] if sig else [],
        hard_hints=["emergency_preemption"],
    )
    if incident.severity in {"high", "critical"}:
        p = toolkit.first_available(
            state,
            kind="police_car",
            capabilities=["traffic_control"],
        )
        goals["police_car"] = SubGoal(
            role="police_car",
            incident_id=incident_id,
            narrative="Police traffic control at incident perimeter.",
            allowed_resource_ids=[p.id] if p else [],
            allowed_signal_ids=[],
            hard_hints=["police_control"],
        )
    u = toolkit.first_available(state, kind="unmanned_vehicle")
    goals["unmanned_vehicle"] = SubGoal(
        role="unmanned_vehicle",
        incident_id=incident_id,
        narrative="Unmanned ground logistics / perimeter support.",
        allowed_resource_ids=[u.id] if u else [],
        allowed_signal_ids=[],
        hard_hints=["logistics_support"],
    )
    d = toolkit.first_available(state, kind="drone", capabilities=["aerial_recon"])
    goals["drone"] = SubGoal(
        role="drone",
        incident_id=incident_id,
        narrative="Aerial reconnaissance at incident.",
        allowed_resource_ids=[d.id] if d else [],
        allowed_signal_ids=[],
        hard_hints=["aerial_recon"],
    )
    return goals


async def llm_decompose(task: UrbanTask, state: CityState, llm, toolkit: SubAgentToolkit) -> dict[str, SubGoal]:
    payload = {
        "task": {
            "intent": task.intent,
            "entities": task.entities,
            "constraints": [
                {"name": c.name, "kind": c.kind, "expression": c.expression}
                for c in task.constraints
            ],
        },
        "world": {
            "incidents": [
                {
                    "id": i.id,
                    "kind": i.kind,
                    "severity": i.severity,
                    "status": i.status,
                }
                for i in state.incidents
            ],
            "resources": [
                {"id": r.id, "kind": r.kind, "capabilities": r.capabilities, "status": r.status}
                for r in state.resources
            ],
            "signals": [s.id for s in state.traffic_signals],
        },
    }
    resp = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "UrbanAgent 元分解 G3。输出仅一个 JSON 对象，键为 "
                    "traffic_signal, police_car, unmanned_vehicle, drone（可省略某键）。"
                    "每个值为 { incident_id, narrative, allowed_resource_ids, allowed_signal_ids, hard_hints }。\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            )
        ],
        temperature=0.1,
        max_tokens=2500,
        system="Strict JSON. allowed_resource_ids must exist in world.resources with matching kind.",
    )
    data = try_loads_json_object(resp.content)
    out: dict[str, SubGoal] = {}
    for key, spec in data.items():
        if key not in {"traffic_signal", "police_car", "unmanned_vehicle", "drone"}:
            continue
        if not isinstance(spec, dict):
            continue
        rid = str(spec.get("incident_id", task.entities.get("incident_id", "")))
        out[key] = SubGoal(
            role=cast(SubAgentRole, key),
            incident_id=rid,
            narrative=str(spec.get("narrative", "")).strip(),
            allowed_resource_ids=[str(x) for x in spec.get("allowed_resource_ids", []) if str(x).strip()],
            allowed_signal_ids=[str(x) for x in spec.get("allowed_signal_ids", []) if str(x).strip()],
            hard_hints=[str(x) for x in spec.get("hard_hints", []) if str(x).strip()],
        )
    if not out:
        return rule_decompose(task, state, toolkit)
    return out


async def llm_rerank_actions(
    actions: list[UrbanAction],
    task: UrbanTask,
    llm,
) -> list[UrbanAction]:
    rows = [{"index": i, "summary": f"{a.kind}:{a.target_id}"} for i, a in enumerate(actions)]
    resp = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "你是 G5 批次排序模块。给定 actions 列表，只输出 JSON："
                    f"ranked_indices 为 0..{len(actions) - 1} 的一个排列（越靠前越先执行）。\n"
                    f"task={json.dumps({'intent': task.intent, 'entities': task.entities}, ensure_ascii=False)}\n"
                    f"candidates={json.dumps(rows, ensure_ascii=False)}"
                ),
            )
        ],
        temperature=0.0,
        max_tokens=1024,
        system="Output only strict JSON with ranked_indices as a permutation.",
    )
    data = try_loads_json_object(resp.content)
    raw = data.get("ranked_indices")
    if not isinstance(raw, list):
        return actions
    n = len(actions)
    seen: set[int] = set()
    out: list[UrbanAction] = []
    for x in raw:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in seen:
            out.append(actions[i])
            seen.add(i)
    for i, a in enumerate(actions):
        if i not in seen:
            out.append(a)
    return out if len(out) == n else actions


def _append_mark_incident(actions: list[UrbanAction], incident_id: str) -> None:
    actions.append(
        UrbanAction(
            kind="mark_incident",
            target_id=incident_id,
            parameters={"status": "responding"},
            reason="Multi-agent batch completed initial dispatch.",
        )
    )


async def build_committed_batch(
    sub_plans: list[SubPlan],
    *,
    state: CityState,
    task: UrbanTask,
    dispatch_policy: DispatchPolicy,
    llm,
    use_llm: bool,
    use_llm_batch_rerank: bool,
) -> CommittedBatch:
    actions, rationale = integrate_actions_deterministic(
        sub_plans, state=state, dispatch_policy=dispatch_policy
    )
    incident_id = str(task.entities.get("incident_id", ""))
    if use_llm and use_llm_batch_rerank and llm is not None and len(actions) > 1:
        try:
            actions = await llm_rerank_actions(actions, task, llm)
        except Exception:
            pass
    before_supported_filter = len(actions)
    actions = [
        action
        for action in actions
        if action.kind in {"dispatch_vehicle", "dispatch_drone"}
    ]
    if len(actions) != before_supported_filter:
        rationale = f"{rationale}; filtered unsupported CarlaBridge v1.0 actions"
    if not any(a.kind in {"dispatch_vehicle", "dispatch_drone"} for a in actions):
        rationale = f"{rationale}; no dispatchable mobile resources"
        actions = []
    else:
        actions = apply_fire_goto_offset_to_actions(actions)
    return CommittedBatch(batch_id=new_batch_id(), actions=actions, rationale=rationale)


async def llm_final_report(
    query: str,
    task: UrbanTask,
    sub_plans: list[SubPlan],
    batch: CommittedBatch | None,
    outcome,
    llm,
) -> str:
    facts = {
        "query": query,
        "task": {
            "intent": task.intent,
            "entities": task.entities,
            "constraints": [c.__dict__ for c in task.constraints],
        },
        "sub_plans": [
            {
                "role": sp.role,
                "status": sp.status,
                "rationale": sp.rationale,
                "drafts": len(sp.action_drafts),
            }
            for sp in sub_plans
        ],
        "batch": (
            {
                "batch_id": batch.batch_id,
                "n_actions": len(batch.actions),
            }
            if batch
            else None
        ),
        "outcome": (
            {
                "criteria_satisfied": outcome.criteria_satisfied,
                "polling_iterations": outcome.polling_iterations,
                "notes": outcome.notes,
            }
            if outcome
            else None
        ),
    }
    resp = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "你是 UrbanAgent 多智能体 G8 报告模块。根据 facts 写中文应急调度摘要，"
                    "勿编造未出现的 id 或结果。\n"
                    f"{json.dumps(facts, ensure_ascii=False, indent=2)}"
                ),
            )
        ],
        temperature=0.2,
        max_tokens=2500,
        system="Operational Chinese report grounded in JSON facts.",
    )
    return resp.content.strip()


def deterministic_final_report(
    query: str,
    task: UrbanTask,
    sub_plans: list[SubPlan],
    batch: CommittedBatch | None,
    outcome,
) -> str:
    lines = [
        "UrbanAgent 多智能体 MVP 结果",
        f"Query: {query}",
        f"Intent: {task.intent} source={task.source}",
        "",
        "子智能体:",
    ]
    for sp in sub_plans:
        lines.append(f"- {sp.role}: {sp.status} — {sp.rationale[:200]}")
    if batch:
        lines.append("")
        lines.append(f"批次 {batch.batch_id} 共 {len(batch.actions)} 条动作")
    if outcome:
        lines.append(
            f"整批轮询: satisfied={outcome.criteria_satisfied} "
            f"polls={outcome.polling_iterations}"
        )
        for n in outcome.notes:
            lines.append(f"  note: {n}")
    return "\n".join(lines)
