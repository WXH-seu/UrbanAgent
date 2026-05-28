"""Structured task objects shared by the multi-agent orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ConstraintKind = Literal["hard", "soft"]


@dataclass
class UrbanConstraint:
    """A computable user or domain constraint."""

    name: str
    kind: ConstraintKind
    expression: str
    satisfied: bool | None = None


@dataclass
class UrbanTask:
    """Cognition output U = <I, E, C> from the paper."""

    intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    constraints: list[UrbanConstraint] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    rationale: str = ""
    source: str = "rule"
