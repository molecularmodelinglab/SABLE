"""Objective-aware ranking and reporting derived from workflow state."""

from __future__ import annotations

import math
import os
from collections import defaultdict
from typing import Any, Iterable, Sequence


_BASELINE_KEY = "is_starting_molecule_baseline"


def resolve_ranking_strategy(state: Any) -> str:
    """Return the reporting strategy used by the optimizer for this state."""
    if len(state.targets) <= 1:
        return "single"

    configured = None
    if isinstance(state.stage_config, dict):
        configured = state.stage_config.get("bo_iteration", {}).get("multi_objective_strategy")
    configured = configured or state.parsed_arguments.get("multi_objective_strategy")
    strategy = str(configured or os.getenv("MULTI_OPT_TYPE", "pareto")).strip().lower()
    return strategy if strategy in {"pareto", "desirability"} else "pareto"


def rank_experimental_results(
    targets: Sequence[Any],
    results: Iterable[Any],
    strategy: str,
) -> dict[str, Any]:
    """Rank candidate observations and retain baselines as a separate comparison set."""
    observations = list(results)
    candidate_results = _latest_by_smiles(
        result for result in observations if not result.metadata.get(_BASELINE_KEY, False)
    )
    baseline_results = _latest_by_smiles(
        result for result in observations if result.metadata.get(_BASELINE_KEY, False)
    )
    ranges = _objective_ranges(targets, candidate_results)
    candidates = [_rankable_record(result, targets, ranges) for result in candidate_results]
    baselines = [_rankable_record(result, targets, ranges) for result in baseline_results]

    warnings: list[str] = []
    incomplete_count = sum(not item["complete"] for item in candidates)
    if incomplete_count:
        warnings.append(
            f"{incomplete_count} candidate(s) were excluded from ranking because required objective values are missing."
        )

    complete = [item for item in candidates if item["complete"]]
    incomplete = [item for item in candidates if not item["complete"]]
    normalized_strategy = strategy if strategy in {"single", "pareto", "desirability"} else "pareto"
    if normalized_strategy == "pareto" and len(targets) > 1:
        _apply_pareto_ranking(complete, targets)
        complete.sort(key=lambda item: (item["pareto_front"], -item["crowding_distance"], item["molecule_id"]))
        score_kind = "pareto"
    else:
        _apply_scalar_ranking(complete, targets)
        complete.sort(key=lambda item: (-item["aggregate_score"], item["molecule_id"]))
        score_kind = "desirability"

    ranked = [*complete, *incomplete]
    for index, item in enumerate(complete, 1):
        item["rank"] = index

    return {
        "strategy": normalized_strategy,
        "score_kind": score_kind,
        "rankings": ranked,
        "baselines": baselines,
        "warnings": warnings,
    }


def build_optimization_report(state: Any) -> dict[str, Any]:
    """Build the versioned source document used by summaries and exports."""
    strategy = resolve_ranking_strategy(state)
    ranking = rank_experimental_results(state.targets, state.experimental_results, strategy)
    candidate_smiles = {
        result.smiles
        for result in state.experimental_results
        if not result.metadata.get(_BASELINE_KEY, False)
    }
    iterations = sorted({result.iteration for result in state.experimental_results})

    return {
        "schema_version": 1,
        "workflow_id": state.workflow_id,
        "status": _enum_value(state.status),
        "exit_reason": state.exit_reason,
        "strategy": ranking["strategy"],
        "score_kind": ranking["score_kind"],
        "objectives": [target.model_dump(mode="json") for target in state.targets],
        "counts": {
            "search_space": len(state.search_space),
            "evaluated_candidates": len(candidate_smiles),
            "baseline_molecules": len(ranking["baselines"]),
            "bo_rounds": len(state.bo_rounds),
        },
        "rankings": ranking["rankings"],
        "baselines": ranking["baselines"],
        "progress": [
            {
                "iteration": iteration,
                "evaluated_candidates": len({
                    result.smiles
                    for result in state.experimental_results
                    if result.iteration == iteration and not result.metadata.get(_BASELINE_KEY, False)
                }),
                "recommendations": len(next(
                    (
                        round_data.get("recommendations", [])
                        for round_data in state.bo_rounds
                        if round_data.get("iteration") == iteration
                    ),
                    [],
                )),
            }
            for iteration in iterations
        ],
        "warnings": ranking["warnings"],
    }


def compatibility_best_molecules(report: dict[str, Any], limit: int = 10) -> list[tuple[str, float]]:
    """Project structured rankings onto the legacy ``(smiles, score)`` shape."""
    projected: list[tuple[str, float]] = []
    for item in report["rankings"]:
        if item["rank"] is None:
            continue
        value = item["aggregate_score"]
        if value is None:
            value = 1.0 / float(item["pareto_front"] + 1)
        projected.append((item["smiles"], float(value)))
        if len(projected) == limit:
            break
    return projected


def _latest_by_smiles(results: Iterable[Any]) -> list[Any]:
    latest: dict[str, Any] = {}
    for result in results:
        previous = latest.get(result.smiles)
        if previous is None or (result.iteration, result.timestamp) >= (previous.iteration, previous.timestamp):
            latest[result.smiles] = result
    return list(latest.values())


def _objective_ranges(targets: Sequence[Any], results: Sequence[Any]) -> dict[str, tuple[float, float] | None]:
    ranges: dict[str, tuple[float, float] | None] = {}
    for target in targets:
        if target.bounds and target.bounds[1] > target.bounds[0]:
            ranges[target.name] = (float(target.bounds[0]), float(target.bounds[1]))
            continue
        values = [float(result.properties[target.name]) for result in results if target.name in result.properties]
        ranges[target.name] = (min(values), max(values)) if values else None
    return ranges


def _rankable_record(result: Any, targets: Sequence[Any], ranges: dict[str, tuple[float, float] | None]) -> dict[str, Any]:
    values: dict[str, float] = {}
    utilities: dict[str, float] = {}
    missing: list[str] = []
    for target in targets:
        if target.name not in result.properties:
            missing.append(target.name)
            continue
        value = float(result.properties[target.name])
        values[target.name] = value
        utilities[target.name] = _utility(target, value, ranges[target.name])

    return {
        "molecule_id": result.molecule_id,
        "smiles": result.smiles,
        "iteration": result.iteration,
        "objective_values": values,
        "objective_utilities": utilities,
        "missing_objectives": missing,
        "complete": not missing and bool(targets),
        "rank": None,
        "aggregate_score": None,
        "pareto_front": None,
        "crowding_distance": None,
    }


def _utility(target: Any, value: float, value_range: tuple[float, float] | None) -> float:
    mode = _enum_value(target.mode).upper()
    if value_range is None or value_range[0] == value_range[1]:
        return 1.0
    lower, upper = value_range
    if mode == "MIN":
        return _clamp((upper - value) / (upper - lower))
    if mode == "MATCH":
        midpoint = (lower + upper) / 2.0
        half_width = (upper - lower) / 2.0
        return _clamp(1.0 - abs(value - midpoint) / half_width)
    return _clamp((value - lower) / (upper - lower))


def _apply_scalar_ranking(items: list[dict[str, Any]], targets: Sequence[Any]) -> None:
    weights = [float(target.weight or 0.0) for target in targets]
    weight_total = sum(weights)
    normalized_weights = [weight / weight_total for weight in weights] if weight_total else [1.0 / len(targets)] * len(targets)
    epsilon = 1e-12
    for item in items:
        utilities = [item["objective_utilities"][target.name] for target in targets]
        item["aggregate_score"] = math.exp(sum(
            weight * math.log(max(utility, epsilon))
            for weight, utility in zip(normalized_weights, utilities)
        ))


def _apply_pareto_ranking(items: list[dict[str, Any]], targets: Sequence[Any]) -> None:
    remaining = list(items)
    front_number = 1
    while remaining:
        front = [
            candidate for candidate in remaining
            if not any(_dominates(other, candidate, targets) for other in remaining if other is not candidate)
        ]
        _assign_crowding_distance(front, targets)
        for candidate in front:
            candidate["pareto_front"] = front_number
        remaining = [candidate for candidate in remaining if candidate not in front]
        front_number += 1


def _dominates(left: dict[str, Any], right: dict[str, Any], targets: Sequence[Any]) -> bool:
    left_values = [_pareto_value(left, target) for target in targets]
    right_values = [_pareto_value(right, target) for target in targets]
    return all(a >= b for a, b in zip(left_values, right_values)) and any(
        a > b for a, b in zip(left_values, right_values)
    )


def _assign_crowding_distance(front: list[dict[str, Any]], targets: Sequence[Any]) -> None:
    for item in front:
        item["crowding_distance"] = 0.0
    if len(front) <= 2:
        for item in front:
            item["crowding_distance"] = float(len(targets) + 1)
        return

    for target in targets:
        ordered = sorted(front, key=lambda item: _pareto_value(item, target))
        boundary_distance = float(len(targets) + 1)
        ordered[0]["crowding_distance"] = boundary_distance
        ordered[-1]["crowding_distance"] = boundary_distance
        minimum = _pareto_value(ordered[0], target)
        maximum = _pareto_value(ordered[-1], target)
        if maximum == minimum:
            continue
        for index in range(1, len(ordered) - 1):
            if ordered[index]["crowding_distance"] >= boundary_distance:
                continue
            previous_value = _pareto_value(ordered[index - 1], target)
            next_value = _pareto_value(ordered[index + 1], target)
            ordered[index]["crowding_distance"] += (next_value - previous_value) / (maximum - minimum)


def _pareto_value(item: dict[str, Any], target: Any) -> float:
    mode = _enum_value(target.mode).upper()
    if mode == "MATCH":
        return item["objective_utilities"][target.name]
    value = item["objective_values"][target.name]
    return -value if mode == "MIN" else value


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)