"""Routing estimators for UrbanAgent dispatch algorithms."""
from __future__ import annotations

import heapq
import math
from abc import ABC, abstractmethod
from collections import defaultdict

from urbanagent.types import CityState, Coordinate, ResourceKind, RouteEstimate


class RoutePlanner(ABC):
    """Estimate travel distance and time for a resource."""

    @abstractmethod
    def estimate(
        self,
        state: CityState,
        start: Coordinate,
        end: Coordinate,
        resource_kind: ResourceKind,
        resource_speed: float,
    ) -> RouteEstimate:
        """Return the best available route estimate."""


class StraightLineRoutePlanner(RoutePlanner):
    """Fallback planner based on Euclidean distance and resource speed."""

    def estimate(
        self,
        state: CityState,
        start: Coordinate,
        end: Coordinate,
        resource_kind: ResourceKind,
        resource_speed: float,
    ) -> RouteEstimate:
        distance = euclidean_distance(start, end)
        speed = max(resource_speed, 0.001)
        return RouteEstimate(
            distance=distance,
            travel_time=distance / speed,
            path=[],
            congestion=0.0,
            source="straight_line",
        )


class LocalGraphRoutePlanner(RoutePlanner):
    """Dijkstra planner over `CityState.roads`, with straight-line fallback."""

    def __init__(self, fallback: RoutePlanner | None = None) -> None:
        self._fallback = fallback or StraightLineRoutePlanner()

    def estimate(
        self,
        state: CityState,
        start: Coordinate,
        end: Coordinate,
        resource_kind: ResourceKind,
        resource_speed: float,
    ) -> RouteEstimate:
        graph, positions = self._build_graph(state, resource_kind, resource_speed)
        if not graph or not positions:
            return self._fallback.estimate(state, start, end, resource_kind, resource_speed)

        start_node = _nearest_node(start, positions)
        end_node = _nearest_node(end, positions)
        if start_node is None or end_node is None:
            return self._fallback.estimate(state, start, end, resource_kind, resource_speed)

        search = self._dijkstra(graph, start_node, end_node)
        if search is None:
            return self._fallback.estimate(state, start, end, resource_kind, resource_speed)

        road_time, road_distance, congestion_sum, path = search
        access_distance = euclidean_distance(start, positions[start_node])
        egress_distance = euclidean_distance(positions[end_node], end)
        speed = max(resource_speed, 0.001)
        total_distance = access_distance + road_distance + egress_distance
        total_time = road_time + (access_distance + egress_distance) / speed
        average_congestion = congestion_sum / max(len(path) - 1, 1)
        return RouteEstimate(
            distance=total_distance,
            travel_time=total_time,
            path=path,
            congestion=average_congestion,
            source="local_graph",
        )

    def _build_graph(
        self,
        state: CityState,
        resource_kind: ResourceKind,
        resource_speed: float,
    ) -> tuple[dict[str, list[tuple[str, float, float, float]]], dict[str, Coordinate]]:
        graph: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
        positions: dict[str, Coordinate] = {}
        for road in state.roads:
            if road.blocked:
                continue
            if road.allowed_resource_kinds and resource_kind not in road.allowed_resource_kinds:
                continue
            if road.from_position is None or road.to_position is None:
                continue
            positions[road.from_node] = road.from_position
            positions[road.to_node] = road.to_position
            distance = road.length
            if distance is None:
                distance = euclidean_distance(road.from_position, road.to_position)
            speed = max(min(resource_speed, road.speed_limit), 0.001)
            congestion_factor = 1.0 + max(road.congestion, 0.0)
            travel_time = distance / speed * congestion_factor
            edge = (road.to_node, travel_time, distance, road.congestion)
            reverse = (road.from_node, travel_time, distance, road.congestion)
            graph[road.from_node].append(edge)
            graph[road.to_node].append(reverse)
        return dict(graph), positions

    def _dijkstra(
        self,
        graph: dict[str, list[tuple[str, float, float, float]]],
        start_node: str,
        end_node: str,
    ) -> tuple[float, float, float, list[str]] | None:
        queue: list[tuple[float, str, float, float, list[str]]] = [
            (0.0, start_node, 0.0, 0.0, [start_node])
        ]
        best: dict[str, float] = {start_node: 0.0}
        while queue:
            time_so_far, node, distance_so_far, congestion_so_far, path = heapq.heappop(queue)
            if node == end_node:
                return time_so_far, distance_so_far, congestion_so_far, path
            if time_so_far > best.get(node, math.inf):
                continue
            for next_node, edge_time, edge_distance, edge_congestion in graph.get(node, []):
                candidate_time = time_so_far + edge_time
                if candidate_time >= best.get(next_node, math.inf):
                    continue
                best[next_node] = candidate_time
                heapq.heappush(
                    queue,
                    (
                        candidate_time,
                        next_node,
                        distance_so_far + edge_distance,
                        congestion_so_far + edge_congestion,
                        [*path, next_node],
                    ),
                )
        return None


class ExternalMapRoutePlanner(RoutePlanner):
    """Placeholder for future real-map routing integration."""

    def estimate(
        self,
        state: CityState,
        start: Coordinate,
        end: Coordinate,
        resource_kind: ResourceKind,
        resource_speed: float,
    ) -> RouteEstimate:
        raise NotImplementedError(
            "ExternalMapRoutePlanner needs a concrete map API client and coordinate system."
        )


def euclidean_distance(left: Coordinate, right: Coordinate) -> float:
    return math.sqrt(
        (left.x - right.x) ** 2
        + (left.y - right.y) ** 2
        + (left.z - right.z) ** 2
    )


def _nearest_node(
    point: Coordinate,
    positions: dict[str, Coordinate],
) -> str | None:
    if not positions:
        return None
    return min(positions, key=lambda node: euclidean_distance(point, positions[node]))
