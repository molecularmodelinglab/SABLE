import json

import pytest

from nodes.check_exit_conditions import check_exit_conditions_node
from nodes.summarize_results import summarize_results_node
from schemas.state import BOConfiguration, ExperimentResult, TargetProperty, WorkflowState, WorkflowStatus
from utils.objective_ranking import build_optimization_report, rank_experimental_results


def _result(molecule_id, values, *, smiles=None, iteration=0, baseline=False):
    return ExperimentResult(
        molecule_id=molecule_id,
        smiles=smiles or molecule_id,
        iteration=iteration,
        properties=values,
        metadata={"is_starting_molecule_baseline": baseline},
    )


@pytest.mark.parametrize(
    ("mode", "values", "expected"),
    [
        ("MAX", [("low", 1.0), ("high", 9.0)], ["high", "low"]),
        ("MIN", [("high", 9.0), ("low", 1.0)], ["low", "high"]),
        ("MATCH", [("edge", 0.0), ("match", 5.0), ("far", 9.0)], ["match", "far", "edge"]),
    ],
)
def test_single_objective_ranking_respects_mode(mode, values, expected):
    target = TargetProperty(name="value", mode=mode, bounds=(0.0, 10.0))
    report = rank_experimental_results(
        [target],
        [_result(name, {"value": value}) for name, value in values],
        "single",
    )

    assert [item["molecule_id"] for item in report["rankings"]] == expected


def test_desirability_normalizes_scales_before_applying_weights():
    targets = [
        TargetProperty(name="qed", mode="MAX", weight=0.5, bounds=(0.0, 1.0)),
        TargetProperty(name="mass", mode="MIN", weight=0.5, bounds=(100.0, 500.0)),
    ]
    report = rank_experimental_results(
        targets,
        [
            _result("balanced", {"qed": 0.8, "mass": 180.0}),
            _result("heavy", {"qed": 0.9, "mass": 490.0}),
        ],
        "desirability",
    )

    assert report["rankings"][0]["molecule_id"] == "balanced"
    assert 0.0 <= report["rankings"][0]["aggregate_score"] <= 1.0


def test_pareto_ranking_preserves_tradeoffs_and_demotes_dominated_candidate():
    targets = [
        TargetProperty(name="activity", mode="MAX", bounds=(0.0, 10.0)),
        TargetProperty(name="toxicity", mode="MIN", bounds=(0.0, 10.0)),
    ]
    report = rank_experimental_results(
        targets,
        [
            _result("active", {"activity": 9.0, "toxicity": 8.0}),
            _result("safe", {"activity": 7.0, "toxicity": 2.0}),
            _result("dominated", {"activity": 6.0, "toxicity": 9.0}),
        ],
        "pareto",
    )
    by_id = {item["molecule_id"]: item for item in report["rankings"]}

    assert by_id["active"]["pareto_front"] == 1
    assert by_id["safe"]["pareto_front"] == 1
    assert by_id["dominated"]["pareto_front"] == 2
    json.dumps(report, allow_nan=False)


def test_ranking_excludes_baselines_missing_values_and_older_duplicates():
    targets = [
        TargetProperty(name="x", mode="MAX", bounds=(0.0, 10.0)),
        TargetProperty(name="y", mode="MAX", bounds=(0.0, 10.0)),
    ]
    report = rank_experimental_results(
        targets,
        [
            _result("old", {"x": 1.0, "y": 1.0}, smiles="same", iteration=0),
            _result("new", {"x": 8.0, "y": 8.0}, smiles="same", iteration=1),
            _result("incomplete", {"x": 10.0}),
            _result("baseline", {"x": 10.0, "y": 10.0}, baseline=True),
        ],
        "pareto",
    )

    assert [item["molecule_id"] for item in report["rankings"]] == ["new", "incomplete"]
    assert report["rankings"][1]["rank"] is None
    assert report["rankings"][1]["missing_objectives"] == ["y"]
    assert [item["molecule_id"] for item in report["baselines"]] == ["baseline"]
    assert report["warnings"]


def test_summary_uses_structured_state_results_and_reports_strategy():
    state = WorkflowState(
        user_prompt="optimize",
        targets=[TargetProperty(name="qed", mode="MAX", bounds=(0.0, 1.0))],
        starting_molecules=["BASE"],
        search_space={"candidate": "CANDIDATE"},
    )
    state.add_experimental_result(_result("candidate", {"qed": 0.8}, smiles="CANDIDATE"))
    state.add_experimental_result(_result("baseline", {"qed": 0.4}, smiles="BASE", baseline=True))

    summarize_results_node(state)
    report = build_optimization_report(state)

    assert "Single-objective desirability" in state.summary
    assert "Starting-Molecule Baselines: 1" in state.summary
    assert "qed: 0.4000" in state.summary
    assert report["rankings"][0]["smiles"] == "CANDIDATE"
    assert state.best_molecules[0][0] == "CANDIDATE"


@pytest.mark.parametrize(
    ("mode", "values"),
    [
        ("MAX", [1.0, 1.01, 1.015]),
        ("MIN", [9.0, 8.99, 8.985]),
        ("MATCH", [4.0, 4.05, 4.075]),
    ],
)
def test_scalar_convergence_uses_three_temporal_best_snapshots(mode, values):
    state = WorkflowState(
        user_prompt="optimize",
        targets=[TargetProperty(name="value", mode=mode, bounds=(0.0, 10.0))],
        search_space={str(index): f"S{index}" for index in range(10)},
        bo_config=BOConfiguration(batch_size=1, max_iterations=10, convergence_threshold=0.02),
        max_iterations=10,
        current_bo_recommendations=["next"],
    )

    for index, value in enumerate(values):
        state.add_experimental_result(_result(str(index), {"value": value}, smiles=f"S{index}", iteration=index))
        check_exit_conditions_node(state)

    assert state.status == WorkflowStatus.COMPLETED
    assert state.exit_reason.startswith("Convergence detected")
    assert len(state.profiling["ranking_history"]) == 3


def test_pareto_strategy_does_not_apply_scalar_convergence(monkeypatch):
    monkeypatch.setenv("MULTI_OPT_TYPE", "pareto")
    state = WorkflowState(
        user_prompt="optimize",
        targets=[
            TargetProperty(name="x", mode="MAX", bounds=(0.0, 10.0)),
            TargetProperty(name="y", mode="MAX", bounds=(0.0, 10.0)),
        ],
        search_space={str(index): f"S{index}" for index in range(10)},
        bo_config=BOConfiguration(batch_size=1, max_iterations=10, convergence_threshold=1.0),
        max_iterations=10,
        current_bo_recommendations=["next"],
    )

    for index in range(3):
        state.add_experimental_result(
            _result(str(index), {"x": 5.0, "y": 5.0}, smiles=f"S{index}", iteration=index)
        )
        check_exit_conditions_node(state)

    assert state.status != WorkflowStatus.COMPLETED
    assert state.current_iteration == 3
    assert all(item["best_metric"] is None for item in state.profiling["ranking_history"])