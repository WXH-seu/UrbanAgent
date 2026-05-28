"""G0: latest world snapshot from 3D push (ingest); extend for WebSocket driver."""
from __future__ import annotations

from dataclasses import dataclass, field

from urbanagent.types import CityState


@dataclass
class WorldStateCache:
    """Thread/async-neutral cache for last known ``CityState`` (e.g. ~1Hz 3D push)."""

    _snapshot: CityState | None = None
    _seq: int = 0
    _last_notes: list[str] = field(default_factory=list)

    def ingest(self, state: CityState, *, note: str = "") -> None:
        self._snapshot = state
        self._seq += 1
        if note:
            self._last_notes.append(note)

    def snapshot(self) -> CityState | None:
        return self._snapshot

    @property
    def seq(self) -> int:
        return self._seq
