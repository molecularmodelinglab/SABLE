"""Generate an objective-aware summary from structured workflow state."""

from typing import Any, Dict

from schemas.state import WorkflowState
from utils.objective_ranking import build_optimization_report


def summarize_results_node(state: WorkflowState) -> Dict[str, Any]:
    """Generate the final human-readable view of the optimization report."""
    state.log("summarize_results_started")
    report = build_optimization_report(state)
    state.summary = _render_summary(state, report)

    best = next((item for item in report["rankings"] if item["rank"] == 1), None)
    state.log("summarize_results_completed", {
        "summary_length": len(state.summary),
        "ranking_strategy": report["strategy"],
        "best_rank": best["rank"] if best else None,
        "best_score": best["aggregate_score"] if best else None,
        "best_pareto_front": best["pareto_front"] if best else None,
    })
    print("\n" + state.summary)
    return state


def _render_summary(state: WorkflowState, report: dict[str, Any]) -> str:
    status = state.status.value if hasattr(state.status, "value") else str(state.status)
    source = state.molecule_source.value if hasattr(state.molecule_source, "value") else state.molecule_source
    counts = report["counts"]
    summary = [
        "=== Molecular Optimization Summary ===",
        f"Workflow ID: {state.workflow_id}",
        f"Status: {status}",
        f"Exit Reason: {state.exit_reason or 'Completed normally'}",
        "",
        "Optimization Configuration:",
        f"- Ranking Strategy: {_strategy_label(report['strategy'])}",
        f"- Molecule Source: {source or 'N/A'}",
        f"- BO Rounds: {counts['bo_rounds']}",
        f"- Batch Size: {state.bo_config.batch_size if state.bo_config else 'N/A'}",
    ]

    if state.targets:
        summary.append("- Objectives:")
        for target in state.targets:
            mode = target.mode.value if hasattr(target.mode, "value") else str(target.mode)
            bounds = f", bounds={target.bounds}" if target.bounds else ""
            summary.append(f"  - {target.name}: {mode}, weight={float(target.weight or 0.0):.3f}{bounds}")
    else:
        summary.append("- Objectives: None")

    summary.extend([
        "",
        "Evaluation Statistics:",
        f"- Search Space: {counts['search_space']}",
        f"- Unique Candidates Evaluated: {counts['evaluated_candidates']}",
        f"- Starting-Molecule Baselines: {counts['baseline_molecules']}",
        f"- Coverage: {_coverage(counts['evaluated_candidates'], counts['search_space'])}",
    ])

    if report["baselines"]:
        summary.extend(["", "Starting-Molecule Baselines:"])
        for item in report["baselines"]:
            summary.append(f"- {item['smiles']}")
            summary.append(f"  Values: {_format_values(item['objective_values'])}")

    ranked = [item for item in report["rankings"] if item["rank"] is not None]
    if ranked:
        summary.extend(["", f"Top {min(5, len(ranked))} Optimized Molecules:"])
        for item in ranked[:5]:
            if report["score_kind"] == "pareto":
                ranking_text = (
                    f"Rank {item['rank']}, Pareto front {item['pareto_front']}, "
                    f"crowding distance {item['crowding_distance']:.4f}"
                )
            else:
                ranking_text = f"Rank {item['rank']}, desirability {item['aggregate_score']:.4f}"
            summary.append(f"- {ranking_text}")
            summary.append(f"  SMILES: {item['smiles']}")
            summary.append(f"  Values: {_format_values(item['objective_values'])}")

    incomplete = [item for item in report["rankings"] if item["rank"] is None]
    if incomplete:
        summary.extend(["", "Incomplete Candidates:"])
        for item in incomplete[:5]:
            summary.append(f"- {item['smiles']}: missing {', '.join(item['missing_objectives'])}")

    if report["progress"]:
        summary.extend(["", "Optimization Progress:"])
        for item in report["progress"]:
            summary.append(
                f"- Iteration {item['iteration']}: "
                f"{item['evaluated_candidates']} candidate(s) evaluated, "
                f"{item['recommendations']} recommended"
            )

    if report["warnings"]:
        summary.extend(["", "Warnings:"])
        summary.extend(f"- {warning}" for warning in report["warnings"])

    return "\n".join(summary)


def _strategy_label(strategy: str) -> str:
    return {
        "single": "Single-objective desirability",
        "desirability": "Weighted multi-objective desirability",
        "pareto": "Pareto fronts with crowding-distance tie-breaks",
    }.get(strategy, strategy)


def _format_values(values: dict[str, float]) -> str:
    return ", ".join(f"{name}: {value:.4f}" for name, value in values.items()) or "None recorded"


def _coverage(evaluated: int, search_space: int) -> str:
    return f"{evaluated / search_space * 100:.1f}%" if search_space else "0.0%"